"""V0.13 semantic governance analyzer.

A structural/semantic pass over the source model (NOT a compiler AST — Slither
and multi-version solc are unavailable in this environment, and these historical
contracts span solc 0.5–0.8 with full dependency trees that do not compile
standalone here; this is honestly a source-model analyzer, not an AST/Slither
pipeline). It upgrades the regex/name detectors with:

  * an inheritance-aware, cross-file CALL GRAPH: a function's "effective body" is
    its own body plus the bodies of the internal/base functions it transitively
    calls (so a sink inside an internal helper is attributed to its external
    entrypoint — the Audius guardianExecuteTransaction -> _executeTransaction gap);
  * ACCESS-CONTROL CONTEXT: onlyGovernance / timelock-routed / one-shot-init /
    role gates are modelled, so a normal function that is reachable ONLY through
    governance+timelock is NOT flagged (fixes the OZ Governor.relay false
    positive; honors constraint II.6);
  * generalized sinks: low-level call/delegatecall to a param/stored address AND
    Diamond `diamondCut(init,data)` count as arbitrary external calls;
  * generalized vote power: any address-indexed live balance/stake/roots read
    that feeds a vote tally without a per-proposal snapshot;
  * structure-based execution-path detection (consumes a vote outcome AND hits a
    sink) instead of a function-name whitelist (catches emergencyCommit, commit,
    _executeTransaction regardless of name).

Every finding carries a code location and the permission/call-relation evidence
(which gate protects it, and the internal call chain to the sink).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .solmodel import parse_source, SourceUnit, ContractDef, FunctionDef
from .detectors import Finding, line_at

# ---- access-control context -------------------------------------------------
_GOV_GATE_MOD = re.compile(r"^(onlygovernance|onlygovernor|ongovernance|onlytimelock|"
                           r"onlyexecutor|onlydao)$", re.I)
_GOV_GATE_BODY = re.compile(r"require\s*\(\s*(_?msg[Ss]ender\(\)|msg\.sender)\s*==\s*"
                            r"(address\(this\)|_?executor\(\)|_?governance|_?timelock|_?gov\b)", re.I)
_ROLE_GATE_MOD = re.compile(r"^(onlyowner|onlyrole|onlyadmin|onlyguardian|onlyminter|auth|"
                            r"requiresauth|restricted|onlymanager|onlykeeper|only\w+)$", re.I)
_ROLE_GATE_BODY = re.compile(r"(hasRole\s*\(|_checkRole\s*\(|_checkOwner\s*\(|onlyOwner|"
                             r"require\s*\(\s*(_?msg[Ss]ender\(\)|msg\.sender)\s*==\s*"
                             r"(owner|_owner|admin|_admin|guardian|_guardian|"
                             r"pendingAdmin|pendingOwner|pendingGovernor|pending\w+))", re.I)
# two-step ownership/admin handoff (completes a transfer the current admin began)
_ACCEPT_FN = re.compile(r"^(acceptAdmin|_acceptAdmin|acceptOwnership|acceptGovernance|"
                        r"claimOwnership|_acceptOwnership)$", re.I)
_INIT_GATE = re.compile(r"(initializer\b|onlyInitializing|_disableInitializers|"
                        r"require\s*\(\s*!\s*\w*[Ii]nitialized|require\s*\(\s*\w*[Ii]nitialized\s*==\s*false)")
# case-level timelock routing
_TIMELOCK_CASE = re.compile(r"(TimelockController|timelockcontroller|minDelay|getMinDelay|"
                            r"_timelock\b|onlyTimelock|queue\w*\(|readyTimestamp|_executors?\b|"
                            r"GovernorTimelock)", re.I)
_SNAPSHOT = re.compile(r"(getPastVotes|getPriorVotes|getPastTotalSupply|balanceOfAt|"
                       r"snapshotBlock|snapshotId|checkpoints?\b|_checkpoint)", re.I)

# ---- sinks ------------------------------------------------------------------
# matches modern `.call{...}(` / `.call(` / `.delegatecall(` AND 0.5.x
# `.call.value(v)(data)` / `.call.gas(g)(data)`.
_LOWCALL = re.compile(r"(\w+)\s*\.\s*(call|delegatecall)\s*(\.\s*(value|gas)\s*\([^)]*\)\s*)*[\({]")
_DIAMONDCUT = re.compile(r"\bdiamondCut\s*\(", re.I)
_VOTE_OUTCOME = re.compile(r"(quorum|forVotes|againstVotes|bipVotePercent|rootsFor|"
                           r"\bsupport\b|proposal\w*\.(executed|canceled|eta|forVotes)|"
                           r"state\s*\(\s*\w+\s*\)\s*==|hasSucceeded|_quorumReached|voteSucceeded)", re.I)
# A genuine TIMELOCK delay (queue/eta/minDelay), NOT a voting-deadline or
# proposal-lifecycle block.timestamp check (which a deep callee like state()
# contains and must not be mistaken for a timelock).
_TIMELOCK_DELAY = re.compile(r"(\bminDelay\b|>=\s*\w*eta\b|readyTimestamp|isOperationReady|"
                             r"getTimestamp\s*\(|\bqueue\w*\s*\(|_timelock\b|getMinDelay)", re.I)
_QUEUED_HASH = re.compile(r"(queuedTransactions\s*\[|queued\s*\[|_timestamps\s*\[|"
                          r"isOperationReady|_operationState|timestamps\s*\[)", re.I)

# ---- vote power (generalized live reads) ------------------------------------
_VOTE_FN = re.compile(r"(castvote|_castvote|countvote|recordvote|_vote\b|\bvote\b|ballot|placevote)", re.I)
_LIVE_WEIGHT = re.compile(r"\b(balanceOf|getVotes|balanceOfRoots|lockedBalance|stakedBalance|"
                          r"\w*[Ss]taked?\w*|\w*[Rr]oots\w*|deposited|votingPower|weightOf)\b\s*[\(\[]")
_PRIV_WRITE = re.compile(r"\b(isCouncil|councilMembers?|isAdmin|admins?|isGovernor|guardianAddress|"
                         r"_?owner|roles?|_roles|hasRole|guardian)\b\s*(\[[^\]]*\])?\s*=[^=]")
_INIT_NAME = re.compile(r"^(initialize|init|setup|bootstrap|configure|__\w+_init)", re.I)


@dataclass
class CaseModel:
    units: List[SourceUnit]
    # contract name -> (ContractDef, SourceUnit)
    index: Dict[str, Tuple[ContractDef, SourceUnit]] = field(default_factory=dict)

    def signals(self) -> dict:
        allmasked = "\n".join(c.body_masked for (c, _) in self.index.values())
        return {
            "has_timelock": bool(_TIMELOCK_CASE.search(allmasked)),
            "has_snapshot": bool(_SNAPSHOT.search(allmasked)),
        }


def load_case(contracts_dir: Path) -> CaseModel:
    units = [parse_source(p) for p in sorted(Path(contracts_dir).rglob("*.sol"))]
    cm = CaseModel(units=units)
    for su in units:
        for c in su.contracts:
            cm.index.setdefault(c.name, (c, su))
    return cm


def _all_fns(contract: ContractDef, cm: CaseModel, _seen=None) -> Dict[str, Tuple[FunctionDef, ContractDef, SourceUnit]]:
    """Functions of a contract plus all inherited (resolved across files)."""
    if _seen is None:
        _seen = set()
    out: Dict[str, Tuple[FunctionDef, ContractDef, SourceUnit]] = {}
    if contract.name in _seen:
        return out
    _seen.add(contract.name)
    for base in contract.bases:
        if base in cm.index:
            bc, _ = cm.index[base]
            out.update(_all_fns(bc, cm, _seen))
    su = cm.index.get(contract.name, (None, None))[1]
    for fn in contract.functions:
        out[fn.name] = (fn, contract, su)
    return out


_CALL_NAME = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
# control-flow / builtins that are not user functions to follow in the call graph
_NOT_CALLEE = {"require", "assert", "revert", "emit", "if", "for", "while", "return",
               "address", "payable", "keccak256", "abi", "new", "uint", "int", "bytes",
               "bool", "string", "this", "super", "type", "sha256", "ecrecover",
               "blockhash", "gasleft", "selfdestruct", "mapping"}


def effective_body(fn: FunctionDef, contract: ContractDef, cm: CaseModel, depth: int = 2) -> str:
    """fn body + bodies of internal/base functions it transitively calls (depth-
    bounded; control-flow/builtins are not followed)."""
    fns = _all_fns(contract, cm)
    seen = set()
    parts = []

    def walk(f: FunctionDef, d: int):
        if f.name in seen or d < 0:
            return
        seen.add(f.name)
        parts.append(f.body)
        for m in _CALL_NAME.finditer(f.body):
            callee = m.group(1)
            if callee in _NOT_CALLEE or callee == f.name:
                continue
            if callee in fns:
                walk(fns[callee][0], d - 1)
    walk(fn, depth)
    return "\n".join(parts)


def classify_gate(fn: FunctionDef, contract: ContractDef, cm: CaseModel) -> dict:
    mods = [m for m in fn.modifiers]
    gov = any(_GOV_GATE_MOD.match(m) for m in mods) or bool(_GOV_GATE_BODY.search(fn.body))
    role = any(_ROLE_GATE_MOD.match(m) for m in mods) or bool(_ROLE_GATE_BODY.search(fn.body))
    init = any(m in ("initializer", "onlyInitializing") for m in mods) or bool(_INIT_GATE.search(fn.body))
    # a governance-gated fn in a case that has timelock routing is "gov+timelock"
    gov_timelock = gov and cm.signals()["has_timelock"]
    return {"gov": gov, "role": role, "init": init, "gov_timelock": gov_timelock,
            "modifiers": mods}


def _gate_evidence(g: dict) -> str:
    tags = []
    if g["gov_timelock"]:
        tags.append("onlyGovernance+timelock路由")
    elif g["gov"]:
        tags.append("onlyGovernance(无时间锁)")
    if g["role"]:
        tags.append("角色/owner/guardian门禁")
    if g["init"]:
        tags.append("一次性init守卫")
    if not tags:
        tags.append("无有效门禁")
    if g["modifiers"]:
        tags.append("modifiers=" + ",".join(g["modifiers"]))
    return "; ".join(tags)


def _mk(su, c, fn, rule, title, sev, cat, root, trig, impact, conf, fp, fpr, sig):
    return Finding(rule_id=rule, title=title, severity=sev, category=cat,
                   file=su.path, contract=c.name, function=fn.name, line=fn.header_line,
                   code=(su.text.splitlines()[fn.header_line - 1].strip()[:180]
                         if 0 < fn.header_line <= len(su.text.splitlines()) else ""),
                   root_cause=root, trigger_conditions=trig, impact=impact,
                   confidence=conf, fp_likelihood=fp, fp_reasoning=fpr, remediation="", signals=sig)


def run_semantic_case(contracts_dir) -> List[Finding]:
    cm = load_case(Path(contracts_dir))
    sig = cm.signals()
    out: List[Finding] = []
    for (c, su) in cm.index.values():
        if not c.is_concrete:
            continue
        for fn in c.functions:
            if fn.kind != "function":
                continue
            g = classify_gate(fn, c, cm)
            eff = effective_body(fn, c, cm) if fn.is_externally_callable() else fn.body
            ev = _gate_evidence(g)

            # ---- VULN-4: unconstrained arbitrary external call controlling funds
            call_m = _LOWCALL.search(eff)
            has_diamond = bool(_DIAMONDCUT.search(eff))
            if fn.is_externally_callable() and (call_m or has_diamond):
                # effective constraints that make it NOT over-privileged
                constrained = g["gov_timelock"] or _is_timelock_exec(eff)
                if not constrained:
                    if g["gov"]:
                        pass  # gov but no timelock -> handled as VULN-3 below, not VULN-4 FP
                    else:
                        sink = "delegatecall/diamondCut" if has_diamond else "low-level call"
                        sev = "Critical" if not g["role"] else "High"
                        out.append(_mk(su, c, fn, "VULN-4",
                            "金库/任意外呼权限过大", sev, "treasury",
                            f"外部可达函数经调用链到达任意外呼汇点({sink})，无治理+时间锁/白名单/额度约束。",
                            "具该门禁(或经治理获得)即可一次调用转走任意资产或劫持外部合约。",
                            "单笔调用原子控制金库/任意合约状态。",
                            0.9 if not g["role"] else 0.6,
                            "low" if not g["role"] else "medium",
                            f"门禁: {ev}；汇点在有效调用体内(含内部函数)。",
                            [sink, f"gate:{ev}"]))

            # ---- VULN-3: proposal execution reaching a sink without timelock
            if fn.is_externally_callable() and (call_m or has_diamond) and _VOTE_OUTCOME.search(eff):
                has_delay = bool(_TIMELOCK_DELAY.search(eff)) or _is_timelock_exec(eff)
                if not has_delay and not g["gov_timelock"]:
                    out.append(_mk(su, c, fn, "VULN-3",
                        "治理提案执行缺时间锁", "High", "governance-process",
                        "执行路径消费投票结果并对外调用/delegatecall，但有效调用体内无 queue/eta/minDelay 延迟。",
                        "提案达票后可即时执行，无退出/否决窗(常与投票操纵联动)。",
                        "恶意提案即时落地,治理失守→资产损失。",
                        0.75, "medium",
                        f"门禁: {ev}；消费投票结果+外呼+无延迟。若延迟在外部合约则需确认执行路径。",
                        ["vote-outcome", "sink", "no-delay", f"gate:{ev}"]))

            # ---- VULN-2: live voting power without per-proposal snapshot
            # cast-a-vote entrypoints only — not unvote/withdraw/propose.
            is_unvote = re.match(r"^(un|withdraw|remove|retract|revoke)", fn.name, re.I)
            is_propose = re.match(r"^propose", fn.name, re.I)
            looks_vote = (not is_unvote and not is_propose and
                          (bool(_VOTE_FN.search(fn.name)) or "recordVote" in eff))
            if fn.is_externally_callable() and looks_vote and _LIVE_WEIGHT.search(eff):
                if not (_SNAPSHOT.search(eff) or sig["has_snapshot"]):
                    wm = _LIVE_WEIGHT.search(eff)
                    out.append(_mk(su, c, fn, "VULN-2",
                        "投票权异常(无快照/可临时获取)", "Critical", "governance-voting",
                        f"投票计票取自实时按地址索引的余额/质押/roots({wm.group(1) if wm else '?'})，无逐提案历史快照。",
                        "可借入/临时获取治理权重(闪电贷/质押)在单块内影响投票。",
                        "零成本或低成本达成法定票数,操纵提案(含恶意金库提案)。",
                        0.8, "low",
                        f"门禁: {ev}；投票函数有效体内实时权重读取且全案无快照。",
                        ["vote-fn", "live-weight", "no-snapshot"]))

            # ---- VULN-1: privileged-seat / init takeover without effective guard
            name_init = bool(_INIT_NAME.match(fn.name))
            writes_priv = bool(_PRIV_WRITE.search(fn.body))
            is_accept = bool(_ACCEPT_FN.match(fn.name))  # two-step handoff, legitimately gated
            if (fn.is_externally_callable() and fn.is_state_changing() and not is_accept
                    and (name_init or writes_priv)):
                if not (g["role"] or g["init"] or g["gov_timelock"]):
                    out.append(_mk(su, c, fn, "VULN-1",
                        "治理席位/初始化接管", "Critical", "access-control",
                        "对外可调用的特权赋权/初始化函数,无角色门禁/一次性init守卫/治理+时间锁。",
                        "任意账户调用(或重复调用)写入特权席位/角色/owner/guardian。",
                        "攻击者获治理特权席位,接管治理。",
                        0.85 if (name_init and writes_priv) else 0.65,
                        "low" if (name_init and writes_priv) else "medium",
                        f"门禁: {ev}；" + ("init名+特权写入" if (name_init and writes_priv) else
                                           ("init名" if name_init else "特权状态写入")),
                        ["init-name" if name_init else "", "priv-write" if writes_priv else ""]))
    # de-dup identical (rule,contract,function,line)
    seen = set()
    uniq = []
    for f in out:
        k = (f.rule_id, f.contract, f.function, f.line)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def _is_timelock_exec(body: str) -> bool:
    return bool(_QUEUED_HASH.search(body)) and bool(_TIMELOCK_DELAY.search(body))
