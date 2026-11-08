# 72 小时真实时间可用性验收 · 本地段状态：**未开始**（真实时间前瞻运行（非模拟））

| 分段 | 起点 UTC | 终点 UTC | 时长 h | 实际轮询 | 预期轮询 | 覆盖率 | 最长中断 h | 行情正常占比 | 行情问题数 | 异常数 | 告警 | 错过信号 | 过期订单 | 成交 | 主机 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cloud_legacy | 2026-11-07 17:38 | 2026-11-08 06:02 | 12.41 | 8 | 74 | 10.8% | 9.16 | 100% | 0 | 0 | {} | {'A_BTC_SMA200': 0, 'B_modelB_state_machine': 0, 'REF_ETH_SMA200': 0} | {'A_BTC_SMA200': 0, 'B_modelB_state_machine': 0, 'REF_ETH_SMA200': 0} | {'A_BTC_SMA200': 2, 'B_modelB_state_machine': 4, 'REF_ETH_SMA200': 2} | [] |
| local | — | — | — | — | — | — | — | — | — | — | — | — | — | — | 没有本地（非云容器）运行记录 |
| all | 2026-11-07 17:38 | 2026-11-08 13:00 | 19.38 | 8 | 116 | 6.9% | 9.16 | 100% | 0 | 0 | {} | {'A_BTC_SMA200': 0, 'B_modelB_state_machine': 0, 'REF_ETH_SMA200': 0} | {'A_BTC_SMA200': 0, 'B_modelB_state_machine': 0, 'REF_ETH_SMA200': 0} | {'A_BTC_SMA200': 2, 'B_modelB_state_machine': 4, 'REF_ETH_SMA200': 2} | [] |