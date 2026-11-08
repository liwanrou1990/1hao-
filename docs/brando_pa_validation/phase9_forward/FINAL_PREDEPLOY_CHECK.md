# 部署前最终检查报告（2026-11-08，Claude 云容器内执行）

## 1. 是否存在"03:26 决定、03:00 成交"的问题 —— **存在（时隙错误），但没有发生任何成交**
- 事实：2026-11-08 00:00 UTC 日线收盘；03:22 只读检查确认未处理；03:26:55 形成 BUY 决策并提交 8 张订单（三账户 × 两本账），订单 `exec_after = 03:00`，即允许用 03:00 开盘（早于决策 26 分钟）的 K 线成交。这违反"决策之后才能成交"。
- 为什么没有成交：成交只在执行 K 线收盘后记账（04:00），而云容器在 03:26 之后没有再运行任何一轮；在修复前这些订单一直处于 pending，三账户现金均为 10,000.00，**没有任何一笔以 03:00 价格成交的记录**。
- 根因：`accounts.py` 中迟到信号的执行时隙取 `now.floor("h")`（当前正在形成的 K 线），错误地把"按该 K 线开盘价成交"当成了可执行价格；同时标准路径 `exec_after = close_t` 也允许用决策前 10 分钟开盘的 K 线成交。

## 2. 修复（提交见 Git 日志，本文件同一提交）
- 执行时隙统一为 **决策时刻之后第一根开盘的 1h K 线**（`exec_after = floor(now)+1h`），按其开盘价成交、在其收盘后记账；`decide()` 对 `exec_after ≤ decision_time` 直接抛错；`fill_pending()` 对任何开盘不晚于决策时刻的 K 线拒绝成交并记 `order_cancelled(cancelled_invalid)`。
- 决策时刻 = 服务运行时钟（前瞻即真实 UTC），事件同时记录墙钟；订单号加入决策时刻后缀，同一已收盘日线的纠错后重决策有独立订单号。
- 成交事件字段扩展（兼容新增，不覆盖历史）：signal_candle_close_time、decision_time、order_submit_time、execution_time、execution_price_observed_at、execution_price、market_data_source、order_status、cash_before/after、position_before/after、equity_after、realized_pnl_usdt、unrealized_pnl_usdt、mark_prices。
- 纠错：`forward-timeline-audit --repair` 对错误时隙的挂单写 `corrections.jsonl`（哈希链）标记无效、追加 `order_cancelled` 事件、移出 pending；**原始 order 事件一字未改**，事件链校验通过；服务运行中（持有 service.lock）拒绝纠错。
- 对 11-08 的处理：8 张订单作废（见下表）→ 同一已收盘日线在宽限期内重新决策（03:53:18 UTC，决策时仍在收盘后 4 h 内）→ 新订单 `exec_after = 04:00`（开盘晚于决策），将在 04:00 K 线收盘（05:00）后、且在 05:00–08:00 之间有一轮运行时按其开盘价成交；否则过期作废，不回填。

| 账户 | 作废订单数 | 订单号 | 曾成交 |
|---|---|---|---|
| A_BTC_SMA200 | 2 | A_BTC_SMA200:ideal:BTCUSDT:20261108:BUY:0, A_BTC_SMA200:cost:BTCUSDT:20261108:BUY:0 | 否 |
| B_modelB_state_machine | 4 | B_modelB_state_machine:ideal:BTCUSDT:20261108:BUY:0, B_modelB_state_machine:ideal:ETHUSDT:20261108:BUY:1, B_modelB_state_machine:cost:BTCUSDT:20261108:BUY:0, B_modelB_state_machine:cost:ETHUSDT:20261108:BUY:1 | 否 |
| REF_ETH_SMA200 | 2 | REF_ETH_SMA200:ideal:ETHUSDT:20261108:BUY:0, REF_ETH_SMA200:cost:ETHUSDT:20261108:BUY:0 | 否 |

## 3. 时间顺序测试
- 审计工具 `forward-timeline-audit`（不变量 T1–T7：收盘 ≤ 决策 ≤ 提交 < 执行 K 线开盘 ≤ 记账−1h；订单状态唯一；无重复成交；现金/持仓非负；equity = cash + Σ 持仓 × 标记价）：真实账户审计结果 **ok = True**，已纠错的时隙违规 8 条全部标记为 invalidated、无成交。
- 新增 `tests/test_brando_pa_timeline.py`（4 项）：决策 03:26 → 时隙 04:00、04:30 不成交、05:10 按 04:00 开盘成交且字段齐全；注入"决策后用更早 K 线"的坏订单被拒绝并作废；纠错日志不改原始事件、哈希链完整、作废后不补成交；47 天连续运行的账户真实性不变量（无负现金、无卖空、无重复成交、已实现只来自已成交卖出、净资产 = 持仓 × 市价 + 现金）；备用源缺口补齐与验证、来源切换时 SMA200/趋势一致性检查。既有前瞻/服务/部署测试按修正后的时隙更新。全部测试结果见 `test_report.txt`。

## 4. 数据源
- `source_priority = [okx_spot, coinbase_exchange, binance_spot, bybit_spot, static_klines_mirror]`；当前来源 okx_spot，最后成功 2026-11-08 03:53:18 UTC，1h 落后 0.89 h，1h→1d 一致性 {'checked_days': 10, 'max_rel_err': 5.5373759693665634e-05, 'ok': True}。
- 新增：备用来源窗口内有缺口 → 用其他可达来源补齐，补齐 K 线与原来源重叠偏差 ≤ 0.5% 才采用，补不齐则质量异常（不参与决策）；来源切换时用新来源 210 根日线独立算 SMA200 与趋势，与已存序列不一致 → 质量异常。实时审计（03:25 UTC）：OKX 47 根无缺口；Coinbase BTC-USDT 48 h 有 1–2 个空缺；两者收盘价最大偏差 0.23%。

## 5. Mac 部署文件
`deploy/install_mac_brando_pa.sh`、`deploy/uninstall_mac_brando_pa.sh`、`scripts/brando_pa_forward_ctl.sh` 存在且可执行（-rwxr-xr-x，`bash -n` 通过）；`MAC_RUNBOOK.md` 存在。安装脚本：venv、`requirements.txt` + pyarrow/duckdb/httpx/pyyaml/streamlit/plotly/pytest、日志目录 `reports_out/brando_pa/phase8_forward`、`~/.config/brando_pa/env`（600）、launchd `com.brando-pa.forward`（RunAtLoad + KeepAlive + ThrottleInterval 30）、`plutil -lint`、首轮验证；检测到 `live_trading: true` 拒绝安装。**当前环境是 Linux 云容器，没有在 Mac 上安装。**

## 6. 三套账户最新真实状态（2026-11-08 03:53:18 UTC，真正前瞻）

| 账户 | 含成本权益 | 零成本权益 | USDT | 持仓 | 风险状态 | 挂单 | 最后信号日 |
|---|---|---|---|---|---|---|---|
| 账户 A：BTC SMA200 | 10,000.00 | 10,000.00 | 10,000.00 | {'BTCUSDT': 0.0, 'ETHUSDT': 0.0} | NORMAL | 2（exec_after 04:00） | 2026-11-07 00:00:00+00:00 |
| 账户 B：模型 B 满额配置 + 状态机 | 10,000.00 | 10,000.00 | 10,000.00 | {'BTCUSDT': 0.0, 'ETHUSDT': 0.0} | NORMAL | 4（exec_after 04:00） | 2026-11-07 00:00:00+00:00 |
| 参考：ETH SMA200 | 10,000.00 | 10,000.00 | 10,000.00 | {'BTCUSDT': 0.0, 'ETHUSDT': 0.0} | NORMAL | 2（exec_after 04:00） | 2026-11-07 00:00:00+00:00 |

## 7. 72 小时连续运行验收
**当前不能在云容器内开展**（不常驻、无法保证真实连续时间）。只能在持续在线的 Mac/服务器上用 `deploy/install_mac_brando_pa.sh` 启动后开展；`forward-acceptance` 只统计真实时间心跳，不足 72 h 固定为"进行中"。状态：**未开始**。

真实交易 API：无实现、硬性禁用；最大回撤上限未获批准。
