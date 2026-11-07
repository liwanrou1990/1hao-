# 第三阶段预登记流程结果（执行周期 1h）

窗口：{"train": ["2022-04-01", "2024-06-30"], "valid": ["2024-07-01", "2025-06-30"], "L2_holdout": ["2025-07-01", null], "L1_locked_1h": ["2022-01-01", "2022-03-31"]}

## 训练 / 验证（策略 × 止损模式）

| 策略 | 止损模式 | 训练 n/ExpR/PF | 训练 止损ATR中位(候选) | 验证 n/ExpR/PF | 验证 回撤 | 验证 被扫损占比 |
|---|---|---|---|---|---|---|
| A_breakout_retest | micro | 125 / -0.136 / 0.78 | 3.68 | 91 / 0.008 / 1.00 | -8.69% | 63% |
| A_breakout_retest | atr_buffer | 99 / -0.104 / 0.81 | 3.68 | 72 / -0.155 / 0.76 | -9.78% | 67% |
| A_breakout_retest | fixed_structure | 116 / -0.080 / 0.86 | 3.68 | 86 / -0.149 / 0.76 | -9.89% | 63% |
| A2_structure_entry | micro | 8 / -0.837 / 0.00 | 2.53 | 6 / -0.085 / 0.82 | -0.93% | 50% |
| A2_structure_entry | atr_buffer | 8 / -0.837 / 0.00 | 2.53 | 6 / -0.085 / 0.82 | -0.93% | 50% |
| A2_structure_entry | fixed_structure | 7 / -0.524 / 0.10 | 2.53 | 2 / 0.910 / ∞ | 0.00% | 0% |
| A3_breakout_second_confirm | micro | 4 / -0.709 / 0.01 | 2.53 | 3 / 0.013 / 1.03 | -0.49% | 33% |
| A3_breakout_second_confirm | atr_buffer | 4 / -0.709 / 0.01 | 2.53 | 3 / 0.013 / 1.03 | -0.49% | 33% |
| A3_breakout_second_confirm | fixed_structure | 4 / -0.395 / 0.16 | 2.53 | 3 / -0.068 / 0.79 | -0.49% | 33% |

选定止损模式（验证窗 ExpR，<10 笔退回默认）：A_breakout_retest: micro；A2_structure_entry: micro（默认）；A3_breakout_second_confirm: micro（默认）

## 锁定测试（只运行一次）

| 集合 | 策略 | 止损 | n | ExpR | 胜率 | PF | 净收益 | 回撤 | 均值R 95%CI | 候选→通过 |
|---|---|---|---|---|---|---|---|---|---|---|
| L2 半锁定保留集 2025-07→ | A_breakout_retest | micro | 99 | 0.027 | 38.4% | 1.04 | 1.03% | -6.63% | [-0.275, 0.402] | 690→103 |
| L2 半锁定保留集 2025-07→ | A2_structure_entry | micro | 12 | -0.559 | 25.0% | 0.17 | -3.31% | -3.48% | [-0.916, -0.251] | 702→14 |
| L2 半锁定保留集 2025-07→ | A3_breakout_second_confirm | micro | 5 | -0.964 | 0.0% | 0.00 | -2.39% | -2.39% | [-0.964, -0.964] | 375→6 |
| L1 锁定集 | A_breakout_retest | micro | 29 | -0.574 | 27.6% | 0.42 | -8.23% | -9.62% | [-0.967, 0.168] | 139→30 |
| L1 锁定集 | A2_structure_entry | micro | 2 | 1.361 | 100.0% | ∞ | 1.36% | 0.00% | [1.361, 1.361] | 136→2 |
| L1 锁定集 | A3_breakout_second_confirm | micro | 1 | -0.930 | 0.0% | 0.00 | -0.46% | -0.46% | [-0.930, -0.930] | 72→1 |
| L1 锁定集 | A_breakout_retest_4h_2017-11_2018-05 |  | 6 | 0.425 | 50.0% | 1.85 | 1.26% | -0.49% | [0.258, 1.081] | 156→6 |

## 压力测试（保留集，不参与选型）

| 策略 | 场景 | n | ExpR | PF | 净收益 | 回撤 |
|---|---|---|---|---|---|---|
| A_breakout_retest | fee10 | 74 | 0.119 | 1.22 | 4.20% | -4.38% |
| A_breakout_retest | slip10_stop20 | 34 | 0.407 | 1.85 | 7.00% | -2.36% |
| A_breakout_retest | spread4 | 99 | -0.013 | 0.96 | -0.97% | -6.75% |
| A_breakout_retest | funding_x3 | 82 | -0.012 | 0.96 | -0.79% | -5.79% |
| A2_structure_entry | fee10 | 9 | -0.164 | 0.71 | -0.75% | -2.07% |
| A2_structure_entry | slip10_stop20 | 4 | -0.551 | 0.01 | -1.10% | -1.11% |
| A2_structure_entry | spread4 | 12 | -0.566 | 0.16 | -3.35% | -3.51% |
| A2_structure_entry | funding_x3 | 11 | -0.734 | 0.00 | -3.96% | -3.96% |
| A3_breakout_second_confirm | fee10 | 3 | -0.969 | 0.00 | -1.44% | -1.44% |
| A3_breakout_second_confirm | slip10_stop20 | 1 | -0.976 | 0.00 | -0.49% | -0.49% |
| A3_breakout_second_confirm | spread4 | 5 | -0.972 | 0.00 | -2.40% | -2.40% |
| A3_breakout_second_confirm | funding_x3 | 3 | -0.984 | 0.00 | -1.47% | -1.47% |

## 保留集：标的 × 环境 × 方向（平均 R）

- **A_breakout_retest**：BTCUSDT/上涨趋势/short 3 笔 ExpR -0.63 净 -94.20；BTCUSDT/下跌趋势/long 1 笔 ExpR -0.09 净 -4.66；BTCUSDT/下跌趋势/short 23 笔 ExpR 0.01 净 -5.98；BTCUSDT/横盘震荡/short 8 笔 ExpR -0.83 净 -337.83；ETHUSDT/上涨趋势/long 4 笔 ExpR 0.43 净 80.38；ETHUSDT/上涨趋势/short 10 笔 ExpR 0.79 净 390.95；ETHUSDT/下跌趋势/long 4 笔 ExpR -0.80 净 -160.81；ETHUSDT/下跌趋势/short 18 笔 ExpR 0.08 净 68.18；ETHUSDT/横盘震荡/long 8 笔 ExpR 0.66 净 266.87；ETHUSDT/横盘震荡/short 20 笔 ExpR -0.09 净 -100.25
- **A2_structure_entry**：BTCUSDT/下跌趋势/short 2 笔 ExpR -0.98 净 -95.26；ETHUSDT/上涨趋势/short 3 笔 ExpR -0.64 净 -94.93；ETHUSDT/下跌趋势/long 2 笔 ExpR 0.19 净 17.56；ETHUSDT/下跌趋势/short 3 笔 ExpR -0.74 净 -109.59；ETHUSDT/横盘震荡/long 1 笔 ExpR 0.01 净 0.54；ETHUSDT/横盘震荡/short 1 笔 ExpR -0.98 净 -48.92
- **A3_breakout_second_confirm**：BTCUSDT/下跌趋势/short 1 笔 ExpR -0.99 净 -48.59；ETHUSDT/上涨趋势/short 1 笔 ExpR -0.98 净 -48.89；ETHUSDT/下跌趋势/long 2 笔 ExpR -0.94 净 -92.14；ETHUSDT/横盘震荡/short 1 笔 ExpR -0.98 净 -48.92

## 策略 A 止损距离诊断（训练窗）

- 候选 1028，止损/ATR 中位 3.68（通过过滤 3.16，因止损距离被拒 4.91）
- 分解（均值 ATR）：{'part_zone_height': 2.54, 'part_retest_depth': 0.17, 'part_confirm_offset': 0.78, 'part_buffer': 0.5, 'reconstructed_total': 3.99, 'actual_mean': 3.99}；占比 {'part_zone_height': 0.64, 'part_retest_depth': 0.04, 'part_confirm_offset': 0.2, 'part_buffer': 0.13}；结构点落在区域另一侧占 94%
- 时序：突破→回踩中位 5.0 根，回踩→确认中位 0.0 根
- 结论：主因：结构止损挂在 1W/1D 区域的另一侧，区域高度占止损距离 64%（区域半宽上限 1% 价格，在执行周期 ATR 单位下即数倍 ATR）→ 属于“关键区域对执行周期过宽 + 止损结构选择”问题，不是确认条件滞后；成交后：亏损交易 MAE/止损中位 0.81R、被扫损占 89%；赢家 MFE/止损中位 2.16R → 止损宽度与实际波动的关系：亏损多为真正走反（止损并非被噪声扫掉）
- 图：['/home/user/1hao-/reports_out/brando_pa/phase3/stop_distance_A_1h_train.png']

耗时 483.9 s；备注 []
