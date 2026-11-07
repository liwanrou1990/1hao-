# 20 · Brando Price Action 量化交易引擎（brando_pa）：实施方案、回测验证与模拟盘报告

生成时间：2026-11-07T06:21 UTC · 代码 `src/brando_pa/` · 配置 `config/brando_pa.yaml` · 产物 `reports_out/brando_pa/` · 测试 `tests/test_brando_pa_*.py`（20 项通过）· 截图 `docs/brando_pa_validation/`

> 本引擎只做研究、回测与模拟交易。所有回测数字都含手续费、滑点、资金费假设和保守成交规则，**不是收益承诺**；仓库内不存在任何真实下单代码路径。

## 0. 结论先行

1. **引擎按验收标准全部跑通**：BTC/ETH 多周期关键价位与结构识别、三套策略独立开关与独立回测、每根 K 线给出开仓/不交易的具体理由、回测无已知未来函数且全部成本计入、模拟盘无需真实资金即可完整运行（回放 6000 根 15m K 线：3 个可执行信号、2 笔成交、1 笔因成交价使净盈亏比跌破 2 而取消）、报告可复现。
2. **历史绩效：没有可以上实盘的证据。** 开过滤的全策略组合：15m（2025-02 → 2026-11）67 笔、PF 1.38、净 +6.0%、最大回撤 −4.3%（同期买入持有 −29%）；1h（2022-04 → 2026-11）291 笔、PF 0.93、净 −6.0%（买入持有 +17.6%）；4h（2018-06 → 2026-11）298 笔、PF 1.01、净 +0.7%（买入持有 +597%）。三个周期里只有 15m 这段以下跌为主的样本是正的，而且 67 笔里 40 笔是空单、利润主要来自空单与 ETH。
3. **Walk-Forward 样本外互相矛盾，说明参数选择在过拟合**：策略 A（突破-回踩-确认）在 1h 上六折 OOS 162 笔、ExpR +0.12R、PF 1.22、拼接收益 +9.6%，但前三折中两折亏损，正收益集中在 2025-10 之后；同一策略在 4h 上十三折 OOS 223 笔、ExpR −0.39R、拼接收益 −43%，比“默认参数全程”（ExpR −0.04R）差得多，即训练窗选出的参数在测试窗系统性失效；15m 三折 OOS 仅 10 笔（+0.35R，样本太小）。策略 B（假突破-收复）1h OOS 31 笔 ExpR −0.66R 明确为负，4h/15m OOS 只有 12 笔。策略 C（关键位拒绝）通过过滤的样本极少（15m 2 笔、1h 12 笔、4h 4 笔），无法下结论。没有任何一套策略在三个执行周期上同时给出正的样本外期望。
4. **过滤器不是锦上添花而是生死线**：关闭过滤后所有变体亏损 12%～99%，候选信号 6,000～8,000 个中 94%～99% 被“往返成本/止损风险 > 25%”与“净盈亏比 < 2”拒绝。原因是 Brando 式的结构止损放到 15m/1h 的加密永续上只有 0.1%～0.5% 宽，而 taker 双边 + 滑点 + 资金费约 35 bps，成本吃掉止损风险的一半以上。这与 docs/20 之前的定性判断一致：这套打法在 BTC/ETH 上的瓶颈是成本结构，不是形态识别。
5. **建议**：模拟盘阶段只观察策略 A 在 1h/15m 的表现（B、C 保留开关但建议 `enabled: false`，本报告为保留对照未改默认值）；下一步优先级是 (a) 在能访问交易所的机器上接入 Bitget 公开 K 线与资金费率/未平仓量，取消“资金费保守假设”，(b) 把入场改为区域边界限价（maker），用 `spot90.monitor.fill_model` 的成交概率框架评估成交率，直接把往返成本砍一半，(c) 15m 历史不足 2 年，须从交易所下载中心补齐后重跑 Walk-Forward。在这些完成之前，不应投入真实资金。

## 1. 项目审查与最小侵入式方案

### 1.1 已有组件审查

| 已有组件 | 结论 | brando_pa 的处理 |
|---|---|---|
| `data_store/spot90/bars/*_1d/4h.parquet`（Binance 现货，2017-08 起，经 spot90 校验） | 可用 | **只读复用**（`BarStore` 回退到 spot90 目录），不写入 |
| `spot90.data.static_klines`（GitHub `finom/static-klines` 公开缓存） | 本环境唯一可达的 K 线源；支持 15m（2024-12-30 起）、1h（2022-01 起）、4h/1d/1w | 新写通用多周期适配器 `brando_pa.data.sources.StaticKlinesAdapter`（同一来源、同一缓存策略），下载到 `data_store/brando_pa/` |
| `spot90.data.bitget_v3` / `derivatives`（Bitget 公开 K 线、资金费率、未平仓量） | 本环境 `api.bitget.com` 403 不可达 | 封装为 `BitgetPublicAdapter`，探测失败自动回退；资金费率/OI 过滤器默认关闭并在报告中注明 |
| `spot90.monitor.live`（Bitget → DefiLlama → CoinGecko 参考价） | 可用 | 复用为 `adapter.ticker()` |
| `spot90.atomic`（原子写入）、`spot90.data.quality.continuity_report` | 可用 | 直接复用 |
| `data_store/spot90/onchain/cm_*.parquet`（CoinMetrics，含 `published_at`） | 可用，但交易所流量列依赖地址标签、非 PIT | 链上过滤器按 `published_at ≤ 决策时刻` 对齐，默认关闭，单独报告 |
| `bgw_quant.backtest.metrics`、`walkforward`、`execution.guard` | 面向美股分钟线/日收益序列，与逐根多周期事件回测不匹配 | 借鉴口径（Sharpe/Sortino/PF/Expectancy、训练窗选参-测试窗评估、三重闸门），在 brando_pa 内独立实现 |
| `spot90.dashboard`（Streamlit）、`scripts/spot90_monitor_daily.sh`（flock/日志/告警） | 技术栈一致 | 新建独立看板 `brando_pa/dashboard/app.py`（端口 8502）与 `scripts/brando_pa_daily.sh`，不改原文件 |
| 依赖 | 环境缺 `pyarrow / pytest / duckdb / streamlit / plotly / scipy / httpx`（spot90 本身也需要） | `requirements.txt` 追加 pyarrow/streamlit/plotly，不改 pyproject 的 spot90/bgw_quant 依赖 |

对原项目的改动只有三处且均为追加：`README.md` 增加 brando_pa 章节、`requirements.txt` 追加 3 行、`.gitignore` 追加 2 行。`src/spot90`、`src/bgw_quant`、`config/spot90.yaml` 等零改动。

### 1.2 目录结构

```
config/brando_pa.yaml                 全部参数（周期、关键位、三策略、过滤、风控、成本、回测、模拟盘、执行闸门）
src/brando_pa/
  schema.py                           K 线统一列、周期秒数、周线对齐、剔除未收盘、1d→1w 合成
  data/sources.py | store.py | ingest.py   适配器（static_klines / bitget_public）、存储（自有 + 只读回退 spot90）、采集与数据状态
  analysis/swings.py                  分形摆动点，confirmed_idx = i+k（确认滞后）
  analysis/levels.py                  区域聚类、ATR 自适应宽度（上限 1%）、触及/有效突破/转换统计、事件分类、跨周期合并
  analysis/structure.py               4h HH/HL/LH/LL 与趋势状态
  analysis/mtf.py                     收盘时刻对齐 + 未来数据自检
  analysis/indicators.py              ATR / ADX / 已实现波动 / 相对成交量
  strategies/base.py | breakout_retest.py | reclaim.py | rejection.py   三套状态机策略 + 止盈选择
  filters/pipeline.py                 成交量 / 波动率 / 环境 / 成本 / 衍生品(可选) / 链上(可选)
  risk/costs.py | sizing.py | limits.py   成本模型、仓位（杠杆/保证金/强平/精度）、组合限制
  engine/context.py | signal_engine.py | live.py   多周期上下文（缓存）、信号引擎（WAIT/LONG/SHORT/EXIT/NO_TRADE）、最新信号
  backtest/execution.py               回测与模拟盘共用的成交/持仓规则
  backtest/simulator.py | metrics.py | report.py | runner.py | walkforward.py
  paper/broker.py | state.py | runner.py   PaperBroker（确定性订单号去重）、状态持久化、运行器（once/loop/replay）
  dashboard/app.py                    Streamlit 控制面板
  export.py | cli.py
scripts/brando_pa_daily.sh            cron 包装（锁、日志、告警环境变量）
tests/test_brando_pa_*.py             20 项单元测试
```

## 2. 数据

| 周期 | 来源 | 范围 | 缺口 | 用途 |
|---|---|---|---|---|
| 1w | Binance 现货（GitHub 公开缓存） | 2017-08-14 → 2026-10-26（481 根） | 0 | 长期结构、关键区 |
| 1d | 同上 | 2017-08-17 → 2026-11-05（3368 根） | 0 | 重要高低点、环境分类 |
| 4h | 同上 | 2017-08-17 → 2026-11-06（20193 根） | 16 根（2018–2020 年 8 处，已记录） | 结构 HH/HL/LH/LL、策略 A 突破判定、长历史回测执行周期 |
| 1h | 同上 | 2022-01-01 → 2026-11-06（42489 根） | 1 根 | 中历史回测执行周期 |
| 15m | 同上 | 2024-12-30 → 2026-11-06（64939 根） | 0 | 默认执行周期（实盘/模拟盘），2025–2026 回测 |

数据缺口如实说明：公开缓存的 15m 数据只从 2024-12-30 开始，因此“15m 执行”只能覆盖 2025–2026（主要是下跌与震荡环境：BTC 期内 −26%，ETH −32%）；为覆盖上涨/下跌/横盘/高波动全部环境，另用 1h（2022-04 起）与 4h（2018-06 起）作为执行周期重跑同一套逻辑。缓存每日更新一次，最新 K 线落后数小时到一天：模拟盘据此判定“数据过期 → 禁止新开仓”，这是正确行为而不是缺陷；在能访问交易所 API 的机器上把 `data.exchange_adapter` 切到 `bitget_public` 即可获得实时 K 线。资金费率与未平仓量历史在本环境不可得（交易所域名被拒），对应过滤器默认关闭，资金费在回测中按保守假设计入（多头每 8 小时付 0.01%，空头不计收入）。

## 3. 方法（全部量化、可配置）

**关键价位**：仅用已确认摆动点（分形左右各 k 根，`confirmed_idx = i + k`，在第 i+k 根收盘前不可见）；相近摆动点（< 0.5 ATR）聚为一个区域；半宽 = max(周期 ATR × 系数, 0.15% 价格)，上限 1% 价格；统计触及次数（K 线进入区域且收盘未越过，间隔 ≥ 2 根）、有效突破（收盘越过边界 ≥ 0.10 ATR 且下一根仍在外侧）、支撑/阻力转换；强度 = 周期权重 × (触及 + 0.5×摆动点数 + 转换) × 近期性。跨周期区域仅在“中心落入对方区域”时合并，避免链式黏连。
**事件分类**：`touch`（影线进入、收盘未越过）/ `break_up|down`（收盘越过 ≥ 缓冲）/ `wick_up|down`（影线越过但收盘回到原侧 = 噪声或假突破）/ `none`。
**结构**：4h 已确认摆动点按 ±0.5 ATR 容差标注 HH/HL/LH/LL/EQH/EQL；最近高低点同为 HH/HL → up，LH/LL → down，否则 range。
**跨周期对齐**：决策时刻 = 执行 K 线收盘时刻；高周期索引 = 收盘时刻 ≤ 决策时刻的最后一根（`searchsorted`），并有 `assert_no_lookahead` 自检；区域按高周期索引缓存，只在高周期出新 K 线时重算。
**策略 A 突破-回踩-确认**：4h 收盘越过 1W/1D 区域 ≥ 0.15 ATR(4h) → 设置；48 根执行 K 线内回踩至边界 ±0.25 ATR；回踩后 8 根内确认 K 线（收盘回到突破侧、收盘位置 ≥ 60%、实体同向）；期间收盘回到另一侧 > 0.10 ATR 即失效；止损 = min(回踩最低, 区域下沿) − 0.5 ATR；止盈 = 下一区域前沿（≥ 1R）否则 2.5R；浮盈 1.5R 移保本；最长 192 根。
**策略 B 假突破-收复**：执行 K 线收盘越出区域，深度 ∈ [0.15, 1.5] ATR；6 根内收盘收回区域边界、收盘位置 ≥ 55%；深度超过 1.5 ATR 视为真突破放弃；止损 = 假突破极值 − 0.3 ATR。与 C 的边界：B 必须至少一次收盘在区域外，C 不允许。
**策略 C 关键位拒绝**：触及 1W/1D 区域、收盘未越过；振幅 ≥ 0.8 ATR、反向影线/振幅 ≥ 55%、实体 ≥ 0.1 ATR、收盘位置 ≥ 65%、实体同向；4h 逆势默认不允许；同一区域 12 根冷却。
**过滤器**（可整体关闭，用于对照）：突破根相对成交量 ≥ 1.2×20 根均量（C 用信号根 ≥ 0.8）；ATR/价格 ∈ [0.1%, 3%]；止损距离 ∈ [0.5, 4] ATR；日线强趋势禁止逆势；往返成本/止损风险 ≤ 25%；净盈亏比 ≥ 2。
**风控**：单笔风险 0.5%（0.25%~1%）；数量 = 风险预算 /（止损距离 + 单位往返成本）；合约杠杆 = min(3, 所需, 强平缓冲约束)，逐仓强平价估算并要求强平距离 ≥ 1.5×止损距离；数量按 `qty_step` 取整、`min_notional` 检查；总风险 ≤ 2%、最多 3 仓、同向相关最多 2、单日亏损 2% 暂停、禁止补仓/马丁、数据过期或接口异常禁止新开仓。
**回测成交**：信号在收盘确认、下一根开盘 ± 5 bps 成交（成交前重算净盈亏比与仓位，不达标取消并记录）；跳空越过止损/止盈按开盘价；同一根同时触及止盈止损 → 止损优先；止损额外 10 bps 滑点；taker 费 6 bps（合约）/ 10 bps（现货）双边；资金费按 00/08/16 UTC 结算点计。
**无未来函数的证据**：`test_no_lookahead_truncated_history_gives_identical_signals` 把历史截断到 T，逐根比较截断前 1500 根的状态、理由与区域数，与用完整历史跑出的结果完全一致；`assert_no_lookahead` 在上下文构建时自检；区域只用 confirmed 摆动点；策略 B 的“最终收复”须等到收复 K 线收盘。

## 4. 历史回测结果（含成本；三套策略分别统计；过滤开/关对照；买入持有基准）

### 15m 执行（2025-02-01 → 2026-11-06，15m 数据起点限制）

买入持有基准（期初买入、期末卖出，各扣 10 bps）：BTCUSDT -25.73%（最大回撤 -53.84%），ETHUSDT -32.23%（最大回撤 -69.27%）；等权 -28.98%

| 组合 | 交易数 | 胜率 | PF | Expectancy(R) | 总净收益 | 最大回撤 | Sharpe | Sortino | 最大连亏 | 成本 USDT | 候选→通过 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all_filters | 67 | 47.8% | 1.38 | 0.180 | 6.03% | -4.30% | 0.74 | 0.45 | 5 | 174.00 | 6398→84 |
| all_nofilters | 3215 | 36.0% | 0.49 | -0.299 | -99.17% | -99.19% | -14.08 | -10.92 | 12 | 3952.15 | 6398→6398 |
| A_breakout_retest_filters | 36 | 47.2% | 1.45 | 0.218 | 3.90% | -2.28% | 0.86 | 0.34 | 5 | 72.44 | 755→44 |
| B_reclaim_filters | 29 | 41.4% | 1.20 | 0.110 | 1.52% | -3.06% | 0.26 | 0.11 | 5 | 89.28 | 4045→33 |
| C_rejection_filters | 7 | 57.1% | 0.03 | -0.405 | -1.41% | -2.15% | -0.92 | -0.09 | 1 | 23.31 | 1598→7 |
| A_breakout_retest_nofilters | 508 | 41.5% | 0.90 | -0.048 | -11.96% | -14.61% | -0.80 | -0.85 | 8 | 1240.47 | 755→755 |
| B_reclaim_nofilters | 2691 | 36.7% | 0.47 | -0.298 | -98.19% | -98.24% | -12.14 | -9.84 | 15 | 3892.76 | 4045→4045 |
| C_rejection_nofilters | 1479 | 32.8% | 0.36 | -0.397 | -94.72% | -94.73% | -11.10 | -8.47 | 20 | 3724.30 | 1598→1598 |

按入场时日线环境（all_filters 与三套单策略开过滤）：

| 组合 | 环境 | 次数 | 胜率 | ExpR | 净盈亏 USDT |
|---|---|---|---|---|---|
| all_filters | down | 16 | 50% | 0.09 | 68.93 |
| all_filters | range | 25 | 56% | 0.22 | 282.56 |
| all_filters | strong_down | 17 | 35% | 0.04 | 21.94 |
| all_filters | up | 8 | 50% | 0.62 | 252.19 |
| A_breakout_retest_filters | down | 8 | 38% | 0.07 | 25.39 |
| A_breakout_retest_filters | range | 18 | 56% | 0.23 | 209.44 |
| A_breakout_retest_filters | strong_down | 6 | 33% | 0.05 | 11.52 |
| B_reclaim_filters | down | 7 | 57% | 0.12 | 40.70 |
| B_reclaim_filters | range | 6 | 33% | 0.05 | 13.60 |
| B_reclaim_filters | strong_down | 11 | 36% | 0.04 | 16.01 |

all_filters 细分：按标的 BTCUSDT 12 笔 / 胜率 67% / ExpR 0.44 / 净 269.44；ETHUSDT 55 笔 / 胜率 44% / ExpR 0.12 / 净 333.30。按方向 long 27 笔 / ExpR -0.03 / 净 -51.88；short 40 笔 / ExpR 0.32 / 净 654.62。按出场原因 stop 31；stop_gap 1；take_profit 15；time_stop 12；trail_stop 8。候选 6398 → 通过 84，主要拒绝原因 {"cost_to_risk": 5245, "net_rr": 4055, "volume": 3147, "regime_trend": 1190, "stop_distance": 678, "volatility": 147, "portfolio:已有同向持仓，禁止补仓/摊平": 5}；入场时取消 10。

### 1h 执行（2022-04-01 → 2026-11-06）

买入持有基准（期初买入、期末卖出，各扣 10 bps）：BTCUSDT 67.07%（最大回撤 -66.69%），ETHUSDT -31.90%（最大回撤 -74.49%）；等权 17.58%

| 组合 | 交易数 | 胜率 | PF | Expectancy(R) | 总净收益 | 最大回撤 | Sharpe | Sortino | 最大连亏 | 成本 USDT | 候选→通过 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all_filters | 291 | 36.1% | 0.93 | -0.037 | -5.96% | -19.85% | -0.19 | -0.16 | 10 | 528.65 | 8163→403 |
| all_nofilters | 2760 | 37.6% | 0.75 | -0.135 | -84.80% | -85.11% | -3.67 | -3.60 | 11 | 4171.05 | 8163→8163 |
| A_breakout_retest_filters | 243 | 37.4% | 0.99 | 0.002 | -0.35% | -15.55% | 0.03 | 0.02 | 10 | 412.56 | 2242→343 |
| B_reclaim_filters | 46 | 30.4% | 0.61 | -0.259 | -5.91% | -9.88% | -0.58 | -0.15 | 8 | 121.46 | 4774→47 |
| C_rejection_filters | 13 | 30.8% | 0.64 | -0.235 | -1.55% | -2.42% | -0.40 | -0.05 | 5 | 33.51 | 1147→13 |
| A_breakout_retest_nofilters | 1009 | 36.6% | 0.91 | -0.050 | -23.40% | -26.24% | -0.66 | -0.73 | 11 | 2167.99 | 2242→2242 |
| B_reclaim_nofilters | 2944 | 39.4% | 0.70 | -0.158 | -90.41% | -90.74% | -4.18 | -3.97 | 15 | 4015.04 | 4774→4774 |
| C_rejection_nofilters | 1076 | 37.2% | 0.60 | -0.225 | -70.58% | -70.80% | -3.33 | -2.68 | 13 | 2996.32 | 1147→1147 |

按入场时日线环境（all_filters 与三套单策略开过滤）：

| 组合 | 环境 | 次数 | 胜率 | ExpR | 净盈亏 USDT |
|---|---|---|---|---|---|
| all_filters | down | 55 | 33% | -0.26 | -667.27 |
| all_filters | range | 88 | 39% | 0.07 | 259.19 |
| all_filters | strong_down | 75 | 40% | 0.13 | 418.28 |
| all_filters | strong_up | 18 | 28% | -0.07 | -70.05 |
| all_filters | up | 55 | 33% | -0.20 | -537.88 |
| A_breakout_retest_filters | down | 43 | 35% | -0.14 | -287.78 |
| A_breakout_retest_filters | range | 67 | 40% | 0.16 | 478.14 |
| A_breakout_retest_filters | strong_down | 66 | 42% | 0.10 | 320.18 |
| A_breakout_retest_filters | strong_up | 17 | 29% | -0.01 | -22.08 |
| A_breakout_retest_filters | up | 50 | 32% | -0.21 | -524.65 |
| B_reclaim_filters | down | 11 | 18% | -0.73 | -398.62 |
| B_reclaim_filters | range | 22 | 36% | -0.24 | -261.47 |
| B_reclaim_filters | strong_down | 7 | 29% | 0.42 | 146.69 |
| B_reclaim_filters | up | 5 | 40% | -0.12 | -28.56 |
| C_rejection_filters | range | 5 | 40% | -0.36 | -90.96 |
| C_rejection_filters | strong_down | 5 | 20% | 0.13 | 30.67 |

all_filters 细分：按标的 BTCUSDT 110 笔 / 胜率 38% / ExpR -0.10 / 净 -563.67；ETHUSDT 181 笔 / 胜率 35% / ExpR 0.00 / 净 -34.06。按方向 long 65 笔 / ExpR -0.17 / 净 -519.26；short 226 笔 / ExpR 0.00 / 净 -78.47。按出场原因 end_of_test 1；stop 172；take_profit 59；time_stop 28；trail_stop 31。候选 8163 → 通过 403，主要拒绝原因 {"net_rr": 6061, "cost_to_risk": 4113, "volume": 3925, "regime_trend": 1737, "stop_distance": 894, "portfolio:已有同向持仓，禁止补仓/摊平": 62, "volatility": 54, "portfolio:同向相关持仓 2 已达上限 2（crypto_major）|已有同向持仓，禁止补仓/摊平": 17}；入场时取消 33。

### 4h 执行（2018-06-01 → 2026-11-06，覆盖 2018 熊市、2019 反弹、2020–2021 牛市、2022 熊市、2023–2024 上涨、2025–2026 下跌/震荡）

买入持有基准（期初买入、期末卖出，各扣 10 bps）：BTCUSDT 908.21%（最大回撤 -77.04%），ETHUSDT 284.97%（最大回撤 -86.81%）；等权 596.59%

| 组合 | 交易数 | 胜率 | PF | Expectancy(R) | 总净收益 | 最大回撤 | Sharpe | Sortino | 最大连亏 | 成本 USDT | 候选→通过 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all_filters | 298 | 39.9% | 1.01 | 0.010 | 0.68% | -17.22% | 0.04 | 0.03 | 12 | 443.57 | 7639→495 |
| all_nofilters | 1600 | 34.3% | 0.85 | -0.089 | -51.62% | -52.15% | -1.33 | -1.34 | 14 | 2385.33 | 7639→7639 |
| A_breakout_retest_filters | 280 | 39.6% | 0.96 | -0.018 | -3.29% | -16.95% | -0.07 | -0.05 | 12 | 379.16 | 3380→473 |
| B_reclaim_filters | 16 | 37.5% | 1.28 | 0.150 | 1.12% | -3.09% | 0.12 | 0.02 | 3 | 41.59 | 3691→17 |
| C_rejection_filters | 5 | 60.0% | 3.41 | 0.968 | 2.43% | -0.70% | 0.42 | 0.04 | 1 | 12.86 | 568→5 |
| A_breakout_retest_nofilters | 1073 | 34.3% | 0.95 | -0.028 | -15.03% | -24.16% | -0.29 | -0.31 | 15 | 1950.95 | 3380→3380 |
| B_reclaim_nofilters | 1739 | 37.2% | 0.85 | -0.084 | -52.82% | -53.99% | -1.26 | -1.24 | 11 | 2745.46 | 3691→3691 |
| C_rejection_nofilters | 525 | 36.4% | 0.80 | -0.112 | -25.92% | -30.51% | -0.79 | -0.49 | 13 | 1250.93 | 568→568 |

按入场时日线环境（all_filters 与三套单策略开过滤）：

| 组合 | 环境 | 次数 | 胜率 | ExpR | 净盈亏 USDT |
|---|---|---|---|---|---|
| all_filters | down | 46 | 33% | -0.14 | -334.62 |
| all_filters | range | 98 | 42% | 0.03 | 126.91 |
| all_filters | strong_down | 69 | 38% | -0.01 | -90.73 |
| all_filters | strong_up | 7 | 43% | 1.19 | 410.45 |
| all_filters | up | 78 | 44% | -0.01 | -47.31 |
| A_breakout_retest_filters | down | 43 | 33% | -0.11 | -230.63 |
| A_breakout_retest_filters | range | 92 | 41% | -0.01 | -58.81 |
| A_breakout_retest_filters | strong_down | 67 | 37% | -0.00 | -46.46 |
| A_breakout_retest_filters | up | 75 | 45% | 0.02 | 97.26 |

all_filters 细分：按标的 BTCUSDT 152 笔 / 胜率 38% / ExpR -0.02 / 净 -227.43；ETHUSDT 146 笔 / 胜率 42% / ExpR 0.05 / 净 292.13。按方向 long 14 笔 / ExpR 0.87 / 净 618.35；short 284 笔 / ExpR -0.03 / 净 -553.66。按出场原因 end_of_test 2；stop 175；take_profit 73；time_stop 5；trail_stop 43。候选 7639 → 通过 495，主要拒绝原因 {"net_rr": 5907, "volume": 3658, "cost_to_risk": 2632, "regime_trend": 1373, "volatility": 1200, "stop_distance": 198, "portfolio:已有同向持仓，禁止补仓/摊平": 101, "portfolio:同向相关持仓 2 已达上限 2（crypto_major）|已有同向持仓，禁止补仓/摊平": 83, "portfolio:持仓数 3 已达上限 3|同向相关持仓 2 已达上限 2（crypto_major）|已有同向持仓，禁止补仓/摊平": 1}；入场时取消 10。

## 5. Walk-Forward 验证（每个策略独立；训练窗选参、验证窗确认、测试窗只评估一次；与默认参数全程对照）

### 15m（训练 8 月 / 验证 3 月 / 测试 3 月）

| 策略 | 折数 | OOS 交易 | OOS 胜率 | OOS PF | OOS ExpR | OOS 拼接收益 | OOS 拼接回撤 | 默认参数全程：交易/ExpR/PF | 退回默认的折 |
|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 3 | 10 | 50.0% | 1.68 | 0.346 | 1.71% | -1.94% | 11 / 0.370 / 1.89 | 1 |
| B_reclaim | 3 | 12 | 50.0% | 1.14 | 0.063 | 0.36% | -1.36% | 13 / 0.221 / 1.57 | 3 |
| C_rejection | 3 | 2 | 50.0% | 0.03 | -0.470 | -0.47% | 0.00% | 2 / -0.470 / 0.03 | 3 |

- **A_breakout_retest** 各折测试窗：2026-01-01→2026-04-01 参数 {"break_buffer_atr": 0.1, "sl_buffer_atr": 0.3} → 9 笔 ExpR 0.49；2026-04-01→2026-07-01 参数 {"break_buffer_atr": 0.1, "sl_buffer_atr": 0.3} → 0 笔 ExpR —；2026-07-01→2026-10-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 1 笔 ExpR -0.97
- **B_reclaim** 各折测试窗：2026-01-01→2026-04-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 8 笔 ExpR 0.18；2026-04-01→2026-07-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR -0.97；2026-07-01→2026-10-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR 0.62
- **C_rejection** 各折测试窗：2026-01-01→2026-04-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR -0.47；2026-04-01→2026-07-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2026-07-01→2026-10-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —

### 1h（12 / 6 / 6 月）

| 策略 | 折数 | OOS 交易 | OOS 胜率 | OOS PF | OOS ExpR | OOS 拼接收益 | OOS 拼接回撤 | 默认参数全程：交易/ExpR/PF | 退回默认的折 |
|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 6 | 162 | 39.5% | 1.22 | 0.120 | 9.62% | -11.25% | 172 / 0.033 / 1.05 | 3 |
| B_reclaim | 6 | 31 | 19.4% | 0.15 | -0.661 | -10.17% | -9.75% | 32 / -0.569 / 0.25 | 5 |
| C_rejection | 6 | 12 | 33.3% | 0.72 | -0.174 | -1.08% | -2.42% | 12 / -0.174 / 0.72 | 6 |

- **A_breakout_retest** 各折测试窗：2023-10-01→2024-04-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 19 笔 ExpR -0.21；2024-04-01→2024-10-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 27 笔 ExpR 0.09；2024-10-01→2025-04-01 参数 {"break_buffer_atr": 0.1, "sl_buffer_atr": 0.5} → 40 笔 ExpR -0.18；2025-04-01→2025-10-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 23 笔 ExpR 0.18；2025-10-01→2026-04-01 参数 {"break_buffer_atr": 0.1, "sl_buffer_atr": 0.3} → 36 笔 ExpR 0.44；2026-04-01→2026-10-01 参数 {"break_buffer_atr": 0.1, "sl_buffer_atr": 0.3} → 17 笔 ExpR 0.49
- **B_reclaim** 各折测试窗：2023-10-01→2024-04-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.5} → 1 笔 ExpR -0.95；2024-04-01→2024-10-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 6 笔 ExpR -0.81；2024-10-01→2025-04-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 5 笔 ExpR -0.58；2025-04-01→2025-10-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 6 笔 ExpR -0.64；2025-10-01→2026-04-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 7 笔 ExpR -0.44；2026-04-01→2026-10-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 6 笔 ExpR -0.81
- **C_rejection** 各折测试窗：2023-10-01→2024-04-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2024-04-01→2024-10-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 1 笔 ExpR 0.00；2024-10-01→2025-04-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR -0.46；2025-04-01→2025-10-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2025-10-01→2026-04-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 7 笔 ExpR 0.11；2026-04-01→2026-10-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR -0.97

### 4h（12 / 6 / 6 月）

| 策略 | 折数 | OOS 交易 | OOS 胜率 | OOS PF | OOS ExpR | OOS 拼接收益 | OOS 拼接回撤 | 默认参数全程：交易/ExpR/PF | 退回默认的折 |
|---|---|---|---|---|---|---|---|---|---|
| A_breakout_retest | 13 | 223 | 37.2% | 0.58 | -0.390 | -43.34% | -46.16% | 234 / -0.039 / 0.93 | 12 |
| B_reclaim | 13 | 12 | 41.7% | 2.07 | 0.441 | 2.64% | -1.47% | 14 / 0.242 / 1.46 | 13 |
| C_rejection | 13 | 4 | 50.0% | 2.38 | 0.683 | 1.36% | -0.49% | 4 / 0.683 / 2.36 | 13 |

- **A_breakout_retest** 各折测试窗：2019-12-01→2020-06-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 25 笔 ExpR -0.22；2020-06-01→2020-12-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 6 笔 ExpR -0.80；2020-12-01→2021-06-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 3 笔 ExpR -0.32；2021-06-01→2021-12-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 9 笔 ExpR -0.65；2021-12-01→2022-06-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 28 笔 ExpR 0.63；2022-06-01→2022-12-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 19 笔 ExpR -0.03；2022-12-01→2023-06-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 16 笔 ExpR -4.05；2023-06-01→2023-12-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 19 笔 ExpR -0.41；2023-12-01→2024-06-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 11 笔 ExpR -0.22；2024-06-01→2024-12-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 24 笔 ExpR -0.23；2024-12-01→2025-06-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 16 笔 ExpR -0.63；2025-06-01→2025-12-01 参数 {"break_buffer_atr": 0.15, "sl_buffer_atr": 0.5}(默认) → 20 笔 ExpR 0.07；2025-12-01→2026-06-01 参数 {"break_buffer_atr": 0.1, "sl_buffer_atr": 0.3} → 27 笔 ExpR 0.09
- **B_reclaim** 各折测试窗：2019-12-01→2020-06-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2020-06-01→2020-12-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 1 笔 ExpR 7.89；2020-12-01→2021-06-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2021-06-01→2021-12-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR -0.51；2021-12-01→2022-06-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR -0.04；2022-06-01→2022-12-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 1 笔 ExpR -0.99；2022-12-01→2023-06-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2023-06-01→2023-12-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2023-12-01→2024-06-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 3 笔 ExpR 0.45；2024-06-01→2024-12-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2024-12-01→2025-06-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR -0.49；2025-06-01→2025-12-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 1 笔 ExpR -0.88；2025-12-01→2026-06-01 参数 {"min_break_depth_atr": 0.15, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —
- **C_rejection** 各折测试窗：2019-12-01→2020-06-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 2 笔 ExpR 0.65；2020-06-01→2020-12-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2020-12-01→2021-06-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2021-06-01→2021-12-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2021-12-01→2022-06-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 1 笔 ExpR 2.41；2022-06-01→2022-12-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2022-12-01→2023-06-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2023-06-01→2023-12-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2023-12-01→2024-06-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2024-06-01→2024-12-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2024-12-01→2025-06-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 1 笔 ExpR -0.98；2025-06-01→2025-12-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —；2025-12-01→2026-06-01 参数 {"wick_ratio_min": 0.55, "sl_buffer_atr": 0.3}(默认) → 0 笔 ExpR —

## 6. 模拟盘（Paper Trading）

回放模式跑过最近 6000 根 15m K 线（两标的）：净值 10086.59（初始 10000.0），已实现 86.59 USDT，交易 2 笔，持仓 0，订单号 4 个，已处理至 {'BTCUSDT': '2026-11-06 10:30:00+00:00', 'ETHUSDT': '2026-11-06 10:30:00+00:00'}。
- ETHUSDT A_breakout_retest long 入 2026-10-15 16:45 @ 2433.56 → 出 2026-10-17 16:45 @ 2402.69，time_stop，净 -19.41（-0.39R）
- ETHUSDT B_reclaim short 入 2026-11-04 15:45 @ 2424.01 → 出 2026-11-05 15:00 @ 2334.69，take_profit，净 106.01（2.12R）


## 7. 验收对照

| 验收项 | 状态 | 证据 |
|---|---|---|
| 1. BTC 与 ETH 能正常识别价格行为信号 | 通过 | `python -m brando_pa.cli analyze / signal`：两标的输出 1W/1D/4H 合并关键区域（含触及、有效突破、支撑阻力转换）、4h 结构、日线环境、三策略状态；回测期内产生 LONG/SHORT 信号（15m 82 个、1h 403 个、4h 495 个通过过滤） |
| 2. 三套策略可以独立开关和回测 | 通过 | `config/brando_pa.yaml strategies.*.enabled`；回测矩阵分别输出 `A_*/B_*/C_*` × 过滤开/关 8 个变体，Walk-Forward 每策略独立 |
| 3. 能明确展示为什么开仓、为什么不交易 | 通过 | `SignalReport.reasons`（策略条件逐条、净盈亏比、成本、仓位、止损止盈、失效条件）与 `candidates[*].filter.checks`（每个过滤器 OK/FAIL/SKIP 及阈值）；看板“为什么开仓 / 为什么不交易”区块；`*_signals_log.csv` 记录每个候选的拒绝原因 |
| 4. 回测无已知未来函数，交易成本计入统计 | 通过 | 摆动点 confirmed_idx、收盘时刻对齐与 `assert_no_lookahead`、截断历史一致性测试；成交规则见 §3；每笔交易记录 fees/funding/gross/net，汇总给出成本占毛利比例与成本合计 |
| 5. 无需真实账户资金即可运行完整模拟交易流程 | 通过 | `paper --replay` / `--once` / `--loop`：PaperBroker 记账、确定性订单号去重、状态持久化与重启恢复测试、锁/重试/限速/告警；真实下单三重闸门且无实现 |
| 6. 输出真实可复现的绩效报告，不虚构交易或承诺固定收益 | 通过 | 全部数字来自 `reports_out/brando_pa/backtest/*/compare.json`、`*_summary.json`、`walkforward/*/walkforward.json`；命令见 README；报告明确写出负面结果 |

## 8. 新增第三方数据/API 需求（未使用任何已有密钥，日志不输出密钥）

| 用途 | 来源 | 费用 | 本环境状态 | 替代 |
|---|---|---|---|---|
| 实时/完整 K 线（15m 2024-12 之前、分钟级） | Bitget 公开 K 线（已有适配器）/ Bitget 下载中心 / Binance data.binance.vision | 免费 | 域名被拒 | GitHub 公开缓存（已用，每日更新） |
| 资金费率、未平仓量历史 | Bitget `history-fund-rate` / `open-interest`（spot90 已封装）、Coinglass（付费）、Binance futures data | 免费 / 付费 | 域名被拒 → 过滤器关闭 | 回测用保守资金费假设并在报告注明 |
| 交易所净流入、大额转账 | CoinMetrics 社区 CSV（已在 spot90 数据仓，含 published_at）、Glassnode（付费） | 免费 / 付费 | 可用但交易所标签非 PIT | 已按发布时间对齐，默认关闭，可用 `filters.onchain.enabled` 单独对照 |

## 9. 运行命令速查

```bash
export PYTHONPATH=src
python -m brando_pa.cli ingest
python -m brando_pa.cli analyze
python -m brando_pa.cli signal
python -m brando_pa.cli --exec-tf 15m backtest --start 2025-02-01 --name m15_2025_2026
python -m brando_pa.cli --exec-tf 1h  backtest --start 2022-04-01 --name h1_2022_2026
python -m brando_pa.cli --exec-tf 4h  backtest --start 2018-06-01 --name h4_2018_2026
python -m brando_pa.cli --exec-tf 15m walkforward --start 2025-02-01 --name m15 --train-months 8 --valid-months 3 --test-months 3
python -m brando_pa.cli --exec-tf 1h  walkforward --start 2022-04-01 --name h1
python -m brando_pa.cli --exec-tf 4h  walkforward --start 2018-06-01 --name h4
python -m brando_pa.cli paper --replay --replay-bars 6000
python -m brando_pa.cli paper --once            # 或 --loop；或 crontab */15 * * * * scripts/brando_pa_daily.sh
python -m brando_pa.cli dashboard --port 8502
python -m brando_pa.cli export
pytest tests/test_brando_pa_*.py -q
```
