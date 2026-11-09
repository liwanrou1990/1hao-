# Brando Price Action 第十四阶段 · Brando Investment Intelligence Dashboard（只读投资可视化管理平台）

> 分支 `claude/brando-pa-phase14-dashboard`（从第十三阶段 `bdf77fa` 分出）。只读：服务端拒绝所有非 GET 请求；没有下单、提币、改参数、删记录、改本金、重置账户功能；默认只监听 127.0.0.1；独立 venv、独立缓存目录；不导入任何下单代码（仓库本身也没有真实下单实现）。本阶段**未部署到 Mac**，未启动任何常驻服务；原三账户、SMA200 参数、B2、第十三阶段冻结策略、事件链、72 小时验收、launchd 生产服务均未触碰（测试逐文件哈希验证）。

## 1. 先核查的仓库状态（实际）

- 第十三阶段分支 `claude/brando-pa-phase13-forward` 提交 `bdf77fa`；Mac 运行分支 `claude/brando-pa-phase9-deploy` 仍在 `5a19020`。
- 云容器里的三账户记录是 2026-11-08 06:02 UTC 后与 Mac 分叉的副本，最后一轮 2026-11-08 13:02 UTC；页面在云端显示的数字是副本，页面上有明确提示（健康页：“当前读取的是云端副本（主机 vm）”）。Mac 上运行时读取的就是 Mac 的权威记录。
- 影子账户（第十三阶段）在云端只跑过一轮（2026-11-09 02:39 UTC），页面显示“云容器一次性运行（非 Mac 连续运行）”。
- 依赖：仓库已有 pandas / httpx / plotly / uvicorn / pyarrow；新增 fastapi；前端图表库 Lightweight Charts 4.2.3（已随仓库 vendored，离线可用），plotly.js 直接从 Python plotly 包内置文件提供（离线可用）。

## 2. 技术架构

| 层 | 选择 | 理由 |
|---|---|---|
| 后端 | FastAPI（`src/brando_pa/dashboard14/api.py`）+ 只读数据层（`data.py`） | 复用现有 Python 模块：账本重放（`forward.ledger_audit.replay_ledger`）、验收（`forward.acceptance`）、时间顺序审计、第十一阶段结构 / 区域检测、第十三阶段审计；无需 Node 构建链，Mac 部署只需 Python |
| 前端 | 原生 JS + CSS（hash 路由单页）、Lightweight Charts（K 线）、Plotly（曲线 / 分布） | 零构建、离线可用、响应式（侧栏 ↔ 底栏导航、网格自适应）；iPhone 13 视口无横向溢出（自动化测试断言） |
| 数据 | 三类严格分区：`mac_prod`（`reports_out/brando_pa/phase8_forward`）、`shadow13`（`reports_out/brando_pa/phase13_forward`）、`research`（`docs/brando_pa_validation/*.json`） | 原子读取：JSON 重试、JSONL 跳过半行；行情来自 `data_store/brando_pa/forward_bars`（1h/1d 原生，4h/1w/1M 重采样，只用已收盘），实时价 OKX ticker 缓存 30 s，失败回退最新已收盘 1h 并标注 |
| 隔离 | 独立 venv `.venv-dashboard14`、缓存 `reports_out/brando_pa/dashboard14_cache/`、pid/日志同目录；HTTP 只有 GET（405 拒绝其他方法）；`--host` 非 127.0.0.1 直接退出 | 与交易服务完全分离，不写任何账户 / 行情 / 配置文件 |

接口（全部 GET）：`/api/meta`、`/api/overview`、`/api/accounts`、`/api/accounts/{group}/{name}[/nav|/positions|/analytics|/buyhold]`、`/api/compare`、`/api/market/ticker|trend|bars|structure`、`/api/opportunities`、`/api/health`、`/api/trades`、`/api/trades.csv`、`/api/trades/{group}/{name}/{order_id}`、`/api/research`、`/api/phase13`、`/healthz`。

## 3. 页面与功能（已完成 / 数据来源）

| 页面 | 状态 | 数据 | 说明 |
|---|---|---|---|
| 投资总览 | 完成 | 真实只读：OKX ticker + 账户 events/state + 验收规则 | 顶栏 BTC/ETH 实时价与 24h 变化、趋势、来源、更新时间、延迟、服务状态、真实交易禁用；每账户权益 / 今日变化 / 持仓市值 / 现金 / 已实现 / 未实现 / 手续费+滑点 / 预估清仓净利润 / 最大回撤；原 Mac 账户与影子账户分区，**不相加** |
| 策略对比 | 完成 | 真实只读 | 本金、净资产、清仓净利润、收益率、最大回撤、成交、平均持仓、手续费/滑点、BTC/ETH/USDT 比例、vs BTC/ETH 买入持有（同起点同成本估算）；跨组标注“不可直接比较”；净值与回撤曲线 |
| K 线与价格行为 | 完成 | 真实只读 | BTC/ETH × 1h/4h/1d/1w/1M；SMA50/100/200、±2 ATR 带、已确认摆动点、支撑/阻力区、趋势线；叠加任一账户的模拟买卖标记；“结构截至”日期选择 = 只显示当时已确认结构，勾选后才显示事后结构（紫色虚线）；点击成交行弹出当时的 signal/risk/market 事件原文与哈希 |
| 买入机会 | 完成 | 真实只读 + 研究性分析 | 冻结策略最新信号 → BUY/WAIT/HOLD/REDUCE/EXIT（唯一可执行口径，含有效期与理由）；第十一阶段结构检测单独标注“研究性分析”，无候选时 WAIT / 证据不足 |
| 持仓与卖出 | 完成 | 真实只读 + 研究性分析 | 建仓时间/价、数量、标记价/实时价、未实现、持仓天数、策略退出条件（SMA200 水平、熔断状态、F 的 3 ATR 跟踪）、结构失效价（研究）、第一/第二候选卖出区（已确认阻力区，研究；无则“尚无可信目标”）、扣卖出成本估算净值 |
| 收益与风险 | 完成 | 真实只读；底部历史研究为只读引用 | 日/周/月变化、累计净利润、回撤与持续、资金使用率、持仓周期、成本、单笔分布、最大 5 笔占比、vs 买入持有、市场环境（< 90 日显示“数据不足”）；时间选择器；历史区间（第十/十二阶段）单独分区且声明不填充前瞻 |
| 验收与健康 | 完成 | 真实只读 | 心跳、运行、轮询实际/预计、覆盖率、最长中断、数据完整率、延迟、错过/过期/暂停、告警、真实交易状态；验收状态按原规则，未通过不显示“验收成功”；页面不改计时/日志 |
| 交易记录 | 完成 | 真实只读 | 全部模拟成交（事件链），筛选/搜索/排序/分页、CSV 导出、点击查看关联事件与决策依据 |
| 前瞻验证中心 | 完成 | 真实只读（第十三阶段审计） | 六套冻结策略、冻结提交、起点、天数、覆盖率、信号/成交、净值、回撤、30/90/180/365 状态（未启动 / 未到期 / 统计证据不足） |
| 使用说明 | 完成 | — | 口径、启动 / 关闭、路径 |

**仍是演示 / 受限的部分**：① 云端截图里的账户数字来自云容器副本（Mac 上才是权威）；② 第十三阶段影子账户在云端只有一轮，页面显示未启动 / 一次性运行；③ iPhone 远程访问未开放（本阶段只监听 127.0.0.1，远程需后续认证方案）；④ “研究性分析”模块（第十一阶段结构）只是解释性信息，不是可执行信号。

## 4. 口径

权益 = 现金 + 持仓 × 最新已收盘 1h 收盘；清仓净利润 = 权益 − 本金 − 假设卖出成本（15 bps 单侧，与账户 cost 账本一致）；已实现净额 = 卖出净盈亏 − 买入手续费（买入成本在成交时只扣一次）；权益 − 本金 = 已实现净额 + 未实现（每次请求用事件链重放核对，`nav_consistent_with_replay`）；今日变化 = 相对前一 UTC 日末；买入持有参考 = 账户起点后首根 1h 开盘买入、同本金同成本。

## 5. 测试

`tests/test_brando_pa_dashboard14.py` 8 项（2026-11-09 中文化后）：① 合成前瞻数据上 API 正确且与账本一致（权益 = 现金 + 市值；清仓净利润恒等式；已实现 + 未实现 = 利润）；② 交易记录信号 ≤ 决策 < 价格时刻 ≤ 执行、CSV、详情含当时结构；③ 五个周期 K 线 OHLC 合法、结构“当时已确认 ≠ 事后”、机会状态枚举、错误参数 400/404；④ 空影子目录 → “未启动”、无心跳 → 不显示运行中、验收未满 → 无“验收成功”、分析不足 90 日 → “数据不足 / 统计证据不足”；⑤ 只读：访问全部端点后数据目录哈希不变、POST/PUT/DELETE/PATCH 405、源码无下单接口、`--host 0.0.0.0` 拒绝；⑥ 真实云端副本可读且账本一致、目录哈希不变；⑦ 买入机会页研究字段齐全（`next_resistance / second_resistance / structure_invalidation / last_confirmed_high|low / atr14 / volume_confirmation.buy_confirmed / unmet_conditions / daily_filter_ok / plan`），研究性观察标注“不可执行”，非 BUY 时必须列出尚未满足的条件，冻结策略状态编码仍为 BUY/WAIT/HOLD/REDUCE/EXIT；⑧ Playwright（桌面 1440×900 + iPhone 13）：10 个页面默认 `lang=zh-CN`，正文出现“投资总览 / 买入机会 / 使用说明”与中文状态（买入信号 / 等待买入条件 / 继续持有 / 减少仓位 / 卖出退出），正文不得出现原始编码（WAIT / HOLD / REDUCE / EXIT / NORMAL / RISK_REDUCED / filled_late / filled_estimated / live_quote_ask|bid / estimated_closed_bar_close / cancelled_invalid / mac_prod / shadow13），顶栏切换 English 后出现 Overview / Help / Buy Opportunity Monitor 且 `localStorage.brando_lang=en`，切回中文恢复“买入机会监控”；两种语言、两种设备每页 `scrollWidth ≤ 视口宽度 + 2`，无页面错误。全套结果见 `docs/brando_pa_validation/phase14_dashboard/test_report.txt`。调试记录：UI 测试在全套运行中曾两次失败（单独运行通过），根因是 `tests/test_brando_pa_service.py` 在导入时把全局 `time.sleep` 替换为空操作，使测试里“按睡眠次数等待服务端口”的 10–60 s 实际为 0 s；修复为用单调时钟判定超时（未删除或弱化任何测试）。

## 6. 使用（Mac 本机）

```bash
cd ~/1hao-
git fetch origin claude/brando-pa-phase14-dashboard && git worktree add --detach ../1hao-dash14 origin/claude/brando-pa-phase14-dashboard   # 不切换运行中的生产检出
cd ../1hao-dash14
BRANDO_DASH_PROD_DIR="$HOME/1hao-/reports_out/brando_pa/phase8_forward" BRANDO_DASH_BARS="$HOME/1hao-/data_store/brando_pa/forward_bars" bash scripts/brando_dashboard14.sh start   # 首次自动创建 .venv-dashboard14 并安装依赖
bash scripts/brando_dashboard14.sh open      # 浏览器打开 http://127.0.0.1:8514/
bash scripts/brando_dashboard14.sh status | logs | stop
```
用独立工作树的原因：不改动 Mac 正在运行的 `~/1hao-` 检出（生产服务仍在 `5a19020`），看板通过环境变量只读指向生产目录。端口可用 `BRANDO_DASH_PORT=8501` 等覆盖；主机固定 127.0.0.1。iPhone：本阶段不开放；需要时在 Mac Safari 用“响应式设计模式”预览，或等后续带认证的远程方案。

## 7. Mac 安全部署前检查清单（看板）

1. 生产服务健康：`bash scripts/brando_pa_forward_ctl.sh health` ok，不要在升级 / 停服期间启动看板。
2. 用独立工作树或独立克隆运行看板，不切换 `~/1hao-` 的分支（生产服务从该目录加载代码）。
3. `scripts/brando_dashboard14.sh` 创建的是 `.venv-dashboard14`，不要把 fastapi 装进生产 `.venv`。
4. 环境变量只指向只读目录：`BRANDO_DASH_PROD_DIR`、`BRANDO_DASH_BARS`、（可选）`BRANDO_DASH_SHADOW_DIR`、`BRANDO_DASH_CACHE`（默认在看板自己的 reports_out 下）。
5. 启动后确认：`curl -s http://127.0.0.1:8514/healthz` = ok；`lsof -iTCP:8514 -sTCP:LISTEN` 显示 127.0.0.1；`curl -X POST http://127.0.0.1:8514/api/meta` 返回 405。
6. 运行 1 小时后比对生产目录哈希不变（可用 `scripts/brando_pa_mac_check.sh` 的只读检查），确认看板没有写入。
7. 不要把端口转发到公网；不要修改 launchd 生产任务。

## 8. 路径与数据接口说明

| 项 | 路径 |
|---|---|
| 后端 / 前端源码 | `src/brando_pa/dashboard14/{api.py,data.py,static/index.html,static/app.js,static/styles.css,static/vendor/}` |
| 启动脚本 / 依赖 | `scripts/brando_dashboard14.sh`、`deploy/requirements-dashboard14.txt` |
| 原 Mac 三账户（只读） | `reports_out/brando_pa/phase8_forward/<账户>/{events.jsonl,state.json,corrections.jsonl}`、`health.json`、`runner_events.jsonl`、`alerts.jsonl`、`startup_check.json` |
| 影子账户（只读） | `reports_out/brando_pa/phase13_forward/…`（同结构） |
| 行情缓存（只读） | `data_store/brando_pa/forward_bars/bars/{BTCUSDT,ETHUSDT}_{1h,1d}.parquet`，`data_store/brando_pa/forward_data_status.json` |
| 历史研究（只读） | `docs/brando_pa_validation/phase10_holding/phase10_results.json`、`phase12_core_pa/phase12_results.json`、`phase12_core_pa/phase11_reject_audit.json`、`phase13_forward/freeze_manifest.json` |
| 看板自有写入 | `reports_out/brando_pa/dashboard14_cache/`（pid、日志）仅此一处 |
| 截图 | `docs/brando_pa_validation/phase14_dashboard/screens/{desktop,iphone}_<页面>.png` |

## 9. 界面中文化（2026-11-09，仅展示层）

**原则**：API 字段、状态编码、数据结构、账本计算、策略、参数、账户数据、事件链、验收计时全部不变；中文化只在 `static/app.js` 的 `I18N` 字典与格式化函数中完成（后端 `data.py` 仅新增买入机会页需要的研究字段，不改任何既有字段）。默认简体中文；顶栏下拉可切换 English，选择保存在浏览器 `localStorage.brando_lang`（不写服务端）。

**状态编码 → 中文（展示层映射，编码本身不变）**

| 类别 | 编码 | 中文 | English |
|---|---|---|---|
| 策略状态 | BUY | 买入信号 | Buy signal |
| | WAIT | 等待买入条件 | Waiting for buy conditions |
| | HOLD | 继续持有 | Keep holding |
| | REDUCE | 减少仓位 | Reduce position |
| | EXIT | 卖出退出 | Sell / exit |
| 信号 | BUY / SELL / HOLD / REBALANCE | 买入 / 卖出 / 持有 / 再平衡 | Buy / Sell / Hold / Rebalance |
| 风险状态 | NORMAL / RISK_REDUCED / COOLDOWN / RECOVERY / EMERGENCY_HALT | 正常 / 限仓（回撤 −10%） / 冷静期 / 恢复期 / 紧急熔断 | Normal / Risk reduced / Cooldown / Recovery / Emergency halt |
| 订单状态 | filled / filled_late / filled_estimated / filled_late_estimated / expired / cancelled_invalid / cancelled_below_minimum / submitted | 已成交 / 迟到成交 / 估算价成交 / 迟到·估算价成交 / 已过期 / 已作废 / 低于最小下单取消 / 已提交 | Filled / Filled late / … |
| 价格类型 | live_quote_ask / live_quote_bid / estimated_closed_bar_close | 实时卖一价 / 实时买一价 / 已收盘 K 线估算价 | Live ask / Live bid / Estimated (closed bar) |
| 数据分区 | mac_prod / shadow13 / research | 原 Mac 账户 / 前瞻模拟 / 历史回测 | Mac paper accounts / Forward sim / Backtest |
| 引擎理由 | `target_changed[both|NORMAL|cap=1.00];drift 1.000 ≥ 0.1;scaled_to_cash` | 目标权重变化（BTC+ETH 双标的，风险状态：正常，仓位上限 100%）；权重偏离 1.000 ≥ 再平衡阈值 0.1；按可用现金等比缩减 | 同样逐段翻译 |

引擎理由的翻译只识别已知片段（`target_changed[...]`、`drift a ≥ b`、`scaled_to_cash`、`exposure … > hard_cap …`、`data_quality_fault`、`late: close … first processed at …`、`no fresh price …`、`engine first processed the order … h after exec_after`），未识别片段保持原文；原始字符串仍在交易详情的“事件原文”中逐字可见。第十一阶段检测器给出的理由本来就是中文，`→ WAIT` 显示为“→ 继续等待”。

**买入机会监控页（面向普通用户）**：每个标的一组卡片——当前价格、当前是否适合买入（只由冻结策略最新信号事件推导：BUY → “策略已发出买入信号”，已持有 → “已持有，继续持有”，EXIT/REDUCE → “策略要求卖出 / 减仓”，否则 “暂不买入 · 继续等待”）、候选买入价格区域（最近已确认支撑区）、买盘是否获得确认（量比 ≥ 0.8 且收盘位置 ≥ 0.6 且有触发）、当前趋势方向、关键支撑 / 阻力、结构失效价格（最近已确认摆动低点 − 0.25 ATR）、下一候选卖出区域（上方第一 / 第二已确认阻力区，无则“尚无可信目标”）、风险收益比（仅在有候选计划时）、预计持仓周期、尚未满足的入场条件（逐条列出）。下方两张表严格分开：“已确认的策略信号（冻结策略，唯一可执行口径）”与“研究性观察（第十一阶段结构检测，不可执行）”，研究性内容全部带“研究性观察”标签；没有证据时显示“继续等待”，不制造信号。

**时间与金额**：所有时间按浏览器本机时区显示，顶栏与各页说明标注时区（如 `UTC`、`GMT+8`）；UTC 原值仍可在交易详情事件原文查看。金额统一 `USDT` 两位小数；百分比两位小数；数量六位小数。Plotly 日期轴改为 `YYYY/MM/DD HH:MM` 数字格式（不依赖 Plotly 语言包）。

**布局修复（iPhone）**：卡片 `grid-column:span 3` 在两列网格中会生成隐式第三列，导致前两张卡片被压成竖排文字，改为 `.span2/.span3` 类并在 ≤ 900px 时占满整行；表格可换行单元格改为 `td.wrap`（最小宽度 240px，手机 180px），避免桌面表格行被拉高；≤ 600px 隐藏顶栏副标题。截图：`docs/brando_pa_validation/phase14_dashboard/screens/{desktop,iphone}_{zh,en}_<page>.png`（中文 10 页 × 2 设备，英文 4 页 × 2 设备，云端副本数据）。

**已知限制**：英文模式下，策略引擎 / 第十一阶段检测器的原始中文理由（如“回调深度 130% > 61.8%，结构破坏”）以及后端生成的尚未满足条件文本保持中文（它们是数据而非界面文案）；浏览器原生日期选择框的占位符（`mm/dd/yyyy` 或 `年/月/日`）由系统语言决定。

**安全更新 Mac 上的看板（不碰生产服务）**

```bash
# 1) 生产服务不动：不要切换 ~/1hao- 的分支，不要 restart launchd
cd ~/1hao-dash14                                    # 第 6 节创建的独立工作树（或独立克隆）
bash scripts/brando_dashboard14.sh stop             # 只停看板进程（pid 文件在 reports_out/brando_pa/dashboard14_cache）
git fetch origin claude/brando-pa-phase14-dashboard
git checkout --detach origin/claude/brando-pa-phase14-dashboard
git log --oneline -1                                # 确认是中文化提交
BRANDO_DASH_PROD_DIR="$HOME/1hao-/reports_out/brando_pa/phase8_forward" BRANDO_DASH_BARS="$HOME/1hao-/data_store/brando_pa/forward_bars" bash scripts/brando_dashboard14.sh start
bash scripts/brando_dashboard14.sh status           # healthz ok
bash ~/1hao-/scripts/brando_pa_forward_ctl.sh health   # 生产服务仍 ok（看板从未写入生产目录）
```
浏览器用 Shift+刷新 清缓存（静态文件无版本号）。回滚：`git checkout --detach <上一提交>` 后 `restart`。看板依赖未变（无需重新安装 venv）。
