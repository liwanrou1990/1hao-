# 24 · Brando Price Action 第五阶段：纯现货趋势参与、持仓优化与风险调整收益

生成时间：2026-11-07T10:11 UTC · 分支 `claude/brando-pa-phase5-trend` · 基准锁定 `docs/brando_pa_validation/phase5_spot/BASELINE_LOCK.md`（第四阶段提交 a07a68b）· 预先登记 `docs/brando_pa_validation/phase5_spot/preregistration.md` · 测试 46 项通过（前四阶段 42 项 + 本阶段 4 项）

> 只做多现货、无杠杆、无做空、无借币；BUY / HOLD / SELL；下一根开盘成交；无未来数据；现金与持仓不为负。第一轮零成本为理想研究环境，第二轮 10 bps + 5 bps 独立报告。零参数自由度：3 模型 × 3 退出 × 2 标的 × 6 窗口 × 2 成本轮次一次性全部运行并全部报告，没有挑选。真实交易接口保持禁用。

## 0. 结论先行

**总判定（按预先登记判据，未调整）**：三套新模型在预登记配置下**没有一个**同时满足“优于原 Brando 且回撤不更差”与“风险调整收益不低于合理基准中较好者”。最强的合理基准不是买入持有，而是最简单的 **SMA200 趋势基准**：全周期 1h（2022-04→2026-11）BTC +232%（最大回撤 −33%、Sharpe 0.93、Calmar 0.89），ETH +114%；4h 长历史（2018-06→）BTC +1,053%、ETH +1,620%。所有 Brando 系模型与三套新模型在全部窗口都没有在风险调整收益上接近它。本阶段的具体回答：

1. **为什么 BTC 验证窗买入持有 +73% 而原策略只有 +7.9%（逐笔还原）**：不是因为长期踏空。原策略在该窗口持仓时间占 46%、BULL 期间持仓 50%、最长连续空仓只有 5.7 天，持仓段期间标的复合上涨 +62.5%（空仓段只有 +6.3%）。利润是在持仓期间被**“止损—更高价再买—再止损”的来回**磨掉的：391 次卖出、平均持仓 0.7 天、325 次止损/保本止损、止损后重新买入 240 次；上涨月捕获率只有 23%。换言之，第四阶段系统的问题是持仓退出逻辑（1R 保本 + 紧止损）把一段大行情切成几百笔小亏小赚，而不是没有参与。
2. **预登记的硬性“回撤 20% 暂停买入”规则是本阶段结果的第一决定因素**：现货空仓时权益不变、峰值回撤不会收窄，规则一旦触发就变成**永久停机**。原 Brando / 模型 B / 模型 C 在全周期 BTC 上平均仓位只有 1%–14%、BULL 期间持仓时间 0%–29%；4h 长历史上三套模型平均仓位 1%–10%，2018 年触发后到 2026 年都基本空仓；模型 B 含成本时三种退出结果完全相同（−19.05%、159 笔），就是因为核心仓位从未被允许建立。这不是交易优势，报告中的低回撤数字相当一部分来自这条规则。为量化它，追加了**明确标注为事后消融、不作为判据依据**的对照（`phase5_spot_ablation_no_halt/`）：关闭熔断后，全周期 BTC 零成本：原 Brando +34%/+112%/+46%（三种退出），模型 A +252%/+81%/+67%，模型 B +144%/+64%/+57%，模型 C +17%/+82%/+31%，SMA200 仍为 +232%（回撤 −33%）；但含成本后原 Brando 与模型 B 变成 −40% 到 −93%（一千多笔交易的成本），只有模型 A 与模型 C 的趋势失效退出维持正收益（BTC +74% / +54%，ETH +62% / +40%），仍全部低于 SMA200。消融的结论：熔断规则掩盖了模型间的差异，但去掉它之后新模型依然没有超过最简单的趋势基准。
3. **模型 A（趋势持仓）**：在“结构止损”退出下实际等于“首次 BULL 后买入持有”（止损挂在入场时的结构位，之后不再移动）：全周期 BTC +252%、ETH +43%，但最大回撤 −54% / −69%，超过预登记可接受范围；在“趋势失效”与“ATR 跟踪”退出下回撤压到 −23%～−28%，收益却降到 +4%、−10%（BTC），因为本阶段的 BULL 定义（SMA50 > SMA200 + 0.5 ATR 且 20 日斜率 > 0，且不创 60 日新低）只覆盖 24% 的时间、在 2024–2025 年来回切换 100 余次，趋势失效退出把一段行情切成多段并反复错过再入场（卖后错过合计 +209%）。4h 长历史 BTC 趋势失效 +96%（回撤 −34%）对 SMA200 +1,053%。**结论：持续持有完整上涨行情只有“几乎不退出”的版本做到了，而它的回撤不可接受；一旦加上趋势失效或 ATR 退出，BULL 定义的抖动让它远不如 SMA200。**
4. **模型 B（核心 50% + 战术 50%）**：战术袖子（Brando A/B，1h 本地结构）在全周期产生 1,000～1,800 笔交易，零成本下核心贡献了几乎全部收益、战术袖子接近零或为负；含成本后战术袖子把整体拖成 −19% 到 −63%（消融后更明显：BTC −47%～−52%）。核心仓位没有被战术信号干扰（设计上隔离），但战术袖子没有提供增量。**结论：核心 + 战术在成本下劣于单独的核心（即模型 A），战术部分应被否定。**
5. **模型 C（趋势过滤 Brando）**：过滤确实改变了信号质量而不只是减少次数——全周期 BTC 零成本：原 Brando（结构止损）−18%，趋势过滤后 −0.4%；趋势失效退出下 +82%（消融）对原 Brando +112%（消融），ATR 跟踪 +18% 对 −26%；ETH 过滤后 +37%/+5%/+41% 对原 −21%/−9%/−22%。含成本后模型 C 的趋势失效退出仍为正（BTC +5%～+54%、ETH +20%～+40%），是三套模型里唯一在含成本、两标的、两窗口都不为负的组合。但它的平均仓位 8%–33%、上涨月捕获 20%–40%，Sharpe 0.05–0.63，全部低于 SMA200（0.90–0.93）。**结论：趋势过滤改善了 Brando 信号的方向正确率，但 Brando 入场本身的低参与率使它无法把趋势过滤的优势转化为收益。**
6. **三种退出的区别**：结构止损对趋势仓位 = 不退出（收益最高、回撤最大）；趋势失效退出 = 回撤最小、但依赖 BULL 定义的稳定性，本阶段定义过于敏感；ATR 跟踪（3×ATR(1d)）在 BTC 上频繁被 1 日级别回调触发（模型 A 全周期 38 次退出，卖后错过 +290%），对 ETH 更差。没有一种退出能同时做到“保留完整行情”和“压住回撤”。
7. **BTC vs ETH、牛熊环境**：保留窗（2025-07→，先跌后涨）ETH 系模型大多为正（+21%～+46%，含成本后缩水一半）、BTC 系全部为负；全周期相反。与第四阶段一致：结果随窗口翻转，不能据此选标的。按月份捕获：所有模型的下跌月捕获率都低于上涨月捕获率（例如模型 A 趋势失效 BTC 上涨 8%/下跌 10%），说明它们并没有系统性地“躲过下跌、吃到上涨”，而是两头都少。
8. **是否存在可信的独立验证**：没有。全部 1h 窗口在前四阶段已被观察；本阶段是零自由度的历史稳健性研究，结果一致指向同一结论（SMA200 基准优于全部模型），这一点的可信度较高，但任何模型的正收益都不能作为样本外证据。
9. **是否值得进入前瞻性模拟研究**：以本阶段三套模型的形态，**不值得**；Brando 1h 入场 + 1% 风险 + 本地结构止损这条线在五个阶段里没有产生过可复现的优势。值得前瞻跟踪的只有两件事：(a) SMA200 趋势基准本身（作为“合理基准”持续记录，不作为策略宣称）；(b) 模型 C 趋势失效退出（含成本两标的两窗口均为正）作为低频观察对象，前提是把趋势状态定义的抖动问题与熔断规则的永久停机问题先在新一轮预登记里修正。下一轮如要继续，应研究的是趋势状态的稳定性与退出规则，而不是 Brando 入场。


## 1. 保留与新增

- 保留第四阶段全部内容与历史结果（docs/23、`docs/brando_pa_validation/phase4_spot/`）；本阶段为独立子包 `src/brando_pa/trend/`：`regime.py`（TrendRegimeEngine）、`engine.py`（TrendSpotBacktester = SpotPositionManager + TrendFollowingStrategy + CoreSatelliteStrategy + TrendFilteredBrando + 原 Brando 同口径运行 + SMA200 基准）、`exposure.py`（ExposureAnalyzer 与逐笔还原）、`benchmarks.py`（BenchmarkEvaluator）、`phase5.py`（预登记流程）。复用第四阶段现货账本、1h 本地结构、Brando A/B/C 策略、买入持有基准、绩效模块。
- CLI：`phase5`、`trend-signal`；看板新增“趋势参与研究（第五阶段）”页。

## 2. 趋势状态引擎（§二）

| 周期 | 输入 | 输出 |
|---|---|---|
| 日线（只用已收盘） | SMA50、SMA200、SMA50 的 20 日斜率、ATR(14)、60 日收盘新高/新低（历史高低点结构） | BULL：close > SMA200 且 SMA50 > SMA200 + 0.5 ATR 且斜率 > 0；BEAR：对称；RANGE：|SMA50 − SMA200| ≤ 0.5 ATR；其余 UNCERTAIN；BULL 当日创 60 日新低 / BEAR 当日创 60 日新高 → UNCERTAIN；切换需 1 个日线收盘确认 |
| 4h | 已确认摆动点 HH/HL/LH/LL | 中期结构 up / down / range（模型 C 要求非 down） |
| 1h | 本地结构 + Brando A/B/C | 交易机会与战术止损 |

对执行 K 线的对齐使用“日线收盘时刻 ≤ 决策时刻”（`MarketContext.htf['1d']['idx']`）；测试 `test_regime_is_point_in_time_truncation_identical` 证明截断历史不改变此前任何一天的状态。BTC 日线状态：BULL 2020–2021、2023–2024 占一半以上，2018 与 2022 为 0；ETH 类似（见下文“趋势状态引擎输出”）。

## 3. 三套研究模型与退出（§三、§四）

- **模型 A 趋势持仓**：日线进入 BULL 后下一根 1h 开盘买入 100%（单资产）并持有；只按退出模式退出；允许再入场。不因 1h 回调卖出。
- **模型 B 核心 + 战术**：核心 50% 由趋势管理（与模型 A 同），战术 50% 由 Brando A/B 买点管理（单笔风险 = 战术资金 × 1%），策略 C 只卖战术袖子 50%；两袖子共用一个现金池，总敞口 ≤ 100%（测试逐根验证）；战术袖子始终用 Brando 结构止损，不随退出模式变化（预登记已注明）。
- **模型 C 趋势过滤 Brando**：日线 BULL 且 4h 非 down 时才用 Brando A/B 买入；策略 C 只减仓 25% 作为风险提示。
- **原 Brando 现货**：第四阶段规则，去掉 1R 保本与摆动低跟踪，改用本阶段三种退出之一同口径运行（第四阶段原始结果见 docs/23）。
- **退出模式**（独立运行、不事后挑选）：A 结构止损（趋势入场 = min(最近已确认 1h 摆动低, SMA50 − 1 ATR(1d)) − 0.2 ATR(1h)）；B 日线趋势失效（离开 BULL 持续 2 个日线收盘）；C ATR 跟踪（自入场最高 1h 收盘 − 3 ATR(1d)，只上移）。已取消 1R 保本默认规则。硬性限制保留：账户回撤 ≥ 20% 暂停买入、单资产 ≤ 100%、单日线跌幅 ≥ 15% 全部退出。

## 4. 参与率指标（§五）

ExposureAnalyzer 对每条权益曲线输出：BULL 期间持仓时间占比、全周期平均仓位、空仓占比与最长空仓天数、上涨月捕获率（策略上涨月收益之和 / 标的上涨月收益之和）、下跌月捕获率、每次卖出后到下一次买入期间标的变动（“卖后错过”）、止损后再入场次数、平均持仓天数、与买入持有的净值差距。`low_dd_due_to_idle_cash` 标记“平均仓位 < 30% 且回撤 < 15%”的组合，提醒低回撤来自空仓而非交易优势。

## 5. 对照组与窗口（§六、§八）

十组对照（同日期、同数据、同 10,000 USDT）：买入持有 BTC / ETH / 等权、100% USDT、期初 50% 现货 + 50% USDT（不再平衡）、SMA200 趋势基准、原 Brando、模型 A/B/C。窗口：全周期 1h（2022-04→2026-11）、保留窗（2025-07→）、验证窗、训练窗、真正未使用的 2022-01→03、4h 长历史（2018-06→）。**所有 1h 区间在前四阶段均已被观察，本阶段只是历史稳健性研究，不存在新的样本外验证**；前瞻记录由 `trend-signal` 逐次写入。

# 结果

## 趋势状态引擎输出

- **BTCUSDT**：状态分布 {'BULL': 1135, 'UNCERTAIN': 1111, 'BEAR': 934, 'RANGE': 188}；切换 121 次；各年 BULL 占比 2017 0%，2018 0%，2019 34%，2020 51%，2021 54%，2022 0%，2023 57%，2024 57%，2025 43%，2026 17%
- **ETHUSDT**：状态分布 {'BULL': 1127, 'UNCERTAIN': 1090, 'BEAR': 985, 'RANGE': 166}；切换 94 次；各年 BULL 占比 2017 0%，2018 0%，2019 24%，2020 60%，2021 82%，2022 0%，2023 51%，2024 48%，2025 31%，2026 15%

## 全周期 1h：2022-04 → 2026-11

### 第一轮：零成本（理想研究环境）

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 67.40% | 11.84% | -66.69% | 0.47 | 0.18 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | -31.77% | -7.97% | -74.49% | 0.20 | -0.11 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | 17.82% | 3.63% | -68.36% | 0.32 | 0.05 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 33.70% | 6.51% | -39.48% | 0.37 | 0.16 | 0 | 52.93% | — | — | — | — | — | — | -33.70% | 否 |
| 6_SMA200趋势_BTC | 231.92% | 29.78% | -33.44% | 0.93 | 0.89 | 17 | 55.45% | — | 68.10% | 44.10% | 23.51% | 0 | — | 164.52% | 否 |
| 7_原Brando_BTC|A_structure_stop | -17.72% | -4.15% | -20.83% | -1.02 | -0.20 | 59 | 0.97% | 0.00% | 0.00% | 3.93% | 1089.69% | 28 | 1.68 | -85.12% | 否 |
| 7_原Brando_BTC|B_trend_failure | -15.55% | -3.61% | -22.45% | -0.24 | -0.16 | 466 | 7.91% | 23.52% | 3.06% | 11.29% | 192.03% | 0 | 1.05 | -82.95% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -25.83% | -6.29% | -28.58% | -0.83 | -0.22 | 32 | 1.89% | 0.00% | 0.00% | 6.43% | 440.01% | 1 | 3.41 | -93.24% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 252.04% | 31.45% | -53.73% | 0.86 | 0.59 | 0 | 81.16% | 99.99% | 85.70% | 64.72% | — | 0 | — | 184.64% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 3.71% | 0.79% | -27.91% | 0.13 | 0.03 | 2 | 8.97% | 23.64% | 8.45% | 9.91% | 208.58% | 0 | 75.46 | -63.69% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | -10.31% | -2.34% | -22.63% | -0.29 | -0.10 | 3 | 1.63% | 4.37% | -2.40% | -0.00% | 290.23% | 2 | 9.11 | -77.71% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | 146.70% | 21.68% | -44.25% | 0.75 | 0.49 | 1033 | 68.07% | 99.99% | 69.57% | 57.01% | 17.78% | 449 | 2.02 | 79.30% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | 8.76% | 1.84% | -20.06% | 0.18 | 0.09 | 503 | 13.15% | 23.65% | 12.73% | 12.48% | 92.49% | 212 | 2.43 | -58.64% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | 8.35% | 1.76% | -20.55% | 0.18 | 0.09 | 514 | 13.61% | 23.60% | 12.12% | 11.75% | 322.65% | 220 | 2.38 | -59.06% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | -0.36% | -0.08% | -20.22% | 0.04 | -0.00 | 128 | 4.95% | 16.12% | 5.28% | 6.57% | 382.92% | 51 | 2.86 | -67.76% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | -0.76% | -0.17% | -27.36% | 0.05 | -0.01 | 78 | 8.24% | 23.36% | 6.88% | 9.72% | 649.53% | 0 | 11.82 | -68.17% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 17.73% | 3.61% | -21.65% | 0.33 | 0.17 | 107 | 9.26% | 28.61% | 8.72% | 5.59% | 1309.43% | 6 | 9.92 | -49.67% | 否 |
| 5_固定50%现货50%USDT_ETH | -15.88% | -3.69% | -41.53% | -0.02 | -0.09 | 0 | 41.14% | — | — | — | — | — | — | 15.88% | 否 |
| 6_SMA200趋势_ETH | 113.92% | 17.96% | -40.65% | 0.61 | 0.44 | 14 | 46.70% | — | 51.11% | 33.60% | -83.32% | 0 | — | 145.69% | 否 |
| 7_原Brando_ETH|A_structure_stop | -21.11% | -5.02% | -21.11% | -0.92 | -0.24 | 59 | 0.93% | 0.00% | 0.00% | 3.23% | -9.75% | 31 | 0.84 | 10.66% | 否 |
| 7_原Brando_ETH|B_trend_failure | -9.05% | -2.04% | -21.07% | -0.08 | -0.10 | 498 | 7.57% | 28.33% | 3.55% | 6.17% | 12.99% | 0 | 1.01 | 22.71% | 否 |
| 7_原Brando_ETH|C_atr_trailing | -22.48% | -5.38% | -24.00% | -0.81 | -0.22 | 32 | 1.60% | 0.00% | 0.00% | 2.38% | -36.53% | 0 | 3.54 | 9.29% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | 42.67% | 8.03% | -69.13% | 0.42 | 0.12 | 2 | 80.90% | 99.98% | 77.57% | 68.27% | 0.85% | 2 | 11.65 | 74.44% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 9.45% | 1.98% | -21.95% | 0.21 | 0.09 | 2 | 9.03% | 28.82% | 4.30% | 2.61% | 32.33% | 0 | 75.96 | 41.22% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | 3.78% | 0.81% | -22.48% | 0.13 | 0.04 | 8 | 9.31% | 28.71% | 2.23% | 1.55% | 36.42% | 7 | 19.57 | 35.54% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | 12.17% | 2.53% | -56.76% | 0.26 | 0.04 | 805 | 58.84% | 99.98% | 53.51% | 53.75% | 91.26% | 361 | 1.79 | 43.93% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | 68.90% | 12.06% | -20.20% | 0.66 | 0.60 | 756 | 20.45% | 57.87% | 22.73% | 10.66% | -90.30% | 315 | 2.35 | 100.67% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | -9.26% | -2.09% | -20.08% | -0.07 | -0.10 | 572 | 12.24% | 28.78% | 7.13% | 9.42% | -4.27% | 246 | 1.76 | 22.51% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | 37.37% | 7.14% | -20.07% | 0.50 | 0.36 | 283 | 11.74% | 48.74% | 11.10% | 3.70% | 63.34% | 107 | 3.87 | 69.14% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 4.70% | 1.00% | -21.89% | 0.14 | 0.05 | 101 | 8.00% | 28.10% | 3.36% | 2.64% | 142.97% | 0 | 10.02 | 36.46% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 41.18% | 7.78% | -20.25% | 0.51 | 0.38 | 164 | 13.39% | 46.96% | 10.28% | 1.64% | -117.41% | 12 | 9.02 | 72.94% | 否 |

- 7_原Brando_BTC|A_structure_stop：年度 2022 -17.7%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 38, "sell|C_rejection": 21}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 41, "exit_structure": 38, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1838, "buy_rejected_cash": 10, "buy_rejected_regime": 0}
- 7_原Brando_BTC|B_trend_failure：年度 2022 -10.6%，2023 -5.5%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trend_failure": 390, "sell|C_rejection": 76}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 424, "exit_structure": 0, "exit_trend": 390, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1323, "buy_rejected_cash": 144, "buy_rejected_regime": 0}
- 7_原Brando_BTC|C_atr_trailing：年度 2022 -25.8%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"sell|C_rejection": 28, "trail_stop": 4}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 17, "exit_structure": 0, "exit_trend": 0, "exit_trail": 4, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1851, "buy_rejected_cash": 21, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|B_trend_failure：年度 2022 0.0%，2023 3.7%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trend_failure": 2}；计数 {"core_entries": 2, "core_reentries": 1, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 2, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 11459, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|C_atr_trailing：年度 2022 0.0%，2023 -10.3%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trail_stop": 3}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 3, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 14351, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|A_structure_stop：年度 2022 -11.8%，2023 94.7%，2024 72.2%，2025 -7.8%，2026 -9.6%；退出 {"structure_stop": 616, "sell|C_rejection": 417}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 735, "exit_structure": 616, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 683, "buy_rejected_cash": 471, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|B_trend_failure：年度 2022 -11.8%，2023 23.3%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 302, "sell|C_rejection": 199, "trend_failure": 2}；计数 {"core_entries": 2, "core_reentries": 1, "tactical_entries": 361, "exit_structure": 302, "exit_trend": 2, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 12773, "buy_rejected_cash": 214, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|C_atr_trailing：年度 2022 -11.8%，2023 22.8%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 305, "sell|C_rejection": 199, "trail_stop": 10}；计数 {"core_entries": 10, "core_reentries": 9, "tactical_entries": 364, "exit_structure": 305, "exit_trend": 0, "exit_trail": 10, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 12771, "buy_rejected_cash": 213, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_BTC|A_structure_stop：年度 2022 0.0%，2023 -0.4%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 82, "reduce|C_rejection": 46}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 97, "exit_structure": 82, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 34, "buy_rejected_halt": 447, "buy_rejected_cash": 55, "buy_rejected_regime": 1290}
- 10_模型C趋势过滤Brando_BTC|B_trend_failure：年度 2022 0.0%，2023 -0.8%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"reduce|C_rejection": 72, "trend_failure": 6}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 36, "exit_structure": 0, "exit_trend": 6, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 42, "buy_rejected_halt": 440, "buy_rejected_cash": 124, "buy_rejected_regime": 1290}
- 10_模型C趋势过滤Brando_BTC|C_atr_trailing：年度 2022 0.0%，2023 17.7%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"reduce|C_rejection": 83, "trail_stop": 24}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 49, "exit_structure": 0, "exit_trend": 0, "exit_trail": 24, "exit_crash": 0, "exit_rejection_reduce": 58, "buy_rejected_halt": 422, "buy_rejected_cash": 128, "buy_rejected_regime": 1290}
- 7_原Brando_ETH|A_structure_stop：年度 2022 -21.1%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 41, "sell|C_rejection": 18}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 45, "exit_structure": 41, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1785, "buy_rejected_cash": 8, "buy_rejected_regime": 0}
- 7_原Brando_ETH|B_trend_failure：年度 2022 -11.5%，2023 2.8%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trend_failure": 385, "sell|C_rejection": 111, "crash_exit": 2}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 441, "exit_structure": 0, "exit_trend": 385, "exit_trail": 0, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 1296, "buy_rejected_cash": 101, "buy_rejected_regime": 0}
- 7_原Brando_ETH|C_atr_trailing：年度 2022 -22.5%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"sell|C_rejection": 30, "trail_stop": 2}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 14, "exit_structure": 0, "exit_trend": 0, "exit_trail": 2, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1807, "buy_rejected_cash": 17, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|A_structure_stop：年度 2022 0.0%，2023 45.4%，2024 46.2%，2025 -11.0%，2026 -24.6%；退出 {"structure_stop": 2}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 0, "exit_structure": 2, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|B_trend_failure：年度 2022 0.0%，2023 9.5%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trend_failure": 2}；计数 {"core_entries": 2, "core_reentries": 1, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 2, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 8880, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|C_atr_trailing：年度 2022 0.0%，2023 3.8%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trail_stop": 8}；计数 {"core_entries": 8, "core_reentries": 7, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 8, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 8888, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|A_structure_stop：年度 2022 -16.1%，2023 37.9%，2024 36.6%，2025 -14.8%，2026 -16.7%；退出 {"structure_stop": 504, "sell|C_rejection": 299, "crash_exit": 2}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 595, "exit_structure": 504, "exit_trend": 0, "exit_trail": 0, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 986, "buy_rejected_cash": 257, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|B_trend_failure：年度 2022 -16.1%，2023 38.8%，2024 45.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 450, "sell|C_rejection": 301, "trend_failure": 3, "crash_exit": 2}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 549, "exit_structure": 450, "exit_trend": 3, "exit_trail": 0, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 6294, "buy_rejected_cash": 251, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|C_atr_trailing：年度 2022 -16.1%，2023 8.1%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 348, "sell|C_rejection": 212, "trail_stop": 10, "crash_exit": 2}；计数 {"core_entries": 10, "core_reentries": 9, "tactical_entries": 416, "exit_structure": 348, "exit_trend": 0, "exit_trail": 10, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 10162, "buy_rejected_cash": 140, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_ETH|A_structure_stop：年度 2022 0.0%，2023 -3.1%，2024 41.8%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 172, "reduce|C_rejection": 111}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 192, "exit_structure": 172, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 91, "buy_rejected_halt": 193, "buy_rejected_cash": 115, "buy_rejected_regime": 1338}
- 10_模型C趋势过滤Brando_ETH|B_trend_failure：年度 2022 0.0%，2023 4.7%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"reduce|C_rejection": 94, "trend_failure": 7}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 46, "exit_structure": 0, "exit_trend": 7, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 55, "buy_rejected_halt": 366, "buy_rejected_cash": 88, "buy_rejected_regime": 1338}
- 10_模型C趋势过滤Brando_ETH|C_atr_trailing：年度 2022 0.0%，2023 9.6%，2024 28.8%，2025 0.0%，2026 0.0%；退出 {"reduce|C_rejection": 126, "trail_stop": 38}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 81, "exit_structure": 0, "exit_trend": 0, "exit_trail": 38, "exit_crash": 0, "exit_rejection_reduce": 83, "buy_rejected_halt": 241, "buy_rejected_cash": 178, "buy_rejected_regime": 1338}

图：`full_1h_round1_equity_BTC.png`


### 第二轮：手续费 10 bps + 滑点 5 bps

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 67.23% | 11.82% | -66.69% | 0.47 | 0.18 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | -31.83% | -7.99% | -74.49% | 0.20 | -0.11 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | 17.70% | 3.60% | -68.36% | 0.32 | 0.05 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 33.62% | 6.50% | -39.47% | 0.37 | 0.16 | 0 | 52.91% | — | — | — | — | — | — | -33.62% | 否 |
| 6_SMA200趋势_BTC | 215.90% | 28.39% | -34.43% | 0.90 | 0.82 | 17 | 55.45% | — | 67.66% | 45.26% | 25.13% | 0 | — | 148.67% | 否 |
| 7_原Brando_BTC|A_structure_stop | -18.41% | -4.32% | -20.77% | -1.21 | -0.21 | 42 | 0.80% | 0.00% | 0.00% | 2.71% | 307.62% | 22 | 0.99 | -85.64% | 否 |
| 7_原Brando_BTC|B_trend_failure | -19.94% | -4.72% | -20.03% | -2.18 | -0.24 | 98 | 0.00% | 0.00% | -1.06% | 4.80% | 219.20% | 0 | 0.00 | -87.18% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -25.35% | -6.16% | -28.01% | -0.88 | -0.22 | 31 | 1.87% | 0.00% | 0.00% | 5.63% | 279.92% | 1 | 3.43 | -92.59% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 251.51% | 31.41% | -53.73% | 0.86 | 0.58 | 0 | 81.16% | 99.99% | 85.66% | 64.72% | — | 0 | — | 184.28% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 3.09% | 0.66% | -28.13% | 0.12 | 0.02 | 2 | 8.97% | 23.64% | 8.41% | 10.07% | 208.84% | 0 | 75.46 | -64.14% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | -11.11% | -2.53% | -23.21% | -0.31 | -0.11 | 3 | 1.63% | 4.37% | -2.62% | -0.00% | 290.63% | 2 | 9.11 | -78.34% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | -19.05% | -4.49% | -20.24% | -0.89 | -0.22 | 159 | 1.92% | 0.00% | 0.54% | 6.30% | 235.78% | 79 | 1.40 | -86.28% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | -19.05% | -4.49% | -20.24% | -0.89 | -0.22 | 159 | 1.92% | 0.00% | 0.54% | 6.30% | 235.78% | 79 | 1.40 | -86.28% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | -19.05% | -4.49% | -20.24% | -0.89 | -0.22 | 159 | 1.92% | 0.00% | 0.54% | 6.30% | 235.78% | 79 | 1.40 | -86.28% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | -5.85% | -1.30% | -20.34% | -0.09 | -0.06 | 105 | 4.00% | 13.05% | 3.11% | 5.64% | 219.53% | 43 | 3.01 | -73.08% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | 4.81% | 1.03% | -22.18% | 0.14 | 0.05 | 75 | 7.36% | 23.36% | 6.38% | 7.05% | 1068.84% | 0 | 12.02 | -62.42% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 11.31% | 2.36% | -20.66% | 0.25 | 0.11 | 87 | 8.16% | 22.90% | 7.37% | 5.81% | 575.02% | 4 | 9.37 | -55.92% | 否 |
| 5_固定50%现货50%USDT_ETH | -15.92% | -3.70% | -41.51% | -0.02 | -0.09 | 0 | 41.12% | — | — | — | — | — | — | 15.92% | 否 |
| 6_SMA200趋势_ETH | 105.44% | 16.93% | -41.35% | 0.59 | 0.41 | 14 | 46.70% | — | 50.77% | 34.14% | -82.10% | 0 | — | 137.27% | 否 |
| 7_原Brando_ETH|A_structure_stop | -20.21% | -4.79% | -20.21% | -1.31 | -0.24 | 39 | 0.52% | 0.00% | 0.00% | 1.82% | -35.04% | 23 | 0.69 | 11.63% | 否 |
| 7_原Brando_ETH|B_trend_failure | -20.26% | -4.80% | -20.26% | -2.08 | -0.24 | 73 | 0.00% | 0.00% | 0.00% | 3.87% | 29.21% | 0 | 0.00 | 11.57% | 否 |
| 7_原Brando_ETH|C_atr_trailing | -24.37% | -5.89% | -25.64% | -0.88 | -0.23 | 32 | 1.60% | 0.00% | 0.00% | 2.50% | -34.72% | 0 | 3.54 | 7.46% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | 41.61% | 7.85% | -69.13% | 0.41 | 0.11 | 2 | 80.90% | 99.98% | 77.40% | 68.27% | 1.06% | 2 | 11.65 | 73.44% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 8.80% | 1.85% | -22.18% | 0.20 | 0.08 | 2 | 9.03% | 28.82% | 4.23% | 2.68% | 32.50% | 0 | 75.96 | 40.63% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | -11.66% | -2.66% | -20.16% | -0.38 | -0.13 | 2 | 1.38% | 4.47% | -2.36% | -0.00% | 61.46% | 1 | 11.62 | 20.18% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | -19.41% | -4.58% | -20.49% | -1.27 | -0.22 | 83 | 0.71% | 0.00% | 0.00% | 3.50% | 431.89% | 40 | 1.05 | 12.43% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | -19.41% | -4.58% | -20.49% | -1.27 | -0.22 | 83 | 0.71% | 0.00% | 0.00% | 3.50% | 431.89% | 40 | 1.05 | 12.43% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | -19.41% | -4.58% | -20.49% | -1.27 | -0.22 | 83 | 0.71% | 0.00% | 0.00% | 3.50% | 431.89% | 40 | 1.05 | 12.43% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | -13.23% | -3.03% | -20.39% | -0.31 | -0.15 | 93 | 3.04% | 13.86% | -2.87% | -0.00% | 131.57% | 35 | 3.26 | 18.61% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 0.19% | 0.04% | -24.00% | 0.07 | 0.00 | 101 | 8.00% | 28.10% | 2.67% | 2.93% | 149.05% | 0 | 10.09 | 32.02% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 30.09% | 5.88% | -20.72% | 0.41 | 0.28 | 163 | 13.39% | 46.96% | 8.83% | 2.01% | -106.55% | 12 | 9.03 | 61.93% | 否 |


## 保留窗 1h：2025-07 → 2026-11（半锁定）

### 第一轮：零成本（理想研究环境）

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | -28.90% | -22.31% | -53.74% | -0.37 | -0.42 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | -9.91% | -7.44% | -69.15% | 0.23 | -0.11 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | -19.40% | -14.76% | -62.29% | -0.01 | -0.24 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | -14.45% | -10.91% | -29.05% | -0.52 | -0.38 | 0 | 44.15% | — | — | — | — | — | — | 14.45% | 否 |
| 6_SMA200趋势_BTC | 3.18% | 2.35% | -20.59% | 0.27 | 0.11 | 5 | 40.57% | — | 43.43% | 30.70% | -29.35% | 0 | — | 32.08% | 否 |
| 7_原Brando_BTC|A_structure_stop | -13.18% | -9.94% | -20.74% | -0.77 | -0.48 | 172 | 18.88% | 43.20% | -4.83% | 13.31% | -341.20% | 71 | 7.86 | 15.72% | 否 |
| 7_原Brando_BTC|B_trend_failure | 0.91% | 0.67% | -14.08% | 0.12 | 0.05 | 513 | 22.26% | 95.15% | 20.23% | 12.81% | -27.09% | 0 | 0.85 | 29.80% | 是 |
| 7_原Brando_BTC|C_atr_trailing | -14.06% | -10.61% | -21.57% | -0.67 | -0.49 | 97 | 22.91% | 57.90% | 2.19% | 19.49% | -58.10% | 4 | 4.78 | 14.84% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | -8.24% | -6.17% | -21.80% | -0.14 | -0.28 | 3 | 20.69% | 51.48% | 9.27% | 22.57% | -18.95% | 2 | 34.06 | 20.66% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | -10.38% | -7.79% | -23.67% | -0.43 | -0.33 | 3 | 15.15% | 54.25% | 3.10% | 21.52% | -17.83% | 0 | 24.94 | 18.52% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | -11.22% | -8.44% | -22.63% | -0.44 | -0.37 | 6 | 18.30% | 54.15% | 6.03% | 23.54% | -17.51% | 5 | 15.06 | 17.67% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | -11.28% | -8.48% | -20.76% | -0.46 | -0.41 | 124 | 20.57% | 60.26% | 2.21% | 18.98% | -17.10% | 57 | 2.90 | 17.62% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | -10.46% | -7.86% | -20.03% | -0.59 | -0.39 | 138 | 17.07% | 60.35% | -0.86% | 16.16% | -13.70% | 64 | 2.51 | 18.43% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | -10.69% | -8.03% | -20.15% | -0.57 | -0.40 | 129 | 18.46% | 60.23% | 0.62% | 17.01% | -10.88% | 59 | 2.69 | 18.20% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | -3.07% | -2.28% | -16.55% | -0.12 | -0.14 | 115 | 15.29% | 69.95% | 11.59% | 13.13% | -50.66% | 46 | 3.12 | 25.83% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | -4.81% | -3.58% | -16.95% | -0.15 | -0.21 | 86 | 22.90% | 95.15% | 18.78% | 21.47% | -71.11% | 0 | 9.08 | 24.09% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | -7.90% | -5.91% | -18.59% | -0.31 | -0.32 | 98 | 24.67% | 95.03% | 21.96% | 25.84% | -50.17% | 4 | 8.14 | 20.99% | 否 |
| 5_固定50%现货50%USDT_ETH | -4.96% | -3.69% | -45.99% | 0.09 | -0.08 | 0 | 51.58% | — | — | — | — | — | — | 4.96% | 否 |
| 6_SMA200趋势_ETH | 15.78% | 11.46% | -40.65% | 0.47 | 0.28 | 6 | 41.80% | — | 39.91% | 41.94% | -20.58% | 0 | — | 25.69% | 否 |
| 7_原Brando_ETH|A_structure_stop | 31.03% | 22.15% | -20.86% | 1.29 | 1.06 | 83 | 8.63% | 35.22% | 7.95% | 6.82% | -33.10% | 33 | 2.66 | 40.94% | 否 |
| 7_原Brando_ETH|B_trend_failure | 26.67% | 19.13% | -20.40% | 0.85 | 0.94 | 97 | 15.39% | 65.65% | 15.13% | 9.29% | -23.54% | 0 | 3.54 | 36.58% | 否 |
| 7_原Brando_ETH|C_atr_trailing | 46.11% | 32.41% | -21.02% | 1.18 | 1.54 | 87 | 20.14% | 64.90% | 13.99% | 7.73% | -113.34% | 2 | 5.47 | 56.02% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | -18.00% | -13.67% | -50.94% | -0.12 | -0.27 | 1 | 41.45% | 66.16% | 20.15% | 44.24% | -7.45% | 0 | 204.71 | -8.09% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 26.93% | 19.31% | -25.11% | 0.72 | 0.77 | 1 | 18.83% | 66.16% | 20.15% | 12.41% | -40.24% | 0 | 92.96 | 36.84% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | 31.22% | 22.29% | -21.85% | 0.87 | 1.02 | 5 | 15.50% | 55.07% | 20.74% | 9.95% | -41.84% | 4 | 15.31 | 41.14% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | 4.06% | 2.99% | -36.36% | 0.26 | 0.08 | 86 | 25.21% | 66.19% | 14.02% | 26.29% | -85.16% | 34 | 5.24 | 13.97% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | 27.31% | 19.57% | -22.69% | 0.94 | 0.86 | 86 | 14.35% | 66.19% | 14.02% | 10.70% | -117.95% | 34 | 3.94 | 37.22% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | 30.11% | 21.52% | -20.57% | 1.07 | 1.05 | 90 | 12.47% | 55.10% | 14.50% | 9.08% | 8.06% | 37 | 3.58 | 40.03% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | 25.79% | 18.52% | -19.80% | 0.93 | 0.93 | 141 | 15.75% | 77.34% | 15.59% | 7.84% | -93.85% | 51 | 3.81 | 35.70% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 29.55% | 21.13% | -20.58% | 0.85 | 1.03 | 61 | 16.71% | 65.65% | 18.46% | 8.64% | -201.17% | 0 | 10.73 | 39.46% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 21.16% | 15.27% | -22.06% | 0.65 | 0.69 | 99 | 24.01% | 93.91% | 19.85% | 14.67% | -165.42% | 7 | 7.35 | 31.07% | 否 |

- 7_原Brando_BTC|A_structure_stop：年度 2025 -13.2%，2026 0.0%；退出 {"structure_stop": 98, "sell|C_rejection": 74}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 115, "exit_structure": 98, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 387, "buy_rejected_cash": 60, "buy_rejected_regime": 0}
- 7_原Brando_BTC|B_trend_failure：年度 2025 0.3%，2026 0.6%；退出 {"trend_failure": 428, "sell|C_rejection": 85}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 466, "exit_structure": 0, "exit_trend": 428, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 96, "buy_rejected_regime": 0}
- 7_原Brando_BTC|C_atr_trailing：年度 2025 -14.1%，2026 0.0%；退出 {"sell|C_rejection": 85, "trail_stop": 12}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 46, "exit_structure": 0, "exit_trend": 0, "exit_trail": 12, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 407, "buy_rejected_cash": 109, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|A_structure_stop：年度 2025 -8.2%，2026 0.0%；退出 {"structure_stop": 3}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 0, "exit_structure": 3, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1539, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|B_trend_failure：年度 2025 -10.4%，2026 0.0%；退出 {"trend_failure": 3}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 3, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1451, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|C_atr_trailing：年度 2025 -11.2%，2026 0.0%；退出 {"trail_stop": 6}；计数 {"core_entries": 6, "core_reentries": 5, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 6, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1451, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|A_structure_stop：年度 2025 -11.3%，2026 0.0%；退出 {"structure_stop": 77, "sell|C_rejection": 47}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 85, "exit_structure": 77, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1677, "buy_rejected_cash": 59, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|B_trend_failure：年度 2025 -10.5%，2026 0.0%；退出 {"structure_stop": 87, "sell|C_rejection": 47, "trend_failure": 4}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 98, "exit_structure": 87, "exit_trend": 4, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1660, "buy_rejected_cash": 63, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|C_atr_trailing：年度 2025 -10.7%，2026 0.0%；退出 {"structure_stop": 76, "sell|C_rejection": 46, "trail_stop": 7}；计数 {"core_entries": 7, "core_reentries": 6, "tactical_entries": 87, "exit_structure": 76, "exit_trend": 0, "exit_trail": 7, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1674, "buy_rejected_cash": 60, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_BTC|A_structure_stop：年度 2025 -5.4%，2026 2.5%；退出 {"structure_stop": 73, "reduce|C_rejection": 42}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 79, "exit_structure": 73, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 37, "buy_rejected_halt": 0, "buy_rejected_cash": 32, "buy_rejected_regime": 451}
- 10_模型C趋势过滤Brando_BTC|B_trend_failure：年度 2025 -5.2%，2026 0.4%；退出 {"reduce|C_rejection": 78, "trend_failure": 8}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 42, "exit_structure": 0, "exit_trend": 8, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 49, "buy_rejected_halt": 0, "buy_rejected_cash": 69, "buy_rejected_regime": 451}
- 10_模型C趋势过滤Brando_BTC|C_atr_trailing：年度 2025 -7.7%，2026 -0.3%；退出 {"reduce|C_rejection": 79, "trail_stop": 19}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 43, "exit_structure": 0, "exit_trend": 0, "exit_trail": 19, "exit_crash": 0, "exit_rejection_reduce": 57, "buy_rejected_halt": 0, "buy_rejected_cash": 68, "buy_rejected_regime": 451}
- 7_原Brando_ETH|A_structure_stop：年度 2025 31.0%，2026 0.0%；退出 {"structure_stop": 51, "sell|C_rejection": 32}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 63, "exit_structure": 51, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 445, "buy_rejected_cash": 24, "buy_rejected_regime": 0}
- 7_原Brando_ETH|B_trend_failure：年度 2025 26.7%，2026 0.0%；退出 {"sell|C_rejection": 64, "trend_failure": 33}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 64, "exit_structure": 0, "exit_trend": 33, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 398, "buy_rejected_cash": 70, "buy_rejected_regime": 0}
- 7_原Brando_ETH|C_atr_trailing：年度 2025 46.1%，2026 0.0%；退出 {"sell|C_rejection": 80, "trail_stop": 6, "structure_stop": 1}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 43, "exit_structure": 1, "exit_trend": 0, "exit_trail": 6, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 409, "buy_rejected_cash": 80, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|A_structure_stop：年度 2025 0.7%，2026 -18.6%；退出 {"structure_stop": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 0, "exit_structure": 1, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1128, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|B_trend_failure：年度 2025 26.9%，2026 0.0%；退出 {"trend_failure": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 1, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1128, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|C_atr_trailing：年度 2025 31.2%，2026 0.0%；退出 {"trail_stop": 5}；计数 {"core_entries": 5, "core_reentries": 4, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 5, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1494, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|A_structure_stop：年度 2025 13.7%，2026 -8.5%；退出 {"structure_stop": 52, "sell|C_rejection": 34}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 62, "exit_structure": 52, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1569, "buy_rejected_cash": 29, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|B_trend_failure：年度 2025 27.3%，2026 0.0%；退出 {"structure_stop": 51, "sell|C_rejection": 34, "trend_failure": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 62, "exit_structure": 51, "exit_trend": 1, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1569, "buy_rejected_cash": 29, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|C_atr_trailing：年度 2025 30.1%，2026 0.0%；退出 {"structure_stop": 51, "sell|C_rejection": 34, "trail_stop": 5}；计数 {"core_entries": 5, "core_reentries": 4, "tactical_entries": 62, "exit_structure": 51, "exit_trend": 0, "exit_trail": 5, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 1935, "buy_rejected_cash": 29, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_ETH|A_structure_stop：年度 2025 29.5%，2026 -2.9%；退出 {"structure_stop": 85, "reduce|C_rejection": 56}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 98, "exit_structure": 85, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 43, "buy_rejected_halt": 0, "buy_rejected_cash": 34, "buy_rejected_regime": 400}
- 10_模型C趋势过滤Brando_ETH|B_trend_failure：年度 2025 29.5%，2026 0.0%；退出 {"reduce|C_rejection": 58, "trend_failure": 3}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 28, "exit_structure": 0, "exit_trend": 3, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 33, "buy_rejected_halt": 40, "buy_rejected_cash": 64, "buy_rejected_regime": 400}
- 10_模型C趋势过滤Brando_ETH|C_atr_trailing：年度 2025 35.0%，2026 -10.2%；退出 {"reduce|C_rejection": 70, "trail_stop": 28, "structure_stop": 1}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 51, "exit_structure": 1, "exit_trend": 0, "exit_trail": 28, "exit_crash": 0, "exit_rejection_reduce": 48, "buy_rejected_halt": 3, "buy_rejected_cash": 79, "buy_rejected_regime": 400}

图：`holdout_round1_equity_BTC.png`


### 第二轮：手续费 10 bps + 滑点 5 bps

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | -28.97% | -22.37% | -53.74% | -0.37 | -0.42 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | -10.00% | -7.51% | -69.15% | 0.23 | -0.11 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | -19.49% | -14.82% | -62.29% | -0.01 | -0.24 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | -14.48% | -10.94% | -29.03% | -0.52 | -0.38 | 0 | 44.12% | — | — | — | — | — | — | 14.48% | 否 |
| 6_SMA200趋势_BTC | 1.80% | 1.33% | -21.30% | 0.23 | 0.06 | 5 | 40.57% | — | 43.14% | 31.79% | -28.98% | 0 | — | 30.77% | 否 |
| 7_原Brando_BTC|A_structure_stop | -14.09% | -10.63% | -20.59% | -1.36 | -0.52 | 80 | 9.88% | 34.83% | -11.34% | 5.62% | -96.36% | 37 | 2.32 | 14.88% | 否 |
| 7_原Brando_BTC|B_trend_failure | -13.00% | -9.80% | -20.02% | -1.13 | -0.49 | 80 | 11.19% | 49.47% | -17.71% | 3.69% | -16.70% | 0 | 2.98 | 15.97% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -14.80% | -11.18% | -22.41% | -0.78 | -0.50 | 92 | 21.57% | 57.90% | -1.18% | 17.00% | -67.81% | 4 | 4.85 | 14.17% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | -6.55% | -4.89% | -20.23% | -0.08 | -0.24 | 2 | 20.62% | 51.23% | 9.27% | 20.63% | -21.25% | 1 | 50.92 | 22.42% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | -3.61% | -2.69% | -20.04% | -0.04 | -0.13 | 2 | 14.35% | 52.01% | 2.84% | 13.79% | -23.17% | 0 | 35.44 | 25.35% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | -12.81% | -9.65% | -23.67% | -0.51 | -0.41 | 6 | 18.30% | 54.15% | 5.76% | 24.74% | -16.96% | 5 | 15.06 | 16.16% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | -11.78% | -8.86% | -20.63% | -0.53 | -0.43 | 102 | 16.74% | 51.48% | -1.61% | 14.96% | 8.77% | 46 | 3.01 | 17.19% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | -13.20% | -9.95% | -22.53% | -0.87 | -0.44 | 108 | 14.04% | 54.31% | -5.30% | 14.38% | -125.21% | 44 | 2.72 | 15.77% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | -13.29% | -10.02% | -21.99% | -0.86 | -0.46 | 105 | 15.33% | 54.19% | -3.78% | 14.93% | -6.30% | 46 | 2.81 | 15.68% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | -11.62% | -8.74% | -20.08% | -1.02 | -0.44 | 60 | 8.84% | 40.21% | -8.10% | 8.41% | -25.98% | 28 | 3.59 | 17.35% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | -8.67% | -6.50% | -18.49% | -0.35 | -0.35 | 85 | 22.90% | 95.15% | 17.15% | 23.97% | -65.48% | 0 | 9.02 | 20.29% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | -3.59% | -2.67% | -20.67% | -0.15 | -0.13 | 71 | 17.47% | 74.17% | 17.26% | 17.01% | -132.14% | 3 | 9.05 | 25.38% | 否 |
| 5_固定50%现货50%USDT_ETH | -5.00% | -3.73% | -45.97% | 0.09 | -0.08 | 0 | 51.55% | — | — | — | — | — | — | 5.00% | 否 |
| 6_SMA200趋势_ETH | 13.89% | 10.11% | -41.35% | 0.44 | 0.24 | 6 | 41.80% | — | 39.74% | 42.61% | -20.10% | 0 | — | 23.89% | 否 |
| 7_原Brando_ETH|A_structure_stop | 25.17% | 18.09% | -20.44% | 1.10 | 0.88 | 67 | 7.67% | 31.12% | 3.52% | 4.55% | 11.04% | 27 | 3.02 | 35.18% | 否 |
| 7_原Brando_ETH|B_trend_failure | 21.91% | 15.80% | -20.17% | 0.75 | 0.78 | 78 | 15.40% | 65.65% | 13.45% | 8.91% | -9.68% | 0 | 4.57 | 31.91% | 否 |
| 7_原Brando_ETH|C_atr_trailing | 43.68% | 30.78% | -20.54% | 1.17 | 1.50 | 82 | 17.81% | 64.24% | 12.28% | 6.76% | -244.52% | 2 | 5.67 | 53.68% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | -18.25% | -13.86% | -51.02% | -0.12 | -0.27 | 1 | 41.45% | 66.16% | 20.15% | 44.34% | -7.41% | 0 | 204.71 | -8.25% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 26.55% | 19.04% | -25.11% | 0.72 | 0.76 | 1 | 18.83% | 66.16% | 20.15% | 12.52% | -40.21% | 0 | 92.96 | 36.55% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | 29.34% | 20.99% | -22.39% | 0.83 | 0.94 | 5 | 15.50% | 55.07% | 19.98% | 10.44% | -41.44% | 4 | 15.31 | 39.35% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | -2.61% | -1.94% | -38.91% | 0.08 | -0.05 | 85 | 26.01% | 66.19% | 11.82% | 28.48% | -80.24% | 34 | 5.29 | 7.39% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | 20.45% | 14.77% | -24.94% | 0.75 | 0.59 | 85 | 14.59% | 66.19% | 11.82% | 12.10% | -113.04% | 34 | 3.98 | 30.46% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | 22.43% | 16.16% | -23.18% | 0.85 | 0.70 | 90 | 12.51% | 55.10% | 11.79% | 10.52% | 15.91% | 37 | 3.58 | 32.43% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | 18.35% | 13.28% | -20.04% | 0.80 | 0.66 | 69 | 8.64% | 48.35% | 7.85% | 5.80% | -249.06% | 26 | 5.21 | 28.35% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 26.32% | 18.88% | -21.61% | 0.78 | 0.87 | 61 | 16.71% | 65.65% | 17.57% | 9.64% | -197.92% | 0 | 10.73 | 36.32% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 21.43% | 15.46% | -20.81% | 0.66 | 0.74 | 96 | 21.88% | 88.94% | 17.56% | 12.91% | -179.56% | 6 | 7.44 | 31.43% | 否 |


## 验证窗 1h：2024-07 → 2025-06

### 第一轮：零成本（理想研究环境）

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 73.19% | 73.45% | -30.94% | 1.36 | 2.37 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | -26.74% | -26.81% | -65.28% | -0.06 | -0.41 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | 23.22% | 23.29% | -44.44% | 0.65 | 0.52 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 36.59% | 36.71% | -19.59% | 1.28 | 1.87 | 0 | 56.57% | — | — | — | — | — | — | -36.59% | 否 |
| 6_SMA200趋势_BTC | 12.19% | 12.23% | -33.44% | 0.49 | 0.37 | 11 | 70.26% | — | 69.34% | 132.30% | 44.35% | 0 | — | -61.00% | 否 |
| 7_原Brando_BTC|A_structure_stop | -17.98% | -18.02% | -20.10% | -1.67 | -0.90 | 72 | 6.85% | 0.09% | -2.00% | 30.62% | 55.54% | 35 | 1.31 | -91.16% | 否 |
| 7_原Brando_BTC|B_trend_failure | 37.82% | 37.94% | -16.09% | 1.35 | 2.36 | 323 | 32.85% | 97.16% | 52.59% | -0.98% | -5.85% | 0 | 1.34 | -35.37% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -15.08% | -15.11% | -20.35% | -0.97 | -0.74 | 37 | 8.86% | 1.03% | -0.00% | 24.87% | 328.47% | 4 | 7.86 | -88.26% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 44.95% | 45.09% | -30.92% | 1.13 | 1.46 | 3 | 66.96% | 99.88% | 81.69% | 72.15% | 19.02% | 3 | 0.97 | -28.24% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 25.46% | 25.54% | -18.28% | 0.94 | 1.40 | 2 | 39.23% | 99.91% | 50.25% | 24.61% | 34.94% | 0 | 54.94 | -47.73% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | 9.60% | 9.63% | -20.39% | 0.47 | 0.47 | 7 | 34.94% | 82.63% | 41.05% | 46.14% | 16.69% | 6 | 18.19 | -63.59% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | 23.08% | 23.15% | -25.03% | 0.84 | 0.92 | 295 | 55.79% | 99.91% | 64.73% | 83.22% | 113.47% | 141 | 1.82 | -50.11% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | 22.76% | 22.82% | -17.02% | 0.88 | 1.34 | 374 | 47.07% | 99.97% | 57.11% | 60.12% | 47.70% | 170 | 2.19 | -50.43% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | 18.72% | 18.78% | -19.97% | 0.76 | 0.94 | 380 | 47.89% | 99.82% | 56.53% | 71.42% | 15.99% | 173 | 2.24 | -54.47% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | -0.42% | -0.42% | -20.22% | 0.08 | -0.02 | 134 | 20.26% | 65.26% | 20.08% | 47.04% | 130.83% | 63 | 2.55 | -73.61% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | 28.69% | 28.78% | -15.85% | 1.08 | 1.82 | 85 | 34.64% | 97.16% | 50.35% | 17.56% | 90.54% | 0 | 9.82 | -44.49% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 12.45% | 12.49% | -20.76% | 0.58 | 0.60 | 94 | 33.68% | 96.99% | 45.93% | 47.92% | 65.50% | 6 | 7.99 | -60.74% | 否 |
| 5_固定50%现货50%USDT_ETH | -13.37% | -13.41% | -35.45% | -0.31 | -0.38 | 0 | 43.41% | — | — | — | — | — | — | 13.37% | 否 |
| 6_SMA200趋势_ETH | -15.83% | -15.87% | -30.98% | -0.29 | -0.51 | 5 | 30.73% | — | 27.33% | 29.09% | -23.87% | 0 | — | 10.91% | 否 |
| 7_原Brando_ETH|A_structure_stop | -20.40% | -20.45% | -20.40% | -2.01 | -1.00 | 77 | 6.38% | 7.20% | -4.91% | 6.76% | -34.75% | 38 | 1.09 | 6.35% | 否 |
| 7_原Brando_ETH|B_trend_failure | -17.31% | -17.35% | -24.02% | -0.96 | -0.72 | 200 | 5.84% | 64.19% | 3.59% | 12.36% | -518.78% | 0 | 1.91 | 9.43% | 否 |
| 7_原Brando_ETH|C_atr_trailing | -21.22% | -21.27% | -22.65% | -1.32 | -0.94 | 27 | 7.89% | 11.45% | 0.00% | 17.13% | -29.70% | 3 | 3.51 | 5.52% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | -33.02% | -33.09% | -34.47% | -1.24 | -0.96 | 7 | 21.55% | 99.43% | 0.00% | 31.16% | 2.02% | 6 | 11.22 | -6.28% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | -14.39% | -14.43% | -26.38% | -0.50 | -0.55 | 3 | 14.77% | 99.75% | 0.00% | 11.12% | -20.05% | 0 | 17.94 | 12.35% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | -21.34% | -21.39% | -23.05% | -1.20 | -0.93 | 4 | 7.10% | 32.46% | 0.00% | 8.00% | -9.19% | 3 | 6.47 | 5.40% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | -18.13% | -18.17% | -21.50% | -0.81 | -0.85 | 221 | 23.26% | 99.59% | 13.15% | 21.71% | -98.58% | 97 | 2.10 | 8.61% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | -7.56% | -7.58% | -20.02% | -0.29 | -0.38 | 217 | 19.83% | 99.92% | 13.15% | 11.76% | -119.04% | 94 | 2.02 | 19.18% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | -18.38% | -18.43% | -21.23% | -0.88 | -0.87 | 220 | 19.51% | 81.36% | 13.15% | 18.75% | -96.03% | 96 | 1.98 | 8.36% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | -13.79% | -13.82% | -18.00% | -1.24 | -0.77 | 55 | 7.22% | 66.48% | 0.00% | 15.73% | -155.04% | 23 | 2.65 | 12.96% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | -15.89% | -15.93% | -24.03% | -0.89 | -0.66 | 27 | 6.92% | 98.04% | 0.00% | 9.65% | -454.33% | 0 | 15.13 | 10.85% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | -16.43% | -16.47% | -20.11% | -0.73 | -0.82 | 42 | 13.71% | 90.27% | 0.00% | 13.65% | -73.75% | 4 | 6.65 | 10.32% | 否 |

- 7_原Brando_BTC|A_structure_stop：年度 2024 -18.0%，2025 0.0%；退出 {"structure_stop": 52, "sell|C_rejection": 20}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 56, "exit_structure": 52, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 329, "buy_rejected_cash": 8, "buy_rejected_regime": 0}
- 7_原Brando_BTC|B_trend_failure：年度 2024 20.4%，2025 14.5%；退出 {"trend_failure": 232, "sell|C_rejection": 91}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 285, "exit_structure": 0, "exit_trend": 232, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 108, "buy_rejected_regime": 0}
- 7_原Brando_BTC|C_atr_trailing：年度 2024 -15.1%，2025 0.0%；退出 {"sell|C_rejection": 30, "trail_stop": 6, "structure_stop": 1}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 16, "exit_structure": 1, "exit_trend": 0, "exit_trail": 6, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 358, "buy_rejected_cash": 19, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|A_structure_stop：年度 2024 24.8%，2025 16.2%；退出 {"structure_stop": 3}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 0, "exit_structure": 3, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|B_trend_failure：年度 2024 20.7%，2025 4.0%；退出 {"trend_failure": 2}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 2, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|C_atr_trailing：年度 2024 19.6%，2025 -8.4%；退出 {"trail_stop": 7}；计数 {"core_entries": 7, "core_reentries": 6, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 7, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 581, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|A_structure_stop：年度 2024 14.3%，2025 7.7%；退出 {"structure_stop": 191, "sell|C_rejection": 104}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 218, "exit_structure": 191, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 88, "buy_rejected_cash": 87, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|B_trend_failure：年度 2024 12.5%，2025 9.2%；退出 {"structure_stop": 233, "sell|C_rejection": 139, "trend_failure": 2}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 276, "exit_structure": 233, "exit_trend": 2, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 117, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|C_atr_trailing：年度 2024 11.8%，2025 6.2%；退出 {"structure_stop": 232, "sell|C_rejection": 140, "trail_stop": 8}；计数 {"core_entries": 9, "core_reentries": 8, "tactical_entries": 276, "exit_structure": 232, "exit_trend": 0, "exit_trail": 8, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 117, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_BTC|A_structure_stop：年度 2024 7.3%，2025 -7.2%；退出 {"structure_stop": 91, "reduce|C_rejection": 43}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 108, "exit_structure": 91, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 35, "buy_rejected_halt": 2, "buy_rejected_cash": 25, "buy_rejected_regime": 260}
- 10_模型C趋势过滤Brando_BTC|B_trend_failure：年度 2024 22.5%，2025 5.1%；退出 {"reduce|C_rejection": 79, "trend_failure": 6}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 43, "exit_structure": 0, "exit_trend": 6, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 47, "buy_rejected_halt": 0, "buy_rejected_cash": 90, "buy_rejected_regime": 260}
- 10_模型C趋势过滤Brando_BTC|C_atr_trailing：年度 2024 16.9%，2025 -3.8%；退出 {"reduce|C_rejection": 76, "trail_stop": 18}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 43, "exit_structure": 0, "exit_trend": 0, "exit_trail": 18, "exit_crash": 0, "exit_rejection_reduce": 52, "buy_rejected_halt": 6, "buy_rejected_cash": 84, "buy_rejected_regime": 260}
- 7_原Brando_ETH|A_structure_stop：年度 2024 -20.4%，2025 0.0%；退出 {"structure_stop": 56, "sell|C_rejection": 21}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 61, "exit_structure": 56, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 343, "buy_rejected_cash": 1, "buy_rejected_regime": 0}
- 7_原Brando_ETH|B_trend_failure：年度 2024 -17.4%，2025 0.2%；退出 {"trend_failure": 177, "sell|C_rejection": 23}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 183, "exit_structure": 0, "exit_trend": 177, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 212, "buy_rejected_cash": 12, "buy_rejected_regime": 0}
- 7_原Brando_ETH|C_atr_trailing：年度 2024 -21.2%，2025 0.0%；退出 {"sell|C_rejection": 19, "trail_stop": 8}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 18, "exit_structure": 0, "exit_trend": 0, "exit_trail": 8, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 368, "buy_rejected_cash": 19, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|A_structure_stop：年度 2024 -22.6%，2025 -13.4%；退出 {"structure_stop": 7}；计数 {"core_entries": 7, "core_reentries": 6, "tactical_entries": 0, "exit_structure": 7, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|B_trend_failure：年度 2024 -14.4%，2025 0.0%；退出 {"trend_failure": 3}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 3, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|C_atr_trailing：年度 2024 -21.3%，2025 0.0%；退出 {"trail_stop": 4}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 4, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 822, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|A_structure_stop：年度 2024 -11.6%，2025 -7.4%；退出 {"structure_stop": 142, "sell|C_rejection": 79}；计数 {"core_entries": 7, "core_reentries": 6, "tactical_entries": 162, "exit_structure": 142, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 197, "buy_rejected_cash": 46, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|B_trend_failure：年度 2024 -6.8%，2025 -0.8%；退出 {"structure_stop": 135, "sell|C_rejection": 79, "trend_failure": 3}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 162, "exit_structure": 135, "exit_trend": 3, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 197, "buy_rejected_cash": 46, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|C_atr_trailing：年度 2024 -12.6%，2025 -6.6%；退出 {"structure_stop": 135, "sell|C_rejection": 79, "trail_stop": 6}；计数 {"core_entries": 6, "core_reentries": 5, "tactical_entries": 162, "exit_structure": 135, "exit_trend": 0, "exit_trail": 6, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 422, "buy_rejected_cash": 46, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_ETH|A_structure_stop：年度 2024 -8.5%，2025 -5.7%；退出 {"structure_stop": 38, "reduce|C_rejection": 17}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 38, "exit_structure": 38, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 17, "buy_rejected_halt": 0, "buy_rejected_cash": 5, "buy_rejected_regime": 362}
- 10_模型C趋势过滤Brando_ETH|B_trend_failure：年度 2024 -16.6%，2025 0.9%；退出 {"reduce|C_rejection": 21, "trend_failure": 6}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 10, "exit_structure": 0, "exit_trend": 6, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 18, "buy_rejected_halt": 24, "buy_rejected_cash": 10, "buy_rejected_regime": 362}
- 10_模型C趋势过滤Brando_ETH|C_atr_trailing：年度 2024 -11.6%，2025 -5.5%；退出 {"reduce|C_rejection": 31, "trail_stop": 11}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 20, "exit_structure": 0, "exit_trend": 0, "exit_trail": 11, "exit_crash": 0, "exit_rejection_reduce": 23, "buy_rejected_halt": 6, "buy_rejected_cash": 23, "buy_rejected_regime": 362}

图：`valid_round1_equity_BTC.png`


### 第二轮：手续费 10 bps + 滑点 5 bps

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 73.02% | 73.28% | -30.94% | 1.36 | 2.37 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | -26.82% | -26.88% | -65.28% | -0.06 | -0.41 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | 23.10% | 23.17% | -44.44% | 0.65 | 0.52 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 36.51% | 36.62% | -19.58% | 1.28 | 1.87 | 0 | 56.55% | — | — | — | — | — | — | -36.51% | 否 |
| 6_SMA200趋势_BTC | 8.72% | 8.74% | -34.43% | 0.41 | 0.25 | 11 | 70.26% | — | 68.37% | 137.31% | 45.40% | 0 | — | -64.30% | 否 |
| 7_原Brando_BTC|A_structure_stop | -20.83% | -20.88% | -20.83% | -2.19 | -1.00 | 47 | 4.19% | 0.09% | 0.00% | 30.95% | 83.23% | 24 | 1.27 | -93.85% | 否 |
| 7_原Brando_BTC|B_trend_failure | -20.26% | -20.31% | -20.26% | -3.11 | -1.00 | 41 | 0.67% | 1.03% | 0.00% | 14.43% | 50.10% | 0 | 0.09 | -93.28% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -20.98% | -21.03% | -20.98% | -1.65 | -1.00 | 26 | 7.70% | 1.03% | 0.00% | 40.09% | 103.02% | 3 | 4.49 | -93.99% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 43.43% | 43.58% | -30.92% | 1.11 | 1.41 | 3 | 66.96% | 99.88% | 81.47% | 72.15% | 19.34% | 3 | 0.97 | -29.58% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 24.53% | 24.60% | -18.53% | 0.91 | 1.33 | 2 | 39.22% | 99.91% | 49.87% | 25.06% | 35.18% | 0 | 54.94 | -48.49% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | 7.32% | 7.34% | -21.46% | 0.40 | 0.34 | 7 | 34.94% | 82.63% | 40.17% | 49.23% | 17.37% | 6 | 18.19 | -65.69% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | 3.43% | 3.44% | -29.14% | 0.26 | 0.12 | 258 | 54.74% | 99.91% | 55.98% | 103.56% | 38.26% | 120 | 1.86 | -69.58% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | -8.01% | -8.03% | -20.36% | -0.19 | -0.39 | 263 | 33.21% | 76.51% | 37.44% | 86.59% | 64.50% | 122 | 2.26 | -81.03% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | -4.84% | -4.85% | -20.10% | -0.08 | -0.24 | 215 | 27.45% | 68.66% | 34.49% | 70.76% | 38.28% | 99 | 2.32 | -77.86% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | -5.26% | -5.28% | -20.65% | -0.17 | -0.26 | 100 | 14.20% | 49.90% | 16.24% | 51.41% | 18.83% | 47 | 2.89 | -78.28% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | 23.60% | 23.67% | -17.05% | 0.92 | 1.39 | 85 | 34.65% | 97.16% | 47.04% | 20.94% | 95.94% | 0 | 9.82 | -49.42% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 5.50% | 5.51% | -21.16% | 0.33 | 0.26 | 93 | 32.98% | 96.99% | 40.64% | 52.48% | 20.27% | 5 | 8.06 | -67.52% | 否 |
| 5_固定50%现货50%USDT_ETH | -13.41% | -13.44% | -35.44% | -0.31 | -0.38 | 0 | 43.39% | — | — | — | — | — | — | 13.41% | 否 |
| 6_SMA200趋势_ETH | -17.08% | -17.12% | -31.18% | -0.32 | -0.55 | 5 | 30.73% | — | 27.13% | 29.73% | -23.44% | 0 | — | 9.74% | 否 |
| 7_原Brando_ETH|A_structure_stop | -20.18% | -20.23% | -20.18% | -2.57 | -1.00 | 49 | 3.69% | 7.20% | 0.00% | 5.34% | -22.74% | 22 | 1.00 | 6.63% | 否 |
| 7_原Brando_ETH|B_trend_failure | -21.79% | -21.84% | -21.79% | -1.93 | -1.00 | 53 | 2.22% | 13.16% | 0.00% | 8.25% | -25.43% | 0 | 0.42 | 5.03% | 否 |
| 7_原Brando_ETH|C_atr_trailing | -23.16% | -23.22% | -23.32% | -1.45 | -1.00 | 27 | 7.89% | 11.45% | 0.00% | 17.41% | -28.06% | 3 | 3.51 | 3.65% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | -34.41% | -34.49% | -35.74% | -1.29 | -0.97 | 7 | 21.55% | 99.43% | 0.00% | 31.61% | 2.68% | 6 | 11.22 | -7.59% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | -15.16% | -15.20% | -26.38% | -0.53 | -0.58 | 3 | 14.77% | 99.75% | 0.00% | 11.43% | -19.80% | 0 | 17.94 | 11.66% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | -22.28% | -22.33% | -23.85% | -1.24 | -0.94 | 4 | 7.10% | 32.46% | 0.00% | 8.31% | -8.83% | 3 | 6.47 | 4.54% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | -19.57% | -19.62% | -20.40% | -1.80 | -0.96 | 66 | 5.76% | 13.25% | 0.00% | 7.66% | -9.72% | 28 | 1.43 | 7.24% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | -19.24% | -19.29% | -20.07% | -1.91 | -0.96 | 116 | 6.59% | 13.57% | -3.26% | 7.56% | -9.76% | 54 | 1.37 | 7.57% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | -18.79% | -18.84% | -20.12% | -1.80 | -0.94 | 65 | 4.63% | 13.49% | 0.00% | 3.79% | -148.49% | 27 | 2.50 | 8.03% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | -18.33% | -18.37% | -21.62% | -1.66 | -0.85 | 54 | 7.08% | 66.48% | 0.00% | 19.64% | -150.85% | 22 | 2.67 | 8.49% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | -16.83% | -16.87% | -24.13% | -0.95 | -0.70 | 27 | 6.93% | 98.04% | 0.00% | 10.02% | -452.88% | 0 | 15.13 | 9.99% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | -14.46% | -14.49% | -20.34% | -0.72 | -0.71 | 40 | 11.25% | 90.27% | 0.00% | 10.36% | -188.09% | 4 | 6.84 | 12.36% | 否 |


## 训练窗 1h：2022-04 → 2024-06

### 第一轮：零成本（理想研究环境）

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 34.09% | 13.93% | -66.69% | 0.50 | 0.21 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | 3.18% | 1.40% | -74.49% | 0.32 | 0.02 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | 18.64% | 7.89% | -68.36% | 0.39 | 0.12 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 17.04% | 7.25% | -33.87% | 0.41 | 0.21 | 0 | 40.77% | — | — | — | — | — | — | -17.04% | 否 |
| 6_SMA200趋势_BTC | 181.51% | 58.43% | -22.68% | 1.41 | 2.58 | 3 | 57.70% | — | 74.69% | 34.20% | 8.50% | 0 | — | 147.42% | 否 |
| 7_原Brando_BTC|A_structure_stop | -17.72% | -8.31% | -20.83% | -1.46 | -0.40 | 59 | 1.99% | 0.00% | 0.00% | 7.46% | 1089.69% | 28 | 1.68 | -51.81% | 否 |
| 7_原Brando_BTC|B_trend_failure | -15.55% | -7.24% | -22.45% | -0.35 | -0.32 | 466 | 16.18% | 42.02% | 5.24% | 21.44% | 192.03% | 0 | 1.05 | -49.64% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -25.83% | -12.44% | -28.58% | -1.19 | -0.44 | 32 | 3.88% | 0.00% | 0.00% | 12.21% | 440.01% | 1 | 3.41 | -59.92% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 182.01% | 58.55% | -22.68% | 1.41 | 2.58 | 0 | 61.47% | 99.99% | 75.49% | 34.92% | — | 0 | — | 147.92% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 3.71% | 1.63% | -27.91% | 0.18 | 0.06 | 2 | 18.36% | 42.24% | 14.50% | 18.83% | 208.58% | 0 | 75.46 | -30.38% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | -10.31% | -4.72% | -22.63% | -0.41 | -0.21 | 3 | 3.33% | 7.81% | -4.12% | -0.00% | 290.23% | 2 | 9.11 | -44.40% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | 119.88% | 41.95% | -23.96% | 1.22 | 1.75 | 744 | 59.63% | 99.99% | 67.70% | 39.17% | -2.02% | 315 | 2.11 | 85.79% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | 8.76% | 3.80% | -20.06% | 0.26 | 0.19 | 503 | 26.92% | 42.26% | 21.84% | 23.71% | 92.49% | 212 | 2.43 | -25.33% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | 8.35% | 3.63% | -20.55% | 0.25 | 0.18 | 514 | 27.86% | 42.17% | 20.80% | 22.32% | 322.65% | 220 | 2.38 | -25.74% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | -0.36% | -0.16% | -20.22% | 0.06 | -0.01 | 128 | 10.13% | 28.79% | 9.05% | 12.48% | 382.92% | 51 | 2.86 | -34.45% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | -0.76% | -0.34% | -27.36% | 0.07 | -0.01 | 78 | 16.86% | 41.73% | 11.80% | 18.46% | 649.53% | 0 | 11.82 | -34.85% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 17.73% | 7.53% | -21.65% | 0.48 | 0.35 | 107 | 18.95% | 51.12% | 14.96% | 10.61% | 1309.43% | 6 | 9.92 | -16.36% | 否 |
| 5_固定50%现货50%USDT_ETH | 1.59% | 0.70% | -38.68% | 0.11 | 0.02 | 0 | 37.42% | — | — | — | — | — | — | -1.59% | 否 |
| 6_SMA200趋势_ETH | 115.89% | 40.80% | -29.61% | 1.03 | 1.38 | 4 | 56.72% | — | 56.77% | 25.12% | -51.40% | 0 | — | 112.70% | 否 |
| 7_原Brando_ETH|A_structure_stop | -21.11% | -10.00% | -21.11% | -1.31 | -0.47 | 59 | 1.90% | 0.00% | 0.00% | 7.60% | -9.75% | 31 | 0.84 | -24.29% | 否 |
| 7_原Brando_ETH|B_trend_failure | -9.05% | -4.13% | -21.07% | -0.11 | -0.20 | 498 | 15.50% | 44.76% | 6.95% | 14.50% | 12.99% | 0 | 1.01 | -12.24% | 否 |
| 7_原Brando_ETH|C_atr_trailing | -22.48% | -10.70% | -24.00% | -1.16 | -0.45 | 32 | 3.28% | 0.00% | 0.00% | 5.60% | -36.53% | 0 | 3.54 | -25.66% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | 115.70% | 40.74% | -29.62% | 1.03 | 1.38 | 2 | 60.97% | 99.96% | 56.11% | 26.31% | 0.85% | 2 | 11.65 | 112.52% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 9.45% | 4.10% | -21.95% | 0.30 | 0.19 | 2 | 18.48% | 45.55% | 8.42% | 6.13% | 32.33% | 0 | 75.96 | 6.27% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | 3.78% | 1.66% | -22.48% | 0.18 | 0.07 | 8 | 19.05% | 45.37% | 4.37% | 3.65% | 36.42% | 7 | 19.57 | 0.59% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | 66.00% | 25.27% | -23.07% | 0.84 | 1.10 | 742 | 54.72% | 99.96% | 51.47% | 35.12% | -31.63% | 323 | 1.83 | 62.81% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | 68.90% | 26.24% | -20.20% | 0.94 | 1.30 | 756 | 41.85% | 91.44% | 44.54% | 25.06% | -90.30% | 315 | 2.35 | 65.72% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | -9.26% | -4.23% | -20.08% | -0.10 | -0.21 | 572 | 25.06% | 45.47% | 13.98% | 22.16% | -4.27% | 246 | 1.76 | -12.44% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | 37.37% | 15.16% | -20.07% | 0.71 | 0.76 | 283 | 24.02% | 77.02% | 21.76% | 8.70% | 63.34% | 107 | 3.87 | 34.19% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 4.70% | 2.06% | -21.89% | 0.20 | 0.09 | 101 | 16.37% | 44.40% | 6.58% | 6.21% | 142.97% | 0 | 10.02 | 1.51% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 41.18% | 16.57% | -20.25% | 0.73 | 0.82 | 164 | 27.40% | 74.21% | 20.15% | 3.85% | -117.41% | 12 | 9.02 | 37.99% | 否 |

- 7_原Brando_BTC|A_structure_stop：年度 2022 -17.7%，2023 0.0%，2024 0.0%；退出 {"structure_stop": 38, "sell|C_rejection": 21}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 41, "exit_structure": 38, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 876, "buy_rejected_cash": 10, "buy_rejected_regime": 0}
- 7_原Brando_BTC|B_trend_failure：年度 2022 -10.6%，2023 -5.5%，2024 0.0%；退出 {"trend_failure": 390, "sell|C_rejection": 76}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 424, "exit_structure": 0, "exit_trend": 390, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 361, "buy_rejected_cash": 144, "buy_rejected_regime": 0}
- 7_原Brando_BTC|C_atr_trailing：年度 2022 -25.8%，2023 0.0%，2024 0.0%；退出 {"sell|C_rejection": 28, "trail_stop": 4}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 17, "exit_structure": 0, "exit_trend": 0, "exit_trail": 4, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 889, "buy_rejected_cash": 21, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|B_trend_failure：年度 2022 0.0%，2023 3.7%，2024 0.0%；退出 {"trend_failure": 2}；计数 {"core_entries": 2, "core_reentries": 1, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 2, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 4850, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|C_atr_trailing：年度 2022 0.0%，2023 -10.3%，2024 0.0%；退出 {"trail_stop": 3}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 3, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 7742, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|A_structure_stop：年度 2022 -11.8%，2023 94.7%，2024 28.0%；退出 {"structure_stop": 439, "sell|C_rejection": 305}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 531, "exit_structure": 439, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 65, "buy_rejected_cash": 331, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|B_trend_failure：年度 2022 -11.8%，2023 23.3%，2024 0.0%；退出 {"structure_stop": 302, "sell|C_rejection": 199, "trend_failure": 2}；计数 {"core_entries": 2, "core_reentries": 1, "tactical_entries": 361, "exit_structure": 302, "exit_trend": 2, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 5202, "buy_rejected_cash": 214, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|C_atr_trailing：年度 2022 -11.8%，2023 22.8%，2024 0.0%；退出 {"structure_stop": 305, "sell|C_rejection": 199, "trail_stop": 10}；计数 {"core_entries": 10, "core_reentries": 9, "tactical_entries": 364, "exit_structure": 305, "exit_trend": 0, "exit_trail": 10, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 5200, "buy_rejected_cash": 213, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_BTC|A_structure_stop：年度 2022 0.0%，2023 -0.4%，2024 0.0%；退出 {"structure_stop": 82, "reduce|C_rejection": 46}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 97, "exit_structure": 82, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 34, "buy_rejected_halt": 198, "buy_rejected_cash": 55, "buy_rejected_regime": 577}
- 10_模型C趋势过滤Brando_BTC|B_trend_failure：年度 2022 0.0%，2023 -0.8%，2024 0.0%；退出 {"reduce|C_rejection": 72, "trend_failure": 6}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 36, "exit_structure": 0, "exit_trend": 6, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 42, "buy_rejected_halt": 191, "buy_rejected_cash": 124, "buy_rejected_regime": 577}
- 10_模型C趋势过滤Brando_BTC|C_atr_trailing：年度 2022 0.0%，2023 17.7%，2024 0.0%；退出 {"reduce|C_rejection": 83, "trail_stop": 24}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 49, "exit_structure": 0, "exit_trend": 0, "exit_trail": 24, "exit_crash": 0, "exit_rejection_reduce": 58, "buy_rejected_halt": 173, "buy_rejected_cash": 128, "buy_rejected_regime": 577}
- 7_原Brando_ETH|A_structure_stop：年度 2022 -21.1%，2023 0.0%，2024 0.0%；退出 {"structure_stop": 41, "sell|C_rejection": 18}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 45, "exit_structure": 41, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 844, "buy_rejected_cash": 8, "buy_rejected_regime": 0}
- 7_原Brando_ETH|B_trend_failure：年度 2022 -11.5%，2023 2.8%，2024 0.0%；退出 {"trend_failure": 385, "sell|C_rejection": 111, "crash_exit": 2}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 441, "exit_structure": 0, "exit_trend": 385, "exit_trail": 0, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 355, "buy_rejected_cash": 101, "buy_rejected_regime": 0}
- 7_原Brando_ETH|C_atr_trailing：年度 2022 -22.5%，2023 0.0%，2024 0.0%；退出 {"sell|C_rejection": 30, "trail_stop": 2}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 14, "exit_structure": 0, "exit_trend": 0, "exit_trail": 2, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 866, "buy_rejected_cash": 17, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|A_structure_stop：年度 2022 0.0%，2023 45.4%，2024 48.4%；退出 {"structure_stop": 2}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 0, "exit_structure": 2, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|B_trend_failure：年度 2022 0.0%，2023 9.5%，2024 0.0%；退出 {"trend_failure": 2}；计数 {"core_entries": 2, "core_reentries": 1, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 2, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 4298, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|C_atr_trailing：年度 2022 0.0%，2023 3.8%，2024 0.0%；退出 {"trail_stop": 8}；计数 {"core_entries": 8, "core_reentries": 7, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 8, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 4306, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|A_structure_stop：年度 2022 -16.1%，2023 37.9%，2024 43.4%；退出 {"structure_stop": 457, "sell|C_rejection": 283, "crash_exit": 2}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 546, "exit_structure": 457, "exit_trend": 0, "exit_trail": 0, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 110, "buy_rejected_cash": 241, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|B_trend_failure：年度 2022 -16.1%，2023 38.8%，2024 45.0%；退出 {"structure_stop": 450, "sell|C_rejection": 301, "trend_failure": 3, "crash_exit": 2}；计数 {"core_entries": 3, "core_reentries": 2, "tactical_entries": 549, "exit_structure": 450, "exit_trend": 3, "exit_trail": 0, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 771, "buy_rejected_cash": 251, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|C_atr_trailing：年度 2022 -16.1%，2023 8.1%，2024 0.0%；退出 {"structure_stop": 348, "sell|C_rejection": 212, "trail_stop": 10, "crash_exit": 2}；计数 {"core_entries": 10, "core_reentries": 9, "tactical_entries": 416, "exit_structure": 348, "exit_trend": 0, "exit_trail": 10, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 4639, "buy_rejected_cash": 140, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_ETH|A_structure_stop：年度 2022 0.0%，2023 -3.1%，2024 41.8%；退出 {"structure_stop": 172, "reduce|C_rejection": 111}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 192, "exit_structure": 172, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 91, "buy_rejected_halt": 15, "buy_rejected_cash": 115, "buy_rejected_regime": 575}
- 10_模型C趋势过滤Brando_ETH|B_trend_failure：年度 2022 0.0%，2023 4.7%，2024 0.0%；退出 {"reduce|C_rejection": 94, "trend_failure": 7}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 46, "exit_structure": 0, "exit_trend": 7, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 55, "buy_rejected_halt": 188, "buy_rejected_cash": 88, "buy_rejected_regime": 575}
- 10_模型C趋势过滤Brando_ETH|C_atr_trailing：年度 2022 0.0%，2023 9.6%，2024 28.8%；退出 {"reduce|C_rejection": 126, "trail_stop": 38}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 81, "exit_structure": 0, "exit_trend": 0, "exit_trail": 38, "exit_crash": 0, "exit_rejection_reduce": 83, "buy_rejected_halt": 63, "buy_rejected_cash": 178, "buy_rejected_regime": 575}

图：`train_round1_equity_BTC.png`


### 第二轮：手续费 10 bps + 滑点 5 bps

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 33.96% | 13.88% | -66.69% | 0.50 | 0.21 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | 3.08% | 1.36% | -74.49% | 0.32 | 0.02 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | 18.52% | 7.85% | -68.36% | 0.39 | 0.11 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 16.98% | 7.22% | -33.86% | 0.41 | 0.21 | 0 | 40.75% | — | — | — | — | — | — | -16.98% | 否 |
| 6_SMA200趋势_BTC | 179.41% | 57.90% | -22.68% | 1.40 | 2.55 | 3 | 57.70% | — | 74.53% | 34.50% | 8.71% | 0 | — | 145.45% | 否 |
| 7_原Brando_BTC|A_structure_stop | -18.41% | -8.65% | -20.77% | -1.73 | -0.42 | 42 | 1.63% | 0.00% | 0.00% | 5.15% | 307.62% | 22 | 0.99 | -52.36% | 否 |
| 7_原Brando_BTC|B_trend_failure | -19.94% | -9.42% | -20.03% | -3.14 | -0.47 | 98 | 0.00% | 0.00% | -1.82% | 9.13% | 219.20% | 0 | 0.00 | -53.90% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -25.35% | -12.19% | -28.01% | -1.26 | -0.44 | 31 | 3.82% | 0.00% | 0.00% | 10.69% | 279.92% | 1 | 3.43 | -59.31% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 181.58% | 58.45% | -22.68% | 1.41 | 2.58 | 0 | 61.47% | 99.99% | 75.41% | 34.92% | — | 0 | — | 147.63% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 3.09% | 1.36% | -28.13% | 0.17 | 0.05 | 2 | 18.36% | 42.24% | 14.43% | 19.14% | 208.84% | 0 | 75.46 | -30.86% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | -11.11% | -5.10% | -23.21% | -0.45 | -0.22 | 3 | 3.33% | 7.81% | -4.50% | -0.00% | 290.63% | 2 | 9.11 | -45.07% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | -19.05% | -8.97% | -20.24% | -1.28 | -0.44 | 159 | 3.94% | 0.00% | 0.93% | 11.97% | 235.78% | 79 | 1.40 | -53.01% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | -19.05% | -8.97% | -20.24% | -1.28 | -0.44 | 159 | 3.94% | 0.00% | 0.93% | 11.97% | 235.78% | 79 | 1.40 | -53.01% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | -19.05% | -8.97% | -20.24% | -1.28 | -0.44 | 159 | 3.94% | 0.00% | 0.93% | 11.97% | 235.78% | 79 | 1.40 | -53.01% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | -5.85% | -2.64% | -20.34% | -0.13 | -0.13 | 105 | 8.18% | 23.32% | 5.34% | 10.71% | 219.53% | 43 | 3.01 | -39.80% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | 4.81% | 2.11% | -22.18% | 0.20 | 0.10 | 75 | 15.07% | 41.73% | 10.95% | 13.39% | 1068.84% | 0 | 12.02 | -29.14% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 11.31% | 4.88% | -20.66% | 0.35 | 0.24 | 87 | 16.71% | 40.91% | 12.65% | 11.04% | 575.02% | 4 | 9.37 | -22.64% | 否 |
| 5_固定50%现货50%USDT_ETH | 1.54% | 0.68% | -38.66% | 0.10 | 0.02 | 0 | 37.39% | — | — | — | — | — | — | -1.54% | 否 |
| 6_SMA200趋势_ETH | 113.63% | 40.14% | -29.61% | 1.01 | 1.36 | 4 | 56.72% | — | 56.52% | 25.21% | -51.15% | 0 | — | 110.55% | 否 |
| 7_原Brando_ETH|A_structure_stop | -20.21% | -9.55% | -20.21% | -1.88 | -0.47 | 39 | 1.07% | 0.00% | 0.00% | 4.28% | -35.04% | 23 | 0.69 | -23.29% | 否 |
| 7_原Brando_ETH|B_trend_failure | -20.26% | -9.58% | -20.26% | -2.99 | -0.47 | 73 | 0.00% | 0.00% | 0.00% | 9.10% | 29.21% | 0 | 0.00 | -23.34% | 否 |
| 7_原Brando_ETH|C_atr_trailing | -24.37% | -11.68% | -25.64% | -1.26 | -0.46 | 32 | 3.28% | 0.00% | 0.00% | 5.88% | -34.72% | 0 | 3.54 | -27.46% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | 114.09% | 40.27% | -29.62% | 1.02 | 1.36 | 2 | 60.97% | 99.96% | 55.77% | 26.31% | 1.06% | 2 | 11.65 | 111.01% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 8.80% | 3.82% | -22.18% | 0.29 | 0.17 | 2 | 18.48% | 45.55% | 8.30% | 6.30% | 32.50% | 0 | 75.96 | 5.72% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | -11.66% | -5.36% | -20.16% | -0.55 | -0.27 | 2 | 2.83% | 7.07% | -4.62% | -0.00% | 61.46% | 1 | 11.62 | -14.74% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | -19.41% | -9.15% | -20.49% | -1.82 | -0.45 | 83 | 1.46% | 0.00% | 0.00% | 8.23% | 431.89% | 40 | 1.05 | -22.49% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | -19.41% | -9.15% | -20.49% | -1.82 | -0.45 | 83 | 1.46% | 0.00% | 0.00% | 8.23% | 431.89% | 40 | 1.05 | -22.49% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | -19.41% | -9.15% | -20.49% | -1.82 | -0.45 | 83 | 1.46% | 0.00% | 0.00% | 8.23% | 431.89% | 40 | 1.05 | -22.49% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | -13.23% | -6.11% | -20.39% | -0.45 | -0.30 | 93 | 6.23% | 21.91% | -5.63% | -0.00% | 131.57% | 35 | 3.26 | -16.31% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 0.19% | 0.08% | -24.00% | 0.10 | 0.00 | 101 | 16.38% | 44.40% | 5.22% | 6.90% | 149.05% | 0 | 10.09 | -2.90% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 30.09% | 12.41% | -20.72% | 0.59 | 0.60 | 163 | 27.40% | 74.21% | 17.30% | 4.73% | -106.55% | 12 | 9.03 | 27.01% | 否 |


## 真正未使用 1h：2022-01 → 2022-03（极短）

### 第一轮：零成本（理想研究环境）

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 2.03% | 8.58% | -30.71% | 0.26 | 0.28 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | -7.53% | -27.45% | -43.65% | -0.14 | -0.63 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | -2.75% | -10.80% | -36.86% | 0.03 | -0.29 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 1.01% | 4.22% | -15.61% | 0.07 | 0.27 | 0 | 47.05% | — | — | — | — | — | — | -1.01% | 否 |
| 6_SMA200趋势_BTC | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -2.03% | 否 |
| 7_原Brando_BTC|A_structure_stop | -11.47% | -39.33% | -19.74% | -1.56 | -1.99 | 94 | 47.16% | — | -32.43% | — | 23.17% | 37 | 1.75 | -13.50% | 否 |
| 7_原Brando_BTC|B_trend_failure | -6.76% | -24.95% | -7.16% | -3.57 | -3.49 | 91 | 0.00% | — | -27.10% | — | 100.29% | 0 | 0.00 | -8.79% | 是 |
| 7_原Brando_BTC|C_atr_trailing | -20.77% | -61.52% | -21.64% | -4.86 | -2.84 | 16 | 14.36% | — | 0.00% | — | 397.89% | 1 | 3.25 | -22.80% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -2.03% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -2.03% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -2.03% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | -5.75% | -21.55% | -10.43% | -1.57 | -2.07 | 94 | 23.78% | — | -15.83% | — | 23.17% | 37 | 1.75 | -7.77% | 是 |
| 9_模型B核心+战术_BTC|B_trend_failure | -5.75% | -21.55% | -10.43% | -1.57 | -2.07 | 94 | 23.78% | — | -15.83% | — | 23.17% | 37 | 1.75 | -7.77% | 是 |
| 9_模型B核心+战术_BTC|C_atr_trailing | -5.75% | -21.55% | -10.43% | -1.57 | -2.07 | 94 | 23.78% | — | -15.83% | — | 23.17% | 37 | 1.75 | -7.77% | 是 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -2.03% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -2.03% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -2.03% | 否 |
| 5_固定50%现货50%USDT_ETH | -3.76% | -14.56% | -22.40% | -0.43 | -0.65 | 0 | 44.20% | — | — | — | — | — | — | 3.76% | 否 |
| 6_SMA200趋势_ETH | -14.07% | -46.31% | -19.17% | -3.25 | -2.42 | 1 | 7.81% | — | 0.00% | — | -30.00% | 0 | — | -6.54% | 否 |
| 7_原Brando_ETH|A_structure_stop | -7.18% | -26.34% | -20.86% | -0.66 | -1.26 | 64 | 30.26% | — | -14.11% | — | -158.24% | 28 | 2.48 | 0.34% | 否 |
| 7_原Brando_ETH|B_trend_failure | 3.44% | 14.89% | -3.61% | 1.95 | 4.12 | 87 | 0.00% | — | -3.04% | — | -55.82% | 0 | 0.00 | 10.97% | 是 |
| 7_原Brando_ETH|C_atr_trailing | -21.99% | -63.89% | -24.35% | -3.52 | -2.62 | 13 | 16.47% | — | 0.00% | — | -113.32% | 1 | 4.38 | -14.46% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.53% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.53% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.53% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | 6.84% | 31.19% | -11.71% | 1.38 | 2.66 | 73 | 23.97% | — | 42.35% | — | 1.28% | 33 | 2.09 | 14.37% | 是 |
| 9_模型B核心+战术_ETH|B_trend_failure | 6.84% | 31.19% | -11.71% | 1.38 | 2.66 | 73 | 23.97% | — | 42.35% | — | 1.28% | 33 | 2.09 | 14.37% | 是 |
| 9_模型B核心+战术_ETH|C_atr_trailing | 6.84% | 31.19% | -11.71% | 1.38 | 2.66 | 73 | 23.97% | — | 42.35% | — | 1.28% | 33 | 2.09 | 14.37% | 是 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.53% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.53% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.53% | 否 |

- 7_原Brando_BTC|A_structure_stop：年度 2022 -11.5%；退出 {"structure_stop": 53, "sell|C_rejection": 41}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 68, "exit_structure": 53, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 23, "buy_rejected_regime": 0}
- 7_原Brando_BTC|B_trend_failure：年度 2022 -6.8%；退出 {"trend_failure": 91}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 91, "exit_structure": 0, "exit_trend": 91, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 7_原Brando_BTC|C_atr_trailing：年度 2022 -20.8%；退出 {"sell|C_rejection": 9, "trail_stop": 7}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 10, "exit_structure": 0, "exit_trend": 0, "exit_trail": 7, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 75, "buy_rejected_cash": 6, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|A_structure_stop：年度 2022 -5.7%；退出 {"structure_stop": 53, "sell|C_rejection": 41}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 68, "exit_structure": 53, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 23, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|B_trend_failure：年度 2022 -5.7%；退出 {"structure_stop": 53, "sell|C_rejection": 41}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 68, "exit_structure": 53, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 23, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|C_atr_trailing：年度 2022 -5.7%；退出 {"structure_stop": 53, "sell|C_rejection": 41}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 68, "exit_structure": 53, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 23, "buy_rejected_regime": 0}
- 7_原Brando_ETH|A_structure_stop：年度 2022 -7.2%；退出 {"structure_stop": 46, "sell|C_rejection": 18}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 51, "exit_structure": 46, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 23, "buy_rejected_cash": 13, "buy_rejected_regime": 0}
- 7_原Brando_ETH|B_trend_failure：年度 2022 3.4%；退出 {"trend_failure": 87}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 87, "exit_structure": 0, "exit_trend": 87, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 7_原Brando_ETH|C_atr_trailing：年度 2022 -22.0%；退出 {"trail_stop": 7, "sell|C_rejection": 6}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 9, "exit_structure": 0, "exit_trend": 0, "exit_trail": 7, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 69, "buy_rejected_cash": 9, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|A_structure_stop：年度 2022 6.8%；退出 {"structure_stop": 51, "sell|C_rejection": 22}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 62, "exit_structure": 51, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 25, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|B_trend_failure：年度 2022 6.8%；退出 {"structure_stop": 51, "sell|C_rejection": 22}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 62, "exit_structure": 51, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 25, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|C_atr_trailing：年度 2022 6.8%；退出 {"structure_stop": 51, "sell|C_rejection": 22}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 62, "exit_structure": 51, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 0, "buy_rejected_cash": 25, "buy_rejected_regime": 0}

图：`locked_unused_1h_round1_equity_BTC.png`


### 第二轮：手续费 10 bps + 滑点 5 bps

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 1.93% | 8.14% | -30.71% | 0.26 | 0.26 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | -7.62% | -27.75% | -43.65% | -0.14 | -0.64 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | -2.85% | -11.17% | -36.86% | 0.03 | -0.30 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 0.96% | 4.01% | -15.61% | 0.07 | 0.26 | 0 | 47.03% | — | — | — | — | — | — | -0.96% | 否 |
| 6_SMA200趋势_BTC | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -1.93% | 否 |
| 7_原Brando_BTC|A_structure_stop | -18.37% | -56.50% | -20.23% | -2.84 | -2.79 | 60 | 33.01% | — | -55.42% | — | 200.23% | 27 | 1.97 | -20.29% | 否 |
| 7_原Brando_BTC|B_trend_failure | -20.19% | -60.35% | -20.19% | -10.53 | -2.99 | 79 | 0.00% | — | -80.30% | — | 74.78% | 0 | 0.00 | -22.12% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -21.73% | -63.38% | -22.48% | -5.03 | -2.82 | 16 | 14.36% | — | 0.00% | — | 399.19% | 1 | 3.25 | -23.65% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -1.93% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -1.93% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -1.93% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | -10.87% | -37.62% | -12.57% | -3.07 | -2.99 | 94 | 23.80% | — | -35.91% | — | 31.30% | 37 | 1.75 | -12.79% | 是 |
| 9_模型B核心+战术_BTC|B_trend_failure | -10.87% | -37.62% | -12.57% | -3.07 | -2.99 | 94 | 23.80% | — | -35.91% | — | 31.30% | 37 | 1.75 | -12.79% | 是 |
| 9_模型B核心+战术_BTC|C_atr_trailing | -10.87% | -37.62% | -12.57% | -3.07 | -2.99 | 94 | 23.80% | — | -35.91% | — | 31.30% | 37 | 1.75 | -12.79% | 是 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -1.93% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -1.93% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | -1.93% | 否 |
| 5_固定50%现货50%USDT_ETH | -3.81% | -14.72% | -22.39% | -0.43 | -0.66 | 0 | 44.18% | — | — | — | — | — | — | 3.81% | 否 |
| 6_SMA200趋势_ETH | -14.33% | -46.96% | -19.17% | -3.28 | -2.45 | 1 | 7.81% | — | 0.00% | — | -29.96% | 0 | — | -6.71% | 否 |
| 7_原Brando_ETH|A_structure_stop | -11.26% | -38.73% | -20.29% | -1.19 | -1.91 | 52 | 26.29% | — | -21.73% | — | -19.61% | 27 | 1.56 | -3.64% | 否 |
| 7_原Brando_ETH|B_trend_failure | -11.81% | -40.26% | -12.83% | -7.05 | -3.14 | 87 | 0.00% | — | -56.78% | — | -47.21% | 0 | 0.00 | -4.19% | 是 |
| 7_原Brando_ETH|C_atr_trailing | -22.92% | -65.61% | -25.02% | -3.68 | -2.62 | 13 | 16.47% | — | 0.00% | — | -112.54% | 1 | 4.38 | -15.30% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.62% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.62% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.62% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | 2.46% | 10.50% | -13.48% | 0.57 | 0.78 | 73 | 23.98% | — | 28.93% | — | 7.79% | 33 | 2.09 | 10.08% | 是 |
| 9_模型B核心+战术_ETH|B_trend_failure | 2.46% | 10.50% | -13.48% | 0.57 | 0.78 | 73 | 23.98% | — | 28.93% | — | 7.79% | 33 | 2.09 | 10.08% | 是 |
| 9_模型B核心+战术_ETH|C_atr_trailing | 2.46% | 10.50% | -13.48% | 0.57 | 0.78 | 73 | 23.98% | — | 28.93% | — | 7.79% | 33 | 2.09 | 10.08% | 是 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.62% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.62% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 0.00% | 0.00% | 0.00% | — | — | 0 | 0.00% | — | 0.00% | — | — | 0 | — | 7.62% | 否 |


## 长历史 4h：2018-06 → 2026-11（跨牛熊）

### 第一轮：零成本（理想研究环境）

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 910.23% | 31.53% | -77.04% | 0.76 | 0.41 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | 285.74% | 17.35% | -86.81% | 0.61 | 0.20 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | 597.98% | 25.89% | -77.27% | 0.68 | 0.34 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 455.11% | 22.52% | -69.45% | 0.68 | 0.32 | 0 | 75.11% | — | — | — | — | — | — | -455.11% | 否 |
| 6_SMA200趋势_BTC | 1053.14% | 33.61% | -64.16% | 0.89 | 0.52 | 31 | 54.00% | — | 64.71% | 49.58% | 74.27% | 0 | — | 142.92% | 否 |
| 7_原Brando_BTC|A_structure_stop | -19.89% | -2.59% | -20.49% | -0.49 | -0.13 | 67 | 2.06% | 0.00% | -0.24% | 2.57% | 2159.34% | 36 | 5.08 | -930.12% | 否 |
| 7_原Brando_BTC|B_trend_failure | 103.52% | 8.79% | -29.31% | 0.62 | 0.30 | 102 | 2.64% | 10.30% | 9.75% | 0.85% | 3754.56% | 0 | 6.25 | -806.71% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -22.02% | -2.91% | -22.68% | -0.55 | -0.13 | 7 | 0.39% | 0.00% | 0.02% | 0.01% | 5382.46% | 1 | 15.52 | -932.25% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 13.97% | 1.56% | -56.74% | 0.19 | 0.03 | 1 | 10.36% | 12.02% | 13.61% | 16.22% | 1152.68% | 0 | 319.83 | -896.26% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 95.77% | 8.29% | -34.40% | 0.54 | 0.24 | 1 | 3.91% | 10.52% | 9.34% | 0.80% | 629.02% | 0 | 120.83 | -814.46% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | 89.18% | 7.85% | -25.92% | 0.67 | 0.30 | 4 | 1.94% | 5.28% | 8.20% | -0.00% | 595.44% | 3 | 15.04 | -821.05% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | 65.69% | 6.17% | -38.54% | 0.43 | 0.16 | 99 | 7.68% | 12.04% | 12.32% | 8.34% | 6443.74% | 43 | 16.65 | -844.54% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | 103.81% | 8.80% | -30.19% | 0.60 | 0.29 | 99 | 5.09% | 10.54% | 10.75% | 1.97% | 5920.08% | 43 | 14.64 | -806.42% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | 104.12% | 8.82% | -27.59% | 0.68 | 0.32 | 102 | 4.25% | 10.54% | 10.45% | 1.60% | 5876.37% | 46 | 13.62 | -806.11% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | 72.22% | 6.65% | -31.40% | 0.49 | 0.21 | 17 | 2.97% | 10.35% | 7.68% | 1.27% | 5243.42% | 2 | 57.50 | -838.00% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | 78.30% | 7.09% | -31.40% | 0.51 | 0.23 | 12 | 2.89% | 9.92% | 7.75% | 0.70% | 3769.70% | 0 | 66.15 | -831.92% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 54.66% | 5.30% | -25.69% | 0.57 | 0.21 | 11 | 1.15% | 4.48% | 7.61% | 2.94% | 2628.21% | 2 | 12.42 | -855.56% | 否 |
| 5_固定50%现货50%USDT_ETH | 142.87% | 11.09% | -72.48% | 0.46 | 0.15 | 0 | 64.12% | — | — | — | — | — | — | -142.87% | 否 |
| 6_SMA200趋势_ETH | 1619.59% | 40.09% | -74.86% | 0.88 | 0.54 | 24 | 51.44% | — | 63.76% | 42.24% | 31.09% | 0 | — | 1333.85% | 否 |
| 7_原Brando_ETH|A_structure_stop | -19.42% | -2.53% | -21.39% | -0.71 | -0.12 | 34 | 0.60% | 0.00% | 0.00% | 2.19% | 3584.41% | 16 | 3.15 | -305.16% | 否 |
| 7_原Brando_ETH|B_trend_failure | 39.94% | 4.06% | -25.44% | 0.41 | 0.16 | 105 | 1.96% | 7.14% | 4.59% | 1.74% | 3085.67% | 0 | 3.94 | -245.80% | 否 |
| 7_原Brando_ETH|C_atr_trailing | -24.09% | -3.21% | -25.84% | -0.64 | -0.12 | 6 | 0.31% | 0.00% | 0.00% | -0.00% | 437.97% | 1 | 5.47 | -309.83% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | 42.61% | 4.30% | -33.79% | 0.35 | 0.13 | 1 | 2.61% | 7.14% | 6.10% | 2.63% | 848.16% | 0 | 80.83 | -243.13% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 42.61% | 4.30% | -33.79% | 0.35 | 0.13 | 1 | 2.61% | 7.14% | 6.10% | 2.63% | 848.16% | 0 | 80.83 | -243.13% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | 58.67% | 5.62% | -25.44% | 0.45 | 0.22 | 4 | 2.48% | 6.78% | 5.84% | 1.11% | 732.90% | 3 | 19.17 | -227.07% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | 41.69% | 4.22% | -30.11% | 0.36 | 0.14 | 119 | 3.51% | 7.16% | 6.36% | 3.93% | 2966.30% | 41 | 6.74 | -244.05% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | 41.69% | 4.22% | -30.11% | 0.36 | 0.14 | 119 | 3.51% | 7.16% | 6.36% | 3.93% | 2966.30% | 41 | 6.74 | -244.05% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | 47.64% | 4.73% | -23.98% | 0.41 | 0.20 | 125 | 3.31% | 7.10% | 5.96% | 2.97% | 4247.24% | 45 | 6.45 | -238.10% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | 6.82% | 0.79% | -24.29% | 0.13 | 0.03 | 31 | 1.49% | 7.04% | 1.58% | 1.40% | 4574.29% | 7 | 10.42 | -278.92% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 49.68% | 4.90% | -29.03% | 0.43 | 0.17 | 20 | 2.26% | 7.14% | 5.71% | 1.88% | 5693.63% | 0 | 32.97 | -236.06% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 24.08% | 2.59% | -22.68% | 0.33 | 0.11 | 18 | 1.02% | 3.85% | 2.43% | -0.00% | 4306.45% | 1 | 10.58 | -261.66% | 否 |

- 7_原Brando_BTC|A_structure_stop：年度 2018 -16.6%，2019 -4.0%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 49, "sell|C_rejection": 18}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 55, "exit_structure": 49, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 801, "buy_rejected_cash": 5, "buy_rejected_regime": 0}
- 7_原Brando_BTC|B_trend_failure：年度 2018 0.3%，2019 102.8%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trend_failure": 90, "sell|C_rejection": 12}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 95, "exit_structure": 0, "exit_trend": 90, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 753, "buy_rejected_cash": 16, "buy_rejected_regime": 0}
- 7_原Brando_BTC|C_atr_trailing：年度 2018 -22.0%，2019 0.0%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trail_stop": 5, "sell|C_rejection": 2}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 5, "exit_structure": 0, "exit_trend": 0, "exit_trail": 5, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 855, "buy_rejected_cash": 1, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|A_structure_stop：年度 2018 0.0%，2019 35.8%，2020 -16.1%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"crash_exit": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 0, "exit_crash": 1, "exit_rejection_reduce": 0, "buy_rejected_halt": 5985, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|B_trend_failure：年度 2018 0.0%，2019 95.8%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trend_failure": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 1, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 6087, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_BTC|C_atr_trailing：年度 2018 0.0%，2019 89.2%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trail_stop": 4}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 4, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 6441, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|A_structure_stop：年度 2018 -8.6%，2019 92.4%，2020 -5.8%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 58, "sell|C_rejection": 40, "crash_exit": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 71, "exit_structure": 58, "exit_trend": 0, "exit_trail": 0, "exit_crash": 1, "exit_rejection_reduce": 0, "buy_rejected_halt": 6738, "buy_rejected_cash": 40, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|B_trend_failure：年度 2018 -8.6%，2019 122.9%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 58, "sell|C_rejection": 40, "trend_failure": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 71, "exit_structure": 58, "exit_trend": 1, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 6840, "buy_rejected_cash": 40, "buy_rejected_regime": 0}
- 9_模型B核心+战术_BTC|C_atr_trailing：年度 2018 -8.6%，2019 123.2%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 58, "sell|C_rejection": 40, "trail_stop": 4}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 71, "exit_structure": 58, "exit_trend": 0, "exit_trail": 4, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 7196, "buy_rejected_cash": 38, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_BTC|A_structure_stop：年度 2018 0.0%，2019 72.2%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"reduce|C_rejection": 13, "structure_stop": 4}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 8, "exit_structure": 4, "exit_trend": 0, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 9, "buy_rejected_halt": 279, "buy_rejected_cash": 11, "buy_rejected_regime": 566}
- 10_模型C趋势过滤Brando_BTC|B_trend_failure：年度 2018 0.0%，2019 78.3%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"reduce|C_rejection": 11, "trend_failure": 1}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 5, "exit_structure": 0, "exit_trend": 1, "exit_trail": 0, "exit_crash": 0, "exit_rejection_reduce": 7, "buy_rejected_halt": 279, "buy_rejected_cash": 14, "buy_rejected_regime": 566}
- 10_模型C趋势过滤Brando_BTC|C_atr_trailing：年度 2018 0.0%，2019 54.7%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trail_stop": 9, "reduce|C_rejection": 2}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 10, "exit_structure": 0, "exit_trend": 0, "exit_trail": 9, "exit_crash": 0, "exit_rejection_reduce": 1, "buy_rejected_halt": 276, "buy_rejected_cash": 9, "buy_rejected_regime": 566}
- 7_原Brando_ETH|A_structure_stop：年度 2018 -19.4%，2019 0.0%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 27, "sell|C_rejection": 6, "crash_exit": 1}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 29, "exit_structure": 27, "exit_trend": 0, "exit_trail": 0, "exit_crash": 1, "exit_rejection_reduce": 0, "buy_rejected_halt": 802, "buy_rejected_cash": 1, "buy_rejected_regime": 0}
- 7_原Brando_ETH|B_trend_failure：年度 2018 -1.4%，2019 41.9%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trend_failure": 81, "sell|C_rejection": 22, "crash_exit": 2}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 96, "exit_structure": 0, "exit_trend": 81, "exit_trail": 0, "exit_crash": 2, "exit_rejection_reduce": 0, "buy_rejected_halt": 725, "buy_rejected_cash": 11, "buy_rejected_regime": 0}
- 7_原Brando_ETH|C_atr_trailing：年度 2018 -24.1%，2019 0.0%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trail_stop": 4, "sell|C_rejection": 2}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 5, "exit_structure": 0, "exit_trend": 0, "exit_trail": 4, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 826, "buy_rejected_cash": 1, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|A_structure_stop：年度 2018 0.0%，2019 42.6%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"crash_exit": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 0, "exit_crash": 1, "exit_rejection_reduce": 0, "buy_rejected_halt": 6276, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|B_trend_failure：年度 2018 0.0%，2019 42.6%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"crash_exit": 1}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 0, "exit_structure": 0, "exit_trend": 0, "exit_trail": 0, "exit_crash": 1, "exit_rejection_reduce": 0, "buy_rejected_halt": 6276, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 8_模型A趋势持仓_ETH|C_atr_trailing：年度 2018 0.0%，2019 58.7%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"trail_stop": 3, "structure_stop": 1}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 0, "exit_structure": 1, "exit_trend": 0, "exit_trail": 3, "exit_crash": 0, "exit_rejection_reduce": 0, "buy_rejected_halt": 6298, "buy_rejected_cash": 0, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|A_structure_stop：年度 2018 2.9%，2019 37.8%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 72, "sell|C_rejection": 39, "crash_exit": 8}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 95, "exit_structure": 72, "exit_trend": 0, "exit_trail": 0, "exit_crash": 8, "exit_rejection_reduce": 0, "buy_rejected_halt": 7001, "buy_rejected_cash": 12, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|B_trend_failure：年度 2018 2.9%，2019 37.8%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 72, "sell|C_rejection": 39, "crash_exit": 8}；计数 {"core_entries": 1, "core_reentries": 0, "tactical_entries": 95, "exit_structure": 72, "exit_trend": 0, "exit_trail": 0, "exit_crash": 8, "exit_rejection_reduce": 0, "buy_rejected_halt": 7001, "buy_rejected_cash": 12, "buy_rejected_regime": 0}
- 9_模型B核心+战术_ETH|C_atr_trailing：年度 2018 2.9%，2019 43.5%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 76, "sell|C_rejection": 39, "crash_exit": 7, "trail_stop": 3}；计数 {"core_entries": 4, "core_reentries": 3, "tactical_entries": 98, "exit_structure": 76, "exit_trend": 0, "exit_trail": 3, "exit_crash": 7, "exit_rejection_reduce": 0, "buy_rejected_halt": 7023, "buy_rejected_cash": 9, "buy_rejected_regime": 0}
- 10_模型C趋势过滤Brando_ETH|A_structure_stop：年度 2018 0.0%，2019 6.8%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"structure_stop": 18, "reduce|C_rejection": 12, "crash_exit": 1}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 22, "exit_structure": 18, "exit_trend": 0, "exit_trail": 0, "exit_crash": 1, "exit_rejection_reduce": 9, "buy_rejected_halt": 268, "buy_rejected_cash": 1, "buy_rejected_regime": 541}
- 10_模型C趋势过滤Brando_ETH|B_trend_failure：年度 2018 0.0%，2019 49.7%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"reduce|C_rejection": 15, "crash_exit": 5}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 11, "exit_structure": 0, "exit_trend": 0, "exit_trail": 0, "exit_crash": 5, "exit_rejection_reduce": 9, "buy_rejected_halt": 268, "buy_rejected_cash": 12, "buy_rejected_regime": 541}
- 10_模型C趋势过滤Brando_ETH|C_atr_trailing：年度 2018 0.0%，2019 24.1%，2020 0.0%，2021 0.0%，2022 0.0%，2023 0.0%，2024 0.0%，2025 0.0%，2026 0.0%；退出 {"reduce|C_rejection": 10, "trail_stop": 8}；计数 {"core_entries": 0, "core_reentries": 0, "tactical_entries": 11, "exit_structure": 0, "exit_trend": 0, "exit_trail": 8, "exit_crash": 0, "exit_rejection_reduce": 7, "buy_rejected_halt": 275, "buy_rejected_cash": 6, "buy_rejected_regime": 541}

图：`full_4h_round1_equity_BTC.png`


### 第二轮：手续费 10 bps + 滑点 5 bps

| 组合 | 总收益 | 年化 | 最大回撤 | Sharpe | Calmar | 交易 | 平均仓位 | BULL期持仓 | 上涨月捕获 | 下跌月捕获 | 卖后错过合计 | 止损后再入 | 均持仓天 | 超额 vs B&H | 低回撤因空仓 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_买入持有_BTC | 909.22% | 31.52% | -77.04% | 0.76 | 0.41 | 0 | — | — | — | — | — | — | — | — | 否 |
| 2_买入持有_ETH | 285.36% | 17.34% | -86.81% | 0.61 | 0.20 | 0 | — | — | — | — | — | — | — | — | 否 |
| 3_等权_BTC+ETH | 597.29% | 25.88% | -77.27% | 0.68 | 0.33 | 0 | — | — | — | — | — | — | — | — | 否 |
| 4_全程_USDT | 0.00% | 0.00% | 0.00% | — | — | 0 | — | — | — | — | — | — | — | — | 否 |
| 5_固定50%现货50%USDT_BTC | 454.61% | 22.51% | -69.45% | 0.68 | 0.32 | 0 | 75.10% | — | — | — | — | — | — | -454.61% | 否 |
| 6_SMA200趋势_BTC | 952.41% | 32.17% | -65.21% | 0.86 | 0.49 | 31 | 54.00% | — | 64.37% | 50.54% | 77.35% | 0 | — | 43.19% | 否 |
| 7_原Brando_BTC|A_structure_stop | -19.83% | -2.59% | -20.18% | -0.55 | -0.13 | 52 | 1.69% | 0.00% | -0.19% | 2.41% | 3570.62% | 27 | 4.76 | -929.05% | 否 |
| 7_原Brando_BTC|B_trend_failure | 74.22% | 6.80% | -29.31% | 0.50 | 0.23 | 102 | 2.64% | 10.30% | 8.97% | 2.13% | 3765.83% | 0 | 6.25 | -835.00% | 否 |
| 7_原Brando_BTC|C_atr_trailing | -22.50% | -2.98% | -23.06% | -0.56 | -0.13 | 7 | 0.39% | 0.00% | 0.01% | 0.01% | 5385.54% | 1 | 15.52 | -931.72% | 否 |
| 8_模型A趋势持仓_BTC|A_structure_stop | 13.63% | 1.53% | -56.81% | 0.19 | 0.03 | 1 | 10.36% | 12.02% | 13.59% | 16.24% | 1153.31% | 0 | 319.83 | -895.59% | 否 |
| 8_模型A趋势持仓_BTC|B_trend_failure | 95.18% | 8.25% | -34.40% | 0.54 | 0.24 | 1 | 3.91% | 10.52% | 9.32% | 0.83% | 629.38% | 0 | 120.83 | -814.04% | 否 |
| 8_模型A趋势持仓_BTC|C_atr_trailing | 87.03% | 7.70% | -26.20% | 0.66 | 0.29 | 4 | 1.94% | 5.28% | 8.04% | -0.00% | 595.68% | 3 | 15.04 | -822.18% | 否 |
| 9_模型B核心+战术_BTC|A_structure_stop | 57.59% | 5.54% | -38.66% | 0.39 | 0.14 | 99 | 7.69% | 12.04% | 12.13% | 8.85% | 6455.15% | 43 | 16.65 | -851.63% | 否 |
| 9_模型B核心+战术_BTC|B_trend_failure | 93.89% | 8.16% | -30.24% | 0.56 | 0.27 | 99 | 5.10% | 10.54% | 10.56% | 2.47% | 5931.22% | 43 | 14.64 | -815.32% | 否 |
| 9_模型B核心+战术_BTC|C_atr_trailing | 93.42% | 8.13% | -27.73% | 0.63 | 0.29 | 102 | 4.25% | 10.54% | 10.20% | 2.10% | 5887.38% | 46 | 13.62 | -815.80% | 否 |
| 10_模型C趋势过滤Brando_BTC|A_structure_stop | 71.09% | 6.57% | -31.43% | 0.48 | 0.21 | 17 | 2.97% | 10.35% | 7.60% | 1.29% | 5246.94% | 2 | 57.50 | -838.13% | 否 |
| 10_模型C趋势过滤Brando_BTC|B_trend_failure | 77.51% | 7.04% | -31.43% | 0.51 | 0.22 | 12 | 2.89% | 9.92% | 7.70% | 0.73% | 3772.08% | 0 | 66.15 | -831.70% | 否 |
| 10_模型C趋势过滤Brando_BTC|C_atr_trailing | 53.19% | 5.18% | -26.02% | 0.56 | 0.20 | 11 | 1.15% | 4.48% | 7.52% | 2.99% | 2630.33% | 2 | 12.42 | -856.03% | 否 |
| 5_固定50%现货50%USDT_ETH | 142.68% | 11.08% | -72.47% | 0.46 | 0.15 | 0 | 64.11% | — | — | — | — | — | — | -142.68% | 否 |
| 6_SMA200趋势_ETH | 1502.64% | 38.93% | -75.35% | 0.86 | 0.52 | 24 | 51.44% | — | 63.47% | 42.66% | 33.42% | 0 | — | 1217.29% | 否 |
| 7_原Brando_ETH|A_structure_stop | -19.89% | -2.59% | -21.65% | -0.75 | -0.12 | 30 | 0.52% | 0.00% | 0.00% | 2.17% | 1899.26% | 15 | 2.18 | -305.24% | 否 |
| 7_原Brando_ETH|B_trend_failure | 26.77% | 2.85% | -25.52% | 0.31 | 0.11 | 105 | 1.96% | 7.14% | 4.22% | 2.39% | 3096.19% | 0 | 3.94 | -258.58% | 否 |
| 7_原Brando_ETH|C_atr_trailing | -24.52% | -3.28% | -26.18% | -0.65 | -0.13 | 6 | 0.31% | 0.00% | 0.00% | -0.00% | 438.58% | 1 | 5.47 | -309.87% | 否 |
| 8_模型A趋势持仓_ETH|A_structure_stop | 42.19% | 4.26% | -33.79% | 0.35 | 0.13 | 1 | 2.61% | 7.14% | 6.08% | 2.65% | 848.64% | 0 | 80.83 | -243.17% | 否 |
| 8_模型A趋势持仓_ETH|B_trend_failure | 42.19% | 4.26% | -33.79% | 0.35 | 0.13 | 1 | 2.61% | 7.14% | 6.08% | 2.65% | 848.64% | 0 | 80.83 | -243.17% | 否 |
| 8_模型A趋势持仓_ETH|C_atr_trailing | 56.87% | 5.48% | -25.73% | 0.44 | 0.21 | 4 | 2.48% | 6.78% | 5.74% | 1.12% | 733.13% | 3 | 19.17 | -228.49% | 否 |
| 9_模型B核心+战术_ETH|A_structure_stop | 35.56% | 3.67% | -30.20% | 0.32 | 0.12 | 118 | 3.52% | 7.16% | 6.19% | 4.22% | 2972.40% | 42 | 6.79 | -249.80% | 否 |
| 9_模型B核心+战术_ETH|B_trend_failure | 35.56% | 3.67% | -30.20% | 0.32 | 0.12 | 118 | 3.52% | 7.16% | 6.19% | 4.22% | 2972.40% | 42 | 6.79 | -249.80% | 否 |
| 9_模型B核心+战术_ETH|C_atr_trailing | 40.53% | 4.11% | -24.22% | 0.36 | 0.17 | 124 | 3.32% | 7.10% | 5.73% | 3.25% | 4267.03% | 45 | 6.50 | -244.83% | 否 |
| 10_模型C趋势过滤Brando_ETH|A_structure_stop | 5.19% | 0.60% | -24.45% | 0.11 | 0.02 | 31 | 1.50% | 7.04% | 1.45% | 1.42% | 4579.12% | 7 | 10.42 | -280.16% | 否 |
| 10_模型C趋势过滤Brando_ETH|B_trend_failure | 48.63% | 4.81% | -29.08% | 0.43 | 0.17 | 20 | 2.26% | 7.14% | 5.64% | 1.89% | 5697.54% | 0 | 32.97 | -236.73% | 否 |
| 10_模型C趋势过滤Brando_ETH|C_atr_trailing | 23.07% | 2.49% | -23.02% | 0.32 | 0.11 | 18 | 1.02% | 3.85% | 2.34% | -0.00% | 4309.89% | 1 | 10.58 | -262.28% | 否 |


## 为什么 BTC 验证窗买入持有 +73% 而第四阶段原策略只有 +7.9%（逐笔还原）

- 持仓时间占比 46%，平均仓位 34%，BULL 期间持仓时间占比 50%；最长连续空仓 5.67 天；上涨月捕获 23%，下跌月捕获 19%。
- 卖出 391 次，卖出后到下一次买入期间标的平均变动 0.05%，合计 21.15%；止损/跟踪止损后重新买入 240 次；平均持仓 0.70 天。
- 空仓段 17 段共 13.54 天，期间标的复合变动 6.29%；持仓段 18 段共 351.33 天，期间标的复合变动 62.52%；标的全程 72.75%。退出原因 {'stop': 175, 'trail_stop': 150, 'C_resistance_rejection_sell': 74, 'stop_gap': 1}；按策略 {"A_breakout_retest_buy": {"count": 184, "sum": 665.6619904138092}, "B_breakdown_reclaim_buy": {"count": 216, "sum": -36.12578030071314}}。
- 错过最多的空仓段：2024-07-07 21:00→2024-07-10 05:00（2.3 天，标的 +5.1%）；2024-07-18 19:00→2024-07-19 15:00（0.8 天，标的 +4.0%）；2024-07-21 16:00→2024-07-21 22:00（0.2 天，标的 +1.8%）；2024-07-23 13:00→2024-07-23 15:00（0.1 天，标的 +1.6%）；2024-07-10 12:00→2024-07-11 12:00（1.0 天，标的 +1.1%）


## 数据完整性与过拟合限制

- 数据：Binance 现货 1h（2022-01→2026-11，缺 1 根）、4h（2017-08→，缺 16 根）、1d（无缺口）；第二阶段审计已确认无重复/错位、跨周期一致。
- 已用于选择模型的区间：第一～四阶段在 1h 2022-04→2026-11、4h 2018→2026-11 的全部区间上做过规则设计与评估；第五阶段的模型规则在这些区间被观察之后写成（分析者先验受影响）。因此**本阶段没有任何窗口可以称为样本外**；真正未使用的 1h 2022-01→03 只有 3 个月，仅供参考。
- 本阶段的自由度：0（模型、退出、参数全部预登记，一次性运行全部组合并全部报告）；没有删除交易或挑选区间；验证窗做过一次冒烟运行（看了聚合结果）后把“模型 B 战术袖子始终由 Brando 管理”写入预登记，保留窗在此之后才运行。
- 零成本是理想条件；真实滑点与价差无历史数据。
- 月度捕获率在窗口短（< 12 个月）时噪声很大；4h 长历史与 1h 的止损尺度不同，不能直接比较绝对数字。

## 工程验收与可复现命令

```bash
export PYTHONPATH=src
pytest tests/test_brando_pa_*.py -q                     # 46 项（前四阶段 42 + 本阶段 4）
python -m brando_pa.cli phase5 --workers 4              # 3 模型 × 3 退出 × 2 标的 × 6 窗口 × 2 成本 + 基准 + 第四阶段逐笔还原 → reports_out/brando_pa/phase5_spot/
python -m brando_pa.cli trend-signal                    # 最新日线状态与模型 A / C 的 BUY/HOLD/SELL（前瞻记录）
python -m brando_pa.cli dashboard --port 8502           # “趋势参与研究（第五阶段）”页
```

真实交易接口三重闸门 + 无实现，保持禁用。

## 附：事后消融（关闭“回撤 20% 暂停买入”，不属于预登记，不作为判据依据）

目的只有一个：量化预登记的熔断规则对结果的影响。产物在 `docs/brando_pa_validation/phase5_spot/ablation_no_halt/`（`phase5_results.json/md`）。全周期 1h 零成本 / 含成本（总收益，括号内为最大回撤）：

| 组合 | BTC 零成本 | BTC 含成本 | ETH 零成本 | ETH 含成本 |
|---|---|---|---|---|
| SMA200 基准 | +232%（−33%） | +216%（−34%） | +114%（−41%） | +105%（−41%） |
| 原 Brando｜结构止损 | +34%（−46%） | −86% | +44%（−45%） | −84% |
| 原 Brando｜趋势失效 | +112%（−35%） | −93% | +50%（−34%） | −93% |
| 模型 A｜结构止损 | +252%（−54%） | +252% | +43%（−69%） | +42% |
| 模型 A｜趋势失效 | +81%（−34%） | +74% | +66%（−44%） | +62% |
| 模型 A｜ATR 跟踪 | +67%（−29%） | +48% | −21%（−60%） | −29% |
| 模型 B｜趋势失效 | +64%（−34%） | −47% | +65%（−34%） | −44% |
| 模型 C｜结构止损 | +17%（−36%） | −37% | +44%（−35%） | −13% |
| 模型 C｜趋势失效 | +82%（−36%） | +54% | +62%（−40%） | +40% |
| 模型 C｜ATR 跟踪 | +31%（−41%） | +6% | +17%（−45%） | −3% |

读法：熔断关闭后差异显现，但没有任何组合在风险调整后超过 SMA200；成本把所有高频组合（原 Brando、模型 B 战术袖子）推成深度亏损。
