# 真实 DEX 套利机会排序报告

> 按扣全部成本后净收益排序。判定: **NO_TRADE**。按扣全部成本后净收益排序。simulated_feasible 需 净>0 且完整双腿 fork 验证通过；真实执行成功率未经验证前，不标记为已实现利润。

| 排名 | 链 | 交易对 | 买@ | 卖@ | 规模USD | 最佳净USD | 决策 | 模拟可行 |
|------|----|--------|-----|-----|---------|-----------|------|----------|
| 1 | arbitrum | USDC/USDT | UniswapV3 | SushiSwapV3 | 1,000 | -0.57 | NO_TRADE | NO |
| 2 | base | WETH/USDC | AerodromeSlipstream | UniswapV3 | 1,000 | -0.64 | NO_TRADE | NO |
| 3 | arbitrum | WETH/USDC | UniswapV3 | SushiSwapV3 | 1,000 | -1.72 | NO_TRADE | NO |

## 结论：NO_TRADE

所有机会扣除全部成本后净收益非正，或未通过完整双腿 fork 验证 → 无可标记为模拟可行的套利。不制造正收益结论。