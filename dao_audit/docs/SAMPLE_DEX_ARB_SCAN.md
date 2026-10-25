# 真实 DEX 同链跨市场套利扫描

> 可执行链上报价（QuoterV2，按金额含价格冲击）；同区块两腿 round-trip；扣除 Gas/费/滑点/闪电贷费/MEV/失败成本；真实数据不足→NO_TRADE；不签名、不执行真实交易。

- 生成: 2026-10-25T07:58:50+00:00  ETH计价: $2346.9284398432164 (DefiLlama)
- 同链候选机会(净>0且过阈值): **0**

## base · WETH/USDC  (block 53062891, venues UniswapV3, SushiSwapV3, AerodromeSlipstream, AerodromeClassic)

- 决策: **NO_TRADE** — 最佳 round-trip 净收益 $-0.64（<= $1.0）→ NO_TRADE

| 本金USD | 买@ | 卖@ | 毛利USD | Gas | MEV | 失败成本 | 总成本 | 净USD |
|---------|-----|-----|---------|-----|-----|----------|--------|-------|
| 1,000 | AerodromeSlipstream | UniswapV3 | -0.43 | 0.0042 | 0.2 | 0.0006 | 0.2 | -0.64 |
| 10,000 | AerodromeSlipstream | UniswapV3 | -8.55 | 0.0042 | 2.0 | 0.0006 | 2.0 | -10.56 |
| 100,000 | UniswapV3 | AerodromeSlipstream | -384.24 | 0.0042 | 20.0 | 0.0006 | 20.0 | -404.25 |
| 1,000,000 | AerodromeSlipstream | UniswapV3 | -23400.65 | 0.0042 | 200.0 | 0.0006 | 200.0 | -23600.66 |

## arbitrum · WETH/USDC  (block 518085024, venues UniswapV3, SushiSwapV3)

- 决策: **NO_TRADE** — 最佳 round-trip 净收益 $-1.72（<= $1.0）→ NO_TRADE

| 本金USD | 买@ | 卖@ | 毛利USD | Gas | MEV | 失败成本 | 总成本 | 净USD |
|---------|-----|-----|---------|-----|-----|----------|--------|-------|
| 1,000 | UniswapV3 | SushiSwapV3 | -1.43 | 0.0751 | 0.2 | 0.0113 | 0.29 | -1.72 |
| 10,000 | UniswapV3 | SushiSwapV3 | -68.14 | 0.0751 | 2.0 | 0.0113 | 2.09 | -70.23 |
| 100,000 | SushiSwapV3 | UniswapV3 | -5740.44 | 0.0751 | 20.0 | 0.0113 | 20.09 | -5760.53 |
| 1,000,000 | SushiSwapV3 | UniswapV3 | -810966.62 | 0.0751 | 200.0 | 0.0113 | 200.09 | -811166.71 |

## arbitrum · USDC/USDT  (block 518085024, venues UniswapV3, SushiSwapV3)

- 决策: **NO_TRADE** — 最佳 round-trip 净收益 $-0.57（<= $1.0）→ NO_TRADE

| 本金USD | 买@ | 卖@ | 毛利USD | Gas | MEV | 失败成本 | 总成本 | 净USD |
|---------|-----|-----|---------|-----|-----|----------|--------|-------|
| 1,000 | UniswapV3 | SushiSwapV3 | -0.28 | 0.0751 | 0.2 | 0.0113 | 0.29 | -0.57 |
| 10,000 | UniswapV3 | SushiSwapV3 | -12.18 | 0.0751 | 2.0 | 0.0113 | 2.09 | -14.27 |
| 100,000 | UniswapV3 | SushiSwapV3 | -89652.1 | 0.0751 | 20.0 | 0.0113 | 20.09 | -89672.19 |
| 1,000,000 | UniswapV3 | SushiSwapV3 | -989652.1 | 0.0751 | 200.0 | 0.0113 | 200.09 | -989852.19 |

## 跨链

跨链不同价不可视为原子无风险套利：需跨链桥/中继，存在桥接延迟、桥风险、两端各自滑点与价格漂移；本模块仅做同链原子 round-trip，跨链单列为高风险、非原子，未计入机会。

## 结论

本次快照中，所有同链跨 DEX round-trip 在扣除全部真实成本后净收益非正 → 全部 NO_TRADE。这与高效的 L2 DEX 市场一致：显示价差多被手续费、价格冲击与 Gas 吞没。不制造盈利结论。