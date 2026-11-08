# 前瞻成交机制最后一次真实性检查（2026-11-08，Claude 云容器内执行）

## 1. 是否存在"离线后按历史价格补成交"的问题 —— **存在，已修复；修复前没有发生任何此类成交**
- 修复前规则：订单 exec_after = 04:00 后，引擎在**任何时刻**首次处理时，只要 04:00 这根 K 线已收盘且距今 ≤ 3 h，就按 04:00 **开盘价**记账。因此若服务 05:10 才恢复，会用 04:00 的历史开盘价"假装"在 04:00 成交——成交时间早于引擎实际处理时间，属于回填。
- 事实核对：03:53:18 的 8 张订单在修复前一直 pending（服务未再运行），账户现金 10,000.00；**没有一笔按 04:00 开盘价记账的成交**。

## 2. 修复（本提交）
- 前瞻模式（mode=forward）成交规则改为：**成交时间 = 引擎实际处理时刻**（execution_time = fill_recorded_at = now）；成交参考价 = 处理时刻可观察的**最新已收盘 1h K 线收盘价**（price_time = 该 K 线收盘时刻，必须 ≥ exec_after 且 > decision_time）；不等待未来 K 线，不使用任何 K 线开盘价。now > exec_after + 1 h → `filled_late`；now > exec_after + 3 h（预登记宽限）→ `expired`，不成交；没有 exec_after 之后可观察的价格（断线）→ 继续 pending 直到有价格或过期。过期/迟到判定只依赖时间，与后来的价格无关（测试用价格路径 ×1.5 验证结论不变）。
- 历史回放（mode=replay）保留回测口径（exec_after 后第一根 K 线开盘，收盘后记账），每条事件标注 `mode=replay`、`fill_rule=replay:…`；历史回测在第四～七阶段引擎中，三者分开保存。
- 每笔成交保存：signal_candle_close_time、decision_time、order_submit_time、exec_after、execution_time、price_time、price_bar_open_time、execution_price_observed_at、fill_recorded_at、reference_price、execution_price、market_data_source、order_status、late、fill_rule、cash/position before/after、equity_after、realized/unrealized、mark_prices。
- 审计 `forward-timeline-audit`：前瞻成交不变量 decision_time < price_time ≤ execution_time = fill_recorded_at，execution_time ≥ exec_after，late 标志与状态一致；回放成交沿用 K 线口径。真实账户审计 **ok = True**。

## 3. 真实账户按修正规则执行（05:05:45 UTC，一轮 `forward-service --once`）
03:53:18 决策的订单（exec_after 04:00）在引擎 05:05:45 首次处理时执行：价格 = 04:00 K 线在 05:00 的收盘价（OKX），执行时间 05:05:45，状态 `filled_late`（超过 exec_after 1 h 以上、在 3 h 宽限内）。不是 04:00 开盘价。

| 账户 | 账本 | 方向 | 决策时刻 | exec_after | 价格时间 | 执行时间 | 参考价 | 成交价 | 来源 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| A_BTC_SMA200 | ideal | BTCUSDT BUY | 2026-11-08 03:53:18 | 2026-11-08 04:00 | 2026-11-08 05:00 | 2026-11-08 05:05:45 | 75865.5 | 75865.50 | okx_spot | filled_late |
| A_BTC_SMA200 | cost | BTCUSDT BUY | 2026-11-08 03:53:18 | 2026-11-08 04:00 | 2026-11-08 05:00 | 2026-11-08 05:05:45 | 75865.5 | 75903.43 | okx_spot | filled_late |
| B_modelB_state_machine | ideal | BTCUSDT BUY | 2026-11-08 03:53:18 | 2026-11-08 04:00 | 2026-11-08 05:00 | 2026-11-08 05:05:45 | 75865.5 | 75865.50 | okx_spot | filled_late |
| B_modelB_state_machine | ideal | ETHUSDT BUY | 2026-11-08 03:53:18 | 2026-11-08 04:00 | 2026-11-08 05:00 | 2026-11-08 05:05:45 | 2235.52 | 2235.52 | okx_spot | filled_late |
| B_modelB_state_machine | cost | BTCUSDT BUY | 2026-11-08 03:53:18 | 2026-11-08 04:00 | 2026-11-08 05:00 | 2026-11-08 05:05:45 | 75865.5 | 75903.43 | okx_spot | filled_late |
| B_modelB_state_machine | cost | ETHUSDT BUY | 2026-11-08 03:53:18 | 2026-11-08 04:00 | 2026-11-08 05:00 | 2026-11-08 05:05:45 | 2235.52 | 2236.64 | okx_spot | filled_late |
| REF_ETH_SMA200 | ideal | ETHUSDT BUY | 2026-11-08 03:53:18 | 2026-11-08 04:00 | 2026-11-08 05:00 | 2026-11-08 05:05:45 | 2235.52 | 2235.52 | okx_spot | filled_late |
| REF_ETH_SMA200 | cost | ETHUSDT BUY | 2026-11-08 03:53:18 | 2026-11-08 04:00 | 2026-11-08 05:00 | 2026-11-08 05:05:45 | 2235.52 | 2236.64 | okx_spot | filled_late |

三账户当前真实状态（真正前瞻，含成本账本已扣 10 bps + 5 bps）：

| 账户 | 含成本权益 | 零成本权益 | 持仓 | USDT | 风险状态 |
|---|---|---|---|---|---|
| 账户 A：BTC SMA200 | 9,985.03 | 10,000.00 | {'BTCUSDT': 0.1314831176725664, 'ETHUSDT': 0.0} | 10.00 | NORMAL |
| 账户 B：模型 B 满额配置 + 状态机 | 9,985.03 | 10,000.00 | {'BTCUSDT': 0.0657415588362832, 'ETHUSDT': 2.231031810001272} | 10.00 | NORMAL |
| 参考：ETH SMA200 | 9,985.03 | 10,000.00 | {'BTCUSDT': 0.0, 'ETHUSDT': 4.462063620002544} | 10.00 | NORMAL |

## 4. 测试
新增 `tests/test_brando_pa_causality.py`（7 项）：正常轮询（01:05 处理 → price_time 01:00、不迟到、无开盘价字段）；延迟轮询（02:30 处理 → late、用 02:00 可观察收盘价而非 01:00 开盘价）；**用户场景**（03:53 决策、05:10 恢复 → late、price_time 05:00、不是 04:00 开盘价）；离线超宽限（07:30 恢复 → expired、不成交、价格路径改变结论不变）；断线无新价格 → 保持 pending，行情恢复后在处理时刻成交；每步重启 vs 连续运行成交完全一致；回放模式独立标注。既有前瞻/服务/部署/时间顺序测试按新规则更新。全套结果见 `test_report.txt`。

## 5. Mac 部署与依赖
服务进程的 Python 依赖：pandas、numpy、httpx、pyyaml、pyarrow（parquet 行情仓）、duckdb（历史仓读取）、spot90（仓库内）。`deploy/install_mac_brando_pa.sh` 安装 `requirements.txt` + pyarrow duckdb httpx pyyaml streamlit plotly pytest，覆盖全部依赖；三个脚本可执行（-rwxr-xr-x）；launchd `RunAtLoad + KeepAlive + ThrottleInterval 30`，`service.lock` 单实例。**当前环境是 Linux 云容器，没有在 Mac 上安装。**

## 6. 是否可以在 Mac 进行 72 小时连续运行验收
可以开始（代码与脚本就绪，成交机制已通过因果审计），但只能在持续在线的 Mac 上由你用 `bash deploy/install_mac_brando_pa.sh` 启动后进行；`forward-acceptance` 按真实时间统计，不足 72 h 固定为"进行中"。当前状态：**未开始**。真实交易 API：无实现、硬性禁用。


---

## 7. 成交报价精度（最后一项改进，2026-11-08）
- 新增 `forward/quotes.py`：处理订单时实时获取现货 Bid/Ask（OKX ticker 优先，Coinbase Exchange ticker 备用；公开接口、无密钥），保存报价时间戳、获取时刻、来源、年龄、点差。实测 OKX 报价年龄约 0.5 s、点差约 0.01 bp；Coinbase ticker 的时间为最后成交时间（实测约 7.5 min，通常超过新鲜度阈值而被拒绝）。
- 成交价：BUY 用 Ask、SELL 用 Bid（`price_kind = live_quote_ask / live_quote_bid`），成本账本在此之上再按预登记规则计 5 bps 滑点与 10 bps 手续费；零成本账本按 Ask/Bid 原价。
- 新鲜度：报价年龄 > `forward.quotes.max_age_s`（120 s）或报价时间不晚于决策时刻 → 不用；退回最新已收盘 1h 收盘价作为**估算**参考（`price_kind = estimated_closed_bar_close`，`estimated = true`，状态 `filled_estimated / filled_late_estimated`，备注“非可成交报价”），且该收盘价年龄 ≤ `estimated_max_age_s`（70 min）、时间 ≥ exec_after 且晚于决策；两者都没有 → 记 `fill_paused`（原因：无新鲜价格），订单保持 pending，绝不使用更早的历史价格；订单仍受 exec_after + 3 h 宽限的过期约束。
- 不依赖后续价格：同一决策、同一处理时刻，之后价格路径不同（×1.3），成交与否与成交价完全相同（测试 `test_execution_does_not_depend_on_future_prices`）。
- 审计新增：estimated 标志必须与订单状态一致。历史事件未改动（05:05 的 8 笔 `filled_late` 成交是改进前按已收盘收盘价记账的，字段中无 price_kind，保留原样供审计）。
- 新增 `tests/test_brando_pa_quotes.py`（4 项）：Ask/Bid 与时间戳来源、过期报价→估算或暂停→恢复后实时成交、报价接口断线恢复、不依赖未来价格；测试默认隔离实时报价接口（conftest 固定 QuoteService.get → None，需要报价的测试显式注入模拟报价）。
