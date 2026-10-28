# Bitget Wallet 美股代币化 AI 量化复盘与交易员训练系统（V1 研究版）

> **V1 仅限研究、历史回测与模拟交易。** 不连接真钱钱包、不请求私钥、不自动签名或下单。
> 每日 0.8%–2.2% 只是研究目标，**不是保证收益**；系统不会为了达标而捏造交易，达不到时会明确显示"未达标"。

## 历史盈利路径模板（playbook，当前阶段主入口）：昨天怎样交易能达标

> 不预测未来。以昨天完整走过的行情为基础，反向求解各本金档位达到日净收益目标（0.8%–2.2%）的**低风险交易路径模板**：
> 多资产（美股代币 × BTC/ETH/SOL/HYPE）、可切换、不限笔数、含手续费/价差/滑点/冲击/Gas/资金成本、受流动性与本金约束。
> **事后模板 ≠ 可执行策略**：可执行（无未来信息）见决策引擎与轮动回测（C 模型）。目标不可达时输出最高可实现净收益与缺口，不伪造达标。

```bash
bgwq import-dir --day 2026-10-22          # 离线导入真实数据：Bitget 官方下载中心 https://www.bitget.com/data-download 的 zip/csv 放到 data_in/<SYMBOL>/<day>/（见 docs/11、12）
bgwq venue-check --day 2026-10-22         # 场所矩阵：Reality rToken=Bitget 交易所现货交易对；xStocks/Ondo=钱包链上代币（chain+contract）；禁止混用
bgwq data-status --day 2026-10-22         # 数据完整性与分级（来源/时间范围/K 线覆盖/缺口/逐笔/深度）
bgwq playbook-sensitivity --day 2026-10-22 --participations 0.01 0.05 0.10   # 参与率敏感性（跨资产模板）
bgwq playbook-daily --require-real        # 每日：昨天（或 --day）七档 × 跨资产 + 单资产模板 → 流水 CSV + 逐笔校验 CSV + K 线买卖点图 + 收益曲线 + HTML/JSON；严格模式拒绝 SIMULATED
bgwq playbook --day 2026-10-22 --trades   # 终端查看七档跨资产模板与完整流水（--mode single:NVDA 单资产）
# 每日自动：crontab 例  30 7 * * 2-6  cd /path && .venv/bin/bgwq playbook-daily >> logs/playbook.log 2>&1
```
- 目标函数：先“累计净收益 ≥ 目标”，再最小化风险 R = 最大回撤(逐分钟盯市) + 0.5×Σ单笔最大不利波动 + 成本 + 每笔罚金；区间得分 = 净利润 − λ×最大不利波动，λ 几何递增至不再达标；按出场时间截断到首次达标。
- 输出：资产、方向、入场/出场时间与价格、仓位金额、数量、成本、单笔净利润、累计净利润、笔数、达标时刻、切换次数、最大回撤、最高可实现净收益与缺口；图表在真实 K 线上标注全部买卖点并展示资金收益曲线。
- 结果分级 SIMULATED / EXECUTION_ESTIMATED / REAL_HISTORICAL，逐笔校验成交量、深度、时间顺序、成交价（不用高低点）、手续费与滑点（`playbook/verify.py`）。
- 接口：`GET /api/playbook?day=&mode=cross|single:NVDA`、`POST /api/playbook/report`、`GET /api/playbook/latest`；参数 `config/playbook.yaml`；输出 `reports_out/playbook/<day>/`。

## 跨市场动态资产轮动（股票代币 × BTC/ETH/SOL/HYPE）：A / B / C 三套模型 + 六类策略排名（当前阶段主入口）

> 目标：通过全市场动态筛选、多资产轮动和多次交易提高各档位日目标达标率。三套模型独立计算、独立报告：
> **A** 历史事后理论最优（全知者跨资产切换、不限流动性）· **B** 历史流动性约束上限（事后，含成交量/成本）· **C** 无未来信息的因果轮动模型（每分钟只用已产生数据，参数只由之前交易日选择）。
> A/B 是“历史上可以赚到的利润”上界；只有 C 才是“真实交易中有可能实现的利润”的估计。均不构成收益承诺。

```bash
bgwq rotation --day 2026-10-16 --ab                 # 单日：A/B/C/C*… 17 个策略实例 × 七档（--trades 输出逐笔）
bgwq rotation-backtest --test-days 10 --train-days 5 # 多日时间顺序样本外回测（可续跑，多进程，结果缓存 data_store/rotation_cache.duckdb）
bgwq rotation-report                                 # 综合报告：按档位排名、六类策略族对比、适用范围/失败原因、最优候选逐日明细、K 线路径图、CSV
bgwq key-audit                                       # 密钥泄露扫描 + 未知 32 位值字段识别（环境变量输入，不输出密钥）
```
- 策略族：A 固定单只股票 · B 固定单种加密 · C 股票轮动 · D 加密轮动 · E 股票×加密跨市场轮动 · F 多资产组合并行；另有 A*/B* “事前选股/选币”版本（只用之前几日样本外表现挑选）。
- 轮动规则（因果）：每分钟对资产池内有 K 线的资产打分（预期净收益 = 仓位 × (持有期预期波动 × 相对胜率 − 往返成本)，风险 = 仓位 × 每根波动 × √持有期）；槛位满时只有新资产预期净收益 − 切换成本 > 持有资产继续持有期望 才切换；全部不满足 → 空仓；达标后进入再评估模式（更高门槛、减半仓位、回吐保护）。
- 加密货币：Bitget 公开现货 K 线（`api.bitget.com`，无需密钥）→ 不可达时 SIMULATED 并标注；24 小时按 America/New_York 自然日与股票同网格；成本档 `crypto_spot`。
- 配置 `config/rotation.yaml`（训练/测试天数、参数网格、达标后规则）、`config/symbols.yaml#crypto_symbols`、`config/costs.yaml#profile_map`。
- 接口：`GET /api/rotation/specs`、`GET /api/rotation/day?strategy=E:cross_rotation`、`GET /api/rotation/summary`、`POST /api/rotation/report`。
- 报告：`docs/09_跨市场轮动回测报告.md`、`reports_out/rotation/rotation_report_*.html`；审计：`docs/08_安全与数据源审计.md`。

## 历史可行性反推：A 事后最优 vs B 可执行最大净收益

> 本阶段**不研究预测能力**。用昨天已经完整走出的真实历史行情，反向寻找每个本金档位能达到指定净收益率的交易路径。
> 结果属于**事后分析**，明确标注"不代表未来可复制收益"。

```bash
bgwq feasibility NVDA --day 2026-10-16 --trades          # 七档：A 理论最大净收益 / B 约束下可执行最大净收益 / 达标所需交易次数 / 完整买卖点
bgwq feasibility-report --day 2026-10-16 [--symbols ...]  # 全标的 × 七档 HTML + CSV + 买卖点 K 线图（reports_out/<day>/feasibility_*）
bgwq --cost-profile token_dex_optimistic feasibility-report
```
- 数据优先级：官方 `transactionList` 逐笔成交聚合到每根 K 线（流动性来源 `transactions`）> 官方 K 线成交额（`kline_amount`）> 未知（标注）；绝不使用模拟价格充当正式结果。
- 约束：严格时间顺序；每根 K 线成交量 ≤ 参与率 × 实际成交额，多笔之间共享；本金上限；手续费/价差/滑点/冲击/Gas；只按收盘价成交，**不假设在最高价/最低价成交**；做空只在能力登记为已验证时允许（默认关闭）。
- 输出：`理论最大净收益(A)`、`约束下可执行最大净收益(B)`、`达标所需交易次数`、达标时刻、分笔数、峰值占用、流动性占用、限制原因；A 与 B 分开报告。
- 工作台接口：`GET /api/feasibility/{symbol}?day=&points=mid`、`POST /api/feasibility/report`。

## 70 组测试矩阵

```bash
bgwq net-diag             # DNS/代理/TLS/时钟/签名/限频诊断 + 接口归属确认 + 需放行域名
bgwq verify-matrix        # 逐品种核实：类型/状态/链合约/方向/订单限制（无法核实 = 未验证，不猜测）
bgwq matrix-report        # 70 组：A 理论 / B 事后可执行上限 / C 因果引擎 + 能力 + 原因 + 下一轮建议
```
引擎新增：执行延迟（latency_bars）、成交率（fill_rate）、异常跳变熔断、数据缺口暂停、日内回撤熔断、最大杠杆 1x、低流动性行情暂停；只读可执行报价 `getSwapPrice`（绝不创建/提交订单）。

## 统一收益决策引擎（`decision/`）

- **能力登记** `capability.py`：按官方 API（stockList/stockInfo）与人工核实登记每个品种支持的方向与订单类型。官方 RWA 接口只有链上现货买卖，无杠杆、无借券做空；引擎只在登记支持的方向内选择，现货卖出只用于平多，绝不冒充开空。
- **因果引擎** `engine.py`：每根 K 线收盘后按趋势（EMA 差/ATR）、区间（z 分数）、波动（实现波动缩仓）、流动性（过去 20 根成交额 × 参与率 × 分批根数定仓）、成本门槛（预期波动/往返成本）自动选择 trend_long / trend_short / range_long / range_short / 暂停；多笔并行、共享流动性、API 限频（子单/分钟）、止损 0.5%、ATR 止盈、趋势跟踪止盈、日亏损上限、达标即停；成本含手续费/价差/滑点/冲击/Gas/资金成本/借券费。
- **滚动优化** `optimize.py`：每个档位参数只在训练窗按稳定性目标选择，测试窗统计样本外达标率、亏损日比例、回撤、胜率、笔数、利用率；昨日复盘使用“昨日之前”选出的参数。
- **原因归类** `analysis.py`：产品不支持 / 产品不支持做空 / 目标高于理论上限 / 成本过高 / 流动性不足 / 资金容量限制 / 策略不足 / 昨日达标但样本外不稳定。

```bash
bgwq engine NVDA --trades          # 七档：昨日因果复盘 + 样本外 + 原因归类 + 逐笔
bgwq engine-report --symbols NVDA SPCX
```
工作台首个页签「决策引擎」；接口 `/api/engine/{symbol}`、`/api/engine-summary`、`POST /api/engine/report`；参数 `config/decision_engine.yaml`。

## 官方 RWA API 接入（live 优先）

```bash
cp .env.example .env            # 填 BGW_API_KEY / BGW_API_SECRET（官方签名 HMAC-SHA256 需要 Secret）；密钥只存本地，不进代码
bgwq live-test --symbols NVDA SPCX TSLA      # 连接测试：网络 → stockList/stockInfo 核对代币/链/合约/发行商 → 1m/5m/15m K线分页；报告写入 docs/api_live_test_<day>.json
bgwq verify-assets --symbols NVDA SPCX TSLA  # 仅核对资产
bgwq ingest --symbols NVDA SPCX TSLA --days 30 --periods 1m 5m 15m   # 分页拉取历史并存 DuckDB（含 tf 列）
bgwq compare-report --symbols NVDA SPCX --wf-symbols NVDA SPCX        # 用真实K线跑完整对比回测
```
live 模式以 Bitget 官方代币 K 线为准（Massive 原生基准可选）；只读行情接口，不含任何下单/转账/签名交易调用。

## 档位对比（当前主入口）：A 理论最优 vs B 多笔可执行 vs C 无未来信息滚动回测（`simple_sim/multi.py`）

- **A 理论最优**：动态规划，单次全仓、不限次数、仅做多，只扣手续费/价差/滑点/Gas，不计流动性与冲击 —— 纯上限，不可执行。
- **B 可执行模拟**：同时最多 max_open 个小额仓位并行，每笔分批成交并共享逐根流动性预算（不重复计算），含冲击、Gas、资金成本、借券费，止损 0.5%，日亏损上限；每笔金额 = min(可用本金, 本金/并行数, 当下可用流动性)，按本金自动调整；累计净利润达目标即停。参数（并行仓位数、最低单笔净收益）在小网格内事后挑选并标注。
- **失败原因分解**：目标高于理论上限 / 成本门槛高于行情波动（按实际可成交单笔规模计算往返成本 vs 60 根波动 90 分位）/ 流动性容量不足（峰值占用、全日参与率预算已用比例）/ 机会数量不足（缺口）。
- **C 滚动回测**：不使用未来信息的因果策略（z 分数低吸 + 按最近 20 根成交额自动缩仓），参数只在训练窗选择，报告大资金档位的样本外达标率、亏损日比例、回撤。若 A/B 达标而 C 不达标，说明达标依赖事后信息。

```bash
bgwq compare NVDA --trades                       # 七档：A/B 对比 + 失败原因 + 逐笔
bgwq compare-report --wf-symbols NVDA SPCX       # 全标的对比报告 + CSV + 可切换 A/B 的 K 线图
```
工作台首个页签「档位对比」；接口 `/api/compare/{symbol}`、`/api/compare-summary`、`POST /api/compare/report`。

## 简化版（多空教学模拟）：昨日真实走势 · 多空多次交易 · 累计净利润达档位目标即停（`simple_sim/`）

1. **数据优先级**：Bitget Wallet 官方 RWA 行情 API（`stockList/stockInfo/kline/transactionList`，HMAC 签名）→ 真实代币 1 分钟 K 线与成交额。配置 `.env` 中的 `BGW_API_KEY/BGW_API_SECRET` 后：`bgwq probe && bgwq ingest`。无密钥或主机不可达时退回明确标注的 SIMULATED 数据，所有报告标为**非正式验证**。
2. **模拟规则**：按时间顺序，在看过全天 K 线后为每个入场点选出止损 0.5% 内、扣除手续费/价差/滑点/冲击/Gas/资金成本（年化 5%）后净收益 ≥ 0.1% 的平仓点；能补足剩余目标的取最早，否则取净收益最大；多空同时可行按最早平仓；不限次数、不强制凑数；单笔投入 ≤ 本金（不加杠杆、不加仓），分批成交受逐根流动性限制；累计净利润达目标即停；当日亏损达上限即停。
3. **做空**：仅对 `config/symbols.yaml` 中 `short_support.verified` 核实为可做空的品种开放（链上现货代币默认不可做空），做空计提借券费。
4. **输出**：本金、目标利润、实际净利润、买/卖次数、做多/做空次数、资金利用率、交易成本、资金成本、是否达标、未达标原因；K 线上标记全部分批买卖点与累计净利润曲线。

```bash
bgwq simple NVDA                     # 七档本金（中位）逐档模拟
bgwq simple NVDA --capital-usd 100000 --target-usd 980
bgwq simple-report                   # 全标的 × 七档 简单报告 + CSV + 可切换档位的 K 线图
```
工作台首个页签「多空教学模拟」；接口 `/api/simple/{symbol}`、`/api/simple-summary`、`POST /api/simple/report`。参数见 `config/simple_sim.yaml`。
**这是已知昨日完整走势的事后教学分析，不是实盘预测，暂不执行真实资金交易。**

## 固定收益目标 · 稳定性优先（`target_model/`，保留）

以“稳定达标”而非“最大利润”为目标，严格分成两部分：

| | A 教学路径（事后） | B 策略回测（无未来信息） |
|---|---|---|
| 信息 | 已知昨日完整走势 | 每根K线收盘后决策，下一根起分批成交 |
| 逻辑 | 按时间顺序挑选“局部低点入场、持有期内不利波动≤止损、净收益≥单笔目标”的稳定小额交易，累计达标即停 | z 分数低吸入场；止盈（扣费净收益达标）/止损/最长持有/日终；达标即停；日亏损上限；次数上限；冷却 |
| 仓位与成交 | 单笔 ≤ 本金 × position_fraction，分批建仓/平仓（每根 ≤ 参与率 × 成交额），不加杠杆、不超本金 | 同左，部分成交按比例减仓 |
| 验证 | 逐笔明细、累计净利润曲线、达标时刻、未达标原因 | 滚动样本外：达标率、亏损日比例、日收益标准差、最大回撤、笔/日、部分成交/不可验证天数；参数只在训练窗按稳定性目标选择 |

```bash
bgwq target NVDA --capital-usd 100000        # 教学路径 + 同日策略回放
bgwq target-backtest NVDA                     # 10万/30万/100万/1000万 滚动样本外验证
bgwq target-report                            # 全标的日报（教学总览 + 逐笔 + 样本外统计）+ CSV + 可切换本金/模式的K线图
```
参数见 `config/target_model.yaml`（仓位比例、止损、单笔目标净收益、日亏损上限、次数上限、分批根数、参与率、目标取值规则）。
工作台首个页签「固定收益目标」；接口 `/api/target/*`。**固定收益是目标而非保证；模拟数据下的结果一律标注“非正式验证”。**

## 事后最优交易路径（昨天怎样交易才能取得更高收益）

每天读取昨日已走完的 **代币成交价 1 分钟 K 线**（token venue，不用普通股票价格冒充），对七档本金分别反向求解：

- 最高可实现的模拟净收益与收益率（动态规划：最大权重不相交区间，O(n²)）；
- 达到档位目标所需的**最少交易次数**及路径（交易次数受限的 DP）；
- 每笔买入/卖出的股票、美东时间、参考价/成交价、投入金额、数量、成本、净盈亏；
- K 线图标记全部买卖点（可切换档位与“最高收益/最少交易达标”两条路径）；
- 目标是否达标；达不到时显示最高可行收益，**不虚构**；
- 多股票组合：轮动（任一时刻只持一只）与并行均分两种资金使用方式。

约束：手续费、半价差、滑点、平方根冲击、Gas、单笔上限拆单、逐根 K 线流动性参与率上限、本金不得超额、同一时刻只有一个持仓；流动性未知（如 ondo 报价 K 线）时标记“不可验证”。
所有输出均标注“**已知完整走势的事后最优模拟**”，不得宣称实盘未来可获得同等收益。

```bash
bgwq optimal NVDA            # 单标的 × 七档（21 个代表本金点）
bgwq optimal-report          # 全标的 × 七档 + 组合 → reports_out/<day>/hindsight_report_<day>.html + CSV
```
工作台首个页签「事后最优路径」提供交互版本；接口：`/api/hindsight/{symbol}`、`/api/hindsight/portfolio`、`/api/hindsight/summary`、`POST /api/hindsight/report`。

## 其他模块（阶段 1–7）

| 阶段 | 内容 | 代码 |
|---|---|---|
| 1 数据与标的 | Bitget Wallet 官方 RWA 行情 API（stockList/stockInfo/kline/transactionList/checkContractIsRwa，HMAC 签名）、Massive 美股分钟线基准、明确标注的模拟源、DuckDB 存储、美东交易日历、质量标记 | `src/bgw_quant/data/` |
| 2 双模式复盘 | A 事后最优（动态规划，扣全部成本的理论上限）；B 当时可执行（只看前缀，下一根开盘成交，反未来函数测试） | `replay/` |
| 3 策略与 ML | 9 类规则策略 + 成本门；LightGBM 5/15/30/60 分钟扣费后上涨概率与期望收益，purged walk-forward、等渗校准、阈值、bootstrap CI，无优势即不交易 | `strategies/`、`ml/` |
| 4 本金分层与容量 | 七档本金（管理员可改），自动标记口径未知/重叠/缺档；逐档仓位、深度、价差、手续费、Gas、滑点、冲击、拆单、成交失败/部分成交、不可验证标记 | `capital/`、`costs/` |
| 5 严格回测 | 参数样本内选、绩效只报样本外；日/累计/年化、胜率、盈亏比、PF、Sharpe、Sortino、回撤、成本占比、相对最优差距；买入持有/无交易/简单规则基准；压力测试 | `backtest/` |
| 6 工作台 | FastAPI + DuckDB + React/Vite + TradingView Lightweight Charts 中文界面：排名、K 线与 A/B 买卖点、逐笔交易、七档容量与达标率、AI 概率与不交易理由、交易员训练模式、HTML/CSV(/PDF) 日报 | `api/`、`frontend/`、`training/`、`reports/` |
| 7 执行安全 | `research_only` 守卫；V2 真实交易预留显式授权/人工确认/限额/紧急停机闸门，默认全关 | `execution/` |

## 快速开始

```bash
pip install -e ".[dev]"
cp .env.example .env                       # 无密钥 → SIMULATED 模式（所有输出带标记）
(cd frontend && npm install && npm run build)
bgwq probe                                 # 探测官方接口权限/跨度/限频
bgwq ingest --days 70                      # 接入 10 个标的 70 个交易日分钟线
bgwq replay NVDA                           # 双模式复盘 + K 线图
bgwq backtest NVDA && bgwq train-ml NVDA   # 样本外回测 / ML
bgwq report                                # 中文日报 HTML + CSV
bgwq serve                                 # http://127.0.0.1:8000 工作台
pytest -q                                  # 85 项自动化测试
```

详见 `docs/01_使用文档.md`。

## 当前状态与阻塞（如实说明）

- 本开发环境无法访问 `bopenapi.bgwapi.io` 与 `api.massive.com`（网络策略 403）且无密钥：**API 权限、历史跨度、频率与成本未验证**；接口规格来自官方 SDK 源码并以签名向量测试锁定。见 `docs/00_环境与接口检查报告.md`。
- SPCX 资产性质/发行商/合约未经官方接口核对：`tradable=false`，不纳入可交易策略。
- 代币真实成交成本未知：`config/costs.yaml` 为明确标注的假设（保守/乐观两档）。
- 在模拟数据与保守成本假设下，**所有规则策略样本外日均净收益为负、ML 四个周期均无统计优势（输出不交易）、七档目标全部未达标**。这是系统如实输出的结论，不是缺陷。

## 目录

```
config/        symbols.yaml（标的与验证状态） costs.yaml（成本假设） tiers.yaml（七档本金）
src/bgw_quant/ data/ costs/ replay/ strategies/ ml/ capital/ backtest/ reports/ api/ training/ execution/ pipeline.py cli.py
frontend/      React + Vite + lightweight-charts 工作台（构建产物由 FastAPI 托管）
tests/         按阶段组织的 pytest 用例
docs/          环境与接口检查报告、使用文档、阶段报告、方法说明
reports_out/   日报与图表（运行后生成，已 gitignore）
```

## spot90：BTC / ETH 90 天现货投资 AI 预测与可信度验证（2026-10-28，第一版）

> **结论先行（样本外 2020-01 → 2026-07，14 折 Walk-Forward）**：LightGBM / CatBoost 对 90 天涨跌的预测**没有**优于“每天买入持有”或“收盘>MA200 才买”；预测盈利概率 ≥65% 时真实盈利率 58%~67%，与同期市场自身上涨频率相同；挂低价限价单等待 14 天总体亏于立即买入。完整报告：`docs/14_spot90_90天预测可信度验证报告.md`，原始信号/预测：`docs/spot90_validation/`。系统因此把最终建议锁定为“暂不买入（模型未通过验证）”，不输出未经验证的高胜率。

```bash
pip install lightgbm catboost duckdb pyarrow scikit-learn httpx pyyaml pandas numpy joblib   # Mac Apple Silicon：先 brew install libomp
export PYTHONPATH=src
python -m spot90.cli ingest            # Bitget V3 现货历史 K 线（首选，api.bitget.com）→ 不可达时自动用 GitHub 公开 Binance 日线缓存；写 data_store/spot90/data_status.json
python -m spot90.cli backtest          # 14 折 Walk-Forward（purge 90 天 + embargo 10 天）：基准 / LightGBM / CatBoost / 平均 → reports_out/spot90/backtest/
python -m spot90.backtest.run_validation   # 泄漏审计 + BTC/ETH 分别验证 + 三基准 + 限价单分析 → reports_out/spot90/validation/
python -m spot90.backtest.report_md    # 生成 docs/14 报告
python -m spot90.cli train && python -m spot90.cli predict   # 最终模型 + 当日预测 JSON（reports_out/spot90/latest_prediction.json；未验证时建议=暂不买入）
pytest tests/test_spot90_data.py -q    # 数据层测试（MockTransport）
```
- 目录：`src/spot90/data/`（bitget_v3 / static_klines / llama / store / quality / ingest）、`labels.py`（A 立即买入、B 限价 14 天等待后持有 90 天；成交=最低价触及，跳空按开盘）、`features.py`（全部因果）、`models/`（baserate、volboot、LightGBM、CatBoost、calibration）、`decision.py`（A/B/C 规则与门槛）、`backtest/`（walkforward、evaluate、audit、validation、report_md）、`pipeline.py`、`cli.py`；参数 `config/spot90.yaml`。
- 已知限制：本容器对 `api.bitget.com` 返回 403，数据为 Binance 现货（GitHub `finom/static-klines` 缓存，每日更新）；链上/稳定币/ETF/宏观/衍生品数据源在本环境全部不可达，未接入也未伪造；Chronos-2 与 Streamlit 看板按用户要求暂缓；第一阶段无任何下单代码。

### spot90 第二阶段（2026-10-28）：链上资金流数据对照实验 —— 失败，路线终止

> 同一 14 折 Walk-Forward 上，A（技术指标）→ B_pit（+链上活跃度/MVRV/稳定币供应/Lido 质押，全部 Point-in-Time 安全）→ B_full（+交易所流量/储备、DeFi TVL，非 PIT）→ D_pit（+月度宏观）。
> 合并样本外 AUC 0.602 → 0.463 → 0.470 → 0.449，Brier 0.2776 → 0.2970 → 0.3023 → 0.3117；16 个实验的按日配对 Brier 差没有一个在 95% 水平显著为负；交易所净流/储备单独加入与 A 无差别（+0.001 [−0.013, +0.020]）；回撤预测 AUC 0.35~0.43（反向）。
> 完整报告 `docs/15_spot90_链上数据对照实验报告.md`，产物 `docs/spot90_validation/phase2/`。模型 C（衍生品）与 ETF 资金流因数据源在本环境全部不可达未运行（接口与可选客户端已写好：`src/spot90/data/derivatives.py`）。

```bash
python -c "from spot90.data.onchain import fetch_all; fetch_all()"   # Coin Metrics GitHub 归档(截至 2026-05-23) + DefiLlama + 月度宏观 → data_store/spot90/onchain/
python -m spot90.backtest.experiments     # A/B_pit/B_full/D_pit + 11 组消融 + 滞后稳健性（约 1 小时）→ reports_out/spot90/phase2/
python -m spot90.backtest.report_phase2   # 生成 docs/15
pytest tests/test_spot90_phase2.py -q     # 链上滞后/PIT 分组/同窗口标签/衍生品客户端 Mock
```
