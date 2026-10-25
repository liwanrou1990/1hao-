# 事件类型真实历史收益报告（小时级 / walk-forward）

> 真实数据：Snapshot 治理历史 + DefiLlama 小时价（ETH 超额）。无未来泄漏；成交价取事件后下一根小时K线（非事后最优）；多重检验校正；不承诺收益；未达标→NO_TRADE。

- 生成: 2026-10-25T06:59:30+00:00  研究窗口: 近 365 天（小时价可得范围）
- 事件数: 406  分组: 32  Bonferroni t≥2.96  确认信号: 0
- Timelock 执行事件: INSUFFICIENT_DATA(需链上 Timelock 追踪；ETH RPC 当前不可用)

## 每类事件：样本量 / 胜率 / 超额 / 样本外 / 回撤 / 利多利空中性

| 类别 | 事件 | 窗口 | n | 利多/利空/中性 | 内均值bps | 内t | OOSn | OOS对齐均值bps | 最大回撤bps | 确认 |
|------|------|------|---|----------------|-----------|-----|------|----------------|-------------|------|
| governance_generic | proposal_created | 1h | 30 | 4/9/17 | 32 | 0.95 | 15 | -554.0 | -9393 |  |
| governance_generic | proposal_created | 4h | 30 | 4/10/16 | -13 | -0.49 | 15 | 579.1 | -9719 |  |
| governance_generic | proposal_created | 24h | 30 | 7/17/6 | -140 | -2.19 | 15 | 471.6 | -9435 |  |
| governance_generic | proposal_created | 72h | 30 | 10/16/4 | -208 | -1.93 | 15 | 257.6 | -8230 |  |
| governance_generic | voting_end | 1h | 30 | 5/5/20 | 45 | 0.69 | 15 | -250.7 | -5587 |  |
| governance_generic | voting_end | 4h | 30 | 9/6/15 | 18 | 0.31 | 15 | -128.0 | -5663 |  |
| governance_generic | voting_end | 24h | 30 | 12/12/6 | 39 | 0.35 | 15 | -240.3 | -7398 |  |
| governance_generic | voting_end | 72h | 30 | 11/12/7 | -28 | -0.30 | 15 | 69.2 | -9278 |  |
| liquidity_incentive | proposal_created | 1h | 75 | 7/23/45 | -154 | -1.85 | 38 | 389.9 | -17096 |  |
| liquidity_incentive | proposal_created | 4h | 75 | 14/28/33 | -184 | -2.06 | 38 | 377.4 | -16661 |  |
| liquidity_incentive | proposal_created | 24h | 75 | 22/37/16 | -186 | -1.71 | 38 | 280.8 | -15159 |  |
| liquidity_incentive | proposal_created | 72h | 74 | 24/38/12 | -244 | -2.06 | 37 | 186.0 | -13800 |  |
| liquidity_incentive | voting_end | 1h | 73 | 13/17/43 | -84 | -0.84 | 37 | 184.4 | -9695 |  |
| liquidity_incentive | voting_end | 4h | 73 | 21/20/32 | -70 | -0.68 | 37 | 134.6 | -9553 |  |
| liquidity_incentive | voting_end | 24h | 73 | 31/30/12 | -42 | -0.42 | 37 | 136.9 | -9349 |  |
| liquidity_incentive | voting_end | 72h | 71 | 25/28/18 | 4 | 0.03 | 36 | -143.1 | -9940 |  |
| tokenomics | proposal_created | 1h | 57 | 11/9/37 | 12 | 0.43 | 29 | 60.2 | -1954 |  |
| tokenomics | proposal_created | 4h | 57 | 16/14/27 | -40 | -1.25 | 29 | -88.4 | -1933 |  |
| tokenomics | proposal_created | 24h | 57 | 20/18/19 | 27 | 0.50 | 29 | 58.3 | -4131 |  |
| tokenomics | proposal_created | 72h | 57 | 26/25/6 | -60 | -0.80 | 29 | -181.7 | -2515 |  |
| tokenomics | voting_end | 1h | 57 | 8/11/38 | -3 | -0.11 | 29 | 235.9 | -8838 |  |
| tokenomics | voting_end | 4h | 57 | 13/13/31 | 65 | 1.70 | 29 | -250.7 | -9027 |  |
| tokenomics | voting_end | 24h | 57 | 21/24/12 | 86 | 1.25 | 29 | -157.0 | -7088 |  |
| tokenomics | voting_end | 72h | 56 | 19/30/7 | 78 | 0.56 | 28 | -55.3 | -7780 |  |
| treasury_major | proposal_created | 1h | 42 | 6/13/23 | 20 | 0.31 | 21 | -485.6 | -11459 |  |
| treasury_major | proposal_created | 4h | 42 | 7/18/17 | -7 | -0.10 | 21 | 563.7 | -13261 |  |
| treasury_major | proposal_created | 24h | 42 | 14/19/9 | 293 | 2.39 | 21 | -683.5 | -15886 |  |
| treasury_major | proposal_created | 72h | 42 | 14/20/8 | 231 | 1.67 | 21 | -605.1 | -13967 |  |
| treasury_major | voting_end | 1h | 42 | 11/9/22 | 37 | 0.62 | 21 | -145.9 | -5234 |  |
| treasury_major | voting_end | 4h | 42 | 15/13/14 | 82 | 1.02 | 21 | -57.8 | -5362 |  |
| treasury_major | voting_end | 24h | 41 | 13/19/9 | -24 | -0.25 | 21 | 67.7 | -4793 |  |
| treasury_major | voting_end | 72h | 39 | 17/17/5 | 200 | 0.82 | 20 | 155.7 | -3712 |  |

## 分本金规模可执行净收益（样本外对齐均值，扣费/滑点/Gas）

> 成交价=事件后下一根小时K线价（非事后最优）。滑点=base*sqrt(size/ref)×2腿；费率30bps/腿；Gas 假设 $15/笔（ETH RPC 实时 gas 不可得，标注为假设）。

| 类别/事件/窗口 | 本金 | 滑点bps | Gas_bps | 净收益bps |
|----------------|------|---------|---------|-----------|
| governance_generic/proposal_created/4h | $10,000 | 3.2 | 15.0 | 500.9 |
| governance_generic/proposal_created/4h | $100,000 | 10.0 | 1.5 | 507.6 |
| governance_generic/proposal_created/4h | $1,000,000 | 31.6 | 0.15 | 487.3 |
| treasury_major/proposal_created/4h | $10,000 | 3.2 | 15.0 | 485.5 |
| treasury_major/proposal_created/4h | $100,000 | 10.0 | 1.5 | 492.2 |
| treasury_major/proposal_created/4h | $1,000,000 | 31.6 | 0.15 | 471.9 |
| governance_generic/proposal_created/24h | $10,000 | 3.2 | 15.0 | 393.5 |
| governance_generic/proposal_created/24h | $100,000 | 10.0 | 1.5 | 400.1 |
| governance_generic/proposal_created/24h | $1,000,000 | 31.6 | 0.15 | 379.9 |
| liquidity_incentive/proposal_created/1h | $10,000 | 3.2 | 15.0 | 311.7 |
| liquidity_incentive/proposal_created/1h | $100,000 | 10.0 | 1.5 | 318.4 |
| liquidity_incentive/proposal_created/1h | $1,000,000 | 31.6 | 0.15 | 298.1 |
| liquidity_incentive/proposal_created/4h | $10,000 | 3.2 | 15.0 | 299.2 |
| liquidity_incentive/proposal_created/4h | $100,000 | 10.0 | 1.5 | 305.9 |
| liquidity_incentive/proposal_created/4h | $1,000,000 | 31.6 | 0.15 | 285.6 |

## 结论：无交易优势

在小时级数据、事件分类、多窗口、walk-forward 样本外与多重检验校正下，**没有任何事件类别**显示出样本内显著且样本外、成本后为正的可持续优势。全部输出 **NO_TRADE**。这是真实数据下的结论，未人为放宽标准，也不制造盈利结论。

> 局限：Timelock 执行与金库实际到账需链上追踪（ETH RPC 当前不可用）；滑点/Gas 为标注假设（真实逐笔深度与实时 gas 未接入）；研究窗口受小时价可得范围（~1 年）限制。