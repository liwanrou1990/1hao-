"""Finding catalog for the five DAO governance risk classes.

Each rule carries: id, title, category, severity, SWC/CWE mapping, the static
detection signature (regex patterns over Solidity source), the remediation
guidance, and the names of the Foundry PoC test and the regression (fix) test
that serve as dynamic evidence.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    category: str
    severity: str  # Critical | High | Medium | Low
    swc: str
    cwe: str
    description: str
    # Regex signatures: each tuple is (regex, why). A hit flags a candidate line.
    signatures: List[tuple] = field(default_factory=list)
    # Negative signatures: presence suggests the issue is mitigated.
    mitigations: List[tuple] = field(default_factory=list)
    remediation: str = ""
    poc_test: str = ""
    fix_test: str = ""


RULES: List[Rule] = [
    Rule(
        id="VULN-1",
        title="治理席位劫持 / Governance seat hijacking",
        category="access-control",
        severity="Critical",
        swc="SWC-105 (Unprotected function) / SWC-118 (Incorrect constructor/init)",
        cwe="CWE-284 Improper Access Control; CWE-665 Improper Initialization",
        description=(
            "特权治理席位（council/admin/role）可被任意账户初始化或重复初始化，"
            "攻击者据此把自己设为唯一席位持有者，接管治理。"
        ),
        signatures=[
            (r"function\s+initialize\w*\s*\([^)]*\)\s*(external|public)(?![^{]*onlyOwner)(?![^{]*initializer)",
             "初始化函数缺少 onlyOwner/initializer 保护"),
            (r"(isCouncil|councilMembers|admins?)\[[^\]]+\]\s*=\s*true",
             "直接写入特权席位映射"),
        ],
        mitigations=[
            (r"require\(\s*!?\w*[Ii]nitialized", "存在一次性初始化守卫"),
            (r"require\(\s*msg\.sender\s*==\s*owner", "owner 门禁"),
        ],
        remediation=(
            "初始化函数加一次性守卫 (require(!initialized)) 并限制 onlyOwner；"
            "席位变更走提案+时间锁；使用 OpenZeppelin AccessControl 的 DEFAULT_ADMIN_ROLE "
            "并在部署后撤销部署者权限。"
        ),
        poc_test="SeatHijackTest",
        fix_test="SeatHijackFixedTest",
    ),
    Rule(
        id="VULN-2",
        title="闪电贷投票操纵 / Flash-loan vote manipulation",
        category="governance-voting",
        severity="Critical",
        swc="SWC-132 (Unexpected balance) / governance snapshot missing",
        cwe="CWE-682 Incorrect Calculation; CWE-840 Business Logic Errors",
        description=(
            "投票权重在投票当刻读取 token.balanceOf()（实时余额），未在提案创建时快照，"
            "攻击者可用闪电贷在单笔交易内借入大量代币投票后归还，零成本操纵结果。"
        ),
        signatures=[
            (r"balanceOf\(\s*msg\.sender\s*\)", "投票权重来自实时余额"),
            (r"token\.balanceOf\([^)]*\)\s*;?\s*$", "直接以实时余额计票"),
        ],
        mitigations=[
            (r"getPastVotes\(", "使用历史区块快照票权"),
            (r"getPastTotalSupply\(|snapshot", "快照机制"),
        ],
        remediation=(
            "采用 ERC20Votes/检查点，castVote 使用 getPastVotes(voter, snapshotBlock)，"
            "快照点设为提案创建区块之前；totalSupply 也用历史快照；设置提案门槛与投票延迟。"
        ),
        poc_test="FlashLoanVoteTest",
        fix_test="FlashVoteFixedTest",
    ),
    Rule(
        id="VULN-3",
        title="治理时间锁缺失 / Missing governance timelock",
        category="governance-process",
        severity="High",
        swc="SWC-undefined (governance process)",
        cwe="CWE-362 Race Condition; CWE-841 Improper Enforcement of Behavioral Workflow",
        description=(
            "提案通过后立即执行，审批与执行之间无延迟，持币人与守护者没有退出或否决的时间窗。"
        ),
        signatures=[
            (r"function\s+execute\s*\([^)]*\)[^{]*\{(?:[^}]*)treasury\.(execute|call)",
             "execute 中直接调用金库，无队列/延迟"),
            (r"votingPeriod\s*=\s*[0-9]+\s*;", "投票期可能过短"),
        ],
        mitigations=[
            (r"(eta|minDelay|timelock|queue)\b", "存在时间锁/队列机制"),
        ],
        remediation=(
            "引入 TimelockController：提案通过→queue(设 eta=now+minDelay)→execute(require now>=eta)；"
            "minDelay 建议 ≥ 2 天；赋予守护者在延迟窗内 cancel/pause 的能力。"
        ),
        poc_test="MissingTimelockTest",
        fix_test="TimelockFixedTest",
    ),
    Rule(
        id="VULN-4",
        title="金库权限过大 / Over-privileged treasury",
        category="treasury",
        severity="Critical",
        swc="SWC-105 (Unprotected) / arbitrary external call",
        cwe="CWE-284 Improper Access Control; CWE-862 Missing Authorization",
        description=(
            "金库对外暴露任意调用 (target+calldata+value)，仅以单一 governor 地址为唯一门禁，"
            "无目标白名单、无额度上限、无熔断，一笔通过的提案即可原子清空全部资产。"
        ),
        signatures=[
            (r"function\s+execute\s*\(\s*address\s+\w+\s*,\s*uint256\s+\w+\s*,\s*bytes\s+calldata",
             "任意外部调用桥 (target,value,data)"),
            (r"\.call\{value:\s*\w+\}\(\s*\w+\s*\)", "无约束的低级 call"),
        ],
        mitigations=[
            (r"allowedTarget\[|allowedRecipient\[", "目标/收款白名单"),
            (r"(epochCap|EpochCap|spendingLimit|tokenEpochCap)", "支出额度上限"),
            (r"(paused|guardian)", "熔断/守护者"),
        ],
        remediation=(
            "最小权限化：移除无约束任意调用；对代币流出用专用 payToken(白名单收款+按资产按周期额度)；"
            "运营调用限定目标白名单；加守护者熔断 (pause)；大额走多签+时间锁。"
        ),
        poc_test="TreasuryOverprivilegeTest",
        fix_test="TreasuryFixedTest",
    ),
    Rule(
        id="VULN-5",
        title="DEX 流动性池操纵 / DEX spot-price oracle manipulation",
        category="oracle",
        severity="Critical",
        swc="SWC-undefined (price oracle manipulation)",
        cwe="CWE-20 Improper Input Validation; CWE-682 Incorrect Calculation",
        description=(
            "合约以 AMM 瞬时现货价 (reserves/spotPrice) 作为预言机为抵押品定价，"
            "攻击者用一笔（常为闪电贷驱动的）大额兑换在单区块内拉高价格后超额借出。"
        ),
        signatures=[
            (r"spotPrice0?\(\)", "读取瞬时现货价"),
            (r"getReserves\(\)", "直接读取储备计价"),
            (r"reserve1?\s*\*\s*1e18\s*/\s*reserve0", "用储备比做价格"),
        ],
        mitigations=[
            (r"(TWAP|price0CumulativeLast|priceCumulative|consult\(\))", "使用 TWAP/累积价"),
            (r"(chainlink|AggregatorV3|latestRoundData)", "使用外部喂价"),
        ],
        remediation=(
            "改用抗操纵价源：Uniswap V2/V3 TWAP（最小窗口 ≥ 30min）或 Chainlink 等外部喂价；"
            "加偏离阈值与陈旧性检查；禁止直接用 getReserves/现货价为抵押品定价。"
        ),
        poc_test="DexOracleManipulationTest",
        fix_test="OracleFixedTest",
    ),
]

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}


def rule_by_id(rid: str) -> Rule:
    for r in RULES:
        if r.id == rid:
            return r
    raise KeyError(rid)
