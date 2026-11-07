## 三、风险层设计说明（§四）

状态机每个日线收盘评估一次，动作在下一根执行 K 线开盘执行；全部阈值在 `trendsys.risk.state_machine` 中预登记：

| 状态 | 最大仓位 | 进入 | 离开 |
|---|---|---|---|
| NORMAL | 100% | 初始；RISK_REDUCED 且 DD > −5%；RECOVERY 满 60 日未再触发 | DD ≤ −10% → RISK_REDUCED；DD ≤ −20% 或持仓时单日 ≤ −15% → EMERGENCY_HALT |
| RISK_REDUCED | 50%（超出部分下一开盘减仓一次） | DD ≤ −10% | DD > −5% → NORMAL；DD ≤ −20% → EMERGENCY_HALT |
| EMERGENCY_HALT | 0%（全部卖出） | 见上；RECOVERY 中相对恢复期高点 ≤ −10% | 下一日线收盘 → COOLDOWN，熔断计数 +1 |
| COOLDOWN | 0% | — | 30 × min(计数, 3) 日后 → RECOVERY |
| RECOVERY | 50% | 冷静期结束，**风险参考高水位重置为当时权益** | 60 日未再触发 → NORMAL |

数据失败安全态：最新 K 线过期（> 3 个周期）或缺口 → 禁止新买入、不强制卖出、记录；恢复后继续。原规则（第五阶段）：全程高水位 DD ≥ 20% 暂停买入、收窄到 10% 才恢复——现货空仓时权益不变、回撤永不收窄，所以是永久停机（本阶段逐根证实：BTC 最长禁买 855 天、ETH 1,177 天，且期末仍在禁买）。

## 四、前瞻模拟（§八）

- 首次真实运行：2026-11-07（UTC），策略 A + 状态机，BTC/ETH 等权，含成本（10 bps + 5 bps）。运行时检测到 1h/1d 数据过期 → 通过只读 ingest 补齐到 2026-11-07 10:00 → 新鲜度通过 → 2026-11-07 00:00 日线收盘信号：BTC、ETH 均在 SMA200 上方（BTC 日线收盘 vs SMA200、ETH 2238.67 vs 2157.67）→ 各买入 50%，在 00:00 开盘的 1h K 线成交（BTC 0.0658、ETH 2.228），现金余 5 USDT，风险状态 NORMAL。记录位于 `docs/brando_pa_validation/phase6_spot/paper_forward/`（`signals / orders / fills / nav / risk_state.jsonl`，每行带 `prev_hash` / `hash`，`trendsys-paper --verify` 可校验）。
- 重放演示（**不是前瞻**，只为验证机制）：2025-07-01 → 2026-11-07 逐日推进、严格只用当日之前已收盘的 K 线，挂单在下一根执行 K 线出现后补成交，状态机经历 2025-11-11 熔断 → 冷静 → 恢复 → NORMAL 全过程；哈希链全部通过。位于 `paper_replay_2025H2/`。
- 真实交易：无实现；`TrendSysPaper(live=True)` 直接抛错；原三重闸门保持关闭。

## 五、数据完整性与过拟合限制

- 全部 1h 与 4h 历史窗口在前五阶段已被观察；本阶段结论是历史稳健性结论，不是样本外证据。真正未使用的 2022-01→03 只有 3 个月且几乎全程在 SMA200 下方（全部策略空仓或一次小亏），无统计力。
- 自由度：策略 B 的确认日数、状态机全部阈值、分类阈值（10% / 30 日）在预登记中写定，未搜索；±1 日敏感性为事后报告（2 日：BTC +145% / ETH +70%；4 日：+148% / +21%，均劣于 A），不用于选择。
- 登记后的一条澄清（崩盘退出只在持仓时触发）在完整矩阵运行前写入并披露；它不改变任何阈值。
- 零成本是理想条件；含成本轮次用固定 10 bps + 5 bps，无真实滑点历史。
- 月度捕获率、Sharpe 在短窗口（holdout 16 个月、valid 12 个月）噪声很大；4h 长历史的 2018–2021 段包含数据源早期波动。

## 六、工程验收与可复现命令

```bash
export PYTHONPATH=src
pytest tests/test_brando_pa_*.py -q                       # {N_TESTS} 项（前五阶段 46 + 本阶段 6）
python -m brando_pa.cli sma200-audit                      # 三套实现复现 SMA200 基准 + 七项核对
python -m brando_pa.cli phase6 --workers 4                # 2 策略 × 3 熔断 × 3 标的 × 6 窗口 × 2 成本 + 敏感性 + 频率分析 + 验收 → reports_out/brando_pa/phase6_spot/
python -m brando_pa.cli trendsys-signal                   # 最新已收盘日线下 A / B 的状态与动作
python -m brando_pa.cli trendsys-paper                    # 前瞻模拟一次（只读行情，追加记录）
python -m brando_pa.cli trendsys-paper --verify           # 校验记录哈希链
python -m brando_pa.cli trendsys-paper --replay --start 2025-07-01 --log-dir reports_out/brando_pa/trendsys_paper_replay_2025H2   # 重放（标注为重放）
python -m brando_pa.cli dashboard --port 8502             # “三层趋势系统（第六阶段）”页
```

真实交易接口三重闸门 + 无实现，保持禁用。前五阶段代码、数据、报告、测试与历史回测结果未改动（`data_store` 只追加了 2026-11-06 → 11-07 的新 K 线）。
