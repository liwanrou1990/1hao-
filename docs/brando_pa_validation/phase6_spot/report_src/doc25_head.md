# 25 · Brando Price Action 第六阶段：现货趋势基准审计、交易频率控制、回撤熔断修复与前瞻模拟验证

生成时间：{GEN_TS} UTC · 分支 `claude/brando-pa-phase6-spot` · 基准锁定 `docs/brando_pa_validation/phase6_spot/BASELINE_LOCK.md`（第五阶段提交 e63b67c）· 预先登记 `docs/brando_pa_validation/phase6_spot/preregistration.md`（含登记后澄清一条，已披露）· 测试 {N_TESTS} 项通过（前五阶段 46 项 + 本阶段 6 项）

> 只做多现货、无杠杆、无做空、无借币、无合约；BUY / HOLD / REDUCE / EXIT；只用已收盘日线；下一根执行 K 线开盘成交；现金与持仓不为负。零参数自由度：策略 {A, B} × 熔断 {原规则, 状态机, 无} × 标的 {BTC, ETH, 等权} × 6 窗口 × 2 成本一次性全部运行并全部报告，没有挑选。Brando 价格行为模块默认关闭。真实交易接口保持禁用。

## 0. 结论先行

{CONCLUSIONS}

## 一、本阶段做了什么（对应任务 §一～§十）

| 任务 | 实现 | 位置 |
|---|---|---|
| §一 SMA200 基准审计 | 第五阶段 `sma200_benchmark`、纯 numpy 日线级独立复算、三层引擎三套实现逐根对比；预热 / 成交时点 / 账本恒等式 / 手续费 / 持仓段回撤 / 截断一致性 / “误用未收盘日线会怎样” | `src/brando_pa/trendsys/audit.py`，CLI `sma200-audit` |
| §二 三层简化系统 | 趋势层 `trend_layer.py`（已收盘日线 SMA200 + 可选连续收盘确认）· 仓位层（`engine.py` 内：目标 = 权重 × 风险上限 × 权益，BUY/HOLD/REDUCE/EXIT，下一开盘成交）· 风险层 `risk_layer.py`（三种模式）；Brando 信号 `trendsys.brando_signals.enabled: false` | `src/brando_pa/trendsys/` |
| §三 策略 A/B | A = 第五阶段规则原样（对照）；B = 连续 3 个日线收盘确认（唯一预登记方案；±1 日只作事后敏感性报告） | `config/brando_pa.yaml: trendsys.trend` |
| §四 熔断状态机 | NORMAL / RISK_REDUCED / COOLDOWN / RECOVERY / EMERGENCY_HALT，有限冷静期（30/60/90 日）、恢复期 60 日、恢复期高水位重置、恢复期再触发、熔断计数 180 日清零、崩盘退出、数据失败安全态；与原规则（永久）和无熔断三方对照 | `risk_layer.py`，测试 `test_circuit_breaker_is_finite_and_resets_hwm` |
| §五 交易频率 | 每次卖出：触发、持仓时长、盈亏、是否再买、间隔、再买价 vs 卖出价、卖后标的最低/最高；保护性 / 来回磨损 / 中性分类；第五阶段模型 B 战术袖子与原 Brando 的历史产物只读分析 | `frequency.py` |
| §六 配置回测 | BTC、ETH、等权 + 买入持有 BTC / ETH / 等权 + USDT，同窗口、同资金、同成交规则；零成本与 10 bps + 5 bps | `phase6.py` |
| §七 数据标签 | train / valid / 已被观察的测试窗 / 真正未使用（3 个月）/ 长历史 4h；无新数据 → 历史稳健性 | `trendsys.window_labels` |
| §八 前瞻模拟 | 只读行情、新鲜度检查、缺口补齐、信号与数据时间戳、下一开盘模拟成交与挂单恢复、每日净值、熔断状态日志、失败记录；JSONL 只追加 + sha256 哈希链 | `paper.py`，CLI `trendsys-paper`（`--replay` 为重放，明确不是前瞻） |
| §九 验收 | 七条预登记判据逐项 pass/fail，自动写入 `acceptance` | `phase6.py: acceptance()` |
| §十 报告 | 本文 + `docs/brando_pa_validation/phase6_spot/`（结果 JSON/MD、图、成交、状态切换、风险日志、审计 CSV、前瞻与重放记录、看板截图） | — |

## 二、数据与窗口（§七）

| 窗口 | 区间 | 执行周期 | 标签 |
|---|---|---|---|
| train | 2022-04-01 → 2024-06-30 | 1h | 训练（已观察） |
| valid | 2024-07-01 → 2025-06-30 | 1h | 验证（已观察） |
| holdout | 2025-07-01 → 2026-11-07 | 1h | **已被观察的测试窗**（第四、五阶段保留窗）——不是样本外 |
| locked_unused_1h | 2022-01-01 → 2022-03-31 | 1h | 真正未使用（3 个月，SMA200 下方几乎全程空仓，无统计力） |
| full_1h | 2022-04-01 → 2026-11-07 | 1h | 主报告窗口（已观察） |
| full_4h | 2018-06-01 → 2026-11-06 | 4h | 长历史（已观察） |

数据：Binance 现货 K 线（只读缓存源）；本阶段开始时 1h 数据截至 2026-11-06 09:00 UTC（第五阶段结果的截止，审计复现用此截止），前瞻模拟首次运行时补齐到 2026-11-07 10:00 UTC，配置矩阵随后在补齐后的数据上重新运行（因此 full_1h / holdout 的末尾比第五阶段多一天）。**没有任何新历史数据**；真正的新数据只来自前瞻模拟记录（见 §四）。

