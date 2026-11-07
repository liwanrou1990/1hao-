# 27 · Brando Price Action 第八阶段：真实行情接入、前瞻模拟稳定性与 USDT 利润验证

生成时间：2026-11-07T17:43 UTC · 分支 `claude/brando-pa-phase8-forward` · 基准锁定 `docs/brando_pa_validation/phase8_forward/BASELINE_LOCK.md`（第七阶段提交 ffa5366）· 测试 59 项通过（前七阶段 55 + 本阶段 4）· **本阶段没有新增策略、没有调参**。

## 0. 结论先行

1. **真实行情已接通，前瞻账户已在真实已收盘行情驱动下运行。** 初始云环境的网络白名单拦截了全部交易所接口（20 个数据源探测，见 `connectivity_diagnosis.md`）；经用户批准为本仓库创建专用环境并放开交易所公开行情主机后：Binance 返回 HTTP 451（服务商按地区限制，不绕过）、Bybit 被 CloudFront 国家封锁，**OKX 与 Coinbase Exchange 公开现货 K 线可达**。数据服务按 Binance → OKX → Bybit → Coinbase → Binance 镜像自动回退，本环境实际来源为 `okx_spot`。
2. **第七阶段先前的"数据过期"根因是镜像来源延迟（当时 1h 落后约 4–7 小时，不是三个星期）**；现在实时来源 1h 落后 0.69 小时、1d 为最新已收盘日线，`stale = False`。与 Binance 历史重叠 K 线收盘价最大相对差 0.5 bp（1d）；1h→1d 聚合一致性 {'checked_days': 10, 'max_rel_err': 0.0, 'ok': True}；CoinGecko 非交易所参考价偏离 0.076%。
3. **三个独立 Paper 账户于 2026-11-07 17:38 UTC 启动**（各 10,000 USDT，两本账：零成本 / 10 bps + 5 bps）。按前瞻纪律，只处理启动之后收盘的日线信号：首个信号将在 2026-11-08 00:00 UTC 日线收盘后产生，并在 00:00 那根 1h K 线收盘后按其开盘价模拟成交。当前三账户均为 100% USDT、风险状态 NORMAL——**这是启动时刻的真实状态，不是历史回测**。
4. **第七阶段工程回归通过**：BTC SMA200 33,134.59、模型 B + 状态机 30,367.64、模型 A + 状态机 29,654.83 USDT，与第七阶段 JSON 逐分一致（`phase7_regression.json`）。
5. **独立回放（mode=replay，2025-09-01 → 2026-11-07，不是前瞻）**验证了全部机制在 14 个月真实历史上的连续运行：1766 / 1834 条事件、哈希链全部通过、无重复成交；同期买入持有 BTC -29.9%、ETH -49.0%。回放结果只说明机制可用，不是前瞻业绩。
6. **是否满足连续前瞻模拟运行条件**：**在本云会话内不满足"持续"**——会话容器会在空闲后回收，没有常驻进程与定时器，且 Binance 在本地区不可用；**在具备合法访问条件的联网机器上满足**：`scripts/brando_pa_forward.sh` + cron/systemd 每小时运行一次即可（`forward-data` → `forward-run` → `forward-status`），数据服务自动重连、补齐、回退来源，账户状态持久化、重启恢复、订单去重、日志哈希链均有测试覆盖。因此本阶段标记为：**机制完成并经真实行情验证；持续运行需要用户在联网机器上部署**（命令见 §四）。
7. 风险回撤上限仍是用户待确认的外部参数（占位 30%）；真实交易 API 无实现、保持禁用。

## 一、真实行情连通性诊断（§一）

见 `docs/brando_pa_validation/phase8_forward/connectivity_diagnosis.md` 与 `connectivity_probe.json`。摘要：

| 阶段 | 结果 |
|---|---|
| 初始环境（Default 白名单） | 全部交易所主机：代理 403（环境白名单拒绝）；仅 CoinGecko / DefiLlama / GitHub 镜像可达 |
| 专用环境（用户批准） | Binance 451（地区限制）；Bybit CloudFront 国家封锁；**OKX、Coinbase Exchange 可达**；镜像可达（延迟数小时） |
| 实际来源 | `okx_spot`（含 confirm 标志，只取已收盘）；Coinbase 为第二实时来源；镜像为最后回退与历史预热 |

## 二、数据更新机制（§一、§五）

见 `data_update_and_recovery.md`。要点：独立前瞻行情仓 `forward_bars`（历史只作预热，实时只追加，每根 K 线带 source）；只保留已收盘 K 线；UTC；去重/乱序/缺口/未收盘统计；1h→1d 一致性；跨来源重叠对照；重试 + 退避 + 来源回退；状态文件与哈希链事件；全部失败不伪造价格。

当前状态（`forward_data_status.json`）：来源 okx_spot，最后成功 2026-11-07 17:41:28 UTC；BTCUSDT_1h 最新收盘 2026-11-07 17:00（落后 0.7 h）；BTCUSDT_1d 最新收盘 2026-11-07 00:00（落后 17.7 h）；ETHUSDT_1h 最新收盘 2026-11-07 17:00（落后 0.7 h）；ETHUSDT_1d 最新收盘 2026-11-07 00:00（落后 17.7 h）。

## 三、前瞻账户（§二～§四）

| 账户 | 前瞻起点 UTC | 含成本权益 | 零成本权益 | 风险状态 | 最后信号日 |
|---|---|---|---|---|---|
| 账户 A：BTC SMA200 | 2026-11-07 17:38 | 10,000 | 10,000 | NORMAL | 尚无（首个日线收盘在 2026-11-08 00:00 UTC） |
| 账户 B：模型 B 满额配置 + 状态机 | 2026-11-07 17:38 | 10,000 | 10,000 | NORMAL | 尚无（首个日线收盘在 2026-11-08 00:00 UTC） |
| 参考：ETH SMA200 | 2026-11-07 17:38 | 10,000 | 10,000 | NORMAL | 尚无（首个日线收盘在 2026-11-08 00:00 UTC） |

记录字段（每条事件）：市场价格与数据时间戳（market）、SMA200 / 日线收盘 / 趋势方向（indicators）、买卖信号与原因、目标与当前权重（signal）、订单（order）、模拟成交价（fill，= 下一根已收盘 1h 开盘 ×(1±滑点)）、BTC/ETH 数量、USDT 现金、净资产、累计利润、最大回撤（nav，两本账）、风险状态（risk）。事件日志 `events.jsonl` 追加 + sha256 哈希链；`forward-status` 校验。

## 四、启动命令与部署（§八）

```bash
export PYTHONPATH=src
python -m brando_pa.cli forward-data                    # 行情更新（自动来源回退；只保留已收盘 K 线）
python -m brando_pa.cli forward-run                     # 三个前瞻账户推进一次（含行情更新）
python -m brando_pa.cli forward-run --loop --interval 600   # 常驻
bash scripts/brando_pa_forward.sh                       # cron 包装：7 * * * * cd /path/1hao- && bash scripts/brando_pa_forward.sh >> reports_out/brando_pa/phase8_forward/cron.log 2>&1
python -m brando_pa.cli forward-status                  # 状态摘要 + 哈希链校验
python -m brando_pa.cli dashboard --port 8502           # "前瞻模拟实时榜（第八阶段）"页
```

## 五、独立回放（不是前瞻）

`forward-replay --start 2025-09-01`（mode=replay，单独目录 `replay_2025Q4/`）：

| 账户 | 零成本权益 | 含成本权益 | 含成本最大回撤 | 期末风险状态 |
|---|---|---|---|---|
| 账户 A：BTC SMA200 | 10,097 | 9,962 | -19.9% | NORMAL |
| 账户 B：模型 B 满额配置 + 状态机 | 9,342 | 9,238 | -20.3% | RISK_REDUCED |
| 参考：ETH SMA200 | 6,923 | 6,830 | -37.4% | NORMAL |

同期买入持有 BTC -29.9%、ETH -49.0%。该区间在前几阶段已被观察，这里只验证机制连续运行（挂单补成交、去重、状态机切换、哈希链），**不得作为前瞻验证结果**。

## 六、测试报告（§七）

新增 `tests/test_brando_pa_forward.py`（4 个测试函数覆盖 10 项要求）：K 线收盘确认与去重/乱序（`test_normalize_closed_only_dedupe_order_and_consistency`）、断线重连与补齐、来源回退、全部失败不伪造（`test_datafeed_reconnect_backfill_and_source_fallback`）、历史预热与前瞻分离、重启恢复、重复订单与重复成交去重、账户 USDT 计算、BTC/ETH 组合净值、日志不可回写（`test_forward_accounts_warmup_separation_restart_dedupe_and_usdt`）、数据过期禁止开仓、紧急停机≠冷却、状态机快照恢复（`test_forward_stale_data_blocks_new_buys_and_risk_recovery`）。全套 59 项通过（见提交信息与 `test_report.txt`）。第七阶段回归：`phase7_regression.json` 三项逐分一致。

## 七、自动恢复与异常处理

见 `data_update_and_recovery.md`：网络断开→重试/回退/补齐；数据过期或异常→禁止开仓、不强制卖出、如实记录；进程重启→state.json 恢复；挂单→下一根已收盘 1h 补成交；订单号去重；风险状态机 EMERGENCY_HALT / COOLDOWN / RECOVERY 分离且可持久化。

## 八、限制

- 本云环境无法常驻，且 Binance 对本地区限制；持续前瞻需用户在联网机器部署。来源切换（Binance 历史 → OKX 实时）在重叠区间差约 1 bp，已记录。
- 模拟成交 = 下一根已收盘 1h 开盘价 ±滑点，不是交易所保证成交；费用为压力假设。
- 前瞻记录刚开始，没有任何可评价的业绩；评价必须等待足够长的真实时间推进，不得用回测或回放替代。
