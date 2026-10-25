# X100 DAO 治理合约安全审计报告

> 防御性安全研究。仅审计授权合约；全部漏洞在**本地测试链 + 模拟金库**上复现；不接触真实资产，不向主网发送交易。

- 审计工程: `/home/user/1hao-/dao_audit`
- 生成时间: 2026-10-25T01:10:26+00:00
- 授权委托: liwanrou1990@gmail.com
- 本地链: http://127.0.0.1:8545
- 结论: **5/5 项已确认**，其中 **5 项已验证修复有效**。

## 汇总

| ID | 风险 | 严重程度 | 状态 | PoC | 修复验证 |
|----|------|----------|------|-----|----------|
| VULN-1 | 治理席位劫持 | 严重 | 🔴 已确认 | ✅ 复现 | ✅ 已验证 |
| VULN-2 | 闪电贷投票操纵 | 严重 | 🔴 已确认 | ✅ 复现 | ✅ 已验证 |
| VULN-4 | 金库权限过大 | 严重 | 🔴 已确认 | ✅ 复现 | ✅ 已验证 |
| VULN-5 | DEX 流动性池操纵 | 严重 | 🔴 已确认 | ✅ 复现 | ✅ 已验证 |
| VULN-3 | 治理时间锁缺失 | 高 | 🔴 已确认 | ✅ 复现 | ✅ 已验证 |

## VULN-1 — 治理席位劫持 / Governance seat hijacking

- **严重程度**: 严重 / Critical
- **状态**: 🔴 已确认
- **分类**: access-control  ·  **SWC**: SWC-105 (Unprotected function) / SWC-118 (Incorrect constructor/init)  ·  **CWE**: CWE-284 Improper Access Control; CWE-665 Improper Initialization

**说明**: 特权治理席位（council/admin/role）可被任意账户初始化或重复初始化，攻击者据此把自己设为唯一席位持有者，接管治理。

**漏洞位置**:
- `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Governor.sol:74` — 初始化函数缺少 onlyOwner/initializer 保护
  ```solidity
  function initializeCouncil(address[] calldata members) external {
  ```

**测试证据 (本地链)**:
- PoC `SeatHijackTest`: ✅ 漏洞在本地链被复现  — test_attacker_hijacks_council_seat=PASS
- 修复回归 `SeatHijackFixedTest`: ✅ 同一攻击对修复版失败，修复有效  — test_reinit_reverts_and_attacker_not_seated=PASS

**修复建议**: 初始化函数加一次性守卫 (require(!initialized)) 并限制 onlyOwner；席位变更走提案+时间锁；使用 OpenZeppelin AccessControl 的 DEFAULT_ADMIN_ROLE 并在部署后撤销部署者权限。

## VULN-2 — 闪电贷投票操纵 / Flash-loan vote manipulation

- **严重程度**: 严重 / Critical
- **状态**: 🔴 已确认
- **分类**: governance-voting  ·  **SWC**: SWC-132 (Unexpected balance) / governance snapshot missing  ·  **CWE**: CWE-682 Incorrect Calculation; CWE-840 Business Logic Errors

**说明**: 投票权重在投票当刻读取 token.balanceOf()（实时余额），未在提案创建时快照，攻击者可用闪电贷在单笔交易内借入大量代币投票后归还，零成本操纵结果。

**漏洞位置**:
- `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Governor.sol:110` — 投票权重来自实时余额
  ```solidity
  uint256 weight = token.balanceOf(msg.sender);
  ```

**测试证据 (本地链)**:
- PoC `FlashLoanVoteTest`: ✅ 漏洞在本地链被复现  — test_flash_loan_swings_a_vote=PASS
- 修复回归 `FlashVoteFixedTest`: ✅ 同一攻击对修复版失败，修复有效  — test_flash_loan_vote_has_zero_weight=PASS

**修复建议**: 采用 ERC20Votes/检查点，castVote 使用 getPastVotes(voter, snapshotBlock)，快照点设为提案创建区块之前；totalSupply 也用历史快照；设置提案门槛与投票延迟。

## VULN-4 — 金库权限过大 / Over-privileged treasury

- **严重程度**: 严重 / Critical
- **状态**: 🔴 已确认
- **分类**: treasury  ·  **SWC**: SWC-105 (Unprotected) / arbitrary external call  ·  **CWE**: CWE-284 Improper Access Control; CWE-862 Missing Authorization

**说明**: 金库对外暴露任意调用 (target+calldata+value)，仅以单一 governor 地址为唯一门禁，无目标白名单、无额度上限、无熔断，一笔通过的提案即可原子清空全部资产。

**漏洞位置**:
- `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Governor.sol:11` — 任意外部调用桥 (target,value,data)
  ```solidity
  function execute(address target, uint256 value, bytes calldata data) external returns (bytes memory);
  ```
- `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Treasury.sol:36` — 任意外部调用桥 (target,value,data)
  ```solidity
  function execute(address target, uint256 value, bytes calldata data)
  ```
- `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Treasury.sol:41` — 无约束的低级 call
  ```solidity
  (bool ok, bytes memory ret) = target.call{value: value}(data);
  ```

**测试证据 (本地链)**:
- PoC `TreasuryOverprivilegeTest`: ✅ 漏洞在本地链被复现  — test_single_proposal_drains_entire_treasury=PASS
- 修复回归 `TreasuryFixedTest`: ✅ 同一攻击对修复版失败，修复有效  — test_arbitrary_drain_to_token_reverts=PASS, test_guardian_can_pause=PASS, test_paytoken_is_capped_and_allowlisted=PASS

**修复建议**: 最小权限化：移除无约束任意调用；对代币流出用专用 payToken(白名单收款+按资产按周期额度)；运营调用限定目标白名单；加守护者熔断 (pause)；大额走多签+时间锁。

## VULN-5 — DEX 流动性池操纵 / DEX spot-price oracle manipulation

- **严重程度**: 严重 / Critical
- **状态**: 🔴 已确认
- **分类**: oracle  ·  **SWC**: SWC-undefined (price oracle manipulation)  ·  **CWE**: CWE-20 Improper Input Validation; CWE-682 Incorrect Calculation

**说明**: 合约以 AMM 瞬时现货价 (reserves/spotPrice) 作为预言机为抵押品定价，攻击者用一笔（常为闪电贷驱动的）大额兑换在单区块内拉高价格后超额借出。

**漏洞位置**:
- `/home/user/1hao-/dao_audit/contracts/targets/VulnOracleVault.sol:5` — 读取瞬时现货价
  ```solidity
  function spotPrice0() external view returns (uint256);
  ```
- `/home/user/1hao-/dao_audit/contracts/targets/VulnOracleVault.sol:50` — 读取瞬时现货价
  ```solidity
  uint256 price = pair.spotPrice0();
  ```
- `/home/user/1hao-/dao_audit/contracts/targets/VulnOracleVault.sol:6` — 直接读取储备计价
  ```solidity
  function getReserves() external view returns (uint112 r0, uint112 r1);
  ```

**测试证据 (本地链)**:
- PoC `DexOracleManipulationTest`: ✅ 漏洞在本地链被复现  — test_spot_oracle_lets_attacker_overborrow=PASS
- 修复回归 `OracleFixedTest`: ✅ 同一攻击对修复版失败，修复有效  — test_twap_blocks_single_block_manipulation=PASS

**修复建议**: 改用抗操纵价源：Uniswap V2/V3 TWAP（最小窗口 ≥ 30min）或 Chainlink 等外部喂价；加偏离阈值与陈旧性检查；禁止直接用 getReserves/现货价为抵押品定价。

## VULN-3 — 治理时间锁缺失 / Missing governance timelock

- **严重程度**: 高 / High
- **状态**: 🔴 已确认
- **分类**: governance-process  ·  **SWC**: SWC-undefined (governance process)  ·  **CWE**: CWE-362 Race Condition; CWE-841 Improper Enforcement of Behavioral Workflow

**说明**: 提案通过后立即执行，审批与执行之间无延迟，持币人与守护者没有退出或否决的时间窗。

**漏洞位置**:
- `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Governor.sol:122` — execute 中直接调用金库，无队列/延迟
  ```solidity
  function execute(uint256 id) external returns (bytes memory ret) {
  ```
- `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Governor.sol:50` — 投票期可能过短
  ```solidity
  uint64 public votingPeriod = 1;
  ```

**测试证据 (本地链)**:
- PoC `MissingTimelockTest`: ✅ 漏洞在本地链被复现  — test_no_delay_between_approval_and_execution=PASS
- 修复回归 `TimelockFixedTest`: ✅ 同一攻击对修复版失败，修复有效  — test_execute_blocked_until_timelock=PASS

**修复建议**: 引入 TimelockController：提案通过→queue(设 eta=now+minDelay)→execute(require now>=eta)；minDelay 建议 ≥ 2 天；赋予守护者在延迟窗内 cancel/pause 的能力。

---
_本报告由 dao_audit 自动生成。靶标合约为授权研究用途，禁止部署到主网。_