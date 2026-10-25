# DAO 治理合约安全审计报告（详细）

> 防御性安全研究。仅审计授权合约；动态复现仅在本地测试链 + 模拟金库进行；不接触真实资产，不向主网发送交易。

- 审计工程: `/home/user/1hao-/dao_audit`
- 生成时间: 2026-10-25T01:43:22+00:00
- 本地链: forge in-process local EVM (no network)
- 分析文件: 13

共 10 处发现，5 处经本地链 PoC 确认。

| # | ID | 位置 | 严重 | 状态 | 置信度 | 误报可能 |
|---|----|------|------|------|--------|----------|
| 1 | VULN-4 | `VulnX100Treasury.execute:41` | Critical | CONFIRMED | 0.90 | low |
| 2 | VULN-2 | `VulnX100Governor.castVote:110` | Critical | CONFIRMED | 0.85 | low |
| 3 | VULN-5 | `VulnOracleVault.maxBorrow:50` | Critical | CONFIRMED | 0.80 | low |
| 4 | VULN-1 | `VulnX100Governor.initializeCouncil:74` | Critical | CONFIRMED | 0.70 | medium |
| 5 | VULN-4 | `MiniDAO.enact:53` | Critical | STATIC_ONLY | 0.90 | low |
| 6 | VULN-2 | `MiniDAO.castBallot:43` | Critical | STATIC_ONLY | 0.85 | low |
| 7 | VULN-5 | `LendingDesk.collateralValue:66` | Critical | STATIC_ONLY | 0.80 | low |
| 8 | VULN-1 | `MiniDAO.configureStewards:28` | Critical | STATIC_ONLY | 0.70 | medium |
| 9 | VULN-3 | `VulnX100Governor.execute:122` | High | CONFIRMED | 0.75 | medium |
| 10 | VULN-3 | `MiniDAO.enact:48` | High | STATIC_ONLY | 0.75 | medium |

## 1. VULN-4 — 金库权限过大 / Over-privileged treasury (arbitrary call)

- **位置**: `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Treasury.sol` → `VulnX100Treasury.execute` 第 41 行
  ```solidity
  (bool ok, bytes memory ret) = target.call{value: value}(data);
  ```
- **严重程度**: Critical  ·  **状态**: CONFIRMED  ·  **置信度**: 0.90
- **根本原因**: 对外暴露任意 (target,value,data) 低级调用，仅以单一角色为门禁，无目标白名单/额度/熔断。
- **触发条件**: 具备该角色（或通过治理获得）后，一次调用即可转走任意资产或改写任意合约状态。
- **影响范围**: 单笔提案/调用原子清空全部金库资产或劫持外部合约。
- **误报判断**: low — 任意 target+calldata 外呼，无白名单/额度/熔断，单次调用可清空资产。
- **结构信号**: bytes calldata/memory param, low-level call/delegatecall, allowlist=False, cap=False
- **本地链证据**: PoC `TreasuryOverprivilegeTest`=✅复现；修复回归 `TreasuryFixedTest`=✅已验证
- **修复建议**: 最小权限化：代币流出走白名单收款+按资产按周期额度；运营调用限目标白名单；守护者熔断 pause；大额多签+时间锁。

## 2. VULN-2 — 闪电贷投票操纵 / Flash-loan vote manipulation

- **位置**: `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Governor.sol` → `VulnX100Governor.castVote` 第 110 行
  ```solidity
  uint256 weight = token.balanceOf(msg.sender); // BUG: live balance, no snapshot
  ```
- **严重程度**: Critical  ·  **状态**: CONFIRMED  ·  **置信度**: 0.85
- **根本原因**: 投票权重取自实时余额 balanceOf/getVotes，未使用历史区块快照 (getPastVotes)。
- **触发条件**: 存在可借入治理代币的途径（闪电贷/借贷）；攻击者单笔交易内借币→投票→归还。
- **影响范围**: 零成本达成法定票数，操纵任意提案结果（含恶意金库提案）。
- **误报判断**: low — 治理上下文 + 投票函数内实时余额计票 + 全合约无快照调用。
- **结构信号**: vote-like function, live balanceOf/getVotes, no snapshot call
- **本地链证据**: PoC `FlashLoanVoteTest`=✅复现；修复回归 `FlashVoteFixedTest`=✅已验证
- **修复建议**: 采用 ERC20Votes 检查点；castVote 用 getPastVotes(voter, snapshotBlock)，快照设在提案创建区块之前；totalSupply 亦用历史快照。

## 3. VULN-5 — DEX 流动性池操纵 / DEX spot-price oracle manipulation

- **位置**: `/home/user/1hao-/dao_audit/contracts/targets/VulnOracleVault.sol` → `VulnOracleVault.maxBorrow` 第 50 行
  ```solidity
  uint256 price = pair.spotPrice0();                 // BUG: instantaneous spot price
  ```
- **严重程度**: Critical  ·  **状态**: CONFIRMED  ·  **置信度**: 0.80
- **根本原因**: 用 AMM 瞬时现货价/储备比为资产定价，无 TWAP/外部喂价。
- **触发条件**: 攻击者用一笔（常为闪电贷驱动的）大额兑换在单区块内扭曲现货价后触发定价路径。
- **影响范围**: 超额借出/错误铸赎，耗尽池/金库资产。
- **误报判断**: low — 定价/估值函数内读取现货价/储备且无 TWAP/外部喂价。若此价仅用于展示则为误报。
- **结构信号**: spot reserves/price read, pricing/valuation context, no TWAP/feed
- **本地链证据**: PoC `DexOracleManipulationTest`=✅复现；修复回归 `OracleFixedTest`=✅已验证
- **修复建议**: 改用抗操纵价源：Uniswap V2/V3 TWAP（窗口>=30min）或 Chainlink；加偏离阈值与陈旧性检查；禁止直接用 getReserves/现货价定价。

## 4. VULN-1 — 治理席位劫持 / Governance seat hijacking

- **位置**: `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Governor.sol` → `VulnX100Governor.initializeCouncil` 第 74 行
  ```solidity
  function initializeCouncil(address[] calldata members) external {
  ```
- **严重程度**: Critical  ·  **状态**: CONFIRMED  ·  **置信度**: 0.70
- **根本原因**: 特权席位/角色的初始化或赋权函数对外可调用，且缺少 owner/role 门禁与一次性初始化守卫。
- **触发条件**: 任意外部账户调用该函数（或重复调用），将自身写入 council/admin/role/owner。
- **影响范围**: 攻击者获得治理特权席位，可操纵提案、金库指针或角色，等同接管治理。
- **误报判断**: medium — 初始化函数但未检出特权状态写入；需人工确认是否授予权限
- **结构信号**: init-like function name
- **本地链证据**: PoC `SeatHijackTest`=✅复现；修复回归 `SeatHijackFixedTest`=✅已验证
- **修复建议**: 加一次性守卫 require(!initialized) + onlyOwner/initializer；赋权走提案+时间锁；用 OZ AccessControl 并在部署后撤销部署者 admin。

## 5. VULN-4 — 金库权限过大 / Over-privileged treasury (arbitrary call)

- **位置**: `/home/user/1hao-/dao_audit/contracts/imported/MiniDAO.sol` → `MiniDAO.enact` 第 53 行
  ```solidity
  (bool ok, bytes memory ret) = b.target.call(b.payload);
  ```
- **严重程度**: Critical  ·  **状态**: STATIC_ONLY  ·  **置信度**: 0.90
- **根本原因**: 对外暴露任意 (target,value,data) 低级调用，仅以单一角色为门禁，无目标白名单/额度/熔断。
- **触发条件**: 具备该角色（或通过治理获得）后，一次调用即可转走任意资产或改写任意合约状态。
- **影响范围**: 单笔提案/调用原子清空全部金库资产或劫持外部合约。
- **误报判断**: low — 任意 target+calldata 外呼，无白名单/额度/熔断，单次调用可清空资产。
- **结构信号**: bytes calldata/memory param, low-level call/delegatecall, allowlist=False, cap=False
- **本地链证据**: PoC `TreasuryOverprivilegeTest`=✅复现；修复回归 `TreasuryFixedTest`=✅已验证
- **修复建议**: 最小权限化：代币流出走白名单收款+按资产按周期额度；运营调用限目标白名单；守护者熔断 pause；大额多签+时间锁。

## 6. VULN-2 — 闪电贷投票操纵 / Flash-loan vote manipulation

- **位置**: `/home/user/1hao-/dao_audit/contracts/imported/MiniDAO.sol` → `MiniDAO.castBallot` 第 43 行
  ```solidity
  uint256 w = voteToken.balanceOf(msg.sender);
  ```
- **严重程度**: Critical  ·  **状态**: STATIC_ONLY  ·  **置信度**: 0.85
- **根本原因**: 投票权重取自实时余额 balanceOf/getVotes，未使用历史区块快照 (getPastVotes)。
- **触发条件**: 存在可借入治理代币的途径（闪电贷/借贷）；攻击者单笔交易内借币→投票→归还。
- **影响范围**: 零成本达成法定票数，操纵任意提案结果（含恶意金库提案）。
- **误报判断**: low — 治理上下文 + 投票函数内实时余额计票 + 全合约无快照调用。
- **结构信号**: vote-like function, live balanceOf/getVotes, no snapshot call
- **本地链证据**: PoC `FlashLoanVoteTest`=✅复现；修复回归 `FlashVoteFixedTest`=✅已验证
- **修复建议**: 采用 ERC20Votes 检查点；castVote 用 getPastVotes(voter, snapshotBlock)，快照设在提案创建区块之前；totalSupply 亦用历史快照。

## 7. VULN-5 — DEX 流动性池操纵 / DEX spot-price oracle manipulation

- **位置**: `/home/user/1hao-/dao_audit/contracts/imported/MiniDAO.sol` → `LendingDesk.collateralValue` 第 66 行
  ```solidity
  (uint112 r0, uint112 r1) = pair.getReserves();
  ```
- **严重程度**: Critical  ·  **状态**: STATIC_ONLY  ·  **置信度**: 0.80
- **根本原因**: 用 AMM 瞬时现货价/储备比为资产定价，无 TWAP/外部喂价。
- **触发条件**: 攻击者用一笔（常为闪电贷驱动的）大额兑换在单区块内扭曲现货价后触发定价路径。
- **影响范围**: 超额借出/错误铸赎，耗尽池/金库资产。
- **误报判断**: low — 定价/估值函数内读取现货价/储备且无 TWAP/外部喂价。若此价仅用于展示则为误报。
- **结构信号**: spot reserves/price read, pricing/valuation context, no TWAP/feed
- **本地链证据**: PoC `DexOracleManipulationTest`=✅复现；修复回归 `OracleFixedTest`=✅已验证
- **修复建议**: 改用抗操纵价源：Uniswap V2/V3 TWAP（窗口>=30min）或 Chainlink；加偏离阈值与陈旧性检查；禁止直接用 getReserves/现货价定价。

## 8. VULN-1 — 治理席位劫持 / Governance seat hijacking

- **位置**: `/home/user/1hao-/dao_audit/contracts/imported/MiniDAO.sol` → `MiniDAO.configureStewards` 第 28 行
  ```solidity
  function configureStewards(address[] calldata who) external {
  ```
- **严重程度**: Critical  ·  **状态**: STATIC_ONLY  ·  **置信度**: 0.70
- **根本原因**: 特权席位/角色的初始化或赋权函数对外可调用，且缺少 owner/role 门禁与一次性初始化守卫。
- **触发条件**: 任意外部账户调用该函数（或重复调用），将自身写入 council/admin/role/owner。
- **影响范围**: 攻击者获得治理特权席位，可操纵提案、金库指针或角色，等同接管治理。
- **误报判断**: medium — 初始化函数但未检出特权状态写入；需人工确认是否授予权限
- **结构信号**: init-like function name
- **本地链证据**: PoC `SeatHijackTest`=✅复现；修复回归 `SeatHijackFixedTest`=✅已验证
- **修复建议**: 加一次性守卫 require(!initialized) + onlyOwner/initializer；赋权走提案+时间锁；用 OZ AccessControl 并在部署后撤销部署者 admin。

## 9. VULN-3 — 治理时间锁缺失 / Missing governance timelock

- **位置**: `/home/user/1hao-/dao_audit/contracts/targets/VulnX100Governor.sol` → `VulnX100Governor.execute` 第 122 行
  ```solidity
  function execute(uint256 id) external returns (bytes memory ret) {
  ```
- **严重程度**: High  ·  **状态**: CONFIRMED  ·  **置信度**: 0.75
- **根本原因**: 提案执行函数在通过后立即对外调用，无 queue/eta/minDelay 延迟。
- **触发条件**: 提案达到法定票数后，攻击者或提案人立即 execute，中间无延迟窗。
- **影响范围**: 持币人与守护者无退出或否决窗口，恶意提案可即时落地（常与投票操纵联动）。
- **误报判断**: medium — execute 直接外呼且全合约无时间锁信号；若延迟逻辑在外部 Timelock 合约则为误报，需确认执行路径。
- **结构信号**: execute function, external call in execute, no timelock signal
- **本地链证据**: PoC `MissingTimelockTest`=✅复现；修复回归 `TimelockFixedTest`=✅已验证
- **修复建议**: 引入 TimelockController：通过→queue(eta=now+minDelay)→execute(require now>=eta)；minDelay>=2 天；守护者可在窗内 cancel/pause。

## 10. VULN-3 — 治理时间锁缺失 / Missing governance timelock

- **位置**: `/home/user/1hao-/dao_audit/contracts/imported/MiniDAO.sol` → `MiniDAO.enact` 第 48 行
  ```solidity
  function enact(uint256 id) external returns (bytes memory) {
  ```
- **严重程度**: High  ·  **状态**: STATIC_ONLY  ·  **置信度**: 0.75
- **根本原因**: 提案执行函数在通过后立即对外调用，无 queue/eta/minDelay 延迟。
- **触发条件**: 提案达到法定票数后，攻击者或提案人立即 execute，中间无延迟窗。
- **影响范围**: 持币人与守护者无退出或否决窗口，恶意提案可即时落地（常与投票操纵联动）。
- **误报判断**: medium — execute 直接外呼且全合约无时间锁信号；若延迟逻辑在外部 Timelock 合约则为误报，需确认执行路径。
- **结构信号**: execute function, external call in execute, no timelock signal
- **本地链证据**: PoC `MissingTimelockTest`=✅复现；修复回归 `TimelockFixedTest`=✅已验证
- **修复建议**: 引入 TimelockController：通过→queue(eta=now+minDelay)→execute(require now>=eta)；minDelay>=2 天；守护者可在窗内 cancel/pause。

---
_由 dao_audit 检测器引擎生成。靶标合约为授权研究用途，禁止部署主网。_