# base-arbitrage-scanner — Base 链闪电贷跨 DEX 套利扫描系统（第一阶段：只读 + 本地 fork 模拟）

> **安全边界（本阶段硬性约束）**：只读链上数据 + 本地 fork 模拟。不接入钱包、不读取/生成私钥、不签名、不发送真实交易、不部署主网合约、不调用治理/金库/流动性提取功能。执行合约 `contracts/FlashArbExecutor.sol` 只部署在本地 fork（Anvil / Hardhat node），只通过 `eth_call` 调用。

目标：在 **Uniswap V3** 与 **Aerodrome（经典 AMM + Slipstream 集中流动性）** 之间寻找可在同一笔交易内闭环完成的合法价差套利机会，以 **USDC** 作为 Aave V3 闪电贷借款与利润计价资产。不预测行情，不持币。

## 三种"利润"的严格区分

| 名称 | 含义 | 本系统如何得到 | 是否可标记为"可执行" |
|---|---|---|---|
| 理论价差 theoreticalSpread | 仅由显示中间价推导、零手续费零冲击 | 池状态 `slot0`/`reserves` | **否，永不** |
| 报价净利润 quoted net | 链上 Quoter / `getAmountOut` 的 ExactInput 实际可成交数量 − 闪电贷费 − Gas(L2+L1) − 执行缓冲 | 每区块 Multicall 批量报价 | 否（仅作为模拟候选） |
| 模拟净利润 simulated net | 在主网 fork 上以 `FlashArbExecutor.simulate()` 原子执行"借款→多腿 swap→偿还本金+费"的结果 − Gas − 缓冲 | `FORK_ATOMIC` 模拟 | **仅此一种可计入"可执行"** |
| 真实成交利润 | 真实链上成交 | 第一阶段不产生 | — |

`OFFLINE_FIXTURE`（合成数据）与 `LOCAL_EVM_SYNTHETIC`（本地 EVM 合成流动性）产生的任何数字都带有数据源标签，报告会明确声明"不代表真实机会"，并且 `executable` 永远为 0。

## 架构

```
src/
  config.ts              .env 读取（RPC、限流、金额梯度、阈值、fork 提供者）
  chain/addresses.ts     Base 主网种子地址（启动时必须链上验证，可用 env 覆盖）
  chain/verify.ts        链上验证：chainId=8453、字节码、代币 symbol/decimals、Quoter.factory()、Aave Provider<->Pool 互链、USDC 闪电贷开关位
  chain/client.ts        viem 客户端：令牌桶限流、并发闸、指数退避重试、多 RPC fallback、断线重连轮询/WS、失败记录
  chain/onchainSource.ts 链上 MarketSource：池发现(Factory.getPool)→池元数据回读校验→状态/报价 Multicall3 批量→Aave 费率/可用流动性→Gas/L1 数据费
  dex/{uniswapV3,slipstream,aerodrome}.ts  各协议 ABI 编解码
  pricing/v3math.ts      Uniswap V3 TickMath/SqrtPriceMath/SwapMath 精确整数移植（离线报价、价格冲击、测试）
  pricing/aeroMath.ts    Aerodrome 稳定池 x³y+xy³=k 与波动池 xy=k 的 getAmountOut 移植
  scanner/routes.ts      路线枚举：USDC→T→USDC 双向（含同 DEX 不同费率）、USDC→T1→T2→USDC 三跳
  scanner/cost.ts        成本模型：DEX 费、价格冲击、Aave FLASHLOAN_PREMIUM_TOTAL、L2 Gas(基础费+优先费)、OP L1 数据费、执行缓冲 → gross/totalCost/net/ROI
  scanner/scan.ts        每区块扫描：池过滤→分阶段链式报价→金额梯度 + 黄金分割最优借款额（受 Aave 可用流动性约束）
  sim/fork.ts            fork 节点管理（Anvil 优先，Hardhat node 等价回退；anvil_reset/hardhat_reset 换块）+ 原子路径 eth_call 模拟、revert 解码
  backtest/historical.ts 历史回测采样规则（逐块或每 N 块）、所有读取钉在历史块号，无未来数据
  runner.ts              扫描→模拟→入库（候选、失败原因、每块统计、梯度统计、去重）
  storage/{db,csv}.ts    node:sqlite 存储与 CSV 导出
  report.ts              Markdown 报告（覆盖范围、三种利润、最优金额、费用构成、失败分布、已验证机会）
  offline/fixture.ts     OFFLINE_FIXTURE：确定性合成市场（真实 AMM 数学 + 合成流动性），无 RPC 时跑通全流程
  cli/index.ts           CLI：env-check / discover / scan / backtest / leaderboard / report / export-csv
contracts/FlashArbExecutor.sol   fork 模拟用执行合约（Aave flashLoanSimple 接收器 + V3 回调 + Aerodrome transfer-then-swap）
contracts/TestFixtures.sol       本地 EVM 测试用 Mock（ERC20、Aave、Aerodrome 风格池、V3 mint 回调）
```

## 安装与运行

```bash
cd base-arbitrage-scanner
scripts/install.sh            # npm install + solc-js 编译合约 + 生成 .env + env-check
# 编辑 .env，填写 BASE_RPC_URL（Base 主网，回测需要 archive 节点）
npm run env-check             # RPC 连通性、chainId=8453、fork 提供者、合约编译状态
npm run discover              # 链上验证全部地址，发现并校验池，读取 Aave USDC 闪电贷费率与可用流动性
scripts/run_scan.sh           # 实时扫描（新块触发），CLI 排行榜；Ctrl+C 结束并生成报告
scripts/run_backtest.sh --hours 24 --sample 30     # 最近 24h 回测（每 30 块采样一次）；完成后 --hours 168 扩展至 7 天
npm run leaderboard -- --run <run_id>
npm run report -- --run <run_id>
npm run export -- --run <run_id>                   # CSV：candidates / block_stats / rpc_failures / sim_failures / pools / pool_snapshots
scripts/test.sh               # 类型检查 + 19 个单元/集成测试（含本地 EVM 原子模拟）
```

无 RPC 时的离线验证（结果带 OFFLINE_FIXTURE 标签）：

```bash
npm run -s cli -- discover --offline
npm run -s cli -- scan --offline --blocks 5
npm run -s cli -- backtest --offline --hours 2 --sample 30
```

## 关键设计点

- **地址不可信任**：`addresses.ts` 只是种子。`verify.ts` 在链上检查：字节码存在、代币 symbol/decimals 与预期一致（排除假币）、`QuoterV2.factory()==Factory`、`AddressesProvider.getPool()==Pool` 且 `Pool.ADDRESSES_PROVIDER()==Provider`、USDC 储备配置位 63（闪电贷启用）。任一失败则拒绝扫描。
- **池只来自官方 Factory**：`getPool(tokenA,tokenB,fee|tickSpacing|stable)`，再回读池的 `token0/token1/factory/fee/tickSpacing/stable/isPool` 交叉校验；TVL 低于 `MIN_POOL_TVL_USD` 的池排除并记录原因。
- **报价即可成交量**：Uniswap/Slipstream 用 QuoterV2 `quoteExactInputSingle`（返回 amountOut、sqrtPriceAfter、gasEstimate），Aerodrome 用池自身 `getAmountOut`。价格冲击由池前后 sqrtPrice / 储备计算，手续费单列。
- **闪电贷容量不是假设**：每块读取 `FLASHLOAN_PREMIUM_TOTAL`、`getReserveData(USDC)` 的 aToken 实际余额与配置位（active/frozen/paused/flashloanEnabled）；超过可用流动性的金额记为 `flashloan_capacity_exceeded`。
- **成本完整**：Aave 费率 + L2 基础费与优先费（`eth_maxPriorityFeePerGas`）× Gas 单位（优先使用 Quoter 返回的 gasEstimate）+ OP 栈 `GasPriceOracle.getL1Fee` + 执行缓冲（`SLIPPAGE_BUFFER_BPS`）。
- **原子模拟**：候选在 fork 上由 `FlashArbExecutor.simulate()` 走完整的 `flashLoanSimple → executeOperation → 多腿 swap → approve 偿还` 路径；偿还不足则 revert `Unprofitable(received, owed)` 并记录差额；通过者再用 `eth_estimateGas` 取实际 Gas。只有 `FORK_ATOMIC` 通过且净利润 ≥ `MIN_NET_PROFIT_USD` 才计入"可执行"。
- **历史回测无未来数据**：所有 `eth_call`/Multicall 钉在采样块号；fork 用 `--fork-block-number`/`*_reset` 切到该块；非 archive 节点无法提供历史状态时记为失败块而非用当前状态替代。
- **稳健性**：令牌桶限流、并发闸、指数退避重试、多 RPC fallback、轮询/WS 断线重连、所有失败调用入库 `rpc_failures`；候选去重（同路线同金额在 `DEDUPE_BLOCKS` 内净利润不变则不重复写入，模拟过的始终写入）。

## 已知限制 / 阻塞项

- 本阶段**从未**发送交易，因此没有真实成交利润；"可执行"仅表示 fork 原子模拟通过。
- 回测需要 archive RPC；大多数免费公共节点只保留近期状态。
- 7 天回测以采样方式进行（默认每 30 块），逐块回测请 `--sample 1`（RPC 成本约 ×30）。
- Slipstream 池手续费可能是动态的，系统每块读取 `fee()`；Uniswap 协议费等于 0 时对报价无影响。
- 多跳路线枚举受 `MAX_ROUTES_PER_BLOCK` 限制。
- 最新真实扫描结果见 `reports/LATEST_SCAN_RESULTS.md`（若运行环境无法访问 Base RPC，该文件会说明阻塞原因并只附离线结果）。
