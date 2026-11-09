# 第十三阶段策略冻结清单 · 2026-11-09 02:40:09 UTC · commit `ddef2f611064a1d8191169740d46e7393a9d29da` · 分支 `claude/brando-pa-phase13-forward`

| 影子账户 | 类型 | 规则 | 风险 | 备注 |
|---|---|---|---|---|
| A_B2_state_machine | trend | A：原 B2 状态机（= Mac 账户 B_modelB_state_machine 同规则同成本） | state_machine |  |
| B_BTC_SMA200 | trend | B：BTC SMA200（= Mac 账户 A_BTC_SMA200 同规则） | none |  |
| C_BUYHOLD_BTC | buyhold | C：BTC 买入持有（首轮买入，不再交易） | none | alloc {"drift_band": 10.0} |
| D_BUYHOLD_ETH | buyhold | D：ETH 买入持有 | none | alloc {"drift_band": 10.0} |
| E_BUYHOLD_5050 | buyhold | E：BTC/ETH 50:50 买入持有（不再平衡） | none | alloc {"drift_band": 10.0} |
| F_B2_vol_trail | trend | F：实验对照 = B2 + 3 ATR 波动跟踪减仓（Phase 12 E_B2_vol_trail 规则） | state_machine | vol_trail {"atr_mult": 3.0, "atr_n": 14, "swing_k": 3, "reduce_to": 0.5} |

- 趋势规则：{'signal': '日线收盘 > SMA200 → ON（confirm_days=1）', 'sma': 200, 'data': '已收盘 UTC 日线；日线收盘后下一轮服务（600 s）决策'}
- 状态机参数：{'reduce_dd': 0.1, 'reduce_cap': 0.5, 'recover_dd': 0.05, 'halt_dd': 0.2, 'cooldown_days_per_halt': 30, 'cooldown_max_multiplier': 3, 'recovery_days': 60, 'recovery_retrigger_dd': 0.1, 'halt_count_reset_days': 180, 'crash_exit_daily_drop': 0.15}
- 分配器：{'drift_band': 0.1, 'min_rebalance_usdt': 10, 'min_rebalance_frac': 0.005, 'cash_buffer': 0.001}
- 成本：{'cost_ledger_one_side_bps': {'fee': 10, 'slippage': 5, 'total': 15}, 'ideal_ledger': '0 费用（仅对照）', 'convention': '单侧；买入按 Ask×(1+滑点)、卖出按 Bid×(1−滑点)，手续费按成交额；清仓估值再扣一次卖出成本'}
- 成交规则：{'decision': '日线收盘后首轮运行时刻 = decision_time', 'exec_after': '决策所在小时 + 1 h', 'price': '处理时刻实时 OKX/Coinbase Ask(买)/Bid(卖)，年龄 ≤ 120 s；否则最新已收盘 1h 收盘价并标 estimated（≤ 4200 s）；否则 fill_paused', 'late': '> exec_after + 1h → filled_late', 'expired': '> exec_after + 3 h → expired', 'missed': '日线收盘后 > 宽限期 + 1h 才首次处理 → missed_signal（不回填）'}
- 数据规则：{'sources_priority': ['okx_spot', 'coinbase_exchange', 'binance_spot', 'bybit_spot', 'static_klines_mirror'], 'closed_bars_only': True, 'cross_source_max_rel_diff': 0.005, 'store': 'data_store/brando_pa/phase13_bars（独立；首次从 forward_bars 只读复制预热）'}
- 隔离：{'log_dir': 'reports_out/brando_pa/phase13_forward', 'lock': 'reports_out/brando_pa/phase13_forward/service.lock', 'never_touch': ['reports_out/brando_pa/phase8_forward/**', 'config/brando_pa.yaml', 'launchd com.brando-pa.forward'], 'real_trading': 'disabled (no implementation)'}
- 起点规则：各账户 forward_start_utc = 影子服务首次成功运行时刻（≥ 2026-11-09）；此前历史只用于 SMA200/ATR 预热，不产生任何信号或成交

## 历史 B2 vs Mac B vs 影子 A

| 项 | 历史 B2（Phase 10/12 日线引擎） | Mac B_modelB_state_machine | 影子 A | 是否相同 |
|---|---|---|---|---|
| 分配规则 | ON 资产等权（both 50/50，单一 100%） | B_full_single：both 50/50，单一 100% | 同 Mac B | 规则相同 |
| 再平衡 | 仅持有集合 / 限仓变化时 | 目标变化 或 权重漂移 ≥ 10 个百分点 | 同 Mac B | **不同**（Mac 多了漂移再平衡） |
| 风险状态机 | {'reduce_dd': 0.1, 'reduce_cap': 0.5, 'recover_dd': 0.05, 'halt_dd': 0.2, 'cooldown_days_per_halt': 30, 'cooldown_max_multiplier': 3, 'recovery_days': 60, 'recovery_retrigger_dd': 0.1, 'halt_count_reset_days': 180, 'crash_exit_daily_drop': 0.15} | 同 trendsys.risk.state_machine | 同 | 相同 |
| 执行时点与价格 | 次日开盘价 | 决策后下一整点之后实时 Ask/Bid（或估算的已收盘 1h 收盘） | 同 Mac B | **不同** |
| 成本（单侧） | 研究报告主口径 25 bps（亦有 15 / 0） | cost 账本 15 bps（10 费 + 5 滑）+ ideal 0 | 同 Mac B（15 bps） | **不同**（主口径 25 vs 15） |
| 行情数据 | Binance 现货日线镜像 2017-08 起 | OKX 现货实时（预热自同一镜像） | 同 Mac B（独立仓） | 来源不同（交叉比对偏差 ≈ 0.5 bps） |
| 信号 | close > SMA200，confirm 1 | 同 | 同 | 相同 |

**结论**：历史研究 B2 与 Mac 运行 B 账户：信号与风险规则相同，但再平衡、执行时点/价格、成本主口径不同 → 报告中分别标记为“历史 B2（日线研究引擎）”与“前瞻 B2（Mac/影子前瞻引擎）”，不得混为一谈。影子 A 与 Mac B 规则与成本完全相同（同一引擎代码），差别只在起点时刻与独立数据仓。