"""Structural detectors for the five DAO governance risk classes.

Each detector consumes the SourceUnit model and emits rich Findings with
location, root cause, trigger conditions, impact, a false-positive assessment,
and remediation. Detectors "fire" (report) only when they judge the risk
present; when a mitigation is detected they stay silent (so the benchmark's
false-positive / false-negative accounting is meaningful).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from .solmodel import SourceUnit, ContractDef, FunctionDef


@dataclass
class Finding:
    rule_id: str
    title: str
    severity: str
    category: str
    file: str
    contract: str
    function: str
    line: int
    code: str
    root_cause: str
    trigger_conditions: str
    impact: str
    confidence: float                 # 0..1 detector confidence it is a true positive
    fp_likelihood: str                # low | medium | high
    fp_reasoning: str
    remediation: str
    signals: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------
# shared heuristics
# --------------------------------------------------------------------------
GATE_MODIFIER_HINTS = ("only", "auth", "restricted", "requires")
GATE_MODIFIER_EXACT = {
    "auth", "requiresAuth", "restricted", "initializer", "onlyInitializing",
    "nonReentrant",  # not a gate but recorded; filtered where it matters
}
_ROLE_CHECK_RE = re.compile(r"(hasRole\s*\(|_checkRole\s*\(|require\s*\(\s*msg\.sender\s*==|"
                            r"require\s*\(\s*_?msg[Ss]ender\(\)\s*==|onlyOwner|_checkOwner)")
_INIT_GUARD_RE = re.compile(r"(require\s*\(\s*!\s*\w*[Ii]nitialized|"
                            r"require\s*\(\s*\w*[Ii]nitialized\s*==\s*false|"
                            r"_disableInitializers|initializer\b|onlyInitializing)")
_GOV_HINT_RE = re.compile(r"(propose|proposal|castvote|castballot|ballot|quorum|forvotes|"
                          r"againstvotes|getvotes|steward|council|enact|"
                          r"votingperiod|votingdelay|votingwindow|snapshotblock)", re.I)


def line_at(su: SourceUnit, offset: int) -> int:
    return su.masked.count("\n", 0, offset) + 1


def _snippet(su: SourceUnit, line: int) -> str:
    lines = su.text.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:180]
    return ""


def _is_gate_modifier(name: str) -> bool:
    n = name.lower()
    if name in GATE_MODIFIER_EXACT:
        return name != "nonReentrant"
    return any(n.startswith(h) for h in GATE_MODIFIER_HINTS)


def has_access_gate(fn: FunctionDef) -> bool:
    if any(_is_gate_modifier(m) for m in fn.modifiers):
        return True
    if _ROLE_CHECK_RE.search(fn.body):
        return True
    return False


def has_init_guard(fn: FunctionDef) -> bool:
    if any(m in ("initializer", "onlyInitializing") for m in fn.modifiers):
        return True
    return bool(_INIT_GUARD_RE.search(fn.body))


def is_governance_contract(c: ContractDef) -> bool:
    # Voting-specific signals only (scanned on masked text so comments/strings
    # cannot create a false classification). A treasury that merely has an
    # execute() is NOT a governor.
    return bool(_GOV_HINT_RE.search(c.body_masked))


# --------------------------------------------------------------------------
# D1 — governance seat hijacking (VULN-1)
# --------------------------------------------------------------------------
_PRIV_WRITE_RE = re.compile(
    r"\b(isCouncil|councilMembers?|isAdmin|admins?|isGovernor|roles?|_roles|"
    r"owner|_owner|hasRole)\b\s*(\[[^\]]*\])?\s*=", )
_INIT_NAME_RE = re.compile(r"^(initialize|init|setup|bootstrap|configure|__\w+_init)", re.I)


def detect_seat_hijack(su: SourceUnit) -> List[Finding]:
    out: List[Finding] = []
    for c in su.contracts:
        if not c.is_concrete:
            continue
        for fn in c.functions:
            if fn.kind != "function" or not fn.is_externally_callable() or not fn.is_state_changing():
                continue
            name_init = bool(_INIT_NAME_RE.match(fn.name))
            writes_priv = bool(_PRIV_WRITE_RE.search(fn.body))
            if not (name_init or writes_priv):
                continue
            gated = has_access_gate(fn)
            guarded = has_init_guard(fn)
            if gated or guarded:
                continue  # mitigated -> do not fire
            # fire
            signals = []
            if name_init:
                signals.append("init-like function name")
            if writes_priv:
                signals.append("writes privileged state (council/admin/role/owner)")
            # confidence & FP
            conf = 0.9 if (name_init and writes_priv) else 0.7
            if name_init and not writes_priv:
                fp, fpr = "medium", "初始化函数但未检出特权状态写入；需人工确认是否授予权限"
            else:
                fp, fpr = "low", "特权状态写入 + 无 owner/role 门禁 + 无一次性初始化守卫"
            out.append(Finding(
                rule_id="VULN-1",
                title="治理席位劫持 / Governance seat hijacking",
                severity="Critical", category="access-control",
                file=su.path, contract=c.name, function=fn.name, line=fn.header_line,
                code=_snippet(su, fn.header_line),
                root_cause="特权席位/角色的初始化或赋权函数对外可调用，且缺少 owner/role 门禁与一次性初始化守卫。",
                trigger_conditions="任意外部账户调用该函数（或重复调用），将自身写入 council/admin/role/owner。",
                impact="攻击者获得治理特权席位，可操纵提案、金库指针或角色，等同接管治理。",
                confidence=conf, fp_likelihood=fp, fp_reasoning=fpr,
                remediation="加一次性守卫 require(!initialized) + onlyOwner/initializer；赋权走提案+时间锁；"
                            "用 OZ AccessControl 并在部署后撤销部署者 admin。",
                signals=signals,
            ))
    return out


# --------------------------------------------------------------------------
# D2 — flash-loan vote manipulation (VULN-2)
# --------------------------------------------------------------------------
_VOTE_NAME_RE = re.compile(r"(castvote|_?countvote|_castvote|vote|ballot)", re.I)
_LIVE_BAL_RE = re.compile(r"\b(balanceOf|getVotes)\s*\(")
_PAST_VOTES_RE = re.compile(r"\b(getPastVotes|getPriorVotes|balanceOfAt|getPastTotalSupply|"
                            r"checkpoints?|_checkpoint|snapshot)\b", re.I)


def detect_flashloan_vote(su: SourceUnit) -> List[Finding]:
    out: List[Finding] = []
    for c in su.contracts:
        if not c.is_concrete or not is_governance_contract(c):
            continue
        for fn in c.functions:
            if fn.kind != "function":
                continue
            looks_vote = bool(_VOTE_NAME_RE.search(fn.name))
            uses_live = bool(_LIVE_BAL_RE.search(fn.body))
            if not (looks_vote and uses_live):
                continue
            if _PAST_VOTES_RE.search(fn.body) or _PAST_VOTES_RE.search(c.body_masked):
                continue  # snapshot-based -> mitigated
            m = _LIVE_BAL_RE.search(fn.body)
            line = line_at(su, fn.body_offset + m.start()) if m else fn.header_line
            out.append(Finding(
                rule_id="VULN-2",
                title="闪电贷投票操纵 / Flash-loan vote manipulation",
                severity="Critical", category="governance-voting",
                file=su.path, contract=c.name, function=fn.name, line=line,
                code=_snippet(su, line),
                root_cause="投票权重取自实时余额 balanceOf/getVotes，未使用历史区块快照 (getPastVotes)。",
                trigger_conditions="存在可借入治理代币的途径（闪电贷/借贷）；攻击者单笔交易内借币→投票→归还。",
                impact="零成本达成法定票数，操纵任意提案结果（含恶意金库提案）。",
                confidence=0.85, fp_likelihood="low",
                fp_reasoning="治理上下文 + 投票函数内实时余额计票 + 全合约无快照调用。",
                remediation="采用 ERC20Votes 检查点；castVote 用 getPastVotes(voter, snapshotBlock)，"
                            "快照设在提案创建区块之前；totalSupply 亦用历史快照。",
                signals=["vote-like function", "live balanceOf/getVotes", "no snapshot call"],
            ))
    return out


# --------------------------------------------------------------------------
# D3 — missing governance timelock (VULN-3)
# --------------------------------------------------------------------------
_EXEC_NAME_RE = re.compile(r"^(execute|_execute|executeProposal|_executeProposal|"
                           r"enact|_enact|perform|finalize|_finalize|run)$", re.I)
_EXEC_CALL_RE = re.compile(r"(\.call\s*\{|\.call\s*\(|treasury\.execute|\.functionCall|"
                           r"\.delegatecall|target\.call)", re.I)
_TIMELOCK_RE = re.compile(r"\b(eta|minDelay|timelock|timeLock|queue|readyTimestamp|"
                          r"delay|getTimestamp|_timelock)\b", re.I)
# A genuine proposal-execution path consumes a vote outcome; a treasury's
# plain execute() does not. Require this so D3 targets governors, not treasuries.
_VOTE_OUTCOME_RE = re.compile(r"(quorum|forVotes|againstVotes|\byes\b|votes|proposal|"
                              r"ballot|deadline|closes|voteEnd|snapshotBlock)", re.I)


def detect_missing_timelock(su: SourceUnit) -> List[Finding]:
    out: List[Finding] = []
    for c in su.contracts:
        if not c.is_concrete or not is_governance_contract(c):
            continue
        has_timelock = bool(_TIMELOCK_RE.search(c.body_masked)) or \
            any("timelock" in b.lower() for b in c.bases)
        for fn in c.functions:
            if fn.kind != "function" or not _EXEC_NAME_RE.match(fn.name):
                continue
            if not _EXEC_CALL_RE.search(fn.body):
                continue
            if not _VOTE_OUTCOME_RE.search(fn.body):
                continue  # not a proposal-execution path (e.g. a treasury bridge)
            # does THIS execute path check a delay/eta?
            local_timelock = bool(_TIMELOCK_RE.search(fn.body))
            if has_timelock or local_timelock:
                continue  # mitigated
            out.append(Finding(
                rule_id="VULN-3",
                title="治理时间锁缺失 / Missing governance timelock",
                severity="High", category="governance-process",
                file=su.path, contract=c.name, function=fn.name, line=fn.header_line,
                code=_snippet(su, fn.header_line),
                root_cause="提案执行函数在通过后立即对外调用，无 queue/eta/minDelay 延迟。",
                trigger_conditions="提案达到法定票数后，攻击者或提案人立即 execute，中间无延迟窗。",
                impact="持币人与守护者无退出或否决窗口，恶意提案可即时落地（常与投票操纵联动）。",
                confidence=0.75, fp_likelihood="medium",
                fp_reasoning="execute 直接外呼且全合约无时间锁信号；若延迟逻辑在外部 Timelock 合约则为误报，需确认执行路径。",
                remediation="引入 TimelockController：通过→queue(eta=now+minDelay)→execute(require now>=eta)；"
                            "minDelay>=2 天；守护者可在窗内 cancel/pause。",
                signals=["execute function", "external call in execute", "no timelock signal"],
            ))
    return out


# --------------------------------------------------------------------------
# D4 — over-privileged treasury / arbitrary external call (VULN-4)
# --------------------------------------------------------------------------
_ARBITRARY_CALL_RE = re.compile(r"(\w+)\s*\.\s*call\s*\{[^}]*\}\s*\(|(\w+)\s*\.\s*call\s*\(|"
                                r"(\w+)\s*\.\s*delegatecall\s*\(")
_BYTES_PARAM_RE = re.compile(r"\bbytes\s+(calldata|memory)\s+\w+")
_ALLOWLIST_RE = re.compile(r"\b(allowedTarget|allowedRecipient|whitelist|isAllowed|allowlist|"
                           r"allowed\w*)\b", re.I)
_CAP_RE = re.compile(r"\b(epochCap|EpochCap|spendingLimit|spendLimit|cap|limit|maxAmount|"
                     r"rateLimit|budget)\b", re.I)
_SNAPSHOT_SIG_RE = re.compile(r"(getPastVotes|getPriorVotes|snapshotBlock|balanceOfAt|"
                              r"getPastTotalSupply|checkpoints?)", re.I)


def _has_sound_governance(c: ContractDef) -> bool:
    """A contract that executes arbitrary proposal calls is NOT over-privileged
    if that power is gated by BOTH snapshot voting and a timelock (the standard
    Governor+Timelock pattern)."""
    return bool(_SNAPSHOT_SIG_RE.search(c.body_masked)) and bool(_TIMELOCK_RE.search(c.body_masked))


# A Timelock's job is to make arbitrary calls, but the SAFE pattern gates each
# call by a per-operation queued-hash check AND an elapsed-delay check (plus an
# admin gate). That is constrained, not over-privileged.
_QUEUED_HASH_RE = re.compile(r"(queuedTransactions\s*\[|queued\s*\[|_timestamps\s*\[|"
                             r"isOperationReady|_operationState|timestamps\s*\[)", re.I)
_DELAY_CHECK_RE = re.compile(r"(>=\s*eta|getBlockTimestamp\(\)\s*>=|block\.timestamp\s*>=|"
                             r"isOperationReady|getTimestamp\s*\()", re.I)


def _is_timelock_exec(fn: FunctionDef) -> bool:
    return bool(_QUEUED_HASH_RE.search(fn.body)) and bool(_DELAY_CHECK_RE.search(fn.body))


def _call_has_dynamic_data(body: str, call_m) -> bool:
    """True if the low-level call passes a variable (not empty/constant) as data.
    Masking turns string/hex literals into spaces, so an identifier inside the
    argument list signals attacker/governance-controlled calldata."""
    open_paren = body.find("(", call_m.end() - 1)
    if open_paren < 0:
        return False
    depth = 0
    end = -1
    for i in range(open_paren, len(body)):
        if body[i] == "(":
            depth += 1
        elif body[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return False
    arg = body[open_paren + 1:end]
    return bool(re.search(r"[A-Za-z_]\w*", arg))


def detect_over_privileged_treasury(su: SourceUnit) -> List[Finding]:
    out: List[Finding] = []
    for c in su.contracts:
        if not c.is_concrete:
            continue
        has_allowlist = bool(_ALLOWLIST_RE.search(c.body_masked))
        has_cap = bool(_CAP_RE.search(c.body_masked))
        sound_gov = _has_sound_governance(c)
        for fn in c.functions:
            if fn.kind != "function" or not fn.is_externally_callable():
                continue
            call_m = _ARBITRARY_CALL_RE.search(fn.body)
            if not call_m:
                continue
            has_bytes_param = bool(_BYTES_PARAM_RE.search(fn.header_masked))
            dynamic_data = _call_has_dynamic_data(fn.body, call_m)
            if not (has_bytes_param or dynamic_data):
                continue  # e.g. to.call{value:v}("") with empty/constant data
            # arbitrary call bridge. Is it constrained?
            local_allow = bool(_ALLOWLIST_RE.search(fn.body))
            if ((has_allowlist or local_allow) and has_cap) or sound_gov or _is_timelock_exec(fn):
                continue  # allowlist+cap, snapshot+timelock, or a constrained
                          # timelock (admin + queued-hash + elapsed delay) -> mitigated
            # partial mitigation lowers confidence / raises FP
            if has_allowlist or local_allow:
                conf, fp, fpr = 0.6, "medium", "存在目标白名单但未见额度上限，残余过度授权风险。"
                sev = "High"
            else:
                conf, fp, fpr = 0.9, "low", "任意 target+calldata 外呼，无白名单/额度/熔断，单次调用可清空资产。"
                sev = "Critical"
            line = line_at(su, fn.body_offset + call_m.start())
            out.append(Finding(
                rule_id="VULN-4",
                title="金库权限过大 / Over-privileged treasury (arbitrary call)",
                severity=sev, category="treasury",
                file=su.path, contract=c.name, function=fn.name, line=line,
                code=_snippet(su, line),
                root_cause="对外暴露任意 (target,value,data) 低级调用，仅以单一角色为门禁，无目标白名单/额度/熔断。",
                trigger_conditions="具备该角色（或通过治理获得）后，一次调用即可转走任意资产或改写任意合约状态。",
                impact="单笔提案/调用原子清空全部金库资产或劫持外部合约。",
                confidence=conf, fp_likelihood=fp, fp_reasoning=fpr,
                remediation="最小权限化：代币流出走白名单收款+按资产按周期额度；运营调用限目标白名单；"
                            "守护者熔断 pause；大额多签+时间锁。",
                signals=["bytes calldata/memory param", "low-level call/delegatecall",
                         f"allowlist={has_allowlist or local_allow}", f"cap={has_cap}"],
            ))
    return out


# --------------------------------------------------------------------------
# D5 — DEX spot-price oracle manipulation (VULN-5)
# --------------------------------------------------------------------------
_SPOT_RE = re.compile(r"(getReserves\s*\(|spotPrice|reserve1\s*\*|reserve0\s*\*|"
                      r"\.price0\s*\(|\.price1\s*\(|reserve[01]\s*\)\s*\*\s*1e18)", re.I)
_PRICING_CTX_RE = re.compile(r"\b(price|value|valuation|collateral|borrow|maxBorrow|"
                             r"redeem|mint|quote|worth|ltv)\b", re.I)
_TWAP_RE = re.compile(r"\b(TWAP|twap|cumulative|price0Cumulative|price1Cumulative|consult|"
                      r"observe|latestRoundData|AggregatorV3|chainlink|priceAverage)\b", re.I)


def detect_oracle_manipulation(su: SourceUnit) -> List[Finding]:
    out: List[Finding] = []
    for c in su.contracts:
        if not c.is_concrete:
            continue
        twap_whole = bool(_TWAP_RE.search(c.body_masked))
        for fn in c.functions:
            if fn.kind != "function":
                continue
            spot_m = _SPOT_RE.search(fn.body)
            if not spot_m:
                continue
            pricing_ctx = bool(_PRICING_CTX_RE.search(fn.body)) or bool(_PRICING_CTX_RE.search(fn.name))
            if not pricing_ctx:
                continue
            if _TWAP_RE.search(fn.body) or twap_whole:
                continue  # uses TWAP / external feed -> mitigated
            line = line_at(su, fn.body_offset + spot_m.start())
            out.append(Finding(
                rule_id="VULN-5",
                title="DEX 流动性池操纵 / DEX spot-price oracle manipulation",
                severity="Critical", category="oracle",
                file=su.path, contract=c.name, function=fn.name, line=line,
                code=_snippet(su, line),
                root_cause="用 AMM 瞬时现货价/储备比为资产定价，无 TWAP/外部喂价。",
                trigger_conditions="攻击者用一笔（常为闪电贷驱动的）大额兑换在单区块内扭曲现货价后触发定价路径。",
                impact="超额借出/错误铸赎，耗尽池/金库资产。",
                confidence=0.8, fp_likelihood="low",
                fp_reasoning="定价/估值函数内读取现货价/储备且无 TWAP/外部喂价。若此价仅用于展示则为误报。",
                remediation="改用抗操纵价源：Uniswap V2/V3 TWAP（窗口>=30min）或 Chainlink；"
                            "加偏离阈值与陈旧性检查；禁止直接用 getReserves/现货价定价。",
                signals=["spot reserves/price read", "pricing/valuation context", "no TWAP/feed"],
            ))
    return out


ALL_DETECTORS = [
    detect_seat_hijack,
    detect_flashloan_vote,
    detect_missing_timelock,
    detect_over_privileged_treasury,
    detect_oracle_manipulation,
]


def run_detectors(su: SourceUnit) -> List[Finding]:
    findings: List[Finding] = []
    for det in ALL_DETECTORS:
        findings.extend(det(su))
    return findings
