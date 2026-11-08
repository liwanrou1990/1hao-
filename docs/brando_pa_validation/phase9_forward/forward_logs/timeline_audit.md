# 前瞻事件时间顺序审计 · 2026-11-08 05:05:47 UTC · 总体 通过

| 账户 | 事件 | 订单 | 成交 | 过期 | 作废 | 已纠错 | 违规 | 链 |
|---|---|---|---|---|---|---|---|---|
| A_BTC_SMA200 | 25 | 4 | 2 | 0 | 2 | 2 | 0（含已纠错 2） | ok |
| B_modelB_state_machine | 33 | 8 | 4 | 0 | 4 | 4 | 0（含已纠错 4） | ok |
| REF_ETH_SMA200 | 25 | 4 | 2 | 0 | 2 | 2 | 0（含已纠错 2） | ok |
- A_BTC_SMA200: {"rule": "T3-slot", "order_id": "A_BTC_SMA200:ideal:BTCUSDT:20261108:BUY:0", "decision": "2026-11-08 03:26:55.152576+00:00", "exec_after": "2026-11-08 03:00:00+00:00", "status": "invalidated"}
- A_BTC_SMA200: {"rule": "T3-slot", "order_id": "A_BTC_SMA200:cost:BTCUSDT:20261108:BUY:0", "decision": "2026-11-08 03:26:55.153222+00:00", "exec_after": "2026-11-08 03:00:00+00:00", "status": "invalidated"}
- B_modelB_state_machine: {"rule": "T3-slot", "order_id": "B_modelB_state_machine:ideal:BTCUSDT:20261108:BUY:0", "decision": "2026-11-08 03:26:55.173353+00:00", "exec_after": "2026-11-08 03:00:00+00:00", "status": "invalidated"}
- B_modelB_state_machine: {"rule": "T3-slot", "order_id": "B_modelB_state_machine:ideal:ETHUSDT:20261108:BUY:1", "decision": "2026-11-08 03:26:55.173639+00:00", "exec_after": "2026-11-08 03:00:00+00:00", "status": "invalidated"}
- B_modelB_state_machine: {"rule": "T3-slot", "order_id": "B_modelB_state_machine:cost:BTCUSDT:20261108:BUY:0", "decision": "2026-11-08 03:26:55.174324+00:00", "exec_after": "2026-11-08 03:00:00+00:00", "status": "invalidated"}
- B_modelB_state_machine: {"rule": "T3-slot", "order_id": "B_modelB_state_machine:cost:ETHUSDT:20261108:BUY:1", "decision": "2026-11-08 03:26:55.174613+00:00", "exec_after": "2026-11-08 03:00:00+00:00", "status": "invalidated"}
- REF_ETH_SMA200: {"rule": "T3-slot", "order_id": "REF_ETH_SMA200:ideal:ETHUSDT:20261108:BUY:0", "decision": "2026-11-08 03:26:55.198332+00:00", "exec_after": "2026-11-08 03:00:00+00:00", "status": "invalidated"}
- REF_ETH_SMA200: {"rule": "T3-slot", "order_id": "REF_ETH_SMA200:cost:ETHUSDT:20261108:BUY:0", "decision": "2026-11-08 03:26:55.198942+00:00", "exec_after": "2026-11-08 03:00:00+00:00", "status": "invalidated"}