# Bitget Wallet 美股代币化 AI 量化复盘与交易员训练系统（V1 研究版）

> **V1 仅限研究、历史回测与模拟交易。** 不连接真钱钱包、不请求私钥、不自动签名或下单。
> 每日 0.8%–2.2% 只是研究目标，**不是保证收益**；系统不会为了达标而捏造交易，达不到时会明确显示"未达标"。

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
pytest -q                                  # 77 项自动化测试
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
