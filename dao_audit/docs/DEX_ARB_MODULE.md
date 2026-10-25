# 真实 DEX 跨市场套利研究模块 (dex/, v0.5)

> 目标：验证真实市场中**能够成交、扣除全部成本后仍有正期望收益**的合法套利，而非历史理论最高收益。
> 只读链上数据；不签名、不执行真实交易；缺报价/流动性/成本 → NO_TRADE；不制造盈利结论。
> 与 DAO 治理安全审计模块分开；不使用未授权漏洞转移第三方资金。

## 方法

- **可执行报价**（`dex/quotes.py`）：用 `cast` 只读 eth_call 调用链上 Uniswap V3 / SushiSwap V3
  QuoterV2，按**实际交易金额**取 `amountOut`（QuoterV2 在链上执行 swap 数学，价格冲击已内含），
  跨 fee tier 取最优；两腿锁定同一区块，保证快照一致；无池/报价失败返回 None（不编造）。
- **同链 round-trip 套利引擎**（`dex/scan.py`）：对每条链/每个交易对/每个本金档位，枚举
  “在 A 买入基础资产 → 在 B 卖出”的有序 venue 组合，用两腿可执行报价算毛利，再**扣除全部成本**：
  Gas（真实 gas price × gas units × ETH 价）、手续费（含在报价）、价格冲击（含在报价）、
  闪电贷费（如启用，Aave v3 ~5bps）、MEV/抢跑折价、交易失败期望成本（p×gas）。净收益 > 阈值才算候选。
- **分本金容量**：对 1k/10k/100k/1M USD 分别报价，观察净收益随规模衰减（薄池大额价格冲击巨大）。
- **频率/持续/成交**（`dex-monitor`）：多次真实快照统计机会出现频率与持续性。
- **本地 EVM 成交模拟**（`test/DexSwapFork.t.sol`）：fork 真实 Base 状态执行真实 swap，
  断言**实际余额变化 == 报价输出**（证明报价可成交）。
- **跨链单列**：不同链价差**不是**原子无风险套利（需跨链桥/中继、桥风险、两端滑点与价格漂移），
  本模块只做同链原子 round-trip，跨链仅标注为高风险非原子、不计入机会。

## 命令

```bash
export PATH=/root/.foundry/bin:$PATH PYTHONPATH=tool
python3 -m dao_audit dex-scan         # 单次扫描：各对各档位净收益 + TRADE/NO_TRADE
python3 -m dao_audit dex-monitor --snapshots 3 --interval 20   # 频率/持续统计
# 本地 EVM 成交验证（fork 真实状态，只读不广播）：
FOUNDRY_PROFILE=fork RUN_FORK=true forge test --match-contract DexSwapForkTest -vv
```

## 本环境实测结果（真实数据）

- 可达链：Base(8453)、Arbitrum(42161) RPC 正常；DefiLlama 提供 ETH 计价。
- venues：各链 Uniswap V3 + SushiSwap V3 QuoterV2（地址运行时验证可用）。
- 扫描结果：**0 个候选**。各对最佳 round-trip 净收益均为负：
  Base WETH/USDC ≈ -$3.5、Arbitrum WETH/USDC ≈ -$1.9、Arbitrum USDC/USDT ≈ -$0.5（$1k 档，扣全部成本）。
  大额档位因 SushiSwap V3 薄池价格冲击，毛利急剧转负（真实流动性限制）。
- `dex-monitor`（3 快照 / ~2 分钟）：机会频率 **0.0**，净收益稳定略负。
- 本地 EVM 模拟：fork Base 执行 1 WETH→USDC，报价 2343.616572 USDC = 实际成交 2343.616572 USDC，
  余额变化一致（报价可成交已验证）。

**结论**：当前真实数据下，同链跨 DEX 在扣除手续费、价格冲击、Gas、MEV 与失败成本后，
**无正期望收益的可成交套利**，全部 NO_TRADE。这与高效 L2 DEX 市场一致，未人为放宽标准。

## 局限（诚实披露）

- venue 限于放行主机可达的链上 QuoterV2；聚合器 API（0x/1inch）未放行，未纳入多路由比价。
- MEV 折价与失败概率为**标注假设**（真实私有内存池/竞拍未接入）；Gas 为链上实时 gas price，真实。
- 闪电贷费按参数计（默认自有资金，未计）；真实闪电贷还需 flashloan 合约路径与回调 gas。
- 跨链套利未实现（非原子、高风险）；仅同链原子 round-trip 计为机会。
- 本地 fork 模拟依赖公共 RPC 可 fork；默认离线测试套件跳过该用例。
