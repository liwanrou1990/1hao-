# 第十三阶段前瞻审计 · 2026-11-09 02:42:03 UTC · forward（前瞻模拟；独立影子账户；非历史回测） · 总体 正常

| 影子账户 | 起点 UTC | 已运行(日) | 信号/错过 | 成交(估算/迟) | 最新清仓净值 | 现金 | 暴露 | 最大回撤 | 最长回撤(日) | vs BTC 持有 | 时间审计 | 账本 | 链 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_B2_state_machine | 2026-11-09 02:39 | 0.0 | 0/0 | 0(0/0) | 10,000.00 | 10,000 | 0% | 0.00% | 0 | +0.00 | 通过 | 一致 | ok |
| B_BTC_SMA200 | 2026-11-09 02:39 | 0.0 | 0/0 | 0(0/0) | 10,000.00 | 10,000 | 0% | 0.00% | 0 | +0.00 | 通过 | 一致 | ok |
| C_BUYHOLD_BTC | 2026-11-09 02:39 | 0.0 | 0/0 | 0(0/0) | 10,000.00 | 10,000 | 0% | 0.00% | 0 | +0.00 | 通过 | 一致 | ok |
| D_BUYHOLD_ETH | 2026-11-09 02:39 | 0.0 | 0/0 | 0(0/0) | 10,000.00 | 10,000 | 0% | 0.00% | 0 | +0.00 | 通过 | 一致 | ok |
| E_BUYHOLD_5050 | 2026-11-09 02:39 | 0.0 | 0/0 | 0(0/0) | 10,000.00 | 10,000 | 0% | 0.00% | 0 | +0.00 | 通过 | 一致 | ok |
| F_B2_vol_trail | 2026-11-09 02:39 | 0.0 | 0/0 | 0(0/0) | 10,000.00 | 10,000 | 0% | 0.00% | 0 | +0.00 | 通过 | 一致 | ok |

## 窗口绩效（只评价已到期窗口）

| 影子账户 | 30 天 | 90 天 | 180 天 | 365 天 |
|---|---|---|---|---|
| A_B2_state_machine | 未到期 | 未到期 | 未到期 | 未到期 |
| B_BTC_SMA200 | 未到期 | 未到期 | 未到期 | 未到期 |
| C_BUYHOLD_BTC | 未到期 | 未到期 | 未到期 | 未到期 |
| D_BUYHOLD_ETH | 未到期 | 未到期 | 未到期 | 未到期 |
| E_BUYHOLD_5050 | 未到期 | 未到期 | 未到期 | 未到期 |
| F_B2_vol_trail | 未到期 | 未到期 | 未到期 | 未到期 |

## 行为观察
- A_B2_state_machine: {'note': '前瞻不足 31 日：主升 / 主跌段观察未到期', 'longest_flat_days': 1, 'sell_then_rebuy_higher_within_10d': 0}
- B_BTC_SMA200: {'note': '前瞻不足 31 日：主升 / 主跌段观察未到期', 'longest_flat_days': 1, 'sell_then_rebuy_higher_within_10d': 0}
- C_BUYHOLD_BTC: {'note': '前瞻不足 31 日：主升 / 主跌段观察未到期', 'longest_flat_days': 1, 'sell_then_rebuy_higher_within_10d': 0}
- D_BUYHOLD_ETH: {'note': '前瞻不足 31 日：主升 / 主跌段观察未到期', 'longest_flat_days': 1, 'sell_then_rebuy_higher_within_10d': 0}
- E_BUYHOLD_5050: {'note': '前瞻不足 31 日：主升 / 主跌段观察未到期', 'longest_flat_days': 1, 'sell_then_rebuy_higher_within_10d': 0}
- F_B2_vol_trail: {'note': '前瞻不足 31 日：主升 / 主跌段观察未到期', 'longest_flat_days': 1, 'sell_then_rebuy_higher_within_10d': 0}

## 服务连续性与数据
- {'start_utc': '2026-11-09 02:39:37.755981+00:00', 'end_utc': '2026-11-09 02:42:03.127390+00:00', 'elapsed_hours': 0.04, 'cycles_observed': 1, 'cycles_expected': 1, 'coverage': 1.0, 'longest_gap_hours': 0.04, 'data_ok_share': 1.0, 'n_data_issues': 0, 'hosts': ['vm'], 'status': '进行中'}
- 超过 3 个周期的中断：无
- 延迟：A_B2_state_machine 信号 None 分钟 / 成交 None 分钟; B_BTC_SMA200 信号 None 分钟 / 成交 None 分钟; C_BUYHOLD_BTC 信号 None 分钟 / 成交 None 分钟; D_BUYHOLD_ETH 信号 None 分钟 / 成交 None 分钟; E_BUYHOLD_5050 信号 None 分钟 / 成交 None 分钟; F_B2_vol_trail 信号 None 分钟 / 成交 None 分钟
- 未来日期检查：无

评价规则：docs/brando_pa_validation/phase13_forward/evaluation_rules.md；真实交易禁用；本审计未写任何账户文件。