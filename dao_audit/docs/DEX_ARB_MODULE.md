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

## v0.6 扩展：更多 venue + 完整双腿验证 + 可恢复监控 + 排序报告

- **Base venue 扩展到 4 个**（地址均经 `cast` 链上功能验证）：Uniswap V3、SushiSwap V3、
  **Aerodrome Slipstream**(CL, tickSpacing)、**Aerodrome classic**(solidly Router.getAmountsOut)。
  加入后 Base WETH/USDC 最佳 round-trip 净收益从 -$3.39 收窄到 **-$0.64**（$1k，近盈亏平衡，仍 NO_TRADE）。
  最紧价差来自 AerodromeSlipstream↔UniswapV3。
- **0x 聚合报价**：`api.0x.org` 本环境代理 403 未放行且需 API key → 按 `aggregator_0x.enabled`
  与 `ZEROX_API_KEY` 门控，当前标注 **inactive**，不编造。放行域名并配置 key 后可作交叉参考/去重。
- **双向/多 venue 路径**：引擎枚举有序 venue 组合，天然覆盖正/反向；多跳三角为后续扩展。
- **完整双腿原子 Fork 验证**（`test/DexRoundTripFork.t.sol`）：fork 真实 Base 状态，USDC→WETH→USDC
  完整 round-trip，`balanceOf` 验证最终余额==两腿账目。实测 2000 USDC→0.8532 WETH→1991.60 USDC
  （毛利 -8.40，含两腿手续费，无免费套利）。证明完整交易而非单笔 swap。离线套件以 RUN_FORK 门控跳过。
- **可恢复连续监控**（`dex-monitor`，JSONL 续跑）：每次追加若干真实快照到状态文件，多次调用累积
  至 target-hours（24h+），限流友好；统计机会频率/持续/各对最佳净收益。
- **排序机会报告**（`dex-report`）：按扣全部成本后净收益排序；`simulated_feasible` 需
  净>0 且完整双腿 fork 验证通过；真实执行成功率未验证前不标记已实现利润。当前判定 **NO_TRADE**。

### 命令补充
```bash
python3 -m dao_audit dex-monitor --max-new 5 --interval 30 --target-hours 24   # 续跑累积
python3 -m dao_audit dex-report                                                # 排序机会报告
FOUNDRY_PROFILE=fork RUN_FORK=true forge test --match-contract DexRoundTripForkTest -vv  # 完整双腿验证
```

### v0.6 实测结论
4 venue、3 交易对、1k–1M 档位、同区块双腿可执行报价、完整双腿 fork 验证均成功，但**所有机会扣除
全部成本后净收益非正**（最优 Base WETH/USDC -$0.64@$1k）→ **NO_TRADE，0 个模拟可行机会**。
与高效 L2 DEX 一致；不制造正收益结论。
