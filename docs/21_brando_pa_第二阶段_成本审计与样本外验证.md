# 21 · Brando Price Action 引擎第二阶段：真实成本审计、执行机制优化、严格样本外验证与模拟盘稳定性

生成时间：2026-11-07T07:56 UTC · 分支 `claude/brando-pa-phase2` · 基准快照 `docs/brando_pa_validation/baseline_phase1/`（第一阶段提交 6f196a2）· 第二阶段结果 `docs/brando_pa_validation/phase2/` · 测试 31 项通过

> 本阶段的目标不是让回测数字变漂亮，而是用真实数据和严格测试判断哪些规则能稳定复现正期望。所有数字含手续费、滑点、资金费（无数据区间按保守假设）与保守成交规则；**没有删除任何亏损交易、没有降低成本假设、没有放宽硬性风险限制、没有使用未来数据**。仓库内仍然没有任何真实下单代码路径。

## 0. 结论先行

1. **严格验证后，没有任何策略/周期/方向组合达到正期望的统计标准。** Strategy Evaluator 对 7 组 Walk-Forward（1h / 15m / 4h 基线、1h / 15m 环境启停、1h / 15m 多周期对齐）× 3 策略 × 标的 × 方向共 156 项样本外组合逐一打分：**合格（可进模拟盘）0 项，仅研究 0 项，不合格 156 项**。最接近的是 1h 策略 A（突破-回踩）：六折样本外 165 笔、ExpR +0.10R、PF 1.18、净收益 +8.6%、回撤 −10.7%，但均值 R 的 95% 置信区间为 [−0.15, +0.35]（含 0），六折中两折亏损（2024-10→2025-04 −0.15R、2025-04→2025-10 −0.41R），默认参数 ±20% 扰动下的全程 ExpR 只有 −0.02～+0.03。这不是“能稳定复现的正期望”，而是在噪声范围内的结果。
2. **第一阶段 15m 的 +6% 是样本选择效应。** 15m 只有 2025–2026 下跌/震荡数据；Walk-Forward 三折样本外只剩 10 笔（ExpR +0.35，CI [−0.36, +1.06]）。第一阶段结果中 ETH 空头贡献全部利润（短 40 笔 +655 USDT，多 27 笔 −52 USDT）；在 1h 的 4.5 年样本上同样的 ALL 组合是 291 笔、PF 0.93、−6%，4h 的 8 年样本是 PF 1.01、+0.7%。ETH 空头的优势只在历史下跌阶段出现（1h 全样本 short 226 笔 ExpR 0.00；4h 全样本 short 284 笔 ExpR −0.03），不是可持续的方向性优势。
3. **限价入场在所有周期、所有策略上都更差。** 1h ALL：市价 291 笔 ExpR −0.04 → 限价 232 笔 ExpR −0.22；15m ALL：+0.18 → −0.01。原因是逆向选择：能回踩到挂单价的信号本身就是弱信号，而 127 张未成交限价单若按市价入场，假设净盈亏合计 +2,346 USDT（平均 +0.52R）。手续费节省（1h ALL 470 → 308 USDT）远小于机会损失。限价成交模型已偏向乐观（穿越 0.05 ATR 即成交），真实排队更差。**结论：Brando 式“挂在关键位等回踩”在 BTC/ETH 上不成立，市价确认入场是更优选择。**
4. **多周期对齐（4h 结构 + 日线环境同向）样本内好看、样本外样本不足。** 1h ALL 对齐：47 笔、PF 1.43、ExpR +0.21（样本内）；策略 A 对齐的六折样本外只剩 27 笔、ExpR +0.14、CI [−0.58, +0.86]。它把 2242 个候选砍到 56 个（2.5%），每年约 10 笔，无法形成统计证据，也无法支撑组合。zone_limit（对齐 + 区域限价）继承了限价的全部缺点（1h 38 笔 ExpR −0.23）。
5. **退出机制没有一项能把负期望变正。** 1h 策略 A 七种退出方案的 ExpR 在 −0.08（取消时间止损）到 +0.015（取消保本移动）之间；B、C 在任何退出方案下都为负。分批止盈把胜率抬到 50% 以上但 PF 下降（1h ALL 0.93 → 0.86）；ATR 跟踪、结构退出、固定盈亏比都与基线无显著差别。退出参数（保本 R 数）进入 Walk-Forward 网格后，验证窗多数折退回默认值，说明训练窗选出的退出参数不具备前瞻性。
6. **94%–99% 候选被拒的原因已量化，且三套策略被拒的瓶颈不同。** 1h 漏斗：候选 8163 → 趋势过滤后 7346 → 价格确认后 3059 → 成本检查后 1507 → 盈亏比检查后 403 → 风险检查后 333 → 有效订单 324 → 成交 291。策略 A 的结构止损很宽（1h 平均 2.8%–3.3% ≈ 3.6–4.2 ATR；15m 平均 2.1%–2.7% ≈ 6–7.6 ATR，因为止损挂在 1W/1D 区域另一侧），成本/止损风险只有 10%–21%，它主要被“止损距离 ≤ 4 ATR”与成交量条件挡住；策略 B（1h 止损 1.3%–1.8%，15m 0.55%–0.9%）与 C（1h 0.9%–1.2%，15m 0.4%–0.6%）的止损贴着执行周期噪声，往返成本 35–44 bps 占止损风险 23%–55%（1h）到 44%–114%（15m），成本闸门是它们的主要拒绝原因。`cost_to_risk` 的分母经审计确认是**初始结构止损距离**，与保本/移动止损无关，不存在状态混淆；它把资金费按最长持仓期估算（1h 持 96 小时 = 12 个结算点）属于保守上界。**关键反证**：把资金费假设降到样本实测均值（0.00004/8h）后，1h 策略 A 从 243 笔增加到 303 笔，ExpR 却从 +0.002 降到 −0.038 —— 多放进来的 60 笔整体是亏的。放宽成本闸门不会制造收益，只会放进更多低质量信号；关闭过滤（第一阶段已测）亏损 12%–99%。
7. **成本敏感性：这套打法对滑点极其敏感。** 1h 策略 A 样本外 ExpR +0.10 在手续费 ×2 时降到 +0.07、滑点 ×2 降到 +0.06、滑点 ×3 降到 +0.02、全部成本 ×2 降到 +0.02；盈亏平衡费用倍数 2.4。实验层面把滑点从 5 bps 提到 10 bps（止损 20 bps）后 1h ALL 从 291 笔 ExpR −0.04 变成 84 笔 ExpR −0.18。滑点合计（1h ALL 672 USDT）已超过手续费（470 USDT），而滑点是本研究中最不可验证的假设（无逐笔/盘口数据）。
8. **市场环境结论**：策略 A 在 1h 上只在“横盘震荡”为正（67 笔 ExpR +0.16），在“上涨趋势”明显为负（67 笔 −0.16）—— 突破回踩在加密单边上涨里被追高止损；“下跌趋势”接近零。但用训练窗环境表现来启停策略（regime gating）在样本外更差（1h A 75 笔 ExpR −0.07），说明环境-收益关系本身不稳定，不能作为规则。策略 B 在 1h 全部环境为负（样本外 31 笔 −0.66R，CI 上界 −0.44，是三者中唯一能被统计上判定为负的）；策略 C 通过过滤的样本太少（1h 12 笔、15m 2 笔、4h 4 笔），4h 上 5 笔正收益不构成证据。“高波动异常”环境在三个周期上几乎没有通过过滤的交易（被 ATR 上限挡住），这正是应当 NO_TRADE 的环境。
9. **4h 维持“仅分析”**：策略 A 十三折样本外 219 笔 ExpR −0.05、PF 0.93、−5.1%，13 折中仅 4 折为正；按政策不产生模拟盘开仓。
10. **是否满足进入模拟盘的条件：不满足。** 具体原因见 §J。模拟盘代码已具备实时运行所需的中断检测、补齐、去重、恢复、熔断与日统计，但在本环境只能以“延迟数据模拟盘”运行并禁止新开仓；而且 Evaluator 没有给出任何合格候选，候选池为空。


## 1. 保留与改动范围

- 保留：三套策略（A 突破回踩、B 假突破收复、C 关键位拒绝）的核心价格行为逻辑、风险控制、Streamlit 看板、PaperBroker。第一阶段默认配置下的回测结果与基准**逐笔一致**（`identical to phase-1 baseline: True`，67 笔、净 602.74 USDT），证明本阶段的引擎提速（6000 根 1h 评估 12.8 s → 4.3 s）与新增机制没有改变原逻辑。
- 新增（全部在 `src/brando_pa/` 内，其他系统零改动）：`audit/data_quality.py`、`diagnostics/funnel.py`、`backtest/execution.py` 的限价成交与可开关退出、`backtest/experiments.py`、`backtest/oos_metrics.py`、`backtest/walkforward.py` 升级、`evaluation/evaluator.py`、`paper/runner.py` 稳定性、看板 5 个诊断页、`config/brando_pa.yaml` 的 `execution_modes / exits / mtf / regime_policy / evaluator / paper.circuit_breakers` 块。
- 禁止项：真实下单接口仍为三重闸门 + 无实现；未连接任何实盘。

## 2. 数据真实性与回测审计

### 2.1 现货 K 线 vs 永续合约可交易价格

- 本仓全部 K 线来自 Binance **现货**（GitHub 公开缓存 finom/static-klines）；合约多空用同一价格序列回测。
- 永续历史 K 线在本环境**不可得**（Binance/Bitget/Bybit/OKX/Deribit/Kraken/Gate/KuCoin/MEXC/HuggingFace/data.binance.vision 全部被网络策略拒绝，见审计 JSON）。唯一可达的永续样本是 GitHub 上 powakadata 的免费截断样本：BTC 2019-09-10→2019-11-15 与 2026-02-17→2026-06-23，ETH 2019-11-27→2020-02-01 与 2026-02-17→2026-06-23，8 小时一点，含资金费率、mark price、未平仓量（OI 仅 2026 片段）。
- 用这些样本量化了 **mark price 相对现货收盘的基差**：2026-02→06 的 377 个 8h 点，BTC 均值 −4.7 bps（P5/P95 −6.2/−3.1 bps，|最大| 7.7 bps），ETH 均值 −4.7 bps（−6.6/−3.0，|最大| 8.6 bps）。即在可观测区间内永续价格系统性低于现货约 5 bps，波动幅度小于我们假设的单边滑点；对多空对称的策略，基差的一阶影响接近零，二阶影响（止损/止盈触发价偏移 ≤ 8 bps）已被 10 bps 止损额外滑点覆盖。但这只覆盖 4 个月，不能外推到 2021 年极端行情。
- **资金费率**：样本 8h 均值 BTC 0.000037（年化 4.1%）、ETH 0.000055（年化 6.0%），2026 片段接近 0；回测默认假设 0.0001/8h（年化 11%，多头付、空头不收）高于全部可观测样本。缺失区间**不按零处理**，而是按该保守假设计入，并在 §E 做敏感性（资金费 = 样本均值 0.00004）。
- 现货/永续的另一差异是手续费：合约 taker 6 bps、现货 10 bps（配置可改），回测按方向策略分别取费。

### 2.2 15m 历史补齐

公开缓存的 15m 只从 2024-12-30 开始；30m 从 2024-01-01；1h 从 2022-01-01；4h/1d/1w 从 2017-08 起。所有可能补齐更早 15m 的来源（data.binance.vision 月度包、HuggingFace 数据集）在本环境被拒。本阶段新增了 30m（2024-01 起，49,941 根/标的，无缺口）作为可选的细周期，但 15m 仍只有约 22 个月，**只覆盖 2025–2026 的下跌与震荡环境**。因此 1h（2022-04 起，含 2022 熊市、2023–2024 上涨、2025–2026 下跌震荡）被定为主要候选信号周期，15m 仅作细化入场对照。

### 2.3 时间戳与 K 线完整性

审计脚本 `python -m brando_pa.cli audit` 的结果（`docs/brando_pa_validation/phase2/data_quality.md`）：

| 周期 | 根数（每标的） | 重复 | 周期错位 | 缺口 | 非法 OHLC | 备注 |
|---|---|---|---|---|---|---|
| 1w | 481 | 0 | 0 | 0 | 0 | 周一 00:00 UTC 对齐（1970-01-01 为周四，已按 4 天偏移校验） |
| 1d | 3368 | 0 | 0 | 0 | 0 | 1 个 8σ 收益异常（2020-03-12，真实行情） |
| 4h | 20193 | 0 | 0 | 8 处 / 16 根（2018–2020） | 0 | 1 根零成交量 |
| 1h | 42489 | 0 | 0 | 1 处 / 1 根 | 0 | 1 根零成交量 |
| 30m | 49941 | 0 | 0 | 0 | 0 | |
| 15m | 64939 | 0 | 0 | 0 | 0 | |

跨周期一致性：用 15m→1h、30m→1h、1h→4h、4h→1d、1d→1w 逐根合成并与下载的高周期比对，OHLC 最大相对误差全部为 0.0，成交量中位相对误差 0.0 → 不存在周期边界错位或收盘时间错误。收盘时间 = 开盘时间 + 周期长度；所有跨周期判断只允许使用“收盘时间 ≤ 决策时刻”的高周期 K 线，并有 `assert_no_lookahead` 自检。

### 2.4 成本、滑点、价差、资金费、限价未成交

- 市价：taker 费（合约 6 / 现货 10 bps）+ 滑点 5 bps 每边；止损额外 10 bps；跳空越过止损按开盘价。
- 限价（本阶段新增）：maker 费（合约 2 / 现货 10 bps）、无滑点；**只有后续 K 线穿越挂单价 ≥ 0.05×ATR 才算成交**（仅触及不算），跳空越过按开盘价；最多等 8 根；结构失效或先到止盈即撤单；撤单记为“未成交”，并用同一退出规则推演“若按市价入场会怎样”作为机会损失。
- 买卖价差：未单独建模，包含在滑点假设中；永续基差见 §2.1。
- 资金费：按 00/08/16 UTC 结算点计；过滤阶段按最长持仓估算（保守上界）。

### 2.5 同根 K 线止盈止损撮合优先级

规则 `same_bar_rule: stop_first`：同一根内止损与止盈都可触及 → 按止损成交（最坏情况）。`test_same_bar_stop_first_gap_and_fees` 覆盖；跳空越过止损按开盘价（更差）、跳空越过止盈也按开盘价（不虚增利润）。

### 2.6 未来函数排查

- 摆动点：`confirmed_idx = i + k`，`confirmed_up_to` 二分截断；区域只用已确认摆动点；
- 跨周期：`align_last_closed` 按收盘时间 searchsorted，`assert_no_lookahead` 在上下文构建时强制；
- 策略：A 的突破事件在 4h 收盘时产生、以“上一根 4h 收盘时的区域快照”分类，回踩/确认只用已收盘执行 K 线；B 的收复须等收复 K 线收盘；C 只看已收盘 K 线；
- 回测：信号在收盘确认、下一根成交；
- 测试：`test_no_lookahead_truncated_history_gives_identical_signals` 把历史截断到 T，逐根比较 1500 根的状态/理由/区域数完全一致；第二阶段新增的限价、退出、多周期检查都只读取 ≤ 决策时刻的数据。

## 3. 第二阶段实现的机制

- **入场模式**：`execution_modes.entry_mode = market | limit`；限价价位由策略给出（A：区域上沿/下沿；B：区域边界；C：拒绝 K 线中点与区域边界较优者），相对信号收盘最多改善 1×ATR，不得越过止损。
- **退出方法（独立开关）**：① 固定结构止损 `stop_mode: fixed_structure`；② ATR 缓冲结构止损 `atr_buffer`（默认）；③ 下一关键区止盈 `tp_mode: next_zone_or_rr`；④ 浮盈 N R 后保本 `breakeven_after_rr`；⑤ 结构反转退出 `structure_exit`（收盘越过失效价）；⑥ 超时退出 `time_exit`；⑦ 固定盈亏比目标 `tp_mode: rr`；另有 ATR 跟踪 `atr_trail` 与分批止盈 `partial_tp`。硬约束 `never_widen_stop`：止损只向有利方向移动；禁止马丁、补仓、取消硬止损。保本/跟踪参数进入 Walk-Forward 网格（`exits.breakeven_after_rr: [null, 1.5]`）由训练窗选择。
- **市场环境四分类**：上涨趋势（日线 close>MA200 且斜率>0）、下跌趋势、横盘震荡、高波动异常（rv20 处于 365 日 80% 分位以上，优先级最高）；全部滚动计算。`regime_policy` 可按策略限定允许环境；`--regime-gating` 模式下该策略由**训练窗**交易（≥5 笔且 ExpR>0 的环境）决定，测试窗固定。
- **多周期决策**：`mtf.mode = single | aligned | zone_limit`。aligned：4h 结构方向须与信号一致（range 不允许）且日线环境不反向；zone_limit：在 aligned 基础上要求区域含 1w/1d 并用限价入场。周线/日线出关键区、4h 出结构、1h 出候选的职责划分通过 1h 执行周期实现；15m 精细确认（`mtf.confirm_tf`）因 15m 历史只有 22 个月、且前述结果未显示细化入场有正贡献，本阶段预留接口未启用。
- **漏斗**：原始候选 → 趋势过滤（mtf_4h_trend / mtf_1d_regime / regime_policy / regime_trend）→ 价格确认（volume / volatility / stop_distance）→ 成本（cost_to_risk）→ 盈亏比（net_rr）→ 风险（sizing / portfolio）→ 有效订单 → 实际成交，按标的 × 方向 × 策略统计，并记录策略内部“设置建立/超时/失效/确认”计数。
- **样本外**：Walk-Forward 训练 12 / 验证 6 / 测试 6 月（15m 为 8/3/3），窗间隔离 7 天，滚动步长 = 测试窗；每策略独立；输出 §八全部指标、费用敏感性（按交易记录重算）、块自助 95% CI、默认参数 ±20% 扰动。
- **Strategy Evaluator**：门槛见 §G；4h 周期政策 `analysis_only`。
- **模拟盘**：实时能力判定（公开缓存永远不算实时 → 状态 `paper_delayed_data`，禁止新开仓）、数据中断/缺口/跳变检测、重连与补齐、确定性订单号去重、状态持久化与重启恢复、单日亏损 / 峰值回撤 10% / 连亏 8 笔 / 数据异常熔断、交易与事件日志、每日绩效统计 `daily_stats.jsonl`。

## A. 原系统（第一阶段基准）与优化后系统并排对比

“优化后”不是单一配置，而是第二阶段实验矩阵里每个维度的最优与最差变体；所有变体都在同一数据、同一成本模型、同一保守成交规则下运行。基准 = 第一阶段 all_filters / 单策略 filters。

| 周期 | 组合 | 基准：交易/PF/ExpR/净收益/回撤 | 第二阶段 market+single（应与基准一致）| 第二阶段最佳变体（按 ExpR，≥20 笔） | 第二阶段最差变体 |
|---|---|---|---|---|---|
| 15m | ALL | 67 / 1.38 / 0.180 / 6.03% / -4.30% | 67 / 1.38 / 0.180 / 6.03% / -4.30% | exit_no_breakeven：67 / 1.56 / 0.279 / 9.57% / -4.44% | entry_limit__mtf_single：59 / 0.97 / -0.010 / -0.45% / -7.58% |
| 15m | A_breakout_retest | 36 / 1.45 / 0.218 / 3.90% / -2.28% | 36 / 1.45 / 0.218 / 3.90% / -2.28% | exit_no_time_exit：38 / 1.54 / 0.299 / 5.70% / -2.71% | entry_limit__mtf_single：28 / 1.08 / 0.050 / 0.64% / -3.58% |
| 15m | B_reclaim | 29 / 1.20 / 0.110 / 1.52% / -3.06% | 29 / 1.20 / 0.110 / 1.52% / -3.06% | exit_no_breakeven：29 / 1.27 / 0.157 / 2.22% / -3.66% | exit_partial_tp：29 / 0.63 / -0.188 / -2.71% / -4.75% |
| 15m | C_rejection | 7 / 0.03 / -0.405 / -1.41% / -2.15% | 7 / 0.03 / -0.405 / -1.41% / -2.15% | —：— | —：— |
| 1h | ALL | 291 / 0.93 / -0.037 / -5.96% / -19.85% | 291 / 0.93 / -0.037 / -5.96% / -19.85% | entry_market__mtf_aligned：47 / 1.43 / 0.213 / 4.98% / -4.51% | entry_limit__mtf_aligned：40 / 0.59 / -0.268 / -5.29% / -7.90% |
| 1h | A_breakout_retest | 243 / 0.99 / 0.002 / -0.35% / -15.55% | 243 / 0.99 / 0.002 / -0.35% / -15.55% | entry_market__mtf_aligned：39 / 1.46 / 0.229 / 4.46% / -4.52% | entry_limit__mtf_aligned：33 / 0.59 / -0.267 / -4.36% / -7.10% |
| 1h | B_reclaim | 46 / 0.61 / -0.259 / -5.91% / -9.88% | 46 / 0.61 / -0.259 / -5.91% / -9.88% | entry_limit__mtf_single：40 / 0.61 / -0.250 / -5.01% / -9.15% | exit_no_time_exit：46 / 0.54 / -0.320 / -7.22% / -10.35% |
| 1h | C_rejection | 13 / 0.64 / -0.235 / -1.55% / -2.42% | 13 / 0.64 / -0.235 / -1.55% / -2.42% | —：— | —：— |
| 4h | ALL | 298 / 1.01 / 0.010 / 0.68% / -17.22% | 298 / 1.01 / 0.010 / 0.68% / -17.22% | entry_market__mtf_single：298 / 1.01 / 0.010 / 0.68% / -17.22% | entry_limit__mtf_zone_limit：38 / 0.41 / -0.401 / -7.40% / -8.25% |
| 4h | A_breakout_retest | 280 / 0.96 / -0.018 / -3.29% / -16.95% | 280 / 0.96 / -0.018 / -3.29% / -16.95% | entry_market__mtf_single：280 / 0.96 / -0.018 / -3.29% / -16.95% | entry_limit__mtf_zone_limit：36 / 0.45 / -0.369 / -6.49% / -7.36% |
| 4h | B_reclaim | 16 / 1.28 / 0.150 / 1.12% / -3.09% | 16 / 1.28 / 0.150 / 1.12% / -3.09% | —：— | —：— |
| 4h | C_rejection | 5 / 3.41 / 0.968 / 2.43% / -0.70% | 5 / 3.41 / 0.968 / 2.43% / -0.70% | —：— | —：— |

## B. 市价与限价入场、多周期模式、退出方案对比

### 1h（2022-04 → 2026-11）

| 变体 | 交易 | 胜率 | PF | ExpR | 净收益 | 回撤 | Sharpe | 连亏 | 手续费 | 滑点 | 资金费 | 候选→通过 | 限价 挂/成/撤 | 未成交机会PnL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest__entry_market__mtf_single | 243 | 37.4% | 0.99 | 0.002 | -0.35% | -15.55% | 0.03 | 10 | 364.78 | 512.78 | 47.78 | 2242→343 | — | — |
| A_breakout_retest__entry_market__mtf_aligned | 39 | 48.7% | 1.46 | 0.229 | 4.46% | -4.52% | 0.48 | 9 | 61.37 | 83.70 | 8.40 | 2242→56 | — | — |
| A_breakout_retest__entry_limit__mtf_single | 185 | 31.4% | 0.65 | -0.217 | -18.52% | -21.91% | -0.92 | 11 | 227.24 | 377.22 | 30.74 | 2242→343 | 304/185/119 | 2474.10 |
| A_breakout_retest__entry_limit__mtf_aligned | 33 | 27.3% | 0.59 | -0.267 | -4.36% | -7.10% | -0.51 | 9 | 44.31 | 74.64 | 8.57 | 2242→56 | 55/33/22 | 781.01 |
| A_breakout_retest__entry_limit__mtf_zone_limit | 33 | 27.3% | 0.59 | -0.267 | -4.36% | -7.10% | -0.51 | 9 | 44.31 | 74.64 | 8.57 | 2242→56 | 55/33/22 | 781.01 |
| B_reclaim__entry_market__mtf_single | 46 | 30.4% | 0.61 | -0.259 | -5.91% | -9.88% | -0.58 | 8 | 111.67 | 170.72 | 9.79 | 4774→47 | — | — |
| B_reclaim__entry_market__mtf_aligned | 8 | 50.0% | 1.26 | 0.133 | 0.51% | -1.46% | 0.11 | 2 | 18.82 | 27.53 | 0.84 | 4774→8 | — | — |
| B_reclaim__entry_limit__mtf_single | 40 | 32.5% | 0.61 | -0.250 | -5.01% | -9.15% | -0.55 | 6 | 78.97 | 140.32 | 5.00 | 4774→47 | 47/40/7 | 92.44 |
| B_reclaim__entry_limit__mtf_aligned | 6 | 33.3% | 0.75 | -0.156 | -0.48% | -2.21% | -0.12 | 3 | 9.47 | 16.62 | 1.00 | 4774→8 | 8/6/2 | 112.85 |
| B_reclaim__entry_limit__mtf_zone_limit | 4 | 50.0% | 1.51 | 0.245 | 0.48% | -1.47% | 0.13 | 1 | 5.83 | 9.77 | 1.01 | 4774→6 | 6/4/2 | 114.00 |
| C_rejection__entry_market__mtf_single | 13 | 30.8% | 0.64 | -0.235 | -1.55% | -2.42% | -0.40 | 5 | 29.67 | 45.78 | 3.84 | 1147→13 | — | — |
| C_rejection__entry_market__mtf_aligned | 1 | 0.0% | 0.00 | -0.976 | -0.49% | -0.77% | -0.66 | 1 | 2.96 | 4.95 | 0.00 | 1147→1 | — | — |
| C_rejection__entry_limit__mtf_single | 11 | 27.3% | 0.63 | -0.243 | -1.36% | -1.90% | -0.41 | 3 | 19.68 | 34.41 | 1.83 | 1147→13 | 13/11/2 | 48.90 |
| C_rejection__entry_limit__mtf_aligned | 1 | 0.0% | 0.00 | -0.955 | -0.48% | -0.79% | -0.65 | 1 | 2.03 | 3.82 | 0.00 | 1147→1 | 1/1/0 | — |
| C_rejection__entry_limit__mtf_zone_limit | 1 | 0.0% | 0.00 | -0.955 | -0.48% | -0.79% | -0.65 | 1 | 2.03 | 3.82 | 0.00 | 1147→1 | 1/1/0 | — |
| ALL__entry_market__mtf_single | 291 | 36.1% | 0.93 | -0.037 | -5.96% | -19.85% | -0.19 | 10 | 469.95 | 672.19 | 58.70 | 8163→403 | — | — |
| ALL__entry_market__mtf_aligned | 47 | 48.9% | 1.43 | 0.213 | 4.98% | -4.51% | 0.47 | 7 | 80.40 | 111.51 | 9.24 | 8163→65 | — | — |
| ALL__entry_limit__mtf_single | 232 | 31.5% | 0.65 | -0.216 | -22.65% | -25.76% | -1.05 | 13 | 307.58 | 518.85 | 35.70 | 8163→403 | 359/232/127 | 2345.51 |
| ALL__entry_limit__mtf_aligned | 40 | 27.5% | 0.59 | -0.268 | -5.29% | -7.90% | -0.53 | 8 | 55.22 | 94.07 | 9.46 | 8163→65 | 64/40/24 | 790.54 |
| ALL__entry_limit__mtf_zone_limit | 38 | 28.9% | 0.64 | -0.231 | -4.38% | -7.37% | -0.44 | 8 | 51.95 | 87.85 | 9.56 | 8163→63 | 62/38/24 | 795.73 |
| A_breakout_retest__exit_fixed_structure | 288 | 34.4% | 0.93 | -0.033 | -5.40% | -16.41% | -0.16 | 12 | 521.56 | 768.29 | 78.30 | 2242→343 | — | — |
| A_breakout_retest__exit_no_breakeven | 242 | 35.1% | 1.01 | 0.015 | 1.08% | -15.81% | 0.09 | 10 | 366.36 | 495.96 | 54.30 | 2242→343 | — | — |
| A_breakout_retest__exit_rr_target_only | 263 | 33.1% | 0.94 | -0.031 | -4.63% | -17.11% | -0.14 | 9 | 392.15 | 536.17 | 68.10 | 2242→343 | — | — |
| A_breakout_retest__exit_structure_exit | 245 | 36.3% | 1.00 | 0.003 | -0.22% | -15.81% | 0.04 | 11 | 367.46 | 477.98 | 47.49 | 2242→343 | — | — |
| A_breakout_retest__exit_atr_trail | 250 | 41.2% | 0.97 | -0.013 | -2.10% | -14.29% | -0.05 | 10 | 379.53 | 564.92 | 37.35 | 2242→343 | — | — |
| A_breakout_retest__exit_partial_tp | 251 | 50.2% | 0.96 | -0.018 | -2.57% | -12.47% | -0.10 | 10 | 377.22 | 472.18 | 30.55 | 2242→343 | — | — |
| A_breakout_retest__exit_no_time_exit | 256 | 33.6% | 0.86 | -0.076 | -9.89% | -20.22% | -0.38 | 12 | 364.54 | 537.29 | 93.87 | 2242→343 | — | — |
| B_reclaim__exit_fixed_structure | 47 | 25.5% | 0.59 | -0.287 | -6.68% | -10.86% | -0.58 | 12 | 136.74 | 215.95 | 8.62 | 4774→47 | — | — |
| B_reclaim__exit_no_breakeven | 46 | 19.6% | 0.64 | -0.281 | -6.39% | -10.42% | -0.56 | 11 | 111.02 | 165.34 | 11.85 | 4774→47 | — | — |
| B_reclaim__exit_rr_target_only | 0 | — | — | — | 0.00% | 0.00% | — | — | — | 0.00 | — | 4774→47 | — | — |
| B_reclaim__exit_structure_exit | 46 | 28.3% | 0.60 | -0.272 | -6.19% | -10.15% | -0.61 | 11 | 111.42 | 165.40 | 9.76 | 4774→47 | — | — |
| B_reclaim__exit_atr_trail | 46 | 26.1% | 0.54 | -0.317 | -7.11% | -10.16% | -0.80 | 11 | 109.88 | 171.98 | 8.73 | 4774→47 | — | — |
| B_reclaim__exit_partial_tp | 46 | 41.3% | 0.53 | -0.269 | -6.05% | -8.40% | -0.84 | 6 | 110.19 | 150.51 | 5.58 | 4774→47 | — | — |
| B_reclaim__exit_no_time_exit | 46 | 26.1% | 0.54 | -0.320 | -7.22% | -10.35% | -0.71 | 11 | 110.62 | 173.99 | 11.03 | 4774→47 | — | — |
| C_rejection__exit_fixed_structure | 13 | 23.1% | 0.74 | -0.182 | -1.23% | -2.54% | -0.27 | 5 | 35.29 | 54.24 | 3.32 | 1147→13 | — | — |
| C_rejection__exit_no_breakeven | 13 | 23.1% | 0.60 | -0.288 | -1.89% | -2.42% | -0.46 | 5 | 29.63 | 44.00 | 5.23 | 1147→13 | — | — |
| C_rejection__exit_rr_target_only | 0 | — | — | — | 0.00% | 0.00% | — | — | — | 0.00 | — | 1147→13 | — | — |
| C_rejection__exit_structure_exit | 13 | 30.8% | 0.67 | -0.204 | -1.35% | -2.27% | -0.35 | 5 | 29.67 | 39.78 | 3.84 | 1147→13 | — | — |
| C_rejection__exit_atr_trail | 13 | 23.1% | 0.61 | -0.281 | -1.85% | -2.42% | -0.47 | 5 | 29.63 | 45.75 | 3.66 | 1147→13 | — | — |
| C_rejection__exit_partial_tp | 13 | 38.5% | 0.32 | -0.403 | -2.60% | -2.60% | -0.77 | 3 | 29.70 | 41.57 | 2.48 | 1147→13 | — | — |
| C_rejection__exit_no_time_exit | 13 | 30.8% | 0.79 | -0.137 | -0.93% | -2.42% | -0.23 | 5 | 29.80 | 45.94 | 4.46 | 1147→13 | — | — |
| ALL__exit_fixed_structure | 337 | 32.9% | 0.89 | -0.063 | -11.01% | -22.62% | -0.35 | 13 | 645.05 | 961.86 | 86.18 | 8163→403 | — | — |
| ALL__exit_no_breakeven | 288 | 33.0% | 0.95 | -0.023 | -4.12% | -20.07% | -0.11 | 14 | 470.42 | 649.55 | 66.76 | 8163→403 | — | — |
| ALL__exit_rr_target_only | 263 | 33.1% | 0.94 | -0.031 | -4.63% | -17.11% | -0.14 | 9 | 392.15 | 536.17 | 68.10 | 8163→403 | — | — |
| ALL__exit_structure_exit | 293 | 34.8% | 0.92 | -0.037 | -6.06% | -20.49% | -0.20 | 11 | 471.37 | 629.17 | 58.27 | 8163→403 | — | — |
| ALL__exit_atr_trail | 300 | 38.7% | 0.89 | -0.060 | -9.20% | -19.11% | -0.36 | 9 | 487.39 | 731.80 | 46.87 | 8163→403 | — | — |
| ALL__exit_partial_tp | 300 | 48.7% | 0.86 | -0.065 | -9.65% | -17.69% | -0.48 | 9 | 477.23 | 610.79 | 35.72 | 8163→403 | — | — |
| ALL__exit_no_time_exit | 304 | 32.2% | 0.82 | -0.106 | -15.59% | -25.09% | -0.60 | 13 | 460.71 | 687.53 | 104.93 | 8163→403 | — | — |

### 15m（2025-02 → 2026-11）

| 变体 | 交易 | 胜率 | PF | ExpR | 净收益 | 回撤 | Sharpe | 连亏 | 手续费 | 滑点 | 资金费 | 候选→通过 | 限价 挂/成/撤 | 未成交机会PnL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest__entry_market__mtf_single | 36 | 47.2% | 1.45 | 0.218 | 3.90% | -2.28% | 0.86 | 5 | 66.28 | 82.66 | 6.17 | 755→44 | — | — |
| A_breakout_retest__entry_market__mtf_aligned | 6 | 50.0% | 1.61 | 0.308 | 0.91% | -1.19% | 0.46 | 1 | 11.43 | 14.36 | 0.00 | 755→9 | — | — |
| A_breakout_retest__entry_limit__mtf_single | 28 | 42.9% | 1.08 | 0.050 | 0.64% | -3.58% | 0.16 | 4 | 41.62 | 57.68 | 4.46 | 755→44 | 43/28/15 | 513.75 |
| A_breakout_retest__entry_limit__mtf_aligned | 6 | 50.0% | 1.72 | 0.359 | 1.07% | -1.70% | 0.47 | 2 | 8.83 | 11.17 | 0.59 | 755→9 | 8/6/2 | 108.16 |
| A_breakout_retest__entry_limit__mtf_zone_limit | 6 | 50.0% | 1.72 | 0.359 | 1.07% | -1.70% | 0.47 | 2 | 8.83 | 11.17 | 0.59 | 755→9 | 8/6/2 | 108.16 |
| B_reclaim__entry_market__mtf_single | 29 | 41.4% | 1.20 | 0.110 | 1.52% | -3.06% | 0.26 | 5 | 85.19 | 119.09 | 4.10 | 4045→33 | — | — |
| B_reclaim__entry_market__mtf_aligned | 6 | 33.3% | 1.24 | 0.141 | 0.40% | -2.16% | 0.10 | 3 | 15.92 | 22.35 | 0.72 | 4045→6 | — | — |
| B_reclaim__entry_limit__mtf_single | 27 | 40.7% | 0.97 | -0.009 | -0.19% | -4.18% | -0.02 | 4 | 63.52 | 100.37 | 2.54 | 4045→33 | 33/27/6 | 58.16 |
| B_reclaim__entry_limit__mtf_aligned | 6 | 33.3% | 0.03 | -0.494 | -1.47% | -2.39% | -0.56 | 3 | 12.15 | 19.86 | 0.89 | 4045→6 | 6/6/0 | — |
| B_reclaim__entry_limit__mtf_zone_limit | 3 | 66.7% | 0.08 | -0.289 | -0.43% | -2.39% | -0.17 | 1 | 5.96 | 11.17 | 0.00 | 4045→3 | 3/3/0 | — |
| C_rejection__entry_market__mtf_single | 7 | 57.1% | 0.03 | -0.405 | -1.41% | -2.15% | -0.92 | 1 | 22.23 | 37.12 | 1.08 | 1598→7 | — | — |
| C_rejection__entry_market__mtf_aligned | 4 | 50.0% | 0.02 | -0.476 | -0.95% | -1.33% | -0.97 | 1 | 13.74 | 22.94 | 0.84 | 1598→4 | — | — |
| C_rejection__entry_limit__mtf_single | 6 | 50.0% | 0.05 | -0.451 | -1.35% | -2.23% | -0.83 | 2 | 13.46 | 25.27 | 0.26 | 1598→7 | 7/6/1 | 0.52 |
| C_rejection__entry_limit__mtf_aligned | 3 | 33.3% | 0.03 | -0.613 | -0.92% | -1.30% | -1.03 | 2 | 7.18 | 13.48 | 0.00 | 1598→4 | 4/3/1 | 0.52 |
| C_rejection__entry_limit__mtf_zone_limit | 3 | 33.3% | 0.03 | -0.613 | -0.92% | -1.30% | -1.03 | 2 | 7.18 | 13.48 | 0.00 | 1598→4 | 4/3/1 | 0.52 |
| ALL__entry_market__mtf_single | 67 | 47.8% | 1.38 | 0.180 | 6.03% | -4.30% | 0.74 | 5 | 162.71 | 219.80 | 11.29 | 6398→84 | — | — |
| ALL__entry_market__mtf_aligned | 15 | 46.7% | 1.23 | 0.118 | 0.84% | -2.62% | 0.17 | 3 | 37.68 | 53.96 | 1.56 | 6398→19 | — | — |
| ALL__entry_limit__mtf_single | 59 | 42.4% | 0.97 | -0.010 | -0.45% | -7.58% | -0.04 | 6 | 116.56 | 179.07 | 7.11 | 6398→84 | 81/59/22 | 575.81 |
| ALL__entry_limit__mtf_aligned | 15 | 40.0% | 0.66 | -0.176 | -1.34% | -3.10% | -0.40 | 3 | 28.24 | 44.63 | 1.48 | 6398→19 | 18/15/3 | 107.16 |
| ALL__entry_limit__mtf_zone_limit | 12 | 50.0% | 0.90 | -0.046 | -0.29% | -2.83% | -0.09 | 2 | 22.07 | 36.01 | 0.58 | 6398→16 | 15/12/3 | 108.19 |
| A_breakout_retest__exit_fixed_structure | 44 | 47.7% | 1.30 | 0.146 | 3.14% | -3.45% | 0.59 | 4 | 96.64 | 126.82 | 7.38 | 755→44 | — | — |
| A_breakout_retest__exit_no_breakeven | 36 | 47.2% | 1.56 | 0.272 | 4.91% | -2.28% | 1.02 | 5 | 66.33 | 81.65 | 6.19 | 755→44 | — | — |
| A_breakout_retest__exit_rr_target_only | 43 | 46.5% | 1.38 | 0.189 | 4.03% | -2.61% | 0.79 | 6 | 82.49 | 100.76 | 8.73 | 755→44 | — | — |
| A_breakout_retest__exit_structure_exit | 36 | 44.4% | 1.35 | 0.168 | 2.96% | -3.57% | 0.66 | 7 | 65.61 | 69.43 | 5.63 | 755→44 | — | — |
| A_breakout_retest__exit_atr_trail | 36 | 47.2% | 1.13 | 0.063 | 1.09% | -2.92% | 0.31 | 5 | 65.61 | 94.47 | 5.91 | 755→44 | — | — |
| A_breakout_retest__exit_partial_tp | 36 | 55.6% | 1.38 | 0.162 | 2.89% | -1.88% | 0.76 | 3 | 66.01 | 74.49 | 5.45 | 755→44 | — | — |
| A_breakout_retest__exit_no_time_exit | 38 | 44.7% | 1.54 | 0.299 | 5.70% | -2.71% | 1.10 | 5 | 71.35 | 95.52 | 10.79 | 755→44 | — | — |
| B_reclaim__exit_fixed_structure | 33 | 42.4% | 1.24 | 0.135 | 2.13% | -4.49% | 0.31 | 5 | 114.17 | 166.05 | 3.21 | 4045→33 | — | — |
| B_reclaim__exit_no_breakeven | 29 | 34.5% | 1.27 | 0.157 | 2.22% | -3.66% | 0.36 | 5 | 85.79 | 109.36 | 5.06 | 4045→33 | — | — |
| B_reclaim__exit_rr_target_only | 0 | — | — | — | 0.00% | 0.00% | — | — | — | 0.00 | — | 4045→33 | — | — |
| B_reclaim__exit_structure_exit | 29 | 41.4% | 1.22 | 0.117 | 1.62% | -3.06% | 0.28 | 5 | 85.24 | 116.78 | 4.10 | 4045→33 | — | — |
| B_reclaim__exit_atr_trail | 29 | 44.8% | 1.10 | 0.057 | 0.76% | -3.07% | 0.18 | 5 | 84.70 | 123.01 | 4.06 | 4045→33 | — | — |
| B_reclaim__exit_partial_tp | 29 | 44.8% | 0.63 | -0.188 | -2.71% | -4.75% | -0.75 | 5 | 83.39 | 106.63 | 2.73 | 4045→33 | — | — |
| B_reclaim__exit_no_time_exit | 29 | 34.5% | 1.18 | 0.106 | 1.44% | -3.85% | 0.24 | 5 | 85.59 | 126.75 | 8.71 | 4045→33 | — | — |
| C_rejection__exit_fixed_structure | 7 | 57.1% | 0.03 | -0.401 | -1.40% | -2.23% | -0.86 | 1 | 26.32 | 43.92 | 1.20 | 1598→7 | — | — |
| C_rejection__exit_no_breakeven | 7 | 57.1% | 1.67 | 0.286 | 1.00% | -1.42% | 0.67 | 1 | 22.70 | 27.17 | 1.82 | 1598→7 | — | — |
| C_rejection__exit_rr_target_only | 0 | — | — | — | 0.00% | 0.00% | — | — | — | 0.00 | — | 1598→7 | — | — |
| C_rejection__exit_structure_exit | 7 | 57.1% | 0.03 | -0.405 | -1.41% | -2.15% | -0.92 | 1 | 22.23 | 37.12 | 1.08 | 1598→7 | — | — |
| C_rejection__exit_atr_trail | 7 | 42.9% | 0.68 | -0.156 | -0.55% | -2.04% | -0.28 | 3 | 22.22 | 37.07 | 0.80 | 1598→7 | — | — |
| C_rejection__exit_partial_tp | 7 | 57.1% | 0.53 | -0.195 | -0.68% | -1.43% | -0.54 | 1 | 22.33 | 29.37 | 0.68 | 1598→7 | — | — |
| C_rejection__exit_no_time_exit | 7 | 57.1% | 0.03 | -0.405 | -1.41% | -2.15% | -0.92 | 1 | 22.23 | 37.12 | 1.08 | 1598→7 | — | — |
| ALL__exit_fixed_structure | 79 | 46.8% | 1.27 | 0.138 | 5.35% | -5.42% | 0.56 | 6 | 223.53 | 313.41 | 11.72 | 6398→84 | — | — |
| ALL__exit_no_breakeven | 67 | 44.8% | 1.56 | 0.279 | 9.57% | -4.44% | 1.11 | 5 | 166.49 | 203.56 | 12.83 | 6398→84 | — | — |
| ALL__exit_rr_target_only | 42 | 47.6% | 1.45 | 0.217 | 4.55% | -2.61% | 0.89 | 6 | 81.06 | 98.17 | 8.78 | 6398→84 | — | — |
| ALL__exit_structure_exit | 67 | 46.3% | 1.32 | 0.154 | 5.08% | -4.18% | 0.63 | 5 | 161.45 | 204.76 | 10.69 | 6398→84 | — | — |
| ALL__exit_atr_trail | 67 | 47.8% | 1.19 | 0.092 | 3.01% | -3.51% | 0.50 | 5 | 159.76 | 233.01 | 10.60 | 6398→84 | — | — |
| ALL__exit_partial_tp | 69 | 52.2% | 1.01 | 0.008 | 0.19% | -3.47% | 0.05 | 5 | 165.15 | 200.96 | 8.68 | 6398→84 | — | — |
| ALL__exit_no_time_exit | 69 | 43.5% | 1.42 | 0.224 | 7.79% | -6.19% | 0.89 | 9 | 169.57 | 242.63 | 20.86 | 6398→84 | — | — |

### 4h（2018-06 → 2026-11，仅基线/对齐/区域限价三种）

| 变体 | 交易 | 胜率 | PF | ExpR | 净收益 | 回撤 | Sharpe | 连亏 | 手续费 | 滑点 | 资金费 | 候选→通过 | 限价 挂/成/撤 | 未成交机会PnL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest__entry_market__mtf_single | 280 | 39.6% | 0.96 | -0.018 | -3.29% | -16.95% | -0.07 | 12 | 364.28 | 535.49 | 14.88 | 3380→473 | — | — |
| A_breakout_retest__entry_market__mtf_aligned | 54 | 35.2% | 0.73 | -0.167 | -4.52% | -5.85% | -0.31 | 11 | 69.31 | 104.71 | 2.71 | 3380→63 | — | — |
| A_breakout_retest__entry_limit__mtf_zone_limit | 36 | 30.6% | 0.45 | -0.369 | -6.49% | -7.36% | -0.54 | 8 | 43.92 | 76.96 | 0.00 | 3380→63 | 61/36/25 | 230.65 |
| B_reclaim__entry_market__mtf_single | 16 | 37.5% | 1.28 | 0.150 | 1.12% | -3.09% | 0.12 | 3 | 25.97 | 41.24 | 15.62 | 3691→17 | — | — |
| B_reclaim__entry_market__mtf_aligned | 4 | 50.0% | 1.17 | 0.091 | 0.17% | -1.53% | 0.05 | 1 | 5.31 | 8.16 | 2.68 | 3691→4 | — | — |
| B_reclaim__entry_limit__mtf_zone_limit | 2 | 0.0% | 0.00 | -0.970 | -0.97% | -0.97% | -0.49 | 2 | 2.75 | 5.18 | 0.00 | 3691→3 | 3/2/1 | 115.50 |
| C_rejection__entry_market__mtf_single | 5 | 60.0% | 3.41 | 0.968 | 2.43% | -0.70% | 0.42 | 1 | 12.48 | 14.29 | 0.38 | 568→5 | — | — |
| C_rejection__entry_market__mtf_aligned | 0 | — | — | — | 0.00% | 0.00% | — | — | — | 0.00 | — | 568→0 | — | — |
| C_rejection__entry_limit__mtf_zone_limit | 0 | — | — | — | 0.00% | 0.00% | — | — | — | 0.00 | — | 568→0 | — | — |
| ALL__entry_market__mtf_single | 298 | 39.9% | 1.01 | 0.010 | 0.68% | -17.22% | 0.04 | 12 | 412.07 | 603.20 | 31.50 | 7639→495 | — | — |
| ALL__entry_market__mtf_aligned | 58 | 36.2% | 0.75 | -0.149 | -4.36% | -5.95% | -0.29 | 8 | 74.42 | 112.58 | 5.24 | 7639→67 | — | — |
| ALL__entry_limit__mtf_zone_limit | 38 | 28.9% | 0.41 | -0.401 | -7.40% | -8.25% | -0.62 | 8 | 46.40 | 81.65 | 0.00 | 7639→66 | 64/38/26 | 340.95 |

## C. 不同市场环境下的收益与回撤（入场时日线环境四分类；基线 market+single）

### h1

| 策略 | 环境 | 交易 | 胜率 | ExpR | 净盈亏 | PF |
|---|---|---|---|---|---|---|
| A_breakout_retest | 上涨趋势 | 67 | 31% | -0.16 | -546.73 | 0.70 |
| A_breakout_retest | 下跌趋势 | 109 | 39% | 0.01 | 32.40 | 1.01 |
| A_breakout_retest | 横盘震荡 | 67 | 40% | 0.16 | 478.14 | 1.28 |
| B_reclaim | 上涨趋势 | 6 | 33% | -0.26 | -77.72 | 0.60 |
| B_reclaim | 下跌趋势 | 18 | 22% | -0.28 | -251.93 | 0.61 |
| B_reclaim | 横盘震荡 | 22 | 36% | -0.24 | -261.47 | 0.61 |
| C_rejection | 下跌趋势 | 8 | 25% | -0.15 | -64.21 | 0.78 |
| C_rejection | 横盘震荡 | 5 | 40% | -0.36 | -90.96 | 0.35 |
| ALL | 上涨趋势 | 73 | 32% | -0.17 | -607.93 | 0.70 |
| ALL | 下跌趋势 | 130 | 37% | -0.04 | -248.99 | 0.93 |
| ALL | 横盘震荡 | 88 | 39% | 0.07 | 259.19 | 1.11 |

ALL 按方向：long 65 笔 / ExpR -0.17 / 净 -519.26；short 226 笔 / ExpR 0.00 / 净 -78.47。按标的：BTCUSDT 110 笔 / ExpR -0.10 / 净 -563.67；ETHUSDT 181 笔 / ExpR 0.00 / 净 -34.06。按年：2022 58 笔 / ExpR 0.04；2023 34 笔 / ExpR -0.14；2024 62 笔 / ExpR -0.23；2025 83 笔 / ExpR -0.09；2026 54 笔 / ExpR 0.25

### m15

| 策略 | 环境 | 交易 | 胜率 | ExpR | 净盈亏 | PF |
|---|---|---|---|---|---|---|
| A_breakout_retest | 上涨趋势 | 4 | 50% | 0.72 | 143.44 | 3.05 |
| A_breakout_retest | 下跌趋势 | 14 | 36% | 0.06 | 36.91 | 1.09 |
| A_breakout_retest | 横盘震荡 | 18 | 56% | 0.23 | 209.44 | 1.53 |
| B_reclaim | 上涨趋势 | 5 | 40% | 0.32 | 81.84 | 1.69 |
| B_reclaim | 下跌趋势 | 18 | 44% | 0.07 | 56.71 | 1.13 |
| B_reclaim | 横盘震荡 | 6 | 33% | 0.05 | 13.60 | 1.07 |
| C_rejection | 下跌趋势 | 4 | 50% | -0.48 | -95.06 | 0.02 |
| C_rejection | 横盘震荡 | 3 | 67% | -0.31 | -46.02 | 0.04 |
| ALL | 上涨趋势 | 9 | 44% | 0.50 | 229.32 | 2.21 |
| ALL | 下跌趋势 | 33 | 42% | 0.06 | 90.87 | 1.11 |
| ALL | 横盘震荡 | 25 | 56% | 0.22 | 282.56 | 1.52 |

ALL 按方向：long 27 笔 / ExpR -0.03 / 净 -51.88；short 40 笔 / ExpR 0.32 / 净 654.62。按标的：BTCUSDT 12 笔 / ExpR 0.44 / 净 269.44；ETHUSDT 55 笔 / ExpR 0.12 / 净 333.30。按年：2025 43 笔 / ExpR 0.10；2026 24 笔 / ExpR 0.33

### h4

| 策略 | 环境 | 交易 | 胜率 | ExpR | 净盈亏 | PF |
|---|---|---|---|---|---|---|
| A_breakout_retest | 上涨趋势 | 78 | 44% | 0.00 | 4.13 | 1.00 |
| A_breakout_retest | 下跌趋势 | 110 | 35% | -0.04 | -277.09 | 0.92 |
| A_breakout_retest | 横盘震荡 | 92 | 41% | -0.01 | -58.81 | 0.98 |
| B_reclaim | 上涨趋势 | 7 | 57% | 1.18 | 408.85 | 4.98 |
| B_reclaim | 下跌趋势 | 6 | 33% | -0.64 | -195.96 | 0.01 |
| B_reclaim | 横盘震荡 | 3 | 0% | -0.66 | -101.03 | 0.00 |
| C_rejection | 上涨趋势 | 1 | 0% | -0.98 | -50.19 | 0.00 |
| C_rejection | 横盘震荡 | 4 | 75% | 1.45 | 292.87 | 6.81 |
| ALL | 上涨趋势 | 85 | 44% | 0.09 | 363.14 | 1.16 |
| ALL | 下跌趋势 | 115 | 36% | -0.06 | -425.35 | 0.89 |
| ALL | 横盘震荡 | 98 | 42% | 0.03 | 126.91 | 1.04 |

ALL 按方向：long 14 笔 / ExpR 0.87 / 净 618.35；short 284 笔 / ExpR -0.03 / 净 -553.66。按标的：BTCUSDT 152 笔 / ExpR -0.02 / 净 -227.43；ETHUSDT 146 笔 / ExpR 0.05 / 净 292.13。按年：2018 20 笔 / ExpR 0.26；2019 35 笔 / ExpR 0.01；2020 28 笔 / ExpR -0.06；2021 20 笔 / ExpR 0.19；2022 47 笔 / ExpR 0.25；2023 32 笔 / ExpR -0.11；2024 39 笔 / ExpR -0.20；2025 42 笔 / ExpR -0.30；2026 35 笔 / ExpR 0.21

## D. 候选信号拒绝原因分析（漏斗）

### h1（ALL, market, single）

| symbol | direction | strategy | setup | candidate | after_trend | after_price | after_cost | after_rr | after_risk | order | fill | cancelled_at_fill | avg_stop_distance_pct | avg_stop_distance_atr | median_cost_to_risk | median_cost_share_of_reward | avg_net_rr_valid |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | long | A_breakout_retest | 1472 | 577 | 540 | 126 | 92 | 28 | 26 | 18 | 17 | 16 | 2.76% | 4.21 | 20.96% | 8.46% | 2.28 |
| BTCUSDT | long | B_reclaim | 4491 | 1202 | 1094 | 447 | 110 | 3 | 3 | 10 | 3 | 0 | 1.29% | 1.78 | 41.42% | 19.49% | 3.52 |
| BTCUSDT | long | C_rejection | 11351 | 316 | 269 | 192 | 21 | 2 | 2 | 3 | 2 | 0 | 0.92% | 1.46 | 54.79% | 24.03% | 2.62 |
| BTCUSDT | short | A_breakout_retest | 1472 | 502 | 459 | 135 | 134 | 108 | 88 | 71 | 79 | 16 | 2.92% | 4.14 | 11.77% | 4.77% | 2.23 |
| BTCUSDT | short | B_reclaim | 4491 | 1146 | 983 | 354 | 173 | 8 | 8 | 21 | 8 | 0 | 1.28% | 1.79 | 30.82% | 13.81% | 3.19 |
| BTCUSDT | short | C_rejection | 11351 | 272 | 217 | 167 | 37 | 3 | 1 | 3 | 1 | 0 | 0.93% | 1.49 | 40.71% | 18.20% | 5.86 |
| ETHUSDT | long | A_breakout_retest | 1642 | 579 | 540 | 145 | 132 | 53 | 43 | 36 | 30 | 16 | 3.19% | 3.69 | 18.15% | 7.41% | 2.16 |
| ETHUSDT | long | B_reclaim | 4693 | 1193 | 1084 | 502 | 218 | 11 | 8 | 12 | 8 | 1 | 1.79% | 1.79 | 29.78% | 14.78% | 2.86 |
| ETHUSDT | long | C_rejection | 11382 | 317 | 266 | 196 | 34 | 5 | 5 | 6 | 5 | 0 | 1.17% | 1.38 | 42.66% | 19.87% | 3.15 |
| ETHUSDT | short | A_breakout_retest | 1642 | 584 | 556 | 180 | 179 | 154 | 124 | 102 | 114 | 16 | 3.33% | 3.63 | 10.38% | 4.26% | 2.21 |
| ETHUSDT | short | B_reclaim | 4693 | 1233 | 1135 | 465 | 326 | 25 | 23 | 36 | 22 | 1 | 1.74% | 1.75 | 22.97% | 10.85% | 3.28 |
| ETHUSDT | short | C_rejection | 11382 | 242 | 203 | 150 | 51 | 3 | 2 | 6 | 2 | 0 | 1.13% | 1.42 | 32.17% | 15.34% | 3.47 |

合计：candidate 8163 → after_trend 7346 → after_price 3059 → after_cost 1507 → after_rr 403 → after_risk 333 → order 324 → fill 291

主要拒绝原因：BTCUSDT/long/A_breakout_retest: {'net_rr': 464, 'volume': 315, 'stop_distance': 259, 'regime_trend': 138, 'cost_to_risk': 137, 'portfolio': 2}；BTCUSDT/long/B_reclaim: {'net_rr': 1044, 'cost_to_risk': 961, 'volume': 639, 'regime_trend': 228, 'stop_distance': 12, 'volatility': 4}；BTCUSDT/long/C_rejection: {'cost_to_risk': 290, 'net_rr': 274, 'volume': 75, 'regime_trend': 62, 'stop_distance': 2}；BTCUSDT/short/A_breakout_retest: {'volume': 229, 'stop_distance': 213, 'regime_trend': 143, 'net_rr': 109, 'portfolio': 20, 'volatility': 5}；BTCUSDT/short/B_reclaim: {'net_rr': 942, 'cost_to_risk': 718, 'volume': 619, 'regime_trend': 319, 'stop_distance': 13, 'volatility': 7}；BTCUSDT/short/C_rejection: {'cost_to_risk': 225, 'net_rr': 220, 'regime_trend': 67, 'volume': 49, 'portfolio': 2, 'volatility': 1}；ETHUSDT/long/A_breakout_retest: {'net_rr': 373, 'volume': 348, 'stop_distance': 187, 'regime_trend': 123, 'cost_to_risk': 55, 'portfolio': 10}；ETHUSDT/long/B_reclaim: {'net_rr': 1034, 'cost_to_risk': 736, 'volume': 569, 'regime_trend': 215, 'stop_distance': 12, 'volatility': 11}；ETHUSDT/long/C_rejection: {'cost_to_risk': 269, 'net_rr': 264, 'volume': 70, 'regime_trend': 64}；ETHUSDT/short/A_breakout_retest: {'volume': 304, 'stop_distance': 184, 'regime_trend': 121, 'net_rr': 109, 'portfolio': 30, 'volatility': 8}；ETHUSDT/short/B_reclaim: {'net_rr': 1033, 'volume': 659, 'cost_to_risk': 544, 'regime_trend': 205, 'volatility': 11, 'stop_distance': 9}；ETHUSDT/short/C_rejection: {'net_rr': 195, 'cost_to_risk': 171, 'regime_trend': 52, 'volume': 49, 'portfolio': 3, 'volatility': 2}

限价模式（ALL, limit, single）：挂单 359，成交 232，未成交 127（{'cancel:timeout': 112, 'cancel:replaced_by_new_signal': 10, 'cancel:tp_hit_before_fill': 5}）；未成交单若按市价入场的假设净盈亏合计 2345.51 USDT，平均 0.52R。

### m15（ALL, market, single）

| symbol | direction | strategy | setup | candidate | after_trend | after_price | after_cost | after_rr | after_risk | order | fill | cancelled_at_fill | avg_stop_distance_pct | avg_stop_distance_atr | median_cost_to_risk | median_cost_share_of_reward | avg_net_rr_valid |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | long | A_breakout_retest | 609 | 204 | 202 | 12 | 10 | 3 | 3 | 3 | 2 | 2 | 2.12% | 7.64 | 17.52% | 7.08% | 2.24 |
| BTCUSDT | long | B_reclaim | 3720 | 1033 | 919 | 315 | 14 | 3 | 3 | 3 | 2 | 1 | 0.55% | 1.74 | 77.63% | 25.42% | 3.48 |
| BTCUSDT | long | C_rejection | 15298 | 449 | 363 | 214 | 5 | 0 | 0 | 0 | 0 | 0 | 0.38% | 1.45 | 114.03% | 28.32% | nan |
| BTCUSDT | short | A_breakout_retest | 609 | 173 | 173 | 15 | 15 | 6 | 6 | 4 | 5 | 2 | 2.22% | 7.49 | 14.62% | 5.89% | 2.11 |
| BTCUSDT | short | B_reclaim | 3720 | 953 | 880 | 314 | 30 | 3 | 3 | 5 | 3 | 1 | 0.57% | 1.74 | 70.68% | 20.87% | 7.76 |
| BTCUSDT | short | C_rejection | 15298 | 418 | 362 | 208 | 1 | 0 | 0 | 0 | 0 | 0 | 0.37% | 1.41 | 105.68% | 24.10% | nan |
| ETHUSDT | long | A_breakout_retest | 640 | 178 | 173 | 18 | 18 | 12 | 12 | 11 | 9 | 5 | 2.61% | 6.13 | 14.87% | 6.08% | 2.17 |
| ETHUSDT | long | B_reclaim | 3771 | 1013 | 923 | 379 | 78 | 12 | 12 | 11 | 11 | 2 | 0.88% | 1.74 | 49.53% | 15.62% | 3.49 |
| ETHUSDT | long | C_rejection | 13708 | 384 | 338 | 220 | 11 | 3 | 3 | 4 | 3 | 0 | 0.58% | 1.42 | 71.07% | 20.34% | 2.92 |
| ETHUSDT | short | A_breakout_retest | 640 | 200 | 200 | 29 | 29 | 23 | 22 | 19 | 19 | 5 | 2.65% | 6.01 | 12.75% | 5.30% | 2.35 |
| ETHUSDT | short | B_reclaim | 3771 | 1046 | 989 | 399 | 93 | 15 | 13 | 14 | 11 | 2 | 0.88% | 1.74 | 43.77% | 14.95% | 3.62 |
| ETHUSDT | short | C_rejection | 13708 | 347 | 326 | 213 | 13 | 4 | 2 | 3 | 2 | 0 | 0.56% | 1.40 | 70.27% | 18.96% | 4.15 |

合计：candidate 6398 → after_trend 5848 → after_price 2336 → after_cost 317 → after_rr 84 → after_risk 79 → order 77 → fill 67

主要拒绝原因：BTCUSDT/long/A_breakout_retest: {'stop_distance': 187, 'net_rr': 130, 'volume': 108, 'regime_trend': 67, 'cost_to_risk': 30, 'volatility': 5}；BTCUSDT/long/B_reclaim: {'cost_to_risk': 1002, 'net_rr': 764, 'volume': 589, 'regime_trend': 235, 'volatility': 36, 'stop_distance': 8}；BTCUSDT/long/C_rejection: {'cost_to_risk': 444, 'net_rr': 332, 'volume': 126, 'regime_trend': 126, 'volatility': 26, 'stop_distance': 3}；BTCUSDT/short/A_breakout_retest: {'stop_distance': 157, 'volume': 91, 'net_rr': 70, 'regime_trend': 36, 'cost_to_risk': 4, 'volatility': 1}；BTCUSDT/short/B_reclaim: {'cost_to_risk': 901, 'net_rr': 653, 'volume': 551, 'regime_trend': 192, 'volatility': 30, 'stop_distance': 6}；BTCUSDT/short/C_rejection: {'cost_to_risk': 417, 'net_rr': 265, 'volume': 134, 'regime_trend': 85, 'volatility': 26}；ETHUSDT/long/A_breakout_retest: {'stop_distance': 147, 'volume': 97, 'net_rr': 68, 'regime_trend': 32}；ETHUSDT/long/B_reclaim: {'cost_to_risk': 869, 'net_rr': 621, 'volume': 538, 'regime_trend': 185, 'volatility': 9, 'stop_distance': 3}；ETHUSDT/long/C_rejection: {'cost_to_risk': 370, 'net_rr': 237, 'volume': 113, 'regime_trend': 67, 'volatility': 3, 'stop_distance': 2}；ETHUSDT/short/A_breakout_retest: {'stop_distance': 156, 'volume': 110, 'net_rr': 46, 'regime_trend': 19, 'portfolio': 1, 'cost_to_risk': 1}；ETHUSDT/short/B_reclaim: {'cost_to_risk': 876, 'net_rr': 655, 'volume': 582, 'regime_trend': 116, 'stop_distance': 8, 'volatility': 5}；ETHUSDT/short/C_rejection: {'cost_to_risk': 331, 'net_rr': 214, 'volume': 108, 'regime_trend': 30, 'volatility': 6, 'portfolio': 2}

限价模式（ALL, limit, single）：挂单 81，成交 59，未成交 22（{'cancel:timeout': 22}）；未成交单若按市价入场的假设净盈亏合计 575.81 USDT，平均 0.62R。

### h4（ALL, market, single）

| symbol | direction | strategy | setup | candidate | after_trend | after_price | after_cost | after_rr | after_risk | order | fill | cancelled_at_fill | avg_stop_distance_pct | avg_stop_distance_atr | median_cost_to_risk | median_cost_share_of_reward | avg_net_rr_valid |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | long | A_breakout_retest | 2293 | 896 | 838 | 326 | 56 | 3 | 3 | 2 | 3 | 1 | 4.18% | 2.53 | 33.05% | 13.52% | 2.03 |
| BTCUSDT | long | B_reclaim | 3609 | 948 | 874 | 354 | 125 | 2 | 2 | 3 | 2 | 0 | 3.24% | 1.80 | 30.40% | 15.24% | 6.06 |
| BTCUSDT | long | C_rejection | 7117 | 190 | 159 | 119 | 18 | 0 | 0 | 0 | 0 | 0 | 2.58% | 1.51 | 36.74% | 18.97% | nan |
| BTCUSDT | short | A_breakout_retest | 2293 | 765 | 680 | 256 | 256 | 232 | 157 | 107 | 140 | 1 | 4.58% | 2.60 | 8.24% | 3.35% | 2.23 |
| BTCUSDT | short | B_reclaim | 3609 | 849 | 774 | 257 | 231 | 7 | 5 | 31 | 5 | 0 | 3.13% | 1.74 | 12.32% | 6.03% | 3.30 |
| BTCUSDT | short | C_rejection | 7117 | 127 | 107 | 77 | 64 | 2 | 2 | 10 | 2 | 0 | 2.34% | 1.46 | 17.36% | 8.37% | 2.53 |
| ETHUSDT | long | A_breakout_retest | 2409 | 895 | 839 | 285 | 92 | 8 | 8 | 7 | 4 | 9 | 5.06% | 2.31 | 27.73% | 11.52% | 2.12 |
| ETHUSDT | long | B_reclaim | 3813 | 955 | 913 | 303 | 167 | 5 | 4 | 5 | 4 | 0 | 4.47% | 1.77 | 21.94% | 10.93% | 2.94 |
| ETHUSDT | long | C_rejection | 7085 | 140 | 128 | 79 | 20 | 1 | 1 | 1 | 1 | 0 | 3.28% | 1.41 | 32.36% | 15.99% | 2.31 |
| ETHUSDT | short | A_breakout_retest | 2409 | 824 | 767 | 243 | 243 | 230 | 156 | 110 | 132 | 9 | 5.48% | 2.32 | 6.88% | 2.85% | 2.25 |
| ETHUSDT | short | B_reclaim | 3813 | 939 | 871 | 285 | 277 | 3 | 3 | 27 | 3 | 0 | 4.19% | 1.76 | 9.39% | 4.75% | 2.37 |
| ETHUSDT | short | C_rejection | 7085 | 111 | 87 | 53 | 52 | 2 | 2 | 5 | 2 | 0 | 2.99% | 1.44 | 12.84% | 6.75% | 2.39 |

合计：candidate 7639 → after_trend 7037 → after_price 2637 → after_cost 1601 → after_rr 495 → after_risk 343 → order 308 → fill 298

主要拒绝原因：BTCUSDT/long/A_breakout_retest: {'net_rr': 855, 'cost_to_risk': 696, 'volume': 445, 'regime_trend': 153, 'volatility': 72, 'stop_distance': 54}；BTCUSDT/long/B_reclaim: {'net_rr': 917, 'cost_to_risk': 589, 'volume': 463, 'regime_trend': 137, 'volatility': 96, 'stop_distance': 9}；BTCUSDT/long/C_rejection: {'net_rr': 182, 'cost_to_risk': 153, 'regime_trend': 35, 'volume': 31, 'volatility': 10}；BTCUSDT/short/A_breakout_retest: {'volume': 338, 'regime_trend': 180, 'net_rr': 98, 'volatility': 83, 'portfolio': 75, 'stop_distance': 62}；BTCUSDT/short/B_reclaim: {'net_rr': 795, 'volume': 484, 'regime_trend': 191, 'cost_to_risk': 103, 'volatility': 74, 'portfolio': 13}；BTCUSDT/short/C_rejection: {'net_rr': 115, 'cost_to_risk': 28, 'volume': 25, 'regime_trend': 25, 'volatility': 7, 'portfolio': 3}；ETHUSDT/long/A_breakout_retest: {'net_rr': 792, 'cost_to_risk': 534, 'volume': 453, 'volatility': 173, 'regime_trend': 152, 'stop_distance': 21}；ETHUSDT/long/B_reclaim: {'net_rr': 922, 'volume': 492, 'cost_to_risk': 394, 'volatility': 218, 'regime_trend': 109, 'stop_distance': 10}；ETHUSDT/long/C_rejection: {'net_rr': 134, 'cost_to_risk': 95, 'volume': 33, 'volatility': 20, 'regime_trend': 19}；ETHUSDT/short/A_breakout_retest: {'volume': 388, 'volatility': 213, 'regime_trend': 178, 'net_rr': 89, 'portfolio': 74, 'stop_distance': 26}；ETHUSDT/short/B_reclaim: {'net_rr': 905, 'volume': 486, 'volatility': 216, 'regime_trend': 164, 'cost_to_risk': 38, 'portfolio': 16}；ETHUSDT/short/C_rejection: {'net_rr': 103, 'regime_trend': 30, 'volume': 20, 'volatility': 18, 'portfolio': 3, 'cost_to_risk': 2}

cost_to_risk 语义审计：cost_to_risk = (entry×(fee+slip) + stop×(fee+slip+stop_slip) + funding_est) / |entry − stop_initial|；初始结构止损距离（含 sl_buffer_atr 缓冲），不是保本止损/移动止损后的距离；移动止损发生在持仓期间，不参与开仓过滤；分母使用 Signal.risk_per_unit = |ref_price − stop_price|，在 FilterPipeline.apply 时 stop_price 为策略给出的初始止损，trail/breakeven 只在 PositionManager.manage 中修改持仓对象，不回写信号；审计结论：无混淆。 样例：15m 止损 0.5% → 比值 70%（成本 35 bps）；1h 止损 1.5% → 29%（其中资金费按最长持仓 96 小时估算占 120/437）。资金费按 max_hold_bars 全程估算属于保守上界（多数交易更早退出）；止损额外滑点 10 bps 为假设。

## E. 各项费用对收益的影响

- 1h 资金费=样本均值 0.00004/8h · A_breakout_retest__entry_market__mtf_single：303 笔，PF 0.92，ExpR -0.038，净收益 -6.23%，手续费 457.43 滑点 643.84 资金费 51.22
- 1h 资金费=样本均值 0.00004/8h · ALL__entry_market__mtf_single：359 笔，PF 0.87，ExpR -0.072，净收益 -12.94%，手续费 579.11 滑点 829.50 资金费 57.48
- 1h 滑点 10 bps、止损滑点 20 bps · A_breakout_retest__entry_market__mtf_single：66 笔，PF 0.87，ExpR -0.071，净收益 -2.45%，手续费 73.11 滑点 207.70 资金费 13.05
- 1h 滑点 10 bps、止损滑点 20 bps · ALL__entry_market__mtf_single：84 笔，PF 0.71，ExpR -0.177，净收益 -7.32%，手续费 99.35 滑点 289.47 资金费 17.72
- 1h taker 10 bps · A_breakout_retest__entry_market__mtf_single：190 笔，PF 0.91，ExpR -0.044，净收益 -4.52%，手续费 441.36 滑点 375.08 资金费 30.90
- 1h taker 10 bps · ALL__entry_market__mtf_single：225 笔，PF 0.81，ExpR -0.106，净收益 -11.71%，手续费 549.59 滑点 476.88 资金费 39.06
- 15m 资金费=样本均值 0.00004/8h · A_breakout_retest__entry_market__mtf_single：41 笔，PF 1.36，ExpR 0.180，净收益 3.66%，手续费 78.71 滑点 99.43 资金费 3.86
- 15m 资金费=样本均值 0.00004/8h · ALL__entry_market__mtf_single：74 笔，PF 1.29，ExpR 0.145，净收益 5.30%，手续费 181.47 滑点 247.16 资金费 6.26

## F. 三套策略在 BTC/ETH 上的真实样本外表现（Walk-Forward 第二阶段：含隔离 7 天、退出参数入网格、Bootstrap、扰动）

### 1h（12/6/6 月）

| 策略 | 折 | OOS 交易 | 净收益 | 平均R | 胜率 | PF | 最大回撤 | Sharpe | Sortino | 最长连亏 | 均值R 95%CI | 折非负占比 | 默认参数全程 交易/ExpR/PF | 扰动 ExpR 范围（正占比） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 6 | 165 | 8.59% | 0.103 | 40.0% | 1.18 | -10.67% | 0.46 | 0.28 | 10 | [-0.153, 0.349] | 0.67 | 171 / 0.030 / 1.04 | -0.021~0.030（71%） |
| B_reclaim | 6 | 31 | -10.18% | -0.661 | 19.4% | 0.15 | -10.19% | -2.33 | -0.44 | 8 | [-0.870, -0.436] | 0.00 | 32 / -0.569 / 0.25 | -0.582~-0.538（0%） |
| C_rejection | 6 | 12 | -1.08% | -0.174 | 33.3% | 0.72 | -2.42% | -0.45 | -0.07 | 5 | [-0.796, 0.912] | 0.50 | 12 / -0.174 / 0.72 | -0.958~-0.149（0%） |

- **A_breakout_retest** 年度：2023 6 笔 ExpR -0.39 净 -117.09；2024 54 笔 ExpR 0.03 净 75.20；2025 68 笔 ExpR 0.01 净 43.46；2026 37 笔 ExpR 0.45 净 857.90。方向：long 42 笔 ExpR 0.02；short 123 笔 ExpR 0.13。环境：上涨趋势 50 笔 ExpR 0.17；下跌趋势 57 笔 ExpR -0.09；横盘震荡 58 笔 ExpR 0.23。标的：BTCUSDT 58 笔 ExpR -0.11；ETHUSDT 107 笔 ExpR 0.22
  - 费用敏感性（按交易记录重算）：基准 ExpR 0.103；手续费×2 0.072；滑点×2 0.060；滑点×3 0.018；资金费×3 0.092；全部×2 0.024；盈亏平衡费用倍数 2.42
  - 各折测试：2023-10→2024-04 19 笔 ExpR 0.17(默认)；2024-04→2024-10 27 笔 ExpR 0.09(默认)；2024-10→2025-04 40 笔 ExpR -0.15；2025-04→2025-10 22 笔 ExpR -0.41(默认)；2025-10→2026-04 40 笔 ExpR 0.35(默认)；2026-04→2026-10 17 笔 ExpR 0.74
- **B_reclaim** 年度：2024 9 笔 ExpR -0.86 净 -385.35；2025 13 笔 ExpR -0.66 净 -427.86；2026 9 笔 ExpR -0.46 净 -204.67。方向：long 11 笔 ExpR -0.68；short 20 笔 ExpR -0.65。环境：上涨趋势 5 笔 ExpR -0.77；下跌趋势 9 笔 ExpR -0.86；横盘震荡 17 笔 ExpR -0.53。标的：BTCUSDT 7 笔 ExpR -0.69；ETHUSDT 24 笔 ExpR -0.65
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.661；手续费×2 -0.707；滑点×2 -0.736；滑点×3 -0.811；资金费×3 -0.669；全部×2 -0.786；盈亏平衡费用倍数 -4.47
  - 各折测试：2023-10→2024-04 2 笔 ExpR -0.96；2024-04→2024-10 5 笔 ExpR -0.77(默认)；2024-10→2025-04 6 笔 ExpR -0.64(默认)；2025-04→2025-10 6 笔 ExpR -0.64(默认)；2025-10→2026-04 6 笔 ExpR -0.36(默认)；2026-04→2026-10 6 笔 ExpR -0.81(默认)
- **C_rejection** 年度：2024 1 笔 ExpR 0.00 净 0.15；2025 5 笔 ExpR -0.36 净 -90.26；2026 6 笔 ExpR -0.05 净 -17.56。方向：long 6 笔 ExpR -0.46；short 6 笔 ExpR 0.12。环境：下跌趋势 8 笔 ExpR -0.15；横盘震荡 4 笔 ExpR -0.21。标的：BTCUSDT 4 笔 ExpR -0.48；ETHUSDT 8 笔 ExpR -0.02
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.174；手续费×2 -0.221；滑点×2 -0.247；滑点×3 -0.320；资金费×3 -0.187；全部×2 -0.301；盈亏平衡费用倍数 -0.49
  - 各折测试：2023-10→2024-04 0 笔 ExpR —(默认)；2024-04→2024-10 1 笔 ExpR 0.00(默认)；2024-10→2025-04 2 笔 ExpR -0.46(默认)；2025-04→2025-10 0 笔 ExpR —(默认)；2025-10→2026-04 7 笔 ExpR 0.11(默认)；2026-04→2026-10 2 笔 ExpR -0.97(默认)

### 15m（8/3/3 月）

| 策略 | 折 | OOS 交易 | 净收益 | 平均R | 胜率 | PF | 最大回撤 | Sharpe | Sortino | 最长连亏 | 均值R 95%CI | 折非负占比 | 默认参数全程 交易/ExpR/PF | 扰动 ExpR 范围（正占比） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 3 | 10 | 1.71% | 0.346 | 50.0% | 1.68 | -1.94% | 0.95 | 0.32 | 4 | [-0.363, 1.056] | 0.50 | 11 / 0.370 / 1.89 | 0.155~0.547（100%） |
| B_reclaim | 3 | 12 | 0.36% | 0.063 | 50.0% | 1.14 | -1.36% | 0.20 | 0.08 | 2 | [-0.434, 0.719] | 0.67 | 13 / 0.221 / 1.57 | -0.003~0.321（86%） |
| C_rejection | 3 | 2 | -0.47% | -0.470 | 50.0% | 0.03 | -0.49% | -10.52 | -6.17 | 1 | [-0.470, -0.470] | 0.00 | 2 / -0.470 / 0.03 | -0.972~-0.309（0%） |

- **A_breakout_retest** 年度：2026 10 笔 ExpR 0.35 净 171.36。方向：long 1 笔 ExpR -0.97；short 9 笔 ExpR 0.49。环境：下跌趋势 6 笔 ExpR 0.04；横盘震荡 4 笔 ExpR 0.81。标的：BTCUSDT 4 笔 ExpR 0.55；ETHUSDT 6 笔 ExpR 0.21
  - 费用敏感性（按交易记录重算）：基准 ExpR 0.346；手续费×2 0.307；滑点×2 0.295；滑点×3 0.244；资金费×3 0.344；全部×2 0.255；盈亏平衡费用倍数 4.73
  - 各折测试：2026-01→2026-04 9 笔 ExpR 0.49；2026-04→2026-07 0 笔 ExpR —；2026-07→2026-10 1 笔 ExpR -0.97(默认)
- **B_reclaim** 年度：2026 12 笔 ExpR 0.06 净 35.65。方向：long 5 笔 ExpR -0.10；short 7 笔 ExpR 0.18。环境：下跌趋势 8 笔 ExpR -0.19；横盘震荡 4 笔 ExpR 0.57。标的：BTCUSDT 4 笔 ExpR 0.07；ETHUSDT 8 笔 ExpR 0.06
  - 费用敏感性（按交易记录重算）：基准 ExpR 0.063；手续费×2 0.007；滑点×2 -0.020；滑点×3 -0.102；资金费×3 0.057；全部×2 -0.079；盈亏平衡费用倍数 1.43
  - 各折测试：2026-01→2026-04 8 笔 ExpR 0.18(默认)；2026-04→2026-07 2 笔 ExpR -0.97(默认)；2026-07→2026-10 2 笔 ExpR 0.62(默认)
- **C_rejection** 年度：2026 2 笔 ExpR -0.47 净 -47.00。方向：short 2 笔 ExpR -0.47。环境：横盘震荡 2 笔 ExpR -0.47。标的：ETHUSDT 2 笔 ExpR -0.47
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.470；手续费×2 -0.542；滑点×2 -0.590；滑点×3 -0.709；资金费×3 -0.470；全部×2 -0.661；盈亏平衡费用倍数 -1.47
  - 各折测试：2026-01→2026-04 2 笔 ExpR -0.47(默认)；2026-04→2026-07 0 笔 ExpR —(默认)；2026-07→2026-10 0 笔 ExpR —(默认)

### 4h（12/6/6 月，仅分析）

| 策略 | 折 | OOS 交易 | 净收益 | 平均R | 胜率 | PF | 最大回撤 | Sharpe | Sortino | 最长连亏 | 均值R 95%CI | 折非负占比 | 默认参数全程 交易/ExpR/PF | 扰动 ExpR 范围（正占比） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 13 | 219 | -5.11% | -0.049 | 37.0% | 0.93 | -17.19% | -0.18 | -0.07 | 12 | [-0.283, 0.206] | 0.31 | 229 / -0.046 / 0.91 | -0.094~-0.022（0%） |
| B_reclaim | 13 | 12 | 2.64% | 0.441 | 41.7% | 2.07 | -1.47% | 0.28 | 0.07 | 3 | [-0.427, 1.121] | 0.29 | 14 / 0.242 / 1.46 | 0.242~0.624（100%） |
| C_rejection | 13 | 4 | 1.36% | 0.683 | 50.0% | 2.38 | -0.49% | 0.33 | 0.03 | 1 | [0.683, 0.683] | 0.67 | 4 / 0.683 / 2.36 | 0.147~0.683（100%） |

- **A_breakout_retest** 年度：2019 1 笔 ExpR -0.99 净 -49.46；2020 25 笔 ExpR -0.43 净 -532.61；2021 16 笔 ExpR -0.44 净 -354.67；2022 44 笔 ExpR 0.21 净 481.48；2023 31 笔 ExpR -0.09 净 -131.75；2024 36 笔 ExpR -0.25 净 -453.67；2025 42 笔 ExpR -0.11 净 -218.85；2026 24 笔 ExpR 0.62 净 748.38。方向：long 7 笔 ExpR 0.20；short 212 笔 ExpR -0.06。环境：上涨趋势 62 笔 ExpR -0.24；下跌趋势 85 笔 ExpR 0.05；横盘震荡 72 笔 ExpR -0.00。标的：BTCUSDT 112 笔 ExpR -0.09；ETHUSDT 107 笔 ExpR -0.01
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.049；手续费×2 -0.076；滑点×2 -0.088；滑点×3 -0.126；资金费×3 -0.052；全部×2 -0.116；盈亏平衡费用倍数 0.28
  - 各折测试：2019-12→2020-06 20 笔 ExpR -0.35(默认)；2020-06→2020-12 6 笔 ExpR -0.80(默认)；2020-12→2021-06 3 笔 ExpR -0.32(默认)；2021-06→2021-12 10 笔 ExpR -0.97(默认)；2021-12→2022-06 30 笔 ExpR 0.60(默认)；2022-06→2022-12 15 笔 ExpR -0.21(默认)；2022-12→2023-06 17 笔 ExpR 0.18(默认)；2023-06→2023-12 16 笔 ExpR -0.48(默认)；2023-12→2024-06 11 笔 ExpR -0.22(默认)；2024-06→2024-12 25 笔 ExpR -0.26(默认)；2024-12→2025-06 16 笔 ExpR -0.59(默认)；2025-06→2025-12 23 笔 ExpR 0.34(默认)；2025-12→2026-06 27 笔 ExpR 0.45
- **B_reclaim** 年度：2020 1 笔 ExpR 7.89 净 394.34；2021 2 笔 ExpR -0.51 净 -50.75；2022 3 笔 ExpR -0.36 净 -53.30；2024 3 笔 ExpR 0.45 净 66.60；2025 3 笔 ExpR -0.62 净 -92.82。方向：long 5 笔 ExpR 1.86；short 7 笔 ExpR -0.57。环境：上涨趋势 7 笔 ExpR 1.18；下跌趋势 2 笔 ExpR -0.48；横盘震荡 3 笔 ExpR -0.66。标的：BTCUSDT 5 笔 ExpR 1.17；ETHUSDT 7 笔 ExpR -0.08
  - 费用敏感性（按交易记录重算）：基准 ExpR 0.441；手续费×2 0.409；滑点×2 0.391；滑点×3 0.340；资金费×3 0.400；全部×2 0.338；盈亏平衡费用倍数 6.33
  - 各折测试：2019-12→2020-06 0 笔 ExpR —(默认)；2020-06→2020-12 1 笔 ExpR 7.89(默认)；2020-12→2021-06 0 笔 ExpR —(默认)；2021-06→2021-12 2 笔 ExpR -0.51(默认)；2021-12→2022-06 2 笔 ExpR -0.04(默认)；2022-06→2022-12 1 笔 ExpR -0.99(默认)；2022-12→2023-06 0 笔 ExpR —(默认)；2023-06→2023-12 0 笔 ExpR —(默认)；2023-12→2024-06 3 笔 ExpR 0.45(默认)；2024-06→2024-12 0 笔 ExpR —(默认)；2024-12→2025-06 2 笔 ExpR -0.49(默认)；2025-06→2025-12 1 笔 ExpR -0.88(默认)；2025-12→2026-06 0 笔 ExpR —(默认)
- **C_rejection** 年度：2020 2 笔 ExpR 0.65 净 64.18；2022 1 笔 ExpR 2.41 净 120.50；2025 1 笔 ExpR -0.98 净 -48.83。方向：long 1 笔 ExpR 2.41；short 3 笔 ExpR 0.11。环境：上涨趋势 1 笔 ExpR -0.98；横盘震荡 3 笔 ExpR 1.24。标的：BTCUSDT 2 笔 ExpR 0.65；ETHUSDT 2 笔 ExpR 0.71
  - 费用敏感性（按交易记录重算）：基准 ExpR 0.683；手续费×2 0.637；滑点×2 0.626；滑点×3 0.569；资金费×3 0.679；全部×2 0.579；盈亏平衡费用倍数 7.64
  - 各折测试：2019-12→2020-06 2 笔 ExpR 0.65(默认)；2020-06→2020-12 0 笔 ExpR —(默认)；2020-12→2021-06 0 笔 ExpR —(默认)；2021-06→2021-12 0 笔 ExpR —(默认)；2021-12→2022-06 1 笔 ExpR 2.41(默认)；2022-06→2022-12 0 笔 ExpR —(默认)；2022-12→2023-06 0 笔 ExpR —(默认)；2023-06→2023-12 0 笔 ExpR —(默认)；2023-12→2024-06 0 笔 ExpR —(默认)；2024-06→2024-12 0 笔 ExpR —(默认)；2024-12→2025-06 1 笔 ExpR -0.98(默认)；2025-06→2025-12 0 笔 ExpR —(默认)；2025-12→2026-06 0 笔 ExpR —(默认)

### 1h + 训练窗环境启停

| 策略 | 折 | OOS 交易 | 净收益 | 平均R | 胜率 | PF | 最大回撤 | Sharpe | Sortino | 最长连亏 | 均值R 95%CI | 折非负占比 | 默认参数全程 交易/ExpR/PF | 扰动 ExpR 范围（正占比） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 6 | 75 | -2.58% | -0.067 | 34.7% | 0.89 | -6.66% | -0.26 | -0.09 | 8 | [-0.353, 0.183] | 0.33 | 171 / 0.030 / 1.04 | —~—（—） |
| B_reclaim | 6 | 29 | -9.22% | -0.640 | 20.7% | 0.17 | -9.24% | -2.25 | -0.43 | 8 | [-0.867, -0.415] | 0.00 | 32 / -0.569 / 0.25 | —~—（—） |
| C_rejection | 6 | 12 | -1.08% | -0.174 | 33.3% | 0.72 | -2.42% | -0.45 | -0.07 | 5 | [-0.796, 0.912] | 0.50 | 12 / -0.174 / 0.72 | —~—（—） |

- **A_breakout_retest** 年度：2023 1 笔 ExpR -0.99 净 -49.33；2024 16 笔 ExpR -0.51 净 -401.27；2025 44 笔 ExpR 0.02 净 45.27；2026 14 笔 ExpR 0.22 净 147.18。方向：long 20 笔 ExpR -0.07；short 55 笔 ExpR -0.07。环境：上涨趋势 24 笔 ExpR 0.02；下跌趋势 30 笔 ExpR -0.14；横盘震荡 21 笔 ExpR -0.06。标的：BTCUSDT 24 笔 ExpR -0.13；ETHUSDT 51 笔 ExpR -0.04
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.067；手续费×2 -0.097；滑点×2 -0.110；滑点×3 -0.153；资金费×3 -0.077；全部×2 -0.145；盈亏平衡费用倍数 0.05
  - 各折测试：2023-10→2024-04 1 笔 ExpR -0.99 环境 ['下跌趋势']；2024-04→2024-10 2 笔 ExpR -0.98(默认) 环境 ['下跌趋势']；2024-10→2025-04 40 笔 ExpR -0.15 环境 None；2025-04→2025-10 11 笔 ExpR -0.12(默认) 环境 ['上涨趋势', '横盘震荡']；2025-10→2026-04 20 笔 ExpR 0.11(默认) 环境 ['上涨趋势', '下跌趋势']；2026-04→2026-10 1 笔 ExpR 3.21 环境 ['上涨趋势']
- **B_reclaim** 年度：2024 7 笔 ExpR -0.83 净 -289.29；2025 13 笔 ExpR -0.66 净 -427.86；2026 9 笔 ExpR -0.46 净 -204.67。方向：long 10 笔 ExpR -0.66；short 19 笔 ExpR -0.63。环境：上涨趋势 3 笔 ExpR -0.64；下跌趋势 9 笔 ExpR -0.86；横盘震荡 17 笔 ExpR -0.53。标的：BTCUSDT 6 笔 ExpR -0.65；ETHUSDT 23 笔 ExpR -0.64
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.640；手续费×2 -0.686；滑点×2 -0.715；滑点×3 -0.789；资金费×3 -0.647；全部×2 -0.764；盈亏平衡费用倍数 -4.31
  - 各折测试：2023-10→2024-04 0 笔 ExpR — 环境 ['下跌趋势']；2024-04→2024-10 5 笔 ExpR -0.77(默认) 环境 None；2024-10→2025-04 6 笔 ExpR -0.64(默认) 环境 None；2025-04→2025-10 6 笔 ExpR -0.64(默认) 环境 None；2025-10→2026-04 6 笔 ExpR -0.36(默认) 环境 None；2026-04→2026-10 6 笔 ExpR -0.81(默认) 环境 None
- **C_rejection** 年度：2024 1 笔 ExpR 0.00 净 0.15；2025 5 笔 ExpR -0.36 净 -90.26；2026 6 笔 ExpR -0.05 净 -17.56。方向：long 6 笔 ExpR -0.46；short 6 笔 ExpR 0.12。环境：下跌趋势 8 笔 ExpR -0.15；横盘震荡 4 笔 ExpR -0.21。标的：BTCUSDT 4 笔 ExpR -0.48；ETHUSDT 8 笔 ExpR -0.02
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.174；手续费×2 -0.221；滑点×2 -0.247；滑点×3 -0.320；资金费×3 -0.187；全部×2 -0.301；盈亏平衡费用倍数 -0.49
  - 各折测试：2023-10→2024-04 0 笔 ExpR —(默认) 环境 None；2024-04→2024-10 1 笔 ExpR 0.00(默认) 环境 None；2024-10→2025-04 2 笔 ExpR -0.46(默认) 环境 None；2025-04→2025-10 0 笔 ExpR —(默认) 环境 None；2025-10→2026-04 7 笔 ExpR 0.11(默认) 环境 None；2026-04→2026-10 2 笔 ExpR -0.97(默认) 环境 None

### 15m + 训练窗环境启停

| 策略 | 折 | OOS 交易 | 净收益 | 平均R | 胜率 | PF | 最大回撤 | Sharpe | Sortino | 最长连亏 | 均值R 95%CI | 折非负占比 | 默认参数全程 交易/ExpR/PF | 扰动 ExpR 范围（正占比） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 3 | 9 | 2.20% | 0.493 | 55.6% | 2.08 | -1.47% | 2.87 | 2.13 | 3 | [-0.076, 1.172] | 1.00 | 11 / 0.370 / 1.89 | —~—（—） |
| B_reclaim | 3 | 7 | -0.73% | -0.208 | 57.1% | 0.50 | -1.35% | -0.94 | -0.14 | 2 | [-0.521, -0.180] | 0.33 | 13 / 0.221 / 1.57 | —~—（—） |
| C_rejection | 3 | 2 | -0.47% | -0.470 | 50.0% | 0.03 | -0.49% | -10.52 | -6.17 | 1 | [-0.470, -0.470] | 0.00 | 2 / -0.470 / 0.03 | —~—（—） |

- **A_breakout_retest** 年度：2026 9 笔 ExpR 0.49 净 219.95。方向：short 9 笔 ExpR 0.49。环境：下跌趋势 6 笔 ExpR 0.04；横盘震荡 3 笔 ExpR 1.40。标的：BTCUSDT 4 笔 ExpR 0.55；ETHUSDT 5 笔 ExpR 0.45
  - 费用敏感性（按交易记录重算）：基准 ExpR 0.493；手续费×2 0.454；滑点×2 0.443；滑点×3 0.394；资金费×3 0.493；全部×2 0.405；盈亏平衡费用倍数 6.43
  - 各折测试：2026-01→2026-04 9 笔 ExpR 0.49 环境 None；2026-04→2026-07 0 笔 ExpR — 环境 ['横盘震荡']；2026-07→2026-10 0 笔 ExpR —(默认) 环境 ['下跌趋势', '横盘震荡']
- **B_reclaim** 年度：2026 7 笔 ExpR -0.21 净 -72.60。方向：long 4 笔 ExpR 0.12；short 3 笔 ExpR -0.64。环境：下跌趋势 6 笔 ExpR -0.25；横盘震荡 1 笔 ExpR 0.02。标的：BTCUSDT 3 笔 ExpR 0.08；ETHUSDT 4 笔 ExpR -0.43
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.208；手续费×2 -0.263；滑点×2 -0.287；滑点×3 -0.366；资金费×3 -0.219；全部×2 -0.347；盈亏平衡费用倍数 -0.55
  - 各折测试：2026-01→2026-04 3 笔 ExpR -0.25(默认) 环境 ['下跌趋势']；2026-04→2026-07 2 笔 ExpR -0.97(默认) 环境 None；2026-07→2026-10 2 笔 ExpR 0.62(默认) 环境 None
- **C_rejection** 年度：2026 2 笔 ExpR -0.47 净 -47.00。方向：short 2 笔 ExpR -0.47。环境：横盘震荡 2 笔 ExpR -0.47。标的：ETHUSDT 2 笔 ExpR -0.47
  - 费用敏感性（按交易记录重算）：基准 ExpR -0.470；手续费×2 -0.542；滑点×2 -0.590；滑点×3 -0.709；资金费×3 -0.470；全部×2 -0.661；盈亏平衡费用倍数 -1.47
  - 各折测试：2026-01→2026-04 2 笔 ExpR -0.47(默认) 环境 None；2026-04→2026-07 0 笔 ExpR —(默认) 环境 None；2026-07→2026-10 0 笔 ExpR —(默认) 环境 None

## G. 自动筛选结果（Strategy Evaluator）

门槛（全部样本外）：{"min_trades": 30, "min_expectancy_r": 0.0, "min_profit_factor": 1.1, "max_drawdown": 0.2, "min_fold_nonneg_share": 0.5, "bootstrap_ci_lower_r_min": -0.05, "paper_requires_ci_lower_gt0": true, "paper_min_trades": 50, "tf_policy": {"4h": "analysis_only"}}

| 周期 | 策略 | 标的 | 方向 | 状态 | OOS 交易 | ExpR | PF | 净收益 | 回撤 | 均值R 95%CI | 原因 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1h | A_breakout_retest | ALL | ALL | **unqualified** | 165 | 0.103 | 1.18 | 8.59% | -10.67% | [-0.153, 0.349] | 均值 R 置信下限 -0.15302092121624003 < -0.05 |
| 1h | A_breakout_retest | BTCUSDT | ALL | **unqualified** | 58 | -0.107 | 0.82 | -3.15% | -5.18% | [-0.381, 0.173] | 扣费后期望 -0.10666462107896288 ≤ 0.0；PF 0.8185973596585393 < 1.1；均值 R 置信下限 -0.3812125107999465 < -0.05 |
| 1h | A_breakout_retest | ETHUSDT | ALL | **unqualified** | 107 | 0.217 | 1.40 | 11.74% | -7.72% | [-0.094, 0.502] | 均值 R 置信下限 -0.09384182668370712 < -0.05 |
| 1h | A_breakout_retest | ALL | long | **unqualified** | 42 | 0.025 | 1.04 | 0.49% | -4.97% | [-0.357, 0.375] | PF 1.0434962580845721 < 1.1；均值 R 置信下限 -0.3571602485376312 < -0.05 |
| 1h | A_breakout_retest | ALL | short | **unqualified** | 123 | 0.130 | 1.23 | 8.10% | -9.45% | [-0.184, 0.455] | 均值 R 置信下限 -0.18413230651008697 < -0.05 |
| 1h | A_breakout_retest | BTCUSDT | long | **unqualified** | 14 | -0.242 | 0.60 | -1.69% | -2.01% | [-0.516, -0.017] | 样本外交易 14 < 30；扣费后期望 -0.24159698456223805 ≤ 0.0；PF 0.598697020082608 < 1.1；均值 R 置信下限 -0.5158757631668786 < -0.05 |
| 1h | A_breakout_retest | BTCUSDT | short | **unqualified** | 44 | -0.064 | 0.89 | -1.45% | -3.85% | [-0.323, 0.295] | 扣费后期望 -0.0637315963342843 ≤ 0.0；PF 0.8893332507214549 < 1.1；均值 R 置信下限 -0.32273472263054437 < -0.05 |
| 1h | A_breakout_retest | ETHUSDT | long | **unqualified** | 28 | 0.158 | 1.31 | 2.19% | -3.09% | [-0.350, 0.448] | 样本外交易 28 < 30；均值 R 置信下限 -0.34987850723915165 < -0.05 |
| 1h | A_breakout_retest | ETHUSDT | short | **unqualified** | 79 | 0.237 | 1.43 | 9.55% | -6.21% | [-0.143, 0.567] | 均值 R 置信下限 -0.1425745639088136 < -0.05 |
| 1h | B_reclaim | ALL | ALL | **unqualified** | 31 | -0.661 | 0.15 | -10.18% | -10.19% | [-0.870, -0.436] | 扣费后期望 -0.6606438904109686 ≤ 0.0；PF 0.15225022387583267 < 1.1；测试折非负占比 0.0 < 0.5；均值 R 置信下限 -0.8700383644072386 < -0.05 |
| 1h | B_reclaim | BTCUSDT | ALL | **unqualified** | 7 | -0.695 | 0.17 | -2.42% | -2.42% | [-0.695, -0.410] | 样本外交易 7 < 30；扣费后期望 -0.6948699711508431 ≤ 0.0；PF 0.16612793655402794 < 1.1；均值 R 置信下限 -0.6954045512535567 < -0.05 |
| 1h | B_reclaim | ETHUSDT | ALL | **unqualified** | 24 | -0.651 | 0.15 | -7.75% | -7.76% | [-0.885, -0.389] | 样本外交易 24 < 30；扣费后期望 -0.6506612835285052 ≤ 0.0；PF 0.14781670793370502 < 1.1；均值 R 置信下限 -0.8850987862206652 < -0.05 |
| 1h | B_reclaim | ALL | long | **unqualified** | 11 | -0.683 | 0.12 | -3.73% | -3.73% | [-0.954, -0.415] | 样本外交易 11 < 30；扣费后期望 -0.6830722253508303 ≤ 0.0；PF 0.11600153165690825 < 1.1；均值 R 置信下限 -0.9543169096644387 < -0.05 |
| 1h | B_reclaim | ALL | short | **unqualified** | 20 | -0.648 | 0.17 | -6.45% | -6.45% | [-0.881, -0.337] | 样本外交易 20 < 30；扣费后期望 -0.6483083061940447 ≤ 0.0；PF 0.17187815240667415 < 1.1；均值 R 置信下限 -0.8810898590554629 < -0.05 |
| 1h | B_reclaim | BTCUSDT | long | **unqualified** | 3 | -0.314 | 0.50 | -0.48% | -0.49% | [-0.314, -0.314] | 样本外交易 3 < 30；扣费后期望 -0.31411504363744003 ≤ 0.0；PF 0.5031545361890807 < 1.1；均值 R 置信下限 -0.31411504363744003 < -0.05 |
| 1h | B_reclaim | BTCUSDT | short | **unqualified** | 4 | -0.980 | 0.00 | -1.95% | -1.95% | [-0.980, -0.980] | 样本外交易 4 < 30；扣费后期望 -0.9804361667858954 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9804361667858954 < -0.05 |
| 1h | B_reclaim | ETHUSDT | long | **unqualified** | 8 | -0.821 | 0.00 | -3.25% | -3.26% | [-0.948, -0.821] | 样本外交易 8 < 30；扣费后期望 -0.8214311684933517 ≤ 0.0；PF 0.0019358544376085161 < 1.1；均值 R 置信下限 -0.9484356834072849 < -0.05 |
| 1h | B_reclaim | ETHUSDT | short | **unqualified** | 16 | -0.565 | 0.23 | -4.50% | -4.83% | [-0.856, -0.301] | 样本外交易 16 < 30；扣费后期望 -0.5652763410460819 ≤ 0.0；PF 0.22916934977758638 < 1.1；均值 R 置信下限 -0.8557887204374982 < -0.05 |
| 1h | C_rejection | ALL | ALL | **unqualified** | 12 | -0.174 | 0.72 | -1.08% | -2.42% | [-0.796, 0.912] | 样本外交易 12 < 30；扣费后期望 -0.17392869698593197 ≤ 0.0；PF 0.7213332088483034 < 1.1；均值 R 置信下限 -0.7961832975494207 < -0.05 |
| 1h | C_rejection | BTCUSDT | ALL | **unqualified** | 4 | -0.483 | 0.33 | -0.98% | -1.46% | [-0.483, -0.483] | 样本外交易 4 < 30；扣费后期望 -0.4829923308002153 ≤ 0.0；PF 0.3340344699164746 < 1.1；均值 R 置信下限 -0.4829923308002153 < -0.05 |
| 1h | C_rejection | ETHUSDT | ALL | **unqualified** | 8 | -0.019 | 0.96 | -0.10% | -1.41% | [-0.701, 0.563] | 样本外交易 8 < 30；扣费后期望 -0.019396880078790313 ≤ 0.0；PF 0.9591473377424738 < 1.1；均值 R 置信下限 -0.7010077670851123 < -0.05 |
| 1h | C_rejection | ALL | long | **unqualified** | 6 | -0.464 | 0.26 | -1.40% | -1.41% | [-0.621, -0.302] | 样本外交易 6 < 30；扣费后期望 -0.4643915004934602 ≤ 0.0；PF 0.25995997344705046 < 1.1；均值 R 置信下限 -0.6210986995756475 < -0.05 |
| 1h | C_rejection | ALL | short | **unqualified** | 6 | 0.117 | 1.17 | 0.33% | -1.93% | [0.117, 1.044] | 样本外交易 6 < 30 |
| 1h | C_rejection | BTCUSDT | long | **unqualified** | 1 | 0.998 | ∞ | 0.49% | 0.00% | [0.998, 0.998] | 样本外交易 1 < 30 |
| 1h | C_rejection | BTCUSDT | short | **unqualified** | 3 | -0.977 | 0.00 | -1.47% | -1.47% | [-0.977, -0.977] | 样本外交易 3 < 30；扣费后期望 -0.9767589867516908 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9767589867516908 < -0.05 |
| 1h | C_rejection | ETHUSDT | long | **unqualified** | 5 | -0.757 | 0.00 | -1.89% | -1.89% | [-0.757, -0.757] | 样本外交易 5 < 30；扣费后期望 -0.7569313280029945 ≤ 0.0；PF 0.0007802824215953819 < 1.1；均值 R 置信下限 -0.7569313280029945 < -0.05 |
| 1h | C_rejection | ETHUSDT | short | **unqualified** | 3 | 1.210 | 4.59 | 1.80% | -0.49% | [1.210, 1.210] | 样本外交易 3 < 30 |
| 15m | A_breakout_retest | ALL | ALL | **unqualified** | 10 | 0.346 | 1.68 | 1.71% | -1.94% | [-0.363, 1.056] | 样本外交易 10 < 30；均值 R 置信下限 -0.36324344469590103 < -0.05 |
| 15m | A_breakout_retest | BTCUSDT | ALL | **unqualified** | 4 | 0.551 | 2.07 | 1.09% | -0.51% | [0.551, 0.551] | 样本外交易 4 < 30 |
| 15m | A_breakout_retest | ETHUSDT | ALL | **unqualified** | 6 | 0.210 | 1.41 | 0.62% | -1.47% | [0.200, 0.725] | 样本外交易 6 < 30 |
| 15m | A_breakout_retest | ALL | long | **unqualified** | 1 | -0.972 | 0.00 | -0.49% | -0.49% | [-0.972, -0.972] | 样本外交易 1 < 30；扣费后期望 -0.9718644662017659 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9718644662017659 < -0.05 |
| 15m | A_breakout_retest | ALL | short | **unqualified** | 9 | 0.493 | 2.08 | 2.20% | -1.47% | [-0.076, 1.172] | 样本外交易 9 < 30；均值 R 置信下限 -0.07636481079006735 < -0.05 |
| 15m | A_breakout_retest | BTCUSDT | short | **unqualified** | 4 | 0.551 | 2.07 | 1.09% | -0.51% | [0.551, 0.551] | 样本外交易 4 < 30 |
| 15m | A_breakout_retest | ETHUSDT | long | **unqualified** | 1 | -0.972 | 0.00 | -0.49% | -0.49% | [-0.972, -0.972] | 样本外交易 1 < 30；扣费后期望 -0.9718644662017659 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9718644662017659 < -0.05 |
| 15m | A_breakout_retest | ETHUSDT | short | **unqualified** | 5 | 0.446 | 2.09 | 1.11% | -0.99% | [0.446, 0.446] | 样本外交易 5 < 30 |
| 15m | B_reclaim | ALL | ALL | **unqualified** | 12 | 0.063 | 1.14 | 0.36% | -1.36% | [-0.434, 0.719] | 样本外交易 12 < 30；均值 R 置信下限 -0.4336461241892708 < -0.05 |
| 15m | B_reclaim | BTCUSDT | ALL | **unqualified** | 4 | 0.070 | 1.30 | 0.14% | -0.48% | [0.070, 0.070] | 样本外交易 4 < 30 |
| 15m | B_reclaim | ETHUSDT | ALL | **unqualified** | 8 | 0.059 | 1.11 | 0.21% | -0.97% | [-0.325, 0.695] | 样本外交易 8 < 30；均值 R 置信下限 -0.3246626544459969 < -0.05 |
| 15m | B_reclaim | ALL | long | **unqualified** | 5 | -0.099 | 0.74 | -0.25% | -0.97% | [-0.099, -0.099] | 样本外交易 5 < 30；扣费后期望 -0.09894649110066196 ≤ 0.0；PF 0.7435054310023 < 1.1；均值 R 置信下限 -0.09894649110066196 < -0.05 |
| 15m | B_reclaim | ALL | short | **unqualified** | 7 | 0.178 | 1.41 | 0.61% | -0.97% | [-0.288, 1.059] | 样本外交易 7 < 30；均值 R 置信下限 -0.28755595382821925 < -0.05 |
| 15m | B_reclaim | BTCUSDT | long | **unqualified** | 1 | 1.211 | ∞ | 0.61% | 0.00% | [1.211, 1.211] | 样本外交易 1 < 30 |
| 15m | B_reclaim | BTCUSDT | short | **unqualified** | 3 | -0.310 | 0.05 | -0.46% | -0.48% | [-0.310, -0.310] | 样本外交易 3 < 30；扣费后期望 -0.309566103431049 ≤ 0.0；PF 0.048724785496993654 < 1.1；均值 R 置信下限 -0.309566103431049 < -0.05 |
| 15m | B_reclaim | ETHUSDT | long | **unqualified** | 4 | -0.426 | 0.12 | -0.85% | -0.97% | [-0.426, -0.426] | 样本外交易 4 < 30；扣费后期望 -0.4263505964398324 ≤ 0.0；PF 0.12141144401749555 < 1.1；均值 R 置信下限 -0.4263505964398324 < -0.05 |
| 15m | B_reclaim | ETHUSDT | short | **unqualified** | 4 | 0.544 | 2.06 | 1.07% | -0.51% | [0.544, 0.544] | 样本外交易 4 < 30 |
| 15m | C_rejection | ALL | ALL | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；测试折非负占比 0.0 < 0.5；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ETHUSDT | ALL | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ALL | short | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ETHUSDT | short | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 4h | A_breakout_retest | ALL | ALL | **unqualified** | 219 | -0.049 | 0.93 | -5.11% | -17.19% | [-0.283, 0.206] | 扣费后期望 -0.04929210478595421 ≤ 0.0；PF 0.9291828558600379 < 1.1；测试折非负占比 0.3076923076923077 < 0.5；均值 R 置信下限 -0.282949091688239 < -0.05 |
| 4h | A_breakout_retest | BTCUSDT | ALL | **unqualified** | 112 | -0.088 | 0.88 | -4.88% | -10.66% | [-0.377, 0.198] | 扣费后期望 -0.08847548457606032 ≤ 0.0；PF 0.876622575222128 < 1.1；均值 R 置信下限 -0.3771709027619253 < -0.05 |
| 4h | A_breakout_retest | ETHUSDT | ALL | **unqualified** | 107 | -0.008 | 0.99 | -0.23% | -9.48% | [-0.311, 0.230] | 扣费后期望 -0.008277725940235632 ≤ 0.0；PF 0.992933897166275 < 1.1；均值 R 置信下限 -0.3111196982755669 < -0.05 |
| 4h | A_breakout_retest | ALL | long | **unqualified** | 7 | 0.196 | 1.53 | 0.74% | -0.93% | [-0.111, 0.635] | 样本外交易 7 < 30；均值 R 置信下限 -0.1105917253068669 < -0.05 |
| 4h | A_breakout_retest | ALL | short | **unqualified** | 212 | -0.057 | 0.92 | -5.85% | -16.55% | [-0.306, 0.184] | 扣费后期望 -0.05739489954635027 ≤ 0.0；PF 0.917338588704692 < 1.1；均值 R 置信下限 -0.3064131274779618 < -0.05 |
| 4h | A_breakout_retest | BTCUSDT | long | **unqualified** | 3 | 0.668 | 21.00 | 1.05% | -0.03% | [0.668, 0.668] | 样本外交易 3 < 30 |
| 4h | A_breakout_retest | BTCUSDT | short | **unqualified** | 109 | -0.109 | 0.85 | -5.93% | -10.74% | [-0.387, 0.176] | 扣费后期望 -0.10929019363273293 ≤ 0.0；PF 0.8499360553524217 < 1.1；均值 R 置信下限 -0.38701957226601685 < -0.05 |
| 4h | A_breakout_retest | ETHUSDT | long | **unqualified** | 4 | -0.158 | 0.77 | -0.31% | -1.33% | [-0.158, -0.158] | 样本外交易 4 < 30；扣费后期望 -0.1576572694367095 ≤ 0.0；PF 0.7706443636506902 < 1.1；均值 R 置信下限 -0.1576572694367095 < -0.05 |
| 4h | A_breakout_retest | ETHUSDT | short | **unqualified** | 103 | -0.002 | 1.00 | 0.08% | -8.63% | [-0.306, 0.262] | 扣费后期望 -0.002476578619984209 ≤ 0.0；PF 1.002495722311373 < 1.1；均值 R 置信下限 -0.30578034246624486 < -0.05 |
| 4h | B_reclaim | ALL | ALL | **unqualified** | 12 | 0.441 | 2.07 | 2.64% | -1.47% | [-0.427, 1.121] | 样本外交易 12 < 30；测试折非负占比 0.2857142857142857 < 0.5；均值 R 置信下限 -0.42697249458595843 < -0.05 |
| 4h | B_reclaim | BTCUSDT | ALL | **unqualified** | 5 | 1.167 | 3.83 | 2.92% | -0.99% | [1.167, 1.167] | 样本外交易 5 < 30 |
| 4h | B_reclaim | ETHUSDT | ALL | **unqualified** | 7 | -0.077 | 0.81 | -0.28% | -0.93% | [-0.214, 0.381] | 样本外交易 7 < 30；扣费后期望 -0.0772904800379389 ≤ 0.0；PF 0.8073129001055873 < 1.1；均值 R 置信下限 -0.21373710383532712 < -0.05 |
| 4h | B_reclaim | ALL | long | **unqualified** | 5 | 1.861 | 11.18 | 4.64% | -0.42% | [1.861, 1.861] | 样本外交易 5 < 30 |
| 4h | B_reclaim | ALL | short | **unqualified** | 7 | -0.573 | 0.01 | -2.00% | -2.01% | [-0.589, -0.429] | 样本外交易 7 < 30；扣费后期望 -0.57275929616637 ≤ 0.0；PF 0.006607052412425296 < 1.1；均值 R 置信下限 -0.5886238253688137 < -0.05 |
| 4h | B_reclaim | BTCUSDT | long | **unqualified** | 1 | 7.890 | ∞ | 3.94% | 0.00% | [7.890, 7.890] | 样本外交易 1 < 30 |
| 4h | B_reclaim | BTCUSDT | short | **unqualified** | 4 | -0.513 | 0.01 | -1.03% | -1.03% | [-0.513, -0.513] | 样本外交易 4 < 30；扣费后期望 -0.513429288534236 ≤ 0.0；PF 0.005476729877704507 < 1.1；均值 R 置信下限 -0.513429288534236 < -0.05 |
| 4h | B_reclaim | ETHUSDT | long | **unqualified** | 4 | 0.354 | 2.53 | 0.70% | -0.44% | [0.354, 0.354] | 样本外交易 4 < 30 |
| 4h | B_reclaim | ETHUSDT | short | **unqualified** | 3 | -0.652 | 0.01 | -0.98% | -0.98% | [-0.652, -0.652] | 样本外交易 3 < 30；扣费后期望 -0.6518659730092153 ≤ 0.0；PF 0.007789702433061926 < 1.1；均值 R 置信下限 -0.6518659730092153 < -0.05 |
| 4h | C_rejection | ALL | ALL | **unqualified** | 4 | 0.683 | 2.38 | 1.36% | -0.49% | [0.683, 0.683] | 样本外交易 4 < 30 |
| 4h | C_rejection | BTCUSDT | ALL | **unqualified** | 2 | 0.652 | 2.34 | 0.65% | -0.48% | [0.652, 0.652] | 样本外交易 2 < 30 |
| 4h | C_rejection | ETHUSDT | ALL | **unqualified** | 2 | 0.714 | 2.42 | 0.71% | -0.50% | [0.714, 0.714] | 样本外交易 2 < 30 |
| 4h | C_rejection | ALL | long | **unqualified** | 1 | 2.413 | ∞ | 1.21% | 0.00% | [2.413, 2.413] | 样本外交易 1 < 30 |
| 4h | C_rejection | ALL | short | **unqualified** | 3 | 0.106 | 1.16 | 0.15% | -0.98% | [0.106, 0.106] | 样本外交易 3 < 30 |
| 4h | C_rejection | BTCUSDT | short | **unqualified** | 2 | 0.652 | 2.34 | 0.65% | -0.48% | [0.652, 0.652] | 样本外交易 2 < 30 |
| 4h | C_rejection | ETHUSDT | long | **unqualified** | 1 | 2.413 | ∞ | 1.21% | 0.00% | [2.413, 2.413] | 样本外交易 1 < 30 |
| 4h | C_rejection | ETHUSDT | short | **unqualified** | 1 | -0.986 | 0.00 | -0.50% | -0.50% | [-0.986, -0.986] | 样本外交易 1 < 30；扣费后期望 -0.9859424607637726 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9859424607637726 < -0.05 |
| 1h | A_breakout_retest | ALL | ALL | **unqualified** | 75 | -0.067 | 0.89 | -2.58% | -6.66% | [-0.353, 0.183] | 扣费后期望 -0.06695412472608363 ≤ 0.0；PF 0.8878971565491419 < 1.1；测试折非负占比 0.3333333333333333 < 0.5；均值 R 置信下限 -0.3528286717671463 < -0.05 |
| 1h | A_breakout_retest | BTCUSDT | ALL | **unqualified** | 24 | -0.129 | 0.77 | -1.59% | -3.74% | [-0.613, 0.309] | 样本外交易 24 < 30；扣费后期望 -0.12939925901226637 ≤ 0.0；PF 0.7685095049985888 < 1.1；均值 R 置信下限 -0.6134192263370514 < -0.05 |
| 1h | A_breakout_retest | ETHUSDT | ALL | **unqualified** | 51 | -0.038 | 0.94 | -0.99% | -6.51% | [-0.401, 0.339] | 扣费后期望 -0.037568179179644744 ≤ 0.0；PF 0.9387189931126058 < 1.1；均值 R 置信下限 -0.4007861141882827 < -0.05 |
| 1h | A_breakout_retest | ALL | long | **unqualified** | 20 | -0.065 | 0.89 | -0.65% | -2.94% | [-0.580, 0.355] | 样本外交易 20 < 30；扣费后期望 -0.06503737111741237 ≤ 0.0；PF 0.8931093532525979 < 1.1；均值 R 置信下限 -0.5798260574623979 < -0.05 |
| 1h | A_breakout_retest | ALL | short | **unqualified** | 55 | -0.068 | 0.89 | -1.93% | -6.02% | [-0.493, 0.257] | 扣费后期望 -0.06765112603832772 ≤ 0.0；PF 0.8860303663689981 < 1.1；均值 R 置信下限 -0.4931007776197915 < -0.05 |
| 1h | A_breakout_retest | BTCUSDT | long | **unqualified** | 5 | -0.049 | 0.92 | -0.11% | -1.11% | [-0.049, -0.049] | 样本外交易 5 < 30；扣费后期望 -0.04918633364707421 ≤ 0.0；PF 0.9181725674596021 < 1.1 |
| 1h | A_breakout_retest | BTCUSDT | short | **unqualified** | 19 | -0.151 | 0.73 | -1.48% | -3.00% | [-0.626, 0.426] | 样本外交易 19 < 30；扣费后期望 -0.15050792358205375 ≤ 0.0；PF 0.7308722830100525 < 1.1；均值 R 置信下限 -0.6255263590664027 < -0.05 |
| 1h | A_breakout_retest | ETHUSDT | long | **unqualified** | 15 | -0.070 | 0.89 | -0.54% | -2.30% | [-0.567, 0.688] | 样本外交易 15 < 30；扣费后期望 -0.07032105027419175 ≤ 0.0；PF 0.8857273990631787 < 1.1；均值 R 置信下限 -0.5673018749482331 < -0.05 |
| 1h | A_breakout_retest | ETHUSDT | short | **unqualified** | 36 | -0.024 | 0.96 | -0.45% | -5.13% | [-0.539, 0.369] | 扣费后期望 -0.023921149556916807 ≤ 0.0；PF 0.9604082552695944 < 1.1；均值 R 置信下限 -0.5385780909731991 < -0.05 |
| 1h | B_reclaim | ALL | ALL | **unqualified** | 29 | -0.640 | 0.17 | -9.22% | -9.24% | [-0.867, -0.415] | 样本外交易 29 < 30；扣费后期望 -0.6396741406235987 ≤ 0.0；PF 0.16548990221527451 < 1.1；测试折非负占比 0.0 < 0.5；均值 R 置信下限 -0.8667628021730021 < -0.05 |
| 1h | B_reclaim | BTCUSDT | ALL | **unqualified** | 6 | -0.653 | 0.20 | -1.95% | -1.95% | [-0.653, -0.323] | 样本外交易 6 < 30；扣费后期望 -0.6525360096402277 ≤ 0.0；PF 0.198437935299902 < 1.1；均值 R 置信下限 -0.6531596864267271 < -0.05 |
| 1h | B_reclaim | ETHUSDT | ALL | **unqualified** | 23 | -0.636 | 0.16 | -7.27% | -7.29% | [-0.883, -0.365] | 样本外交易 23 < 30；扣费后期望 -0.6363188704453477 ≤ 0.0；PF 0.15617929957060334 < 1.1；均值 R 置信下限 -0.8825184792651571 < -0.05 |
| 1h | B_reclaim | ALL | long | **unqualified** | 10 | -0.656 | 0.13 | -3.26% | -3.26% | [-0.956, -0.551] | 样本外交易 10 < 30；扣费后期望 -0.65649207386446 ≤ 0.0；PF 0.13066580870975067 < 1.1；均值 R 置信下限 -0.956112275125762 < -0.05 |
| 1h | B_reclaim | ALL | short | **unqualified** | 19 | -0.631 | 0.18 | -5.96% | -5.97% | [-0.927, -0.333] | 样本外交易 19 < 30；扣费后期望 -0.6308225968126192 ≤ 0.0；PF 0.18334727165211442 < 1.1；均值 R 置信下限 -0.9269511496753822 < -0.05 |
| 1h | B_reclaim | BTCUSDT | long | **unqualified** | 2 | 0.003 | 0.99 | -0.00% | -0.48% | [0.003, 0.003] | 样本外交易 2 < 30；PF 0.9926910026940223 < 1.1 |
| 1h | B_reclaim | BTCUSDT | short | **unqualified** | 4 | -0.980 | 0.00 | -1.95% | -1.95% | [-0.980, -0.980] | 样本外交易 4 < 30；扣费后期望 -0.9804361667858954 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9804361667858954 < -0.05 |
| 1h | B_reclaim | ETHUSDT | long | **unqualified** | 8 | -0.821 | 0.00 | -3.25% | -3.26% | [-0.948, -0.821] | 样本外交易 8 < 30；扣费后期望 -0.8214311684933517 ≤ 0.0；PF 0.0019358544376085161 < 1.1；均值 R 置信下限 -0.9484356834072849 < -0.05 |
| 1h | B_reclaim | ETHUSDT | short | **unqualified** | 15 | -0.538 | 0.25 | -4.02% | -4.36% | [-0.847, -0.254] | 样本外交易 15 < 30；扣费后期望 -0.5375923114864122 ≤ 0.0；PF 0.2500224420594863 < 1.1；均值 R 置信下限 -0.8471854021589063 < -0.05 |
| 1h | C_rejection | ALL | ALL | **unqualified** | 12 | -0.174 | 0.72 | -1.08% | -2.42% | [-0.796, 0.912] | 样本外交易 12 < 30；扣费后期望 -0.17392869698593197 ≤ 0.0；PF 0.7213332088483034 < 1.1；均值 R 置信下限 -0.7961832975494207 < -0.05 |
| 1h | C_rejection | BTCUSDT | ALL | **unqualified** | 4 | -0.483 | 0.33 | -0.98% | -1.46% | [-0.483, -0.483] | 样本外交易 4 < 30；扣费后期望 -0.4829923308002153 ≤ 0.0；PF 0.3340344699164746 < 1.1；均值 R 置信下限 -0.4829923308002153 < -0.05 |
| 1h | C_rejection | ETHUSDT | ALL | **unqualified** | 8 | -0.019 | 0.96 | -0.10% | -1.41% | [-0.701, 0.563] | 样本外交易 8 < 30；扣费后期望 -0.019396880078790313 ≤ 0.0；PF 0.9591473377424738 < 1.1；均值 R 置信下限 -0.7010077670851123 < -0.05 |
| 1h | C_rejection | ALL | long | **unqualified** | 6 | -0.464 | 0.26 | -1.40% | -1.41% | [-0.621, -0.302] | 样本外交易 6 < 30；扣费后期望 -0.4643915004934602 ≤ 0.0；PF 0.25995997344705046 < 1.1；均值 R 置信下限 -0.6210986995756475 < -0.05 |
| 1h | C_rejection | ALL | short | **unqualified** | 6 | 0.117 | 1.17 | 0.33% | -1.93% | [0.117, 1.044] | 样本外交易 6 < 30 |
| 1h | C_rejection | BTCUSDT | long | **unqualified** | 1 | 0.998 | ∞ | 0.49% | 0.00% | [0.998, 0.998] | 样本外交易 1 < 30 |
| 1h | C_rejection | BTCUSDT | short | **unqualified** | 3 | -0.977 | 0.00 | -1.47% | -1.47% | [-0.977, -0.977] | 样本外交易 3 < 30；扣费后期望 -0.9767589867516908 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9767589867516908 < -0.05 |
| 1h | C_rejection | ETHUSDT | long | **unqualified** | 5 | -0.757 | 0.00 | -1.89% | -1.89% | [-0.757, -0.757] | 样本外交易 5 < 30；扣费后期望 -0.7569313280029945 ≤ 0.0；PF 0.0007802824215953819 < 1.1；均值 R 置信下限 -0.7569313280029945 < -0.05 |
| 1h | C_rejection | ETHUSDT | short | **unqualified** | 3 | 1.210 | 4.59 | 1.80% | -0.49% | [1.210, 1.210] | 样本外交易 3 < 30 |
| 15m | A_breakout_retest | ALL | ALL | **unqualified** | 9 | 0.493 | 2.08 | 2.20% | -1.47% | [-0.076, 1.172] | 样本外交易 9 < 30；均值 R 置信下限 -0.07636481079006735 < -0.05 |
| 15m | A_breakout_retest | BTCUSDT | ALL | **unqualified** | 4 | 0.551 | 2.07 | 1.09% | -0.51% | [0.551, 0.551] | 样本外交易 4 < 30 |
| 15m | A_breakout_retest | ETHUSDT | ALL | **unqualified** | 5 | 0.446 | 2.09 | 1.11% | -0.99% | [0.446, 0.446] | 样本外交易 5 < 30 |
| 15m | A_breakout_retest | ALL | short | **unqualified** | 9 | 0.493 | 2.08 | 2.20% | -1.47% | [-0.076, 1.172] | 样本外交易 9 < 30；均值 R 置信下限 -0.07636481079006735 < -0.05 |
| 15m | A_breakout_retest | BTCUSDT | short | **unqualified** | 4 | 0.551 | 2.07 | 1.09% | -0.51% | [0.551, 0.551] | 样本外交易 4 < 30 |
| 15m | A_breakout_retest | ETHUSDT | short | **unqualified** | 5 | 0.446 | 2.09 | 1.11% | -0.99% | [0.446, 0.446] | 样本外交易 5 < 30 |
| 15m | B_reclaim | ALL | ALL | **unqualified** | 7 | -0.208 | 0.50 | -0.73% | -1.35% | [-0.521, -0.180] | 样本外交易 7 < 30；扣费后期望 -0.2082173051064884 ≤ 0.0；PF 0.5015695498816148 < 1.1；测试折非负占比 0.3333333333333333 < 0.5；均值 R 置信下限 -0.5205832242984504 < -0.05 |
| 15m | B_reclaim | BTCUSDT | ALL | **unqualified** | 3 | 0.084 | 1.27 | 0.13% | -0.48% | [0.084, 0.084] | 样本外交易 3 < 30 |
| 15m | B_reclaim | ETHUSDT | ALL | **unqualified** | 4 | -0.427 | 0.12 | -0.85% | -0.86% | [-0.427, -0.427] | 样本外交易 4 < 30；扣费后期望 -0.4273887450877615 ≤ 0.0；PF 0.1203253734284108 < 1.1；均值 R 置信下限 -0.4273887450877615 < -0.05 |
| 15m | B_reclaim | ALL | long | **unqualified** | 4 | 0.118 | 1.49 | 0.24% | -0.49% | [0.118, 0.118] | 样本外交易 4 < 30 |
| 15m | B_reclaim | ALL | short | **unqualified** | 3 | -0.643 | 0.01 | -0.96% | -0.97% | [-0.643, -0.643] | 样本外交易 3 < 30；扣费后期望 -0.6433327938136825 ≤ 0.0；PF 0.008758932105126665 < 1.1；均值 R 置信下限 -0.6433327938136825 < -0.05 |
| 15m | B_reclaim | BTCUSDT | long | **unqualified** | 1 | 1.211 | ∞ | 0.61% | 0.00% | [1.211, 1.211] | 样本外交易 1 < 30 |
| 15m | B_reclaim | BTCUSDT | short | **unqualified** | 2 | -0.479 | 0.02 | -0.48% | -0.48% | [-0.479, -0.479] | 样本外交易 2 < 30；扣费后期望 -0.47931804282519636 ≤ 0.0；PF 0.01753329130726901 < 1.1；均值 R 置信下限 -0.47931804282519636 < -0.05 |
| 15m | B_reclaim | ETHUSDT | long | **unqualified** | 3 | -0.246 | 0.24 | -0.37% | -0.49% | [-0.246, -0.246] | 样本外交易 3 < 30；扣费后期望 -0.24606422818679716 ≤ 0.0；PF 0.2405371343345967 < 1.1；均值 R 置信下限 -0.24606422818679716 < -0.05 |
| 15m | B_reclaim | ETHUSDT | short | **unqualified** | 1 | -0.971 | 0.00 | -0.49% | -0.49% | [-0.971, -0.971] | 样本外交易 1 < 30；扣费后期望 -0.9713622957906546 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9713622957906546 < -0.05 |
| 15m | C_rejection | ALL | ALL | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；测试折非负占比 0.0 < 0.5；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ETHUSDT | ALL | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ALL | short | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ETHUSDT | short | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 1h | A_breakout_retest | ALL | ALL | **unqualified** | 27 | 0.136 | 1.23 | 1.84% | -4.26% | [-0.583, 0.561] | 样本外交易 27 < 30；均值 R 置信下限 -0.5828433927877917 < -0.05 |
| 1h | A_breakout_retest | BTCUSDT | ALL | **unqualified** | 12 | -0.111 | 0.82 | -0.69% | -1.95% | [-0.448, 0.500] | 样本外交易 12 < 30；扣费后期望 -0.11106111723060459 ≤ 0.0；PF 0.8245776288348098 < 1.1；均值 R 置信下限 -0.4478398043224543 < -0.05 |
| 1h | A_breakout_retest | ETHUSDT | ALL | **unqualified** | 15 | 0.334 | 1.65 | 2.53% | -3.27% | [-0.764, 0.740] | 样本外交易 15 < 30；均值 R 置信下限 -0.763744129103005 < -0.05 |
| 1h | A_breakout_retest | ALL | long | **unqualified** | 7 | -0.085 | 0.88 | -0.30% | -1.87% | [-0.085, 0.782] | 样本外交易 7 < 30；扣费后期望 -0.08518258602397133 ≤ 0.0；PF 0.8756463884257113 < 1.1；均值 R 置信下限 -0.08518258602397134 < -0.05 |
| 1h | A_breakout_retest | ALL | short | **unqualified** | 20 | 0.214 | 1.39 | 2.13% | -2.91% | [-0.463, 0.634] | 样本外交易 20 < 30；均值 R 置信下限 -0.4627925302345387 < -0.05 |
| 1h | A_breakout_retest | BTCUSDT | long | **unqualified** | 2 | -0.962 | 0.00 | -0.97% | -0.97% | [-0.962, -0.962] | 样本外交易 2 < 30；扣费后期望 -0.9619990505195933 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9619990505195933 < -0.05 |
| 1h | A_breakout_retest | BTCUSDT | short | **unqualified** | 10 | 0.059 | 1.09 | 0.27% | -0.99% | [-0.346, 0.490] | 样本外交易 10 < 30；PF 1.0913165999917382 < 1.1；均值 R 置信下限 -0.3457375069660503 < -0.05 |
| 1h | A_breakout_retest | ETHUSDT | long | **unqualified** | 5 | 0.266 | 1.48 | 0.67% | -1.38% | [0.266, 0.266] | 样本外交易 5 < 30 |
| 1h | A_breakout_retest | ETHUSDT | short | **unqualified** | 10 | 0.369 | 1.75 | 1.86% | -1.94% | [-0.363, 1.101] | 样本外交易 10 < 30；均值 R 置信下限 -0.36300673705848824 < -0.05 |
| 1h | B_reclaim | ALL | ALL | **unqualified** | 5 | 0.600 | 2.53 | 1.49% | -0.95% | [0.600, 0.600] | 样本外交易 5 < 30 |
| 1h | B_reclaim | BTCUSDT | ALL | **unqualified** | 1 | -0.945 | 0.00 | -0.48% | -0.48% | [-0.945, -0.945] | 样本外交易 1 < 30；扣费后期望 -0.9452127973822265 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9452127973822265 < -0.05 |
| 1h | B_reclaim | ETHUSDT | ALL | **unqualified** | 4 | 0.986 | 4.98 | 1.96% | -0.49% | [0.986, 0.986] | 样本外交易 4 < 30 |
| 1h | B_reclaim | ALL | long | **unqualified** | 1 | -0.945 | 0.00 | -0.48% | -0.48% | [-0.945, -0.945] | 样本外交易 1 < 30；扣费后期望 -0.9452127973822265 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9452127973822265 < -0.05 |
| 1h | B_reclaim | ALL | short | **unqualified** | 4 | 0.986 | 4.98 | 1.96% | -0.49% | [0.986, 0.986] | 样本外交易 4 < 30 |
| 1h | B_reclaim | BTCUSDT | long | **unqualified** | 1 | -0.945 | 0.00 | -0.48% | -0.48% | [-0.945, -0.945] | 样本外交易 1 < 30；扣费后期望 -0.9452127973822265 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9452127973822265 < -0.05 |
| 1h | B_reclaim | ETHUSDT | short | **unqualified** | 4 | 0.986 | 4.98 | 1.96% | -0.49% | [0.986, 0.986] | 样本外交易 4 < 30 |
| 1h | C_rejection | ALL | ALL | **unqualified** | 1 | -0.976 | 0.00 | -0.49% | -0.49% | [-0.976, -0.976] | 样本外交易 1 < 30；扣费后期望 -0.975586510750039 ≤ 0.0；PF 0.0 < 1.1；测试折非负占比 0.0 < 0.5；均值 R 置信下限 -0.975586510750039 < -0.05 |
| 1h | C_rejection | BTCUSDT | ALL | **unqualified** | 1 | -0.976 | 0.00 | -0.49% | -0.49% | [-0.976, -0.976] | 样本外交易 1 < 30；扣费后期望 -0.975586510750039 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.975586510750039 < -0.05 |
| 1h | C_rejection | ALL | short | **unqualified** | 1 | -0.976 | 0.00 | -0.49% | -0.49% | [-0.976, -0.976] | 样本外交易 1 < 30；扣费后期望 -0.975586510750039 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.975586510750039 < -0.05 |
| 1h | C_rejection | BTCUSDT | short | **unqualified** | 1 | -0.976 | 0.00 | -0.49% | -0.49% | [-0.976, -0.976] | 样本外交易 1 < 30；扣费后期望 -0.975586510750039 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.975586510750039 < -0.05 |
| 15m | A_breakout_retest | ALL | ALL | **unqualified** | 3 | 0.055 | 1.08 | 0.08% | -0.49% | [0.055, 0.055] | 样本外交易 3 < 30；PF 1.0770214184698037 < 1.1 |
| 15m | A_breakout_retest | BTCUSDT | ALL | **unqualified** | 2 | 0.574 | 2.16 | 0.57% | -0.49% | [0.574, 0.574] | 样本外交易 2 < 30 |
| 15m | A_breakout_retest | ETHUSDT | ALL | **unqualified** | 1 | -0.983 | 0.00 | -0.49% | -0.49% | [-0.983, -0.983] | 样本外交易 1 < 30；扣费后期望 -0.9834508805195941 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9834508805195941 < -0.05 |
| 15m | A_breakout_retest | ALL | short | **unqualified** | 3 | 0.055 | 1.08 | 0.08% | -0.49% | [0.055, 0.055] | 样本外交易 3 < 30；PF 1.0770214184698037 < 1.1 |
| 15m | A_breakout_retest | BTCUSDT | short | **unqualified** | 2 | 0.574 | 2.16 | 0.57% | -0.49% | [0.574, 0.574] | 样本外交易 2 < 30 |
| 15m | A_breakout_retest | ETHUSDT | short | **unqualified** | 1 | -0.983 | 0.00 | -0.49% | -0.49% | [-0.983, -0.983] | 样本外交易 1 < 30；扣费后期望 -0.9834508805195941 ≤ 0.0；PF 0.0 < 1.1；均值 R 置信下限 -0.9834508805195941 < -0.05 |
| 15m | B_reclaim | ALL | ALL | **unqualified** | 3 | 1.079 | 4.33 | 1.62% | -0.48% | [1.079, 1.079] | 样本外交易 3 < 30 |
| 15m | B_reclaim | BTCUSDT | ALL | **unqualified** | 1 | 0.017 | ∞ | 0.01% | 0.00% | [0.017, 0.017] | 样本外交易 1 < 30 |
| 15m | B_reclaim | ETHUSDT | ALL | **unqualified** | 2 | 1.610 | 4.31 | 1.61% | -0.48% | [1.610, 1.610] | 样本外交易 2 < 30 |
| 15m | B_reclaim | ALL | short | **unqualified** | 3 | 1.079 | 4.33 | 1.62% | -0.48% | [1.079, 1.079] | 样本外交易 3 < 30 |
| 15m | B_reclaim | BTCUSDT | short | **unqualified** | 1 | 0.017 | ∞ | 0.01% | 0.00% | [0.017, 0.017] | 样本外交易 1 < 30 |
| 15m | B_reclaim | ETHUSDT | short | **unqualified** | 2 | 1.610 | 4.31 | 1.61% | -0.48% | [1.610, 1.610] | 样本外交易 2 < 30 |
| 15m | C_rejection | ALL | ALL | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；测试折非负占比 0.0 < 0.5；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ETHUSDT | ALL | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ALL | short | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |
| 15m | C_rejection | ETHUSDT | short | **unqualified** | 2 | -0.470 | 0.03 | -0.47% | -0.49% | [-0.470, -0.470] | 样本外交易 2 < 30；扣费后期望 -0.47010414625579583 ≤ 0.0；PF 0.03116162500070484 < 1.1；均值 R 置信下限 -0.47010414625579583 < -0.05 |

合格（模拟盘候选）：无；仅研究：0 项；不合格：156 项


## H. 数据来源、缺口与测试限制

| 项目 | 来源 | 覆盖 | 缺口 / 限制 |
|---|---|---|---|
| 现货 K 线 1w/1d/4h | Binance 现货，GitHub finom/static-klines 公开缓存（每日更新） | 2017-08 → 2026-11 | 4h 在 2018–2020 缺 16 根（已记录） |
| 现货 K 线 1h | 同上 | 2022-01 → 2026-11 | 缺 1 根 |
| 现货 K 线 30m / 15m | 同上 | 30m 2024-01 起；15m 2024-12-30 起 | 15m 仅 22 个月，只覆盖下跌/震荡环境；所有更早 15m 的来源被网络策略拒绝 |
| 永续 K 线 | — | 无 | 不可得：回测用现货价格代替，基差以可得样本量化（§2.1） |
| 资金费率 / mark price / OI | powakadata 免费截断样本（GitHub） | BTC 2019-09→11、2026-02→06；ETH 2019-11→2020-02、2026-02→06 | 其余区间按保守假设 0.0001/8h 计入，不按零；OI 仅 2026 片段 |
| 链上 | CoinMetrics 社区（spot90 已采集，含 published_at） | 2009/2015 → 2026-05 | 本阶段未启用为过滤器（第一阶段已说明非 PIT 风险） |
| 实时行情 | spot90.monitor.live（Bitget → DefiLlama → CoinGecko 参考价） | 参考价可用；K 线实时源不可用 | 模拟盘在本环境只能以“延迟数据模拟盘”运行并禁止新开仓 |

测试限制：Walk-Forward 的 15m 只有 3 折、4h 的训练窗跨越多个完全不同的市场结构；费用敏感性为按交易记录重算的一阶近似（止损触发位置不随成本变化）；Bootstrap 为块长 5 的交易序列自助，不建模持仓重叠；限价成交模型（穿越 ≥ 0.05 ATR）是对排队位置的保守近似，不是逐笔撮合。

## I. 完整测试与可复现命令

```bash
export PYTHONPATH=src
pytest tests/test_brando_pa_*.py -q                                   # 31 项
python -m brando_pa.cli ingest                                        # 数据
python -m brando_pa.cli audit                                         # §2 审计 → reports_out/brando_pa/audit/
python -m brando_pa.cli --exec-tf 1h  experiments --start 2022-04-01 --name h1
python -m brando_pa.cli --exec-tf 15m experiments --start 2025-02-01 --name m15
python -m brando_pa.cli --exec-tf 4h  experiments --start 2018-06-01 --name h4 --only "entry_market__mtf_single,entry_market__mtf_aligned,entry_limit__mtf_zone_limit"
python -m brando_pa.cli --exec-tf 1h  experiments --start 2022-04-01 --name h1_cost_sens --only "A_breakout_retest__entry_market__mtf_single,ALL__entry_market__mtf_single" --set costs.funding.assumed_rate_8h=0.00004
python -m brando_pa.cli --exec-tf 1h  walkforward --start 2022-04-01 --name h1_p2
python -m brando_pa.cli --exec-tf 15m walkforward --start 2025-02-01 --name m15_p2 --train-months 8 --valid-months 3 --test-months 3
python -m brando_pa.cli --exec-tf 4h  walkforward --start 2018-06-01 --name h4_p2
python -m brando_pa.cli --exec-tf 1h  walkforward --start 2022-04-01 --name h1_p2_rg --regime-gating --no-perturb
python -m brando_pa.cli --exec-tf 1h  walkforward --start 2022-04-01 --name h1_p2_aligned --set mtf.mode=aligned --no-perturb
python -m brando_pa.cli evaluate --names h1_p2,m15_p2,h4_p2,h1_p2_rg,m15_p2_rg,h1_p2_aligned,m15_p2_aligned
python -m brando_pa.cli paper --replay --replay-bars 6000             # 模拟盘全流程回放
python -m brando_pa.cli paper --once                                  # 输出 realtime_capable / mode / data_issues
python -m brando_pa.cli dashboard --port 8502                         # 新增：数据审计 / 信号漏斗 / 入场退出多周期对比 / 策略筛选 / 样本外详情
```

## J. 是否满足进入模拟盘的条件

**结论：不满足。** 具体未满足项：

1. **候选池为空**：Strategy Evaluator 的 156 项样本外组合全部不合格；没有任何组合同时满足 ≥30 笔、ExpR>0、PF≥1.1、回撤≤20%、折间稳定、均值 R 置信下限 ≥ −0.05。进入模拟盘还要求置信下限 > 0 且 ≥ 50 笔，距离更远。
2. **最接近的组合（1h 策略 A）在统计上无法与零区分**：CI [−0.15, +0.35]，扰动后全程 ExpR 在 −0.02～+0.03 之间。
3. **成本假设不可验证**：滑点、盘口价差、永续基差在 2021 年前没有任何可得数据；而结果对滑点 ×2 就损失一半期望。
4. **实时数据条件不具备**：本环境行情源为每日更新的公开缓存，模拟盘状态为 `paper_delayed_data`，系统按规则禁止新开仓；需在能访问交易所公开 K 线（配置 `data.exchange_adapter: bitget_public`）的机器上运行才能成为实时模拟盘。
5. **15m 历史不足**：22 个月、三折样本外仅 10 笔，不能用于任何结论。

允许进入模拟盘前必须出现的新证据（任一缺失即不进入）：至少 50 笔样本外交易、均值 R 95% 置信下限 > 0、在交易所真实 K 线与真实资金费率上复现、滑点 ×2 下仍为正。以当前数据与规则，这些条件都未达成；本阶段的正确结论是**明确报告失败**，而不是交付一个“盈利策略”。

可以继续研究、但不改变上述结论的方向：(a) 在有交易所访问的机器上接入真实永续 K 线与资金费率重跑全部 Walk-Forward；(b) 策略 A 的“上涨趋势追高亏损”提示应测试“只在横盘/下跌后的突破”而非环境启停（后者已证明不稳定）；(c) 策略 B 应被视为否证（样本外 CI 上界 −0.44），除非重新设计；(d) 策略 C 的样本永远不足，说明其过滤条件与加密 K 线形态不匹配，应放弃或改为 4h 研究对象。

