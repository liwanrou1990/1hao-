# Bitget Wallet 美股代币化 AI 量化复盘与交易员训练系统（V1 研究版）

> **V1 仅限研究、历史回测与模拟交易。** 不连接真钱钱包、不请求私钥、不自动签名或下单。
> 每日 0.8%–2.2% 只是研究目标，**不是保证收益**；系统不会为了达标而捏造交易，达不到时会明确显示"未达标"。

## 核心功能：事后最优交易路径（昨天怎样交易才能取得更高收益）

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
pytest -q                                  # 57 项自动化测试
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
