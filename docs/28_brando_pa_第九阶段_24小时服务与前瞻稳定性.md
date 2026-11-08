# 28 · Brando Price Action 第九阶段：24 小时持续运行、行情质量控制、前瞻模拟稳定性与 USDT 利润验证

生成时间：2026-11-07T18:31 UTC · 分支 `claude/brando-pa-phase9-deploy` · 基准锁定 `docs/brando_pa_validation/phase9_forward/BASELINE_LOCK.md`（第八阶段提交 c437594）· 测试 62 项通过（前八阶段 59 + 本阶段 3 个故障注入测试函数，覆盖任务 §六全部 12 项）· **不调参、不新增策略**；三账户规则与第八阶段相同。

## 0. 结论先行

1. **前瞻系统已改造成独立后台服务**（`forward-service`：行情更新 → 账户推进 → 心跳 → UTC 日切日报 → 容量检查 → 告警，SIGTERM 优雅退出，异常只记录不退出），并交付三套可直接提交/部署的管理配置：macOS launchd（`deploy/launchd/`，RunAtLoad + KeepAlive）、Linux systemd（`deploy/systemd/`，Restart=always + 健康检查 timer）、Docker Compose（`deploy/docker/`，restart: unless-stopped + HEALTHCHECK）；环境变量模板 `deploy/.env.example`；安装 / 启动 / 停止 / 重启 / 状态 / 日志命令见 `deploy/README.md`。
2. **本 Claude 云容器不能作为 24 小时运行主机**：容器空闲后回收、无常驻进程。本阶段在容器内只做了：真实行情下的单轮服务运行（`forward-service --once`，6.4 s，来源 okx_spot，1h 落后 0.29 h，健康检查 ok）、7 个故障注入测试（模拟时钟下跨 47 天、每 2 小时一轮、每轮重启）、日报生成、看板页。**这不等同于 24 小时运行成功**；持续运行必须由用户在持续在线的主机上用上述配置启动。
3. **行情质量控制**：只用已收盘 K 线、UTC；来源优先级 Binance → OKX → Bybit → Coinbase → Binance 镜像；每次来源切换写 `source_switch` 事件并告警；切换时重叠 K 线收盘价偏差 > 0.5% 或 1h→1d 聚合不一致 → **数据质量异常：暂停模拟成交、挂单顺延到当前小时之后（不回填旧价格）、当日信号记为 missed_signal(data_quality_fault)**；既有 K 线不被新来源覆盖（不盲目拼接）；每条信号记录实际行情来源。本环境实测：Binance 451（地区限制）、Bybit CloudFront 封锁、**OKX / Coinbase 可达**，实际来源 OKX，与 Binance 历史重叠偏差约 1 bp。
4. **离线错过的信号不伪装成当时成交**：日线收盘后超过宽限期（1 h 执行 K 线 + 3 h）才被首次处理的信号只记录 `missed_signal`（含当时应有的决定），不生成订单、不推进分配器与风险状态机；在宽限期内但错过标准执行 K 线的信号在**当前**正在形成的 1h K 线收盘后按其开盘成交并标注 `late`；下一次实时日线收盘按当时价格自然补齐仓位。历史补齐（forward_bars 预热）、回放（`forward-replay`，mode=replay，单独目录）与前瞻（mode=forward）分开保存并在每条事件中标注。
5. **三账户前瞻状态（真正前瞻，不是回测/回放）**：

| 账户 | 前瞻起点 UTC | 含成本权益 | 零成本权益 | 风险状态 | 最新信号日 | 类别 |
|---|---|---|---|---|---|---|
| 账户 A：BTC SMA200 | 2026-11-07 17:38 | 10,000.00 | 10,000.00 | NORMAL | 尚无（首个信号在 2026-11-08 00:00 UTC 收盘后） | 前瞻 |
| 账户 B：模型 B 满额配置 + 状态机 | 2026-11-07 17:38 | 10,000.00 | 10,000.00 | NORMAL | 尚无（首个信号在 2026-11-08 00:00 UTC 收盘后） | 前瞻 |
| 参考：ETH SMA200 | 2026-11-07 17:38 | 10,000.00 | 10,000.00 | NORMAL | 尚无（首个信号在 2026-11-08 00:00 UTC 收盘后） | 前瞻 |

启动至今没有任何日线收盘，因此没有信号、没有交易、没有可评价的业绩（启动时 BTC、ETH 日线均在 SMA200 上方，首个信号将在 2026-11-08 00:00 UTC 收盘后由服务生成——前提是服务在持续在线的主机上运行）。
6. 风险回撤上限仍未获用户批准；全部风险保护保留（数据过期禁止开仓、异常行情、最大敞口、回撤状态机 EMERGENCY_HALT ≠ COOLDOWN ≠ RECOVERY、状态持久化与恢复）。真实下单无实现、硬性禁用。

## 一、代码审查与保留（§一）
第八阶段模块 `forward/datafeed.py`、`forward/accounts.py` 向后兼容扩展（新增字段：mode、market_source、executed、missed_reason、late、deferred_reason、realized/unrealized、quality_fault、source_switch）；第八阶段 2026-11-07 17:38 UTC 起的事件链原样保留并继续追加（哈希链校验通过，见 `forward_logs/status.json`）。新增 `forward/service.py`、`forward/report.py`、`deploy/`、`tests/test_brando_pa_service.py`、看板页"三账户实时利润对比（第九阶段）"。

## 二、24 小时服务（§二）
`ForwardService.cycle()` 每 `forward.interval_s`（默认 600 s）执行；心跳 `health.json`（心跳时间、循环数、最近一轮摘要）；`forward-health` 返回退出码（心跳年龄 > 30 min、数据失败/过期/质量异常、运行异常、容量、哈希链任一不满足 → 1），供 systemd timer / Docker HEALTHCHECK / launchd 外部监控使用；告警写 `alerts.jsonl`，可选 webhook（只从环境变量 `BRANDO_ALERT_WEBHOOK` 读取）；`service.log` 10 MB × 5 轮转；事件链 JSONL 不轮转（只追加、约 2 KB/天/账户、保证完整性）；容量检查（剩余磁盘 ≥ 500 MB、日志目录 ≤ 2 GB）。

## 三、真实行情连通性（§三、§九）

| 来源 | 结果 | 说明 |
|---|---|---|
| binance_spot | 不可达 | 451 {
  "code": 0,
  "msg": "Service unavailable from a restricted locatio |
| okx_spot | 可达 | 200  |
| bybit_spot | 不可达 | 403 {
    error:The Amazon CloudFront distribution is configured to block  |
| coinbase_exchange | 可达 | 200  |
| binance_mirror_github | 可达 | 200  |
| coingecko_reference | 可达 | 200  |

当前 `forward_data_status.json`：来源 okx_spot，最后成功 2026-11-07 18:17:29，BTCUSDT_1h 最新收盘 2026-11-07 18:00（落后 0.3 h）; BTCUSDT_1d 最新收盘 2026-11-07 00:00（落后 18.3 h）; ETHUSDT_1h 最新收盘 2026-11-07 18:00（落后 0.3 h）; ETHUSDT_1d 最新收盘 2026-11-07 00:00（落后 18.3 h）；1h→1d 一致性 {'checked_days': 10, 'max_rel_err': 0.0, 'ok': True}；质量异常 False。

## 四、部署稳定性验证（§六）
`tests/test_brando_pa_service.py` + `tests/test_brando_pa_forward.py`（模拟交易所、模拟时钟，故障注入）：

| 要求 | 测试 |
|---|---|
| 1 断网后重连 | `test_service_disconnect_reconnect_stale_and_health`：来源连续失败 → 告警、不新增订单；恢复后补齐无缺口、下一日线正常执行 |
| 2 数据过期保护 | 同上 + `test_forward_stale_data_blocks_new_buys_and_risk_recovery`：过期 → 信号 data_fault、敞口 0、无成交 |
| 3 服务意外退出和重启 | `test_restart_consistency_...`：每一轮都新建实例从 state.json 恢复 |
| 4 订单重复执行防护 | 同上：processed_orders 无重复、成交 order_id 唯一；`test_forward_accounts_...`：同一信号日重复运行不产生重复订单/成交 |
| 5 多次重启账户权益一致性 | 同上：连续实例 vs 每轮重启实例，三账户两本账现金/批次一致 |
| 6 日线收盘时序 | 信号时间 = 日线收盘、生成时间 ≥ 收盘、成交 K 线开盘 = 收盘时刻且在该 K 线收盘后成交；`test_normalize_closed_only...` 未收盘 K 线剔除 |
| 7 跨交易所行情源切换 | `test_source_switch_logging_and_deviation_gate`：切换记录 + 偏差 0.05% 不门控、偏差 2% 门控（暂停成交、挂单顺延、missed_signal）、既有 K 线不被覆盖 |
| 8 模拟成交无负现金 | 全部 nav 事件 cash ≥ 0、持仓 ≥ 0 |
| 9 USDT 期末清仓计算 | 含成本清算 = 现金 + Σ 数量 × 价 × (1−滑点)(1−费率)；零成本清算 = 权益 |
| 10 日志持久化 | service.log 轮转文件存在；事件链校验 |
| 11 健康检查告警 | 心跳过期、数据失败、质量异常 → health 不 ok；alerts.jsonl 写入 |
| 12 不同账户数据隔离 | 篡改 A 的事件链不影响 B/C 校验；各账户独立目录与 state.json |
| 长时间运行故障注入 | 47 天、每 2 小时一轮、每轮重启，含 1 个多月"离线"后恢复：期间信号全部 missed、无回填成交、风险状态机按实际回撤进入 RISK_REDUCED |

## 五、每日 USDT 账户报告（§七）
`forward-daily-report`（服务在 UTC 日切后自动生成前一日）：`daily/YYYY-MM-DD.json/.md`，含三账户两本账的期末权益、清仓值、当日盈亏、累计净利润、已实现/未实现、最大回撤、BTC/ETH/USDT 比例、当日交易数、累计成本、与其他账户利润差额、风险状态、数据状态、服务状态；类别字段固定标注 forward。已生成 `2026-11-06`、`2026-11-07`（全部 10,000、无交易）。看板页按类别标注（前瞻 / 回测 / 回放）。

## 六、交付清单（§九）
- Mac 部署：`deploy/launchd/com.brando-pa.forward.plist`；Linux：`deploy/systemd/*.service|*.timer`、`deploy/docker/Dockerfile` + `docker-compose.yml`；环境变量模板 `deploy/.env.example`；命令 `deploy/README.md`。
- 真实行情连通性：`connectivity_results.json`（本节表）。
- 工程测试：62 项（`test_report.txt`）。
- 前瞻账户运行报告：`forward_logs/`（事件链、状态、心跳、日报、服务日志）。
- 看板：`dashboard_three_accounts.png`。
- **必须在何种主机启动**：持续在线、可访问任一交易所公开行情、NTP 对时的 Mac/Linux/VPS；用上述配置启动后，用 `forward-health` 与 `health.json` 心跳监控。云容器内的一次成功运行不代表 24 小时运行成功。


---

# 部署验收补充（2026-11-08，Claude 云容器内执行；**Mac 上尚未安装**）

## 1. 环境判定
- 当前执行环境：Linux 云容器（`uname` = Linux vm 6.18），**不是你的 Mac**。因此本轮只生成并提交 Mac 可执行安装脚本与操作步骤（`deploy/install_mac_brando_pa.sh`、`deploy/uninstall_mac_brando_pa.sh`、`scripts/brando_pa_forward_ctl.sh`、`MAC_RUNBOOK.md`），**没有、也不可能在 Mac 上完成安装**。仓库内原有 `deploy/install_mac.sh` 等属于 spot90 项目（标签 com.spot90.*），与本服务（com.brando-pa.forward）互不影响。
- Git：分支 `claude/brando-pa-phase9-deploy`，起点 bcb6645 与 origin 一致；本轮新增提交见 Git 日志。

## 2. 2026-11-08 00:00 UTC 日线的处理（只读检查 → 单轮运行）
- 只读检查（03:22 UTC）：三账户 `last_daily_ts = None`，服务最后一轮为 11-07 18:17，**该日线尚未被处理**，没有任何追补成交。
- 随后执行一轮 `forward-service --once`（03:26 UTC，含启动完整性检查）：日线收盘 00:00 距处理时刻 3 h 27 min，在宽限期（1 h 执行 K 线 + 3 h）内 → 按规则记为**实时但迟到（late）**信号：BTC 日线收盘 75868.0 > SMA200 73,240.19、ETH 2,240.10 > 2,157.24，三账户均为 BUY；订单 `exec_after = 03:00`（在**当前**正在形成的 03:00 1h K 线收盘后按其开盘价成交，**不回填 00:00 的价格**）；若 04:00–07:00 之间没有任何一轮运行（云容器不常驻），这些挂单将在下一轮被标记 `order_expired`、不成交，由下一次实时日线收盘重新决策。

| 账户 | 事件 | 信号 | 目标权重 | 订单（账本, 标的, 方向, USDT, exec_after, late） |
|---|---|---|---|---|
| A_BTC_SMA200 | signal | BUY | {'BTCUSDT': 1.0, 'ETHUSDT': 0.0, 'USDT': 0.0} | [('ideal', 'BTCUSDT', 'BUY', 9990, '2026-11-08 03:00:00+00:00', True), ('cost', 'BTCUSDT', 'BUY', 9990, '2026-11-08 03:00:00+00:00', True)] |
| B_modelB_state_machine | signal | BUY | {'BTCUSDT': 0.5, 'ETHUSDT': 0.5, 'USDT': 0.0} | [('ideal', 'BTCUSDT', 'BUY', 4995, '2026-11-08 03:00:00+00:00', True), ('ideal', 'ETHUSDT', 'BUY', 4995, '2026-11-08 03:00:00+00:00', True), ('cost', 'BTCUSDT', 'BUY', 4995, '2026-11-08 03:00:00+00:00', True), ('cost', 'ETHUSDT', 'BUY', 4995, '2026-11-08 03:00:00+00:00', True)] |
| REF_ETH_SMA200 | signal | BUY | {'BTCUSDT': 0.0, 'ETHUSDT': 1.0, 'USDT': 0.0} | [('ideal', 'ETHUSDT', 'BUY', 9990, '2026-11-08 03:00:00+00:00', True), ('cost', 'ETHUSDT', 'BUY', 9990, '2026-11-08 03:00:00+00:00', True)] |

启动完整性检查 `startup_check.json`：ok = True，哈希链全部通过，三账户状态可载入、无负现金，与 OKX 服务器时钟偏差 0.08 s。

## 3. 行情来源配置与跨来源审计（§4）
- 配置 `forward.source_priority = [okx_spot, coinbase_exchange, binance_spot, bybit_spot, static_klines_mirror]`：OKX 主、Coinbase 备；Binance（451 地区限制）与 Bybit（CloudFront 封锁）保留为合法来源但不绕过限制；GitHub 镜像为最后回退。
- 实时审计 `cross_source_audit_1h.json` / `_1d.json`（2026-11-08 03:25 UTC，48 h 1h 与 30 日 1d）：OKX BTC-USDT / ETH-USDT 47 根已收盘 1h、无重复、无缺口、整点对齐、OHLC 一致、滞后 0.43 h；Coinbase 45/46 根（BTC-USDT 低流动性小时有 1–2 个空缺 K 线，已记录）；镜像滞后 17 h。两两收盘价最大偏差（%）：{'coinbase_exchange vs okx_spot BTCUSDT': 0.228, 'coinbase_exchange vs okx_spot ETHUSDT': 0.179, 'coinbase_exchange vs static_klines_mirror BTCUSDT': 0.227, 'coinbase_exchange vs static_klines_mirror ETHUSDT': 0.118, 'okx_spot vs static_klines_mirror BTCUSDT': 0.016, 'okx_spot vs static_klines_mirror ETHUSDT': 0.013}；1d 全部 < 0.13%。全部低于 0.5% 门控。
- 当前 `forward_data_status.json`：来源 okx_spot，最后成功 2026-11-08 03:26:52，1h 最新收盘 2026-11-08 03:00，1h→1d 一致性 {'checked_days': 10, 'max_rel_err': 5.5373759693665634e-05, 'ok': True}，质量异常 False。

## 4. 新增保护（§5、§6、§10）
- 单实例锁 `service.lock`（flock）：同一目录第二个实例立即退出（含 `--once`）。
- 启动完整性检查：事件链、状态文件与账本一致、行情新鲜度、时钟偏差 → `startup_check.json`，失败即告警。
- 离线挂单过期：执行 K 线收盘超过宽限期仍未成交 → `order_expired`，不回填；离线期间日线 → `missed_signal`。
- 健康检查新增：最新 K 线年龄、最近成功轮询、进程存活（pid）、数据源、账户净值与风险状态、最新日志时间、连续失败次数（≥ 3 告警）、时钟偏差（启动检查）。`--once` 模式下进程已退出，`process_alive=false` 属正常。
- 持久化目录与重启不清空：见 `MAC_RUNBOOK.md`。

## 5. 72 小时真实时间验收（§11）
状态：**未开始**。`forward-acceptance` 工具只统计真实时间推进的心跳与记录，不足 72 小时固定为"进行中"，云容器内的记录（11-07 17:38 起共 3 轮）只是基线，不构成验收。Mac 上启动后请按 `MAC_RUNBOOK.md` 的协议每日记录到 `acceptance_log/`。**本报告不宣称验收成功。**

## 6. 测试（§13）
全部原有测试 + 新增 `tests/test_brando_pa_deploy.py`（单实例锁与启动检查、健康字段与连续失败、离线挂单过期不回填、来源优先级与跨来源审计、72h 验收"未完成"语义）结果见 `test_report.txt`。

## 7. 风险与真实交易（§15）
最大回撤上限未获批准；全部风险保护保留；真实下单无实现；安装脚本在检测到 `live_trading: true` 时拒绝安装。
