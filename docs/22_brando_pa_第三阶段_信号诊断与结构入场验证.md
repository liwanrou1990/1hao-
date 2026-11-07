# 22 · Brando Price Action 引擎第三阶段：信号质量诊断、结构入场优化与可信数据验证

生成时间：2026-11-07T08:32 UTC · 分支 `claude/brando-pa-phase3` · 只读基准：第一阶段 `docs/brando_pa_validation/baseline_phase1/`、第二阶段 `docs/brando_pa_validation/phase2/`（提交 0e5d5a3）· 预先登记 `docs/brando_pa_validation/phase3/preregistration.md`（锁定测试运行前提交）· 测试 38 项通过

> 本阶段不推倒重建。所有数字含手续费、滑点、资金费（无数据区间按保守假设）与保守成交规则；没有删除亏损交易、没有降低成本假设、没有放宽硬性风险限制、没有使用未来数据；评估门槛与第二阶段完全相同。真实交易 API 保持禁用。

## 0. 结论先行

1. **三条预登记假设全部未通过锁定测试，没有策略达到“仅研究”门槛，更没有模拟盘候选。** L2 保留集（1h，2025-07→2026-11）：原策略 A（选定微结构止损）99 笔、ExpR +0.03、PF 1.04、净 +1.0%、均值 R 95% CI [−0.28, +0.40]；模式 A（A2）12 笔、ExpR −0.56、PF 0.17；模式 B（A3）5 笔、全部止损。L1 真正未使用集（1h 2022-01→03）：A 29 笔、ExpR −0.57、PF 0.42；A2/A3 各 2 笔、1 笔。4h 2017-11→2018-05 的 6 笔正收益不构成证据。按预登记 §4 判据，**三者均为失败**。
2. **止损动辄 3–4 倍 ATR 的原因已查明，且不是确认滞后。** 训练窗 1h 的 1028 个策略 A 候选中，94% 的结构止损挂在 1W/1D 区域的**另一侧**；止损距离均值 3.99 ATR 的分解为：区域高度 2.54（64%）+ 确认偏移 0.78（20%）+ ATR 缓冲 0.50（13%）+ 回踩深度 0.17（4%）。区域半宽上限为价格的 1%，一个日线区域在 1h ATR 单位下就是 2–4 ATR；1d/1w 合并区域更宽（止损中位 4.95 ATR）。突破→回踩中位 5 根、回踩→确认中位 0 根（多数在触及当根即确认），因此“入场过迟/确认滞后”只贡献约 20%。根因是**关键区域的尺度（日线/周线）与执行周期（1h）不匹配，而止损又选在区域远端**。成交后亏损交易的 MAE/止损中位 0.81R、89% 以止损离场、90% 的亏损 MAE 落在 0.5–1R：止损并没有“偏窄被噪声扫掉”，而是方向判断本身接近随机。
3. **把止损改到微结构（H3）只带来统计上不可区分的改善。** 对同一组策略 A 信号，验证窗三种止损的 ExpR：micro +0.008、atr_buffer −0.155、fixed_structure −0.149（选 micro）；保留集 micro +0.03。被扫损比例 63% vs 67%、亏损中 MAE 0.5–1R 占比 81% vs 87%，并未因止损收紧而上升。但训练窗三种止损全部为负（−0.08～−0.14），选出来的“最好”只是验证窗的 +0.008，锁定集 CI 跨零，属于噪声。
4. **模式 A / 模式 B 的失败机制很明确：在 1h 上并不存在“微”结构。** 1h 执行周期上围绕日线区域形成的摆动低点 + 反弹高点，本身就是 1.5–2.5 ATR 的结构（候选止损中位 2.5 ATR），入场在反弹高点之上、止损在低点之下，净盈亏比与成本比随即不达标：A2 保留集 702 个候选只有 14 个通过（成交量 219、净盈亏比 187、成本 122 被拒），通过的 12 笔仍亏损；A3 因“必须先有 4h 突破、再有回踩、再有确认”三重条件，4.5 年训练窗只产生 4 笔。15m 对照（22 个月）：A2 32 笔 ExpR −0.26，A3 10 笔 −0.08，同样为负。所谓“更合理的入场位置”在加密 1h/15m 上没有把被动追高变成主动承接——回踩后的二次确认入场平均仍比区域边界高出 1 个 ATR 以上。
5. **成本压力测试暴露了一个必须警惕的假象**：把手续费提到 10 bps 或滑点提到 10/20 bps 后，策略 A 保留集 ExpR 反而从 +0.03 升到 +0.12 / +0.41，原因是更高的假设成本让成本闸门剔除了更多边际交易（99 → 74 → 34 笔）。这是过滤器对成本参数的选择效应，不是真实收益，**不得**用来宣称“策略在高成本下更好”；相反，价差 4 bps 与资金费 ×3 这两种不改变过滤结果的压力下，ExpR 转为 −0.01。真实滑点与价差不可验证，这决定了结论的方向：没有正期望证据。
6. **环境 × 方向切片再次说明 ETH 空头收益是阶段性现象**：保留集策略 A 的正收益集中在 ETH 上涨趋势中的空头（10 笔、+0.79R）与 ETH 横盘多头（8 笔、+0.66R），BTC 横盘空头 8 笔 −0.83R、BTC 全部切片均非正；每个切片样本都 ≤ 23 笔，不能作为规则。
7. **数据层结论不变且已穷尽本环境的办法**：35 个永续数据来源全部被网络策略拒绝；没有绕过限制。全部回测是现货价格代理，**不能声称验证了永续合约策略**。本阶段起每次模拟盘运行采集 CoinGecko 永续快照（价格、指数、资金费率、持仓、价差），作为前瞻数据累积。
8. **是否过拟合/选择偏差**：L2 保留集不是严格未见数据（第二阶段已在该区间评估过旧策略 A）；真正未使用的 1h 数据只有 3 个月；本阶段只做了 1 个自由度的选择（止损模式）且锁定集只运行一次；结论是负面的，因此选择偏差不会把失败误报为成功，但它意味着**即使出现正结果也不能采信**。
9. **下一步只有两条路成立**：(a) 在能访问交易所公开接口的机器上取得真实永续 K 线与资金费率后，用同一套预登记流程重跑（代码与命令已就绪）；(b) 用前瞻模拟盘数据做真正的样本外检验。继续在这份现货历史上调整入场/止损/过滤参数，已经不能产生可信的新证据。


## 1. 项目审计与范围

- 读取了 `src/brando_pa/`、`config/brando_pa.yaml`、docs/21 与 `docs/brando_pa_validation/phase2/`；第二阶段 156 项全部不合格、1h 策略 A 最接近（165 笔、ExpR +0.10、CI 跨零）、限价劣于市价、止损距离上限是策略 A 的主要拒绝原因之一（1h 2242 个候选中 300 个因 > 4 ATR 被拒，更多因成交量被拒）。
- 新建分支 `claude/brando-pa-phase3`；前两阶段结果目录未改动（`phase3/BASELINE_LOCK.md` 记录基准提交）。第一阶段默认配置的回测在本阶段代码上仍逐笔复现（15m 策略 A 36 笔、389.79 USDT 与第二阶段产物一致）。
- 本阶段新增代码：`diagnostics/stop_distance.py`、`strategies/structure_entry.py`（模式 A/B）、`backtest/phase3.py`（预登记流程）、`data/derivatives_live.py`（永续前瞻快照）、策略 A 逐笔诊断字段、`exits.stop_mode: micro`、`costs.*.spread_bps`、回测订单事件日志；其他系统零改动。

## 2. 可信交易数据层：能做到什么、不能做到什么

### 2.1 再次尝试获取永续合约历史数据

本阶段对 35 个候选来源逐一探测（Binance fapi/dapi/data.binance.vision/data-api.binance.vision/api1/api-gcp/binance.me、Bybit API 与 public.bybit.com 归档、OKX API 与 static.okx.com 归档、Bitget、Deribit、BitMEX、dYdX v3/v4、Kraken Futures、Huobi/HTX 合约、Gate、KuCoin、MEXC、Phemex、WOO、Bitfinex 永续、Coinbase、Gemini、Bitstamp、Kraken、CryptoDataDownload、HuggingFace、Kaggle 镜像仓库的原始文件）：**全部被本环境网络策略拒绝（连接层 000）**，与第二阶段一致。唯一可达的衍生品接口是 CoinGecko `derivatives/exchanges/binance_futures`，它只提供**当前**快照（最新价、指数价、资金费率、未平仓量、价差），没有历史。GitHub 上两个资金费率项目（Nicolas-Formenton、haozhu18）的数据文件分别 gitignore 与托管在 Kaggle，均不可达；powakadata 免费样本（第二阶段已用）只有 2019 年与 2026-02→06 两段。

没有绕过任何权限或限制。结论不变：**本环境无法获得 BTC/ETH 永续合约的历史 OHLCV 与连续资金费率**。

### 2.2 本阶段的处理方式（现货与永续彻底区分）

| 数据项 | 状态 | 处理 |
|---|---|---|
| 永续 OHLCV（历史） | 不可得 | 回测全部使用 Binance **现货** K 线；所有产物标注 `data_basis = spot_proxy`。报告中的合约多空结果是“以现货价格代理的永续策略研究”，**不是**已验证的永续策略 |
| 永续 mark price / 基差 | 2026-02→06 样本 377 点 | 基差均值 −4.7 bps、|最大| < 9 bps（第二阶段审计），作为现货代理误差的量级参考；2021 年极端行情无数据 |
| 资金费率（历史） | 2019 两个月 + 2026 四个月 | 其余按 0.0001/8h 多头付（高于全部可观测样本均值）计入；压力测试再 ×3 |
| 合约规格（数量精度、最小名义、维持保证金） | 配置假设（BTC 0.0001 / ETH 0.001 / 5 USDT / 0.5%） | 未能从交易所接口核实；历史变化不可得 |
| 手续费 | 配置假设：合约 taker 6 / maker 2 bps，现货 10 bps | 对应主流交易所普通用户档；压力测试 taker 10 bps |
| 买卖价差 | 无历史 | 新增 `costs.perp.spread_bps`（默认 0 以保持口径一致），压力测试取 4 bps；CoinGecko 当前快照显示 BTC/ETH 永续价差约 1–5 bps 量级 |
| 前瞻数据 | 自本阶段起每次模拟盘运行采集 CoinGecko 永续快照 → `data_store/brando_pa/derivatives/forward_snapshots.parquet` | 只累积、不回填，与历史回测分开 |

### 2.3 现货 K 线质量（第三阶段复核）

与第二阶段审计相同：1w/1d/4h/1h/30m/15m 无重复、无周期错位、OHLC 合法，4h 在 2018–2020 缺 16 根、1h 缺 1 根；低周期合成的高周期与下载的高周期 OHLC 逐根误差为 0。新增测试 `test_data_timeline_and_anomaly_checks` 覆盖重复/错位/缺口/异常振幅的检测。

## 3. 成交模拟修复（§六）

回测与模拟盘共用 `PositionManager`，第三阶段把每张订单的生命周期写成事件日志：`signal_generated`（收盘确认）→ `order_submitted`（市价 / 限价，含挂单价与过期索引）→ `order_touched_not_filled`（限价被触及但未穿透 ≥ 0.05 ATR，**不算成交**）→ `order_filled`（市价：下一根开盘 + 滑点 + 半价差；限价：挂单价、maker 费）或 `order_cancelled`（超时 / 结构失效 / 先到止盈 / 成交时净盈亏比不足 / 开盘越过止损）→ `position_exit`（止损 / 止盈 / 时间 / 保本 / 结构退出）。测试 `test_order_event_sequence_is_causal` 验证每张订单事件时间单调、成交与撤销互斥、触及未穿透的订单不会在同一根成交。不存在用事后行情把未成交限价改为市价的路径。成本压力测试由 `--set` 覆盖实现（手续费、滑点、价差、资金费各自独立）。

## 4. 新增机制（最少量的策略变化）

- **逐笔诊断字段**（策略 A）：4h 突破收盘、建仓时收盘、区域上下沿、区域高度/ATR、回踩极值与深度、确认收盘与偏移、结构点及其是否在区域另一侧、突破→回踩/回踩→确认的 K 线数；写入 signals_log 的 `diag_*` 列。
- **模式 A `A2_structure_entry`**：价格到达 1W/1D 区域（4h 逆势不做）→ 执行周期出现**已确认**摆动低点（分形 k=3，第 i+3 根收盘确认，且位于区域附近）→ 收盘突破“低点之后到确认根之间的最高价”（微结构 BOS）→ 下一根开盘入场。止损候选：微结构低点 − 0.2 ATR（micro）、区域另一侧 − 0.5 ATR（atr_buffer）、区域另一侧（fixed_structure）。
- **模式 B `A3_breakout_second_confirm`**：4h 有效突破 1W/1D 区域后不追价；等待首次回踩进入区域，再走同一套微结构确认；48 根内无回踩 → 记为 `missed_no_pullback`，允许错过。
- 两种模式与原策略 A 分开统计，参数全部固定（预登记 §3），唯一的选择是止损模式（3 选 1，按验证窗）。
- 因果保证：微结构摆动点来自 `MarketContext.extras['exec_swings']`，只包含 `confirmed_idx ≤ 当前索引` 的点；BOS 参考价只用 `[低点+1, 确认根]` 区间的高价；确认根本身不能触发入场。测试 `test_mode_A_requires_confirmed_micro_low_then_bos_and_sets_micro_stop` 逐根验证。
- **`exits.stop_mode: micro`** 只允许比策略止损更紧（never widen）；初始风险在入场时固定；硬止损保留；禁止马丁与补仓（第二阶段限制不变）。

## A. 策略 A 止损距离诊断（为什么常需要 3–4 倍 ATR）

训练窗 1h 候选 1028 个：止损/ATR 中位 3.68（P25–P75 2.96–4.69），通过过滤的中位 3.16，因“止损距离 > 4 ATR”被拒的中位 4.91；止损占价格中位 2.89%。

| 分量 | 均值（ATR） | 占比 |
|---|---|---|
| 区域高度（止损挂在区域另一侧时） | 2.54 | 64% |
| 回踩深度（止损挂在回踩极值时） | 0.17 | 4% |
| 确认偏移（确认收盘到区域边界） | 0.78 | 20% |
| ATR 缓冲 0.5 | 0.50 | 13% |
| 合计（重构） | 3.99 | 实际均值 3.99 |

结构点落在区域另一侧的候选占比 94%；突破→回踩中位 5.0 根、回踩→确认中位 0.0 根。按区域周期：1d 止损中位 3.59 ATR（区域高度中位 2.39 ATR，n=701）；1d/1w 止损中位 4.95 ATR（区域高度中位 3.80 ATR，n=112）；1w 止损中位 3.46 ATR（区域高度中位 2.19 ATR，n=215）。

成交后（训练窗 99 笔）：止损占价格中位 3.49%；亏损交易 MAE/止损中位 0.81R、被扫损占 89%；赢家 MFE/止损中位 2.16R；MAE 在 0.5–1R 之间的亏损占 90%。

诊断结论：主因：结构止损挂在 1W/1D 区域的另一侧，区域高度占止损距离 64%（区域半宽上限 1% 价格，在执行周期 ATR 单位下即数倍 ATR）→ 属于“关键区域对执行周期过宽 + 止损结构选择”问题，不是确认条件滞后；成交后：亏损交易 MAE/止损中位 0.81R、被扫损占 89%；赢家 MFE/止损中位 2.16R → 止损宽度与实际波动的关系：亏损多为真正走反（止损并非被噪声扫掉）

图：`stop_distance_A_1h_train.png`（已复制到本目录）

## B. 原策略 A 与两种新入场模式：训练 / 验证（止损模式选择）

| 策略 | 止损模式 | 训练 n | 训练 ExpR | 训练 PF | 训练 候选止损ATR中位 | 验证 n | 验证 ExpR | 验证 PF | 验证 回撤 | 验证 被扫损 | 验证 亏损中 MAE 0.5–1R 占比 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | micro | 125 | -0.136 | 0.78 | 3.68 | 91 | 0.008 | 1.00 | -8.69% | 63% | 81% |
| A_breakout_retest | atr_buffer | 99 | -0.104 | 0.81 | 3.68 | 72 | -0.155 | 0.76 | -9.78% | 67% | 87% |
| A_breakout_retest | fixed_structure | 116 | -0.080 | 0.86 | 3.68 | 86 | -0.149 | 0.76 | -9.89% | 63% | 84% |
| A2_structure_entry | micro | 8 | -0.837 | 0.00 | 2.53 | 6 | -0.085 | 0.82 | -0.93% | 50% | 100% |
| A2_structure_entry | atr_buffer | 8 | -0.837 | 0.00 | 2.53 | 6 | -0.085 | 0.82 | -0.93% | 50% | 100% |
| A2_structure_entry | fixed_structure | 7 | -0.524 | 0.10 | 2.53 | 2 | 0.910 | ∞ | 0.00% | 0% | — |
| A3_breakout_second_confirm | micro | 4 | -0.709 | 0.01 | 2.53 | 3 | 0.013 | 1.03 | -0.49% | 33% | 0% |
| A3_breakout_second_confirm | atr_buffer | 4 | -0.709 | 0.01 | 2.53 | 3 | 0.013 | 1.03 | -0.49% | 33% | 0% |
| A3_breakout_second_confirm | fixed_structure | 4 | -0.395 | 0.16 | 2.53 | 3 | -0.068 | 0.79 | -0.49% | 33% | 0% |

选定止损模式（按验证窗 ExpR；验证 < 10 笔退回预登记默认）：A_breakout_retest: **micro**，验证 ExpR 按模式 {'micro': 0.008, 'atr_buffer': -0.155, 'fixed_structure': -0.149}；A2_structure_entry: **micro**（退回默认），验证 ExpR 按模式 {'micro': -0.085, 'atr_buffer': -0.085, 'fixed_structure': 0.91}；A3_breakout_second_confirm: **micro**（退回默认），验证 ExpR 按模式 {'micro': 0.013, 'atr_buffer': 0.013, 'fixed_structure': -0.068}

## C. 锁定测试（只运行一次；L2 为半锁定保留集，L1 为真正未使用集）

| 集合 | 策略 | 止损 | n | ExpR | 胜率 | PF | 净收益 | 最大回撤 | Sharpe | 均值R 95%CI | 候选→通过 | 止损/ATR 中位（候选） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| L2 2025-07→2026-11 (1h) | A_breakout_retest | micro | 99 | 0.027 | 38.4% | 1.04 | 1.03% | -6.63% | 0.11 | [-0.275, 0.402] | 690→103 | 3.73 |
| L2 2025-07→2026-11 (1h) | A2_structure_entry | micro | 12 | -0.559 | 25.0% | 0.17 | -3.31% | -3.48% | -1.96 | [-0.916, -0.251] | 702→14 | 2.54 |
| L2 2025-07→2026-11 (1h) | A3_breakout_second_confirm | micro | 5 | -0.964 | 0.0% | 0.00 | -2.39% | -2.39% | -2.25 | [-0.964, -0.964] | 375→6 | 2.58 |
| L1 真正未使用 | A_breakout_retest | micro | 29 | -0.574 | 27.6% | 0.42 | -8.23% | -9.62% | -0.54 | [-0.967, 0.168] | 139→30 | 3.00 |
| L1 真正未使用 | A2_structure_entry | micro | 2 | 1.361 | 100.0% | ∞ | 1.36% | 0.00% | 4.15 | [1.361, 1.361] | 136→2 | 2.40 |
| L1 真正未使用 | A3_breakout_second_confirm | micro | 1 | -0.930 | 0.0% | 0.00 | -0.46% | -0.46% | — | [-0.930, -0.930] | 72→1 | 2.43 |
| L1 真正未使用 | A_breakout_retest_4h_2017-11_2018-05 |  | 6 | 0.425 | 50.0% | 1.85 | 1.26% | -0.49% | 0.82 | [0.258, 1.081] | 156→6 | 1.88 |

- **A_breakout_retest（保留集）** 年度 2025 37 笔 ExpR 0.01；2026 62 笔 ExpR 0.04。方向 long 17 笔 ExpR 0.22；short 82 笔 ExpR -0.01。退出原因 {"end_of_test": 1, "stop": 53, "stop_gap": 4, "take_profit": 12, "time_stop": 2, "trail_stop": 27}。被拒原因 {'volume': 358, 'stop_distance': 104, 'accepted': 99, 'net_rr': 58, 'regime_trend': 44, 'cost_to_risk': 23}
- **A2_structure_entry（保留集）** 年度 2025 3 笔 ExpR -0.65；2026 9 笔 ExpR -0.53。方向 long 3 笔 ExpR 0.13；short 9 笔 ExpR -0.79。退出原因 {"stop": 8, "time_stop": 2, "trail_stop": 2}。被拒原因 {'volume': 219, 'net_rr': 187, 'cost_to_risk': 122, 'regime_trend': 81, 'stop_distance': 76, 'accepted': 12}
- **A3_breakout_second_confirm（保留集）** 年度 2025 2 笔 ExpR -0.98；2026 3 笔 ExpR -0.95。方向 long 2 笔 ExpR -0.94；short 3 笔 ExpR -0.98。退出原因 {"stop": 5}。被拒原因 {'volume': 120, 'net_rr': 95, 'cost_to_risk': 64, 'stop_distance': 45, 'regime_trend': 44, 'accepted': 6}

## D. 保留集：标的 × 市场环境 × 方向（平均 R，BTC/ETH 分开，多空分开）

| 策略 | 标的/环境/方向 | n | ExpR | 胜率 | 净盈亏 |
|---|---|---|---|---|---|
| A_breakout_retest | BTCUSDT/上涨趋势/short | 3 | -0.63 | 33% | -94.20 |
| A_breakout_retest | BTCUSDT/下跌趋势/long | 1 | -0.09 | 0% | -4.66 |
| A_breakout_retest | BTCUSDT/下跌趋势/short | 23 | 0.01 | 43% | -5.98 |
| A_breakout_retest | BTCUSDT/横盘震荡/short | 8 | -0.83 | 12% | -337.83 |
| A_breakout_retest | ETHUSDT/上涨趋势/long | 4 | 0.43 | 25% | 80.38 |
| A_breakout_retest | ETHUSDT/上涨趋势/short | 10 | 0.79 | 60% | 390.95 |
| A_breakout_retest | ETHUSDT/下跌趋势/long | 4 | -0.80 | 0% | -160.81 |
| A_breakout_retest | ETHUSDT/下跌趋势/short | 18 | 0.08 | 44% | 68.18 |
| A_breakout_retest | ETHUSDT/横盘震荡/long | 8 | 0.66 | 50% | 266.87 |
| A_breakout_retest | ETHUSDT/横盘震荡/short | 20 | -0.09 | 35% | -100.25 |
| A2_structure_entry | BTCUSDT/下跌趋势/short | 2 | -0.98 | 0% | -95.26 |
| A2_structure_entry | ETHUSDT/上涨趋势/short | 3 | -0.64 | 33% | -94.93 |
| A2_structure_entry | ETHUSDT/下跌趋势/long | 2 | 0.19 | 50% | 17.56 |
| A2_structure_entry | ETHUSDT/下跌趋势/short | 3 | -0.74 | 0% | -109.59 |
| A2_structure_entry | ETHUSDT/横盘震荡/long | 1 | 0.01 | 100% | 0.54 |
| A2_structure_entry | ETHUSDT/横盘震荡/short | 1 | -0.98 | 0% | -48.92 |
| A3_breakout_second_confirm | BTCUSDT/下跌趋势/short | 1 | -0.99 | 0% | -48.59 |
| A3_breakout_second_confirm | ETHUSDT/上涨趋势/short | 1 | -0.98 | 0% | -48.89 |
| A3_breakout_second_confirm | ETHUSDT/下跌趋势/long | 2 | -0.94 | 0% | -92.14 |
| A3_breakout_second_confirm | ETHUSDT/横盘震荡/short | 1 | -0.98 | 0% | -48.92 |

## E. 成本压力测试（保留集；手续费 10 bps / 滑点 10 + 止损滑点 20 bps / 价差 4 bps / 资金费 ×3）

| 策略 | 场景 | n | ExpR | PF | 净收益 | 回撤 | 手续费 | 滑点 | 资金费 |
|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 基线 | 99 | 0.027 | 1.04 | 1.03% | -6.63% | 388.06 | 612.22 | 21.45 |
| A_breakout_retest | fee10 | 74 | 0.119 | 1.22 | 4.20% | -4.38% | 439.14 | 409.55 | 12.33 |
| A_breakout_retest | slip10_stop20 | 34 | 0.407 | 1.85 | 7.00% | -2.36% | 89.90 | 268.66 | 7.41 |
| A_breakout_retest | spread4 | 99 | -0.013 | 0.96 | -0.97% | -6.75% | 379.61 | 725.37 | 20.95 |
| A_breakout_retest | funding_x3 | 82 | -0.012 | 0.96 | -0.79% | -5.79% | 331.77 | 527.30 | 0.00 |
| A2_structure_entry | 基线 | 12 | -0.559 | 0.17 | -3.31% | -3.48% | 27.83 | 43.24 | 2.38 |
| A2_structure_entry | fee10 | 9 | -0.164 | 0.71 | -0.75% | -2.07% | 31.22 | 26.16 | 2.37 |
| A2_structure_entry | slip10_stop20 | 4 | -0.551 | 0.01 | -1.10% | -1.11% | 6.22 | 17.85 | 1.35 |
| A2_structure_entry | spread4 | 12 | -0.566 | 0.16 | -3.35% | -3.51% | 27.59 | 52.06 | 2.36 |
| A2_structure_entry | funding_x3 | 11 | -0.734 | 0.00 | -3.96% | -3.96% | 25.04 | 40.32 | 3.96 |
| A3_breakout_second_confirm | 基线 | 5 | -0.964 | 0.00 | -2.39% | -2.39% | 10.87 | 18.14 | 0.63 |
| A3_breakout_second_confirm | fee10 | 3 | -0.969 | 0.00 | -1.44% | -1.44% | 10.35 | 10.37 | 0.19 |
| A3_breakout_second_confirm | slip10_stop20 | 1 | -0.976 | 0.00 | -0.49% | -0.49% | 1.48 | 4.96 | 0.00 |
| A3_breakout_second_confirm | spread4 | 5 | -0.972 | 0.00 | -2.40% | -2.40% | 10.78 | 21.59 | 0.62 |
| A3_breakout_second_confirm | funding_x3 | 3 | -0.984 | 0.00 | -1.47% | -1.47% | 5.98 | 10.03 | 0.00 |

## F. 15m 描述性对照（2025-02 → 2026-11，仅 22 个月，不参与选型、不计入判定）

| 策略 | 止损 | n | ExpR | PF | 净收益 | 回撤 | 候选→通过 | 止损/ATR 中位 |
|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | atr_buffer | 36 | 0.218 | 1.45 | 3.90% | -2.31% | 755→44 | 6.16 |
| A2_structure_entry | micro | 32 | -0.259 | 0.53 | -4.10% | -4.98% | 1824→37 | 2.59 |
| A3_breakout_second_confirm | micro | 10 | -0.083 | 0.86 | -0.43% | -1.93% | 523→10 | 2.64 |

## G. 成交模型审计：订单事件（保留集）

- A_breakout_retest：{"signal_generated": 99, "order_submitted": 99, "order_filled": 99, "position_exit": 98}
- A2_structure_entry：{"signal_generated": 12, "order_submitted": 12, "order_filled": 12, "position_exit": 12}
- A3_breakout_second_confirm：{"signal_generated": 6, "order_submitted": 6, "order_filled": 5, "position_exit": 5, "order_cancelled": 1}

## H. 是否存在过拟合或选择偏差（自我审计）

1. **数据重复使用**：第一、二阶段已经在 1h 2022-04→2026-11 的全部数据上做过 156 项评估与多轮实验。因此本阶段的 L2 保留集（2025-07→2026-11）**不是严格未见数据**：旧策略 A 在该区间的表现（含 ETH 空头与 2026 年偏好）在第二阶段已被观察。对新假设（模式 A/B）它没有被用于开发与选参，但分析者的先验已受影响。真正未使用的数据只有 1h 2022-01→03（3 个月）与 4h 2017-11→2018-05，统计力极低。
2. **选择自由度**：本阶段只允许一个选择（止损模式 3 选 1，按验证窗 ExpR），参数全部预先固定；没有网格搜索。但“微结构参数 k=3、touch_tol 0.25、缓冲 0.2”等默认值沿用了前两阶段的经验，不是独立推导。
3. **一次性运行**：锁定测试在选定止损模式后只运行了一次，结果无论正负都写入报告；本阶段开始前做过一次全周期（含保留集）的冒烟运行以确认代码可跑，只看了聚合交易数/PF，这是一个小的信息泄漏，如实记录。
4. **环境 × 方向切片**：§D 的切片是事后描述，不用于选型；任何切片内的正收益不构成证据（多重比较）。
5. **15m**：历史 22 个月，仅描述性对照，不计入任何判定。
6. **前瞻检验**：自本阶段起模拟盘记录与永续快照单独累积，是唯一能摆脱上述偏差的途径。

## I. 数据覆盖率与限制

| 项目 | 覆盖 | 限制 |
|---|---|---|
| 1h 现货 K 线 | 2022-01 → 2026-11，缺 1 根 | 训练 2022-04→2024-06、验证 2024-07→2025-06、保留 2025-07→2026-11、锁定 2022-01→03 |
| 4h 现货 K 线 | 2017-08 → 2026-11，缺 16 根 | 仅用于 4h 结构/关键位与一个 7 个月锁定集 |
| 15m 现货 K 线 | 2024-12-30 → 2026-11 | 描述性对照 |
| 永续 OHLCV / 连续资金费率 / 合约规格历史 / 价差历史 | 不可得 | 全部结果为“现货代理”，未验证永续 |
| 永续前瞻快照 | 自 2026-11-07 起逐次累积 | 仅前瞻 |

## J. 工程验收与可复现命令

- 既有测试 31 项 + 第三阶段 7 项 = 38 项全部通过（`pytest tests/test_brando_pa_*.py -q`）。
- 新增测试覆盖：数据时序/重复/错位/异常振幅（`test_data_timeline_and_anomaly_checks`）、成交模型事件序列与限价触及不成交（`test_order_event_sequence_is_causal`）、止损距离分解（`test_stop_distance_decomposition_matches_definition`）、微结构入场因果顺序与未来数据（`test_mode_A_requires_confirmed_micro_low_then_bos_and_sets_micro_stop`）、微结构止损不放宽（`test_micro_stop_mode_never_widens`）、价差参数（`test_spread_param_widens_market_fill_only_when_set`）、模式 B 无回踩允许错过（`test_mode_A_blocks_counter_trend_and_mode_B_requires_breakout_then_pullback`）。

```bash
export PYTHONPATH=src
pytest tests/test_brando_pa_*.py -q
python -m brando_pa.cli audit                                      # 数据质量
python -m brando_pa.cli --exec-tf 1h phase3                        # 预登记流程：训练/验证 → 锁定测试一次 → 压力测试 → 止损诊断 → reports_out/brando_pa/phase3/
python -m brando_pa.cli paper --once                               # 模拟盘一轮（延迟数据模式；同时采集永续前瞻快照）
python -m brando_pa.cli dashboard --port 8502                      # 新增“第三阶段：止损诊断与结构入场”页
```

默认仅本地研究与历史回测；真实交易 API 三重闸门 + 无实现，保持禁用。
