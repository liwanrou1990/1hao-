# 最近扫描结果与阻塞项（2026-10-25）

## 结论先行

- **真实 Base 主网扫描：未运行（阻塞）。** 本运行环境的网络策略拒绝了所有 Base RPC 主机（`mainnet.base.org`、`base.llamarpc.com`、`base-rpc.publicnode.com`、`1rpc.io`、`base.drpc.org`、Alchemy/Infura/Ankr/Tenderly/Blast 等均返回 CONNECT 403），也拒绝 GitHub Releases（无法安装 Foundry/Anvil）与 Basescan/协议文档站。`.env` 中也没有任何 RPC 密钥。
- **因此没有任何经过验证的真实正收益机会可报告；也没有任何真实池数量、真实候选数、真实模拟通过数。** 下方全部数字来自 `OFFLINE_FIXTURE`（确定性合成市场）与 `LOCAL_EVM_SYNTHETIC`（本地 EVM 合成流动性），仅证明系统流程与数学正确，不代表市场。
- 系统已实现并通过测试的部分：地址链上验证、池发现与校验、Multicall 批量报价、Aave 费率/容量读取、成本模型、金额梯度 + 最优借款额搜索、fork 原子模拟（Anvil / Hardhat node）、历史回测采样器、SQLite 存储、CSV 导出、CLI 排行榜、Markdown 报告、单元与集成测试（19/19 通过）。

## 解除阻塞后需要做的事（按顺序）

1. 在云环境设置中放行 RPC 域名（或切换为更宽的网络级别），并在 `.env` 填入 `BASE_RPC_URL`（回测需 archive 节点）。
2. `npm run env-check` → `npm run discover`（链上验证 17 项检查全部通过后才会扫描）。
3. `scripts/run_scan.sh`（实时）与 `scripts/run_backtest.sh --hours 24 --sample 30`，之后 `--hours 168`。
4. 可选：安装 Foundry 后设置 `FORK_PROVIDER=anvil`（否则自动使用 `hardhat node --fork`，功能等价）。

## 离线验证结果（OFFLINE_FIXTURE，合成数据，不是市场）

运行：`OFFLINE_FIXTURE=1 backtest --hours 2 --sample 30`（run_id `offline-bt-2h`）与 `scan --blocks 5`（run_id `offline-scan-test`）。

| 指标 | 值 | 说明 |
|---|---|---|
| 扫描时间范围 | 夹具时钟 2026-10-24T23:11:10Z → 2026-10-25T01:11:10Z（121 个采样块，每 30 块一采样） | 夹具区块号/时间为合成值 |
| 池数量 | 11 个合成池（Uniswap V3 ×6、Slipstream ×2、Aerodrome v2 ×3），每块 10 个通过 TVL 过滤、1 个被排除（tvl_below_min） | 验证了排除路径 |
| 路线数 | 48 条/块（双向两跳 + 三跳） | |
| 路线×金额评估 | 36,549 | 含 6 档梯度 + 黄金分割优化点 |
| 候选（报价净利润 > 0） | 667 | |
| 进入模拟 / 通过 | 129 / 129 | 模拟类型 `FIXTURE_SEQUENTIAL`（非 EVM） |
| **计入"可执行"** | **0** | 设计如此：非 `FORK_ATOMIC` 永不计入 |
| 最大 / 平均模拟净利润 | 721.88 / 137.91 USD | 合成价差产生，无市场含义 |
| 最优借款金额（最佳单次） | 292,504 USDC（优化器落在 100k–500k 之间） | 1,000,000 USDC 档 31/31 块均为负净利（价格冲击占优） |
| 费用构成（最佳评估） | DEX 费 993.8 / 价格冲击 728.9 / 闪电贷费 146.3 (5 bps) / L2 Gas 0.015 / L1 数据费 ≈0.04 / 执行缓冲 146.3 USD | 前两项已内含在报价中 |
| 失败原因分布 | 报价阶段 0、模拟阶段 0、RPC 0 | 夹具流动性充足；绝对金额过大时报 `insufficient liquidity`（单元测试覆盖） |

## 本地 EVM 原子模拟验证（LOCAL_EVM_SYNTHETIC，`test/localEvm.integration.test.ts`）

在 Hardhat 本地链（chainId 8453）上部署：真实 Uniswap V3 Factory/Pool 字节码（@uniswap/v3-core 构建产物，价格 4000 USDC/WETH，0.05%）、Aerodrome 风格 xy=k 池（价格 4100，0.3%）、Mock Aave（5 bps）、`FlashArbExecutor`。

- 借 10,000 USDC：Uniswap 买入 WETH → Aerodrome 卖出 → 偿还 10,005 USDC：**模拟通过**，利润落在 100–260 USDC 区间，`eth_estimateGas` 在 100k–600k 之间，`eth_call` 不改状态。
- 反向路线：revert `Unprofitable(received≈9.7k, owed=10,005)`，差额被正确解码并记录。
- 1,000,000 USDC 的 ROI 低于 10,000 USDC：价格冲击约束借款规模。

以上为合成流动性上的机制验证，不是真实收益。

## 文件

- 报告：`reports/backtest-2h-offline-bt-2h.md`、`reports/backtest-1h-offline-bt-1h.md`、`reports/live-offline-scan-test.md`
- CSV：`reports/csv-offline-backtest-2h/`、`reports/csv-offline-scan/`（大文件已被 .gitignore 排除，可用 `npm run export` 重新生成）
- 数据库：`data/offline-backtest.sqlite`、`data/offline-scan.sqlite`
