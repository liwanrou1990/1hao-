# iphone-farm — Mac + Sipolar A-832 + 30 台 iPhone 群控（非越狱）

> 主机 MacBook Pro M5 Pro 48GB；Hub Sipolar A-832（32 口 USB 2.0）；30 台 iPhone 6s/7/8 + 30 张实体 SIM。
> 控制链路采用 **YueChen-C/iPhone-use**（MIT）：USB 取画面（AVFoundation muxed 设备）+ 蓝牙 LE HID 送输入 + 本机 HTTP API（127.0.0.1:7390）。
> 不越狱、不装 App、不需要开发者账号。**暂不开发短信聚合工作台**；短信只在自有测试设备与授权联系人范围内、单机人工确认。

## 文档索引（docs/）

| 文档 | 内容 |
|---|---|
| [00_调研报告_iPhone-use与技术风险评估](docs/00_调研报告_iPhone-use与技术风险评估.md) | iPhone-use 架构/API/多机机制调研；USB 2.0 带宽、蓝牙并发、A-832 数据口数量三大风险 |
| [01_软件安装步骤](docs/01_软件安装步骤.md) | 第一阶段：装 iPhoneUse.app、ifarm、3 台连接/信任/AssistiveTouch 配对、独立控制验证 |
| [02_设备注册与编号流程](docs/02_设备注册与编号流程.md) | 改名 IF01..IF30 → 逐台插入 → `ifarm register` → 三重身份（capture_id / 名称 / UDID）+ 蓝牙 host 绑定 |
| [03_单机控制模块](docs/03_单机控制模块.md) | `ifarm run`：点击/滑动/键入/按键/Home/截图；动作前后的目标一致性校验 |
| [04_多机广播控制模块](docs/04_多机广播控制模块.md) | 第二阶段：`ifarm broadcast`，分组、白名单、并发/错峰、逐台结果 |
| [05_故障诊断与恢复模块](docs/05_故障诊断与恢复模块.md) | 第三阶段：状态机、错误记录、自动恢复（match/pair/calibration）、任务互斥 |
| [06_蓝牙瓶颈评估与XCTest备选方案](docs/06_蓝牙瓶颈评估与XCTest备选方案.md) | 蓝牙连接数成为瓶颈时的判定标准与 macOS + XCTest/WebDriverAgent 多设备方案 |
| [07_逐级实机测试报告](docs/07_逐级实机测试报告.md) | 第四阶段：5/10/15/20/30 台测试方法、指标定义、报告模板；附模拟自检（SIMULATED） |
| [08_短信测试规范](docs/08_短信测试规范.md) | 授权范围、单机限制、人工确认、禁止把模拟结果报告为真实 SMS 成功 |

## 快速开始（在 Mac 上）

```bash
cd iphone-farm
./scripts/install_mac.sh                 # 安装 /Applications/iPhoneUse.app（v1.0.1）、libimobiledevice、ifarm 虚拟环境
open -a iPhoneUse                        # 授予摄像头 + 蓝牙权限；或 --args --resident-quiet 后台常驻
./scripts/preflight_mac.sh               # USB 树 / usbmux / 蓝牙 / 服务 预检，输出 reports_out/preflight_*.txt
. .venv/bin/activate
ifarm check                              # iPhone-use 可达性、USB 枚举数、注册表
ifarm devices                            # 当前设备（id / 名称 / 蓝牙 / pointer）
ifarm register                           # 把名称为 IFxx 的设备注册为编号 xx（交叉核对 UDID，记录蓝牙 host）
ifarm verify --bind-hosts                # 核对注册表与实际状态
ifarm run 1 tap --x 0.5 --y 0.5          # 单机：编号 1 点击屏幕中心
ifarm run 2 open_settings                # 单机：Home → Spotlight → Settings → 回车
ifarm screenshot 3                       # 单机截图到 reports_out/screens/
ifarm broadcast home --group pilot       # 多机：对分组 pilot（1,2,3）广播 Home
ifarm broadcast open_settings --slots 1,2,3 --save
ifarm monitor                            # 状态检测 + 自动恢复循环（Ctrl-C 退出）
ifarm scale-test --levels 3,5,10,15,20,30 --pause-between   # 逐级实机测试，生成 reports_out/scale_test_REAL_*.md
ifarm events --kind error --tail 100     # 错误记录
```

离线自检（任何机器，**结果仅证明脚本可运行，标记 SIMULATED**）：

```bash
pip install -e ".[dev]" && pytest                       # 20 个用例，对内置 mock 服务器
./scripts/run_mock.sh 30 7399 &                          # 30 台模拟设备
IFARM_API_URL=http://127.0.0.1:7399 ifarm --simulated register --no-udid
IFARM_API_URL=http://127.0.0.1:7399 ifarm --simulated scale-test --stability-minutes 0.05 --samples 2
```

## 硬性原则

1. **每个动作必须指定完整真实设备 ID**：`client.py` 拒绝空设备；响应 `device` 回显必须等于请求，否则抛 `DeviceMismatch`。
2. **三重身份一致**才允许绑定编号：iPhone-use `capture_id`、手机名称 `IFxx`、USB 序列号/UDID；蓝牙 host 一对一。
3. **动作前校验**：名称未漂移、`pointer.host` 未漂移（防交叉绑定）、pointer 就绪；任一不满足即拒绝并记录错误。
4. **任务互斥**：每台设备一把锁（进程内 + 文件锁），广播、单机、监控恢复互不交叉。
5. **短信类与电源类动作禁止广播**；短信结果在人工确认前一律 `UNVERIFIED`。
6. **模拟数据不得冒充实机结果**：`--simulated` 使所有事件、报告带 `SIMULATED_MOCK` 标签，报告文件名带 `SIMULATED`。
7. **不得假设 32 个 USB 口就能稳定控制 30 台**：必须按 3→5→10→15→20→30 逐级实测并记录。

## 目录

```
iphone-farm/
  config/farm.yaml           API 地址、并发、恢复策略、逐级测试参数
  config/groups.yaml         设备分组（按编号）
  config/sms_policy.yaml     短信授权联系人（模板）
  src/iphone_farm/
    client.py                iPhone-use HTTP 客户端（设备 ID 强制、回显校验）
    registry.py              设备注册与编号（三重身份、蓝牙 host、macOS USB 枚举）
    actions.py               安全动作目录（home / open_settings / tap / swipe / type / key …）
    single.py                单机控制模块（身份校验 + 互斥 + 事件）
    broadcast.py             多机广播控制模块（分组、白名单、并发错峰、逐台结果）
    monitor.py               故障诊断与恢复模块（状态机、错误记录、自动恢复）
    scale_test.py            逐级测试与报告（REAL / SIMULATED 标签）
    locks.py / events.py     任务互斥、JSONL 事件日志
    mock_server.py           iPhone-use 模拟服务器（含故障注入，仅离线用）
    cli.py                   ifarm 命令行
  scripts/install_mac.sh     安装；preflight_mac.sh 预检；run_mock.sh 模拟
  tests/                     20 个离线用例
  docs/                      00–08 文档
  state/ reports_out/        运行时注册表/锁/事件；测试报告（已 gitignore）
```

---

# brando_pa — Brando Price Action 量化交易引擎（独立模块，研究/回测/模拟盘）

> 代码 `src/brando_pa/`，配置 `config/brando_pa.yaml`，数据 `data_store/brando_pa/`（15m/1h/1w 自有；1d/4h 只读复用 spot90），产物 `reports_out/brando_pa/`，测试 `tests/test_brando_pa_*.py`，报告 [docs/20_brando_pa_价格行为引擎.md](docs/20_brando_pa_价格行为引擎.md)（第一阶段）、[docs/21_brando_pa_第二阶段_成本审计与样本外验证.md](docs/21_brando_pa_第二阶段_成本审计与样本外验证.md)（第二阶段：成本审计、入场/退出/多周期对比、严格样本外、Strategy Evaluator；结论：无合格策略，不满足进入模拟盘条件）、[docs/22_brando_pa_第三阶段_信号诊断与结构入场验证.md](docs/22_brando_pa_第三阶段_信号诊断与结构入场验证.md)（第三阶段：止损距离根因、模式 A/B 结构入场、预登记锁定测试、成交事件审计；结论：三条假设全部失败，永续数据仍不可得）、[docs/23_brando_pa_第四阶段_纯现货价格行为系统.md](docs/23_brando_pa_第四阶段_纯现货价格行为系统.md)（第四阶段：纯现货 BUY/HOLD/SELL、两层关键位、六组对照；结论：止损距离降到约 1.4 ATR，但无稳定正期望，熊市像 USDT、牛市落后持币）、[docs/24_brando_pa_第五阶段_现货趋势参与与持仓优化.md](docs/24_brando_pa_第五阶段_现货趋势参与与持仓优化.md)（第五阶段：趋势状态引擎、模型 A/B/C × 三种退出 × 十组对照；结论：SMA200 简单趋势基准优于全部模型，预登记的回撤熔断在现货空仓下成为永久停机，Brando 入场线无增量）、[docs/25_brando_pa_第六阶段_现货趋势基准审计与熔断修复.md](docs/25_brando_pa_第六阶段_现货趋势基准审计与熔断修复.md)（第六阶段：SMA200 基准三套实现逐根复现、三层现货趋势系统、策略 A/B、有限熔断状态机 vs 原规则 vs 无熔断、交易频率分析、前瞻模拟；结论：原始 SMA200 保留，3 日确认不采用，状态机修复永久停机并降低回撤但以上涨参与度为代价、未通过预登记判据，作为带标注的受保护默认；Brando 模块默认关闭）、[docs/26_brando_pa_第七阶段_USDT资金配置与利润排行.md](docs/26_brando_pa_第七阶段_USDT资金配置与利润排行.md)（第七阶段：BTC/ETH/USDT 纯现货动态资金配置模型 A/B/C × 三风险模式 × 六个固定参考、统一 USDT 评价与期末清仓价值、USDT 利润排行榜、月度环境诊断、前瞻模拟；结论见报告 §0）、[docs/27_brando_pa_第八阶段_真实行情接入与前瞻模拟.md](docs/27_brando_pa_第八阶段_真实行情接入与前瞻模拟.md)（第八阶段：交易所公开现货 K 线实时接入（Binance → OKX → Bybit → Coinbase → 镜像，只用已收盘 K 线）、三个独立前瞻 Paper 账户（BTC SMA200 / 模型 B + 状态机 / ETH SMA200 参考）、追加式哈希链事件日志、实时 USDT 利润榜；结论见报告 §0）、[docs/28_brando_pa_第九阶段_24小时服务与前瞻稳定性.md](docs/28_brando_pa_第九阶段_24小时服务与前瞻稳定性.md)（第九阶段：独立后台服务 + launchd / systemd / Docker 部署配置、行情质量门控与来源切换日志、离线错过信号标记、每日 UTC 账户报告、健康检查与告警、12 项故障注入测试；云容器不能常驻，需用户在持续在线主机部署）、[docs/29_brando_pa_前瞻账户费用与净值一致性审计_2026-11-08.md](docs/29_brando_pa_前瞻账户费用与净值一致性审计_2026-11-08.md)（只读审计：10008 vs 9993 差额 = 假设清仓成本 15 bps，非重复计费；费用公式、恒等式、事件链重放、信号去重、72h 分段全部一致；新增 `forward-ledger-audit` 只读审计命令；用户批准后实施 F1/F3：排行榜按预计清仓净利润排名并区分账面权益 / 账面利润 / 预计清仓余额 / 预计清仓净利润 / 已实现 / 未实现，日报与排行榜含买入手续费、买入滑点、卖出手续费与滑点估计、持仓成本、交易成本合计与对账公式）、[docs/30_brando_pa_第九阶段_Mac部署检查与三账户收益审计_2026-11-08.md](docs/30_brando_pa_第九阶段_Mac部署检查与三账户收益审计_2026-11-08.md)（云容器不能访问 Mac：云端副本三账户收益 / 费用 / 回撤分析、72h 分段核查、报表口径复核；新增 Mac 一键只读检查脚本 `scripts/brando_pa_mac_check.sh`）、[docs/31_brando_pa_第九阶段_72小时验收中期报告与门控升级_2026-11-08.md](docs/31_brando_pa_第九阶段_72小时验收中期报告与门控升级_2026-11-08.md)（72h 本地验收中期：覆盖率 94.4% 低于门槛、未满 72h、未达标也未失败；Mac 门控升级脚本 `scripts/brando_pa_mac_upgrade_verified.sh`：dry-run → 硬门控 → 升级 → 三账户资金复核，任一失败即停；2026-11-09 修复 `${X:+VAR=...}` 展开赋值导致含空格 UTC 时间 `command not found` 的缺陷，回归测试 5 项）、[docs/32_brando_pa_第十阶段_中长期现货持仓历史盈利能力研究.md](docs/32_brando_pa_第十阶段_中长期现货持仓历史盈利能力研究.md)（第十阶段：2018–2026 日线、预登记 15 套策略 × 0/10/15/25 bps × 全样本/训练/验证/样本外；结论：SMA200 趋势持有在全样本与 2022–23 熊市优于买入持有，但 2024–26 样本外没有任何核心策略超过 BTC 买入持有；固定 30/60/90 天持仓全部劣于趋势退出且对成本极敏感；证据不足以支持进入 30 天前瞻模拟）、[docs/33_brando_pa_第十一阶段_中长期价格行为投资系统.md](docs/33_brando_pa_第十一阶段_中长期价格行为投资系统.md)（第十一阶段，独立分支 `claude/brando-pa-phase11-pa`：Phase 10 独立复核、预登记价格行为系统（周线趋势 + 日线结构 + 已确认支撑阻力区 + 回调/突破回踩/假跌破入场 + 事前 T1/T2/失效价 + 三种退出 + 风险预算 + 宏观事件过滤）× 0/15/25 bps × 四窗口；结论：严格“目标必须来自已知阻力结构”的规则使 8.8 年仅 5–12 笔成交、96% 时间空仓、净收益 −225 至 +432 USDT，最大回撤 ≤ 6%，但远低于 SMA200 与买入持有；证据不足以进入 90 天前瞻）、[docs/34_brando_pa_第十二阶段_趋势核心与PA择时增强.md](docs/34_brando_pa_第十二阶段_趋势核心与PA择时增强.md)（第十二阶段，独立分支 `claude/brando-pa-phase12-core-pa`：Phase 11 拒绝原因归因（223 候选、80% 距 400 日高点 ≤ 2 ATR、100% 发生在核心已持仓时）、B2/SMA200 核心逐项复现、B2 核心 + 回调加仓 / 突破回踩加仓 / 结构退出 / 波动跟踪 × core_frac × 风险预算 × 成本 × 窗口 + 扰动；结论：没有任何 PA 增强组合在任一窗口超过原 B2，回撤改善仅来自降低核心仓位，证据不足以推荐前瞻）、[docs/35_brando_pa_第十三阶段_前瞻验证与绩效审计系统.md](docs/35_brando_pa_第十三阶段_前瞻验证与绩效审计系统.md)（第十三阶段，独立分支 `claude/brando-pa-phase13-forward`：6 个影子策略冻结清单（原 B2 / BTC SMA200 / BTC·ETH·50:50 买入持有 / B2+波动跟踪实验对照）、复用第九阶段前瞻引擎的独立影子服务（独立目录/行情仓/锁/launchd 标签）、数据与订单审计 + 30/90/180/365 天只评已到期窗口、每日/每周报告模板、只读前瞻监控页面、Mac 部署脚本（未执行）、客观评价规则；云端首轮 2026-11-09 02:39 UTC 建立账户，0 信号 0 成交，无任何前瞻业绩）、[docs/36_brando_pa_第十四阶段_投资可视化管理平台.md](docs/36_brando_pa_第十四阶段_投资可视化管理平台.md)（第十四阶段，独立分支 `claude/brando-pa-phase14-dashboard`：Brando Investment Intelligence Dashboard，只读 FastAPI + 原生前端 + Lightweight Charts/Plotly；投资总览 / 策略对比 / K 线与价格行为 / 买入机会 / 持仓与卖出 / 收益与风险 / 验收与健康 / 交易记录 / 前瞻验证中心；三类数据严格分区、只 GET、仅 127.0.0.1、独立 venv 与缓存；`bash scripts/brando_dashboard14.sh start|stop`；2026-11-09 界面全面简体中文化（默认中文、可切英文，仅展示层：状态编码 BUY→买入信号 / WAIT→等待买入条件 / HOLD→继续持有 / REDUCE→减少仓位 / EXIT→卖出退出，引擎理由翻译，本机时区与 USDT 统一格式，买入机会页面向普通用户重设计并区分“已确认的策略信号 / 研究性观察”；Playwright 桌面 + iPhone 两种语言无溢出）、[docs/37_brando_pa_第十四阶段_系统稳定性与中长期买入机会专项审计_2026-11-10.md](docs/37_brando_pa_第十四阶段_系统稳定性与中长期买入机会专项审计_2026-11-10.md)（只读审计：72h 验收按已知读数反推预计无法达到 95%；结构失效价 75,760 高于现价的原因 = 分形确认滞后 + 结构已失效 + 展示层缺少“不适用”判定；买盘确认仅 K 线代理、无订单流；三账户账本一致；最小展示层修复方案与 Mac 只读检查脚本 `scripts/brando_pa_mac_audit_readonly.sh`）、[docs/38_brando_pa_第十五阶段_矿工经济与链上估值因子研究.md](docs/38_brando_pa_第十五阶段_矿工经济与链上估值因子研究.md)（第十五阶段，独立分支 `claude/brando-pa-phase15-onchain`：数据可获得性与发布滞后审计（ETF 资金流 / 质押队列 / 持有者分层成本 / 机构披露成本均不可得，未用估计冒充）、BTC MVRV / Puell / 算力带 / 矿工电力成本区间 / 减半阶段 / 交易所净流与 ETH MVRV / ETH-BTC 相对强弱 / 净发行因子层（滞后 1 日）、45 策略 × 4 窗口 × 3 成本、训练选参 / 验证确认 / 样本外一次；结论：没有任何新因子改善样本外，全部不采用，不纳入前瞻观察）、[docs/39_brando_pa_第十六阶段_BTC矿工成本与牛熊周期长期投资模型.md](docs/39_brando_pa_第十六阶段_BTC矿工成本与牛熊周期长期投资模型.md)（第十六阶段，独立分支 `claude/brando-pa-phase16-minercycle`：BTC 日线拼接至 2014、价格无关的矿机代际成本模型（预登记原版未通过可靠性检查，事后修正在策略之前）、A/A0/B/C/D 建仓 × X1–X5 退出 × 8 窗口 × 4 档成本 = 880 次回测；结论：25 个组合均无新增优势，2022 → 后段检验中成本区建仓从未触发，成本区事件数随假设在 0–10 次之间变化；独立研究页面“BTC矿工成本与长期投资周期”；只记录读数的前瞻观察日志自 2026-11-10 起）、[docs/40_brando_pa_第十七阶段_成本可信度_错过机会与长期前瞻评估标准.md](docs/40_brando_pa_第十七阶段_成本可信度_错过机会与长期前瞻评估标准.md)（第十七阶段，独立分支 `claude/brando-pa-phase17-eval`，只读：矿工成本模型判定严重误差并冻结；B1 主要代价为 200 日线短期来回，B2 不必要空仓主要来自熔断紧急停机 + 90 天冷静期；长期前瞻评估标准与只读工具 `brando_pa.pa17.forward_eval17`）、[docs/41_brando_pa_第十八阶段_B2长期空仓机会成本与安全恢复机制.md](docs/41_brando_pa_第十八阶段_B2长期空仓机会成本与安全恢复机制.md)（第十八阶段，独立分支 `claude/brando-pa-phase18-b2recovery`：原 B2 7 次不必要长期空仓还原（5 次由熔断 + 冷静期造成）；固定 30/60/90 天、趋势确认、周线结构、阶梯恢复等 7 个变体 × 3 窗口 × 4 档成本；结论：均无增量优势，提高参与度伴随显著更大回撤，维持原 B2 不变）、[docs/42_brando_pa_第十九阶段_中长期买卖决策与风险收益验证系统.md](docs/42_brando_pa_第十九阶段_中长期买卖决策与风险收益验证系统.md)（第十九阶段，独立分支 `claude/brando-pa-phase19-longterm`：周线七类状态、三类候选买点与入场前退出计划、独立样本事件研究、16 个长期方案 × 4 窗口 × 4 档成本、B2 冷静期 V7；结论：16 个方案均不采用，候选买点胜率与“周线上涨中任意一天买入”无差别；V7 按第十八阶段规则通过但全样本回撤更差，仅具备独立影子观察资格；研究页面“Brando长期投资决策中心”与独立前瞻观察日志）、[docs/43_brando_pa_第二十阶段_100000USDT研究本金与BTC_USDT配置.md](docs/43_brando_pa_第二十阶段_100000USDT研究本金与BTC_USDT配置.md)（第二十阶段，独立分支 `claude/brando-pa-phase20-capital100k`：研究本金统一 100,000 USDT 真实重算（Mac 三账户仍为 10,000，未改动）、数量精度 / 最小订单 / 平方根流动性冲击执行模型、0–100% BTC × 不再平衡 / 年度 / 偏离再平衡、50% 动态回撤预算与 35/40/45% 预警、滚动 4 年起点与压力区间；结论：预算内风险收益最好的是 25% BTC 年度再平衡（1,526,469 USDT，最大回撤 −39.7%），但以最终 USDT 计没有证据优于 BTC 买入持有（9,974,359，−83.2%）；研究页面“BTC/USDT 资金配置中心”）。
> 不修改 spot90 / bgw_quant 的策略、数据与配置。**默认禁止真实下单**（仓库内没有任何交易所私有接口调用）。

```bash
pip install -r requirements.txt && export PYTHONPATH=src
python -m brando_pa.cli ingest                                  # 1w/1d/4h/1h/15m K 线（GitHub 公开 Binance 缓存；Bitget 可达时可切换适配器）
python -m brando_pa.cli analyze                                 # 关键区域 / 4h 结构 / 日线环境
python -m brando_pa.cli signal                                  # WAIT/LONG/SHORT/EXIT/NO_TRADE + 开仓/不交易理由 → reports_out/brando_pa/signals/latest.json
python -m brando_pa.cli --exec-tf 15m backtest --start 2025-02-01 --name m15     # 回测矩阵：全部/单策略 × 过滤开/关，含买入持有基准
python -m brando_pa.cli --exec-tf 1h  walkforward --start 2022-04-01 --name h1   # Walk-Forward（每个策略独立）
python -m brando_pa.cli paper --replay --replay-bars 6000      # 模拟盘全流程回放（订单去重、状态恢复）
python -m brando_pa.cli paper --once / --loop                   # 模拟盘单轮 / 持续运行（数据过期自动禁止新开仓）
python -m brando_pa.cli dashboard --port 8502                   # Streamlit 控制面板
python -m brando_pa.cli export                                 # CSV / JSON / HTML 研究导出
pytest tests/test_brando_pa_*.py -q                            # 单元测试：关键价位、跨周期对齐、三策略状态机、仓位/风控、保守成交规则、订单去重、无未来函数
# ---- 第二阶段（docs/21）----
python -m brando_pa.cli audit                                  # 数据质量与回测真实性审计（重复/缺口/错位/跨周期一致性/永续基差/资金费样本/成本假设）
python -m brando_pa.cli --exec-tf 1h experiments --start 2022-04-01 --name h1      # 入场(市价/限价) × 多周期(single/aligned/zone_limit) × 退出方案 × 策略 实验矩阵 + 漏斗
python -m brando_pa.cli --exec-tf 1h experiments --name h1_sens --only ALL__entry_market__mtf_single --set costs.funding.assumed_rate_8h=0.00004   # 成本敏感性
python -m brando_pa.cli --exec-tf 1h walkforward --name h1_p2 [--regime-gating] [--set mtf.mode=aligned]   # Walk-Forward（隔离 7 天、退出参数入网格、Bootstrap、扰动）
python -m brando_pa.cli evaluate --names h1_p2,m15_p2,h4_p2    # Strategy Evaluator：合格 / 仅研究 / 不合格
# ---- 第三阶段（docs/22）----
python -m brando_pa.cli --exec-tf 1h phase3                    # 预登记流程：训练/验证选止损 → 锁定测试一次 → 压力测试 → 策略 A 止损距离诊断（reports_out/brando_pa/phase3/）
# ---- 第四阶段（docs/23，纯现货）----
python -m brando_pa.cli phase4                                 # 六组对照（买入持有 BTC/ETH、策略 BTC/ETH/双资产、USDT）× 5 窗口 × 零成本/含成本 + 4h 长历史 → reports_out/brando_pa/phase4_spot/
python -m brando_pa.cli spot-signal                            # 最新 BUY/HOLD/SELL 与理由（前瞻记录）
# ---- 第五阶段（docs/24，现货趋势参与）----
python -m brando_pa.cli phase5 --workers 4                     # 趋势状态 + 模型 A/B/C × 三种退出 × 十组对照 × 6 窗口 × 2 成本 → reports_out/brando_pa/phase5_spot/
python -m brando_pa.cli phase5 --windows full_1h holdout --set trend.exits.hard_limits.max_drawdown_halt=1.0 --out-suffix ablation_no_halt   # 事后消融（明确标注）
python -m brando_pa.cli trend-signal                           # 最新日线状态与模型 A/C 决策（前瞻记录）
# ---- 第六阶段（docs/25，三层现货趋势系统）----
python -m brando_pa.cli sma200-audit                           # SMA200 基准三套实现逐根复现 + 预热/成交时点/账本/手续费/持仓回撤/截断核对
python -m brando_pa.cli phase6 --workers 4                     # 策略 A/B × 熔断 {原规则, 状态机, 无} × {BTC, ETH, 等权} × 6 窗口 × 2 成本 + 频率分析 + 验收 → reports_out/brando_pa/phase6_spot/
python -m brando_pa.cli trendsys-signal                        # 最新已收盘日线下 A/B 的状态与动作
python -m brando_pa.cli trendsys-paper                         # 前瞻模拟一次（只读行情、追加 JSONL + 哈希链；--verify 校验；--replay 为重放）
# ---- 第七阶段（docs/26，USDT 资金配置）----
python -m brando_pa.cli phase7 --workers 4                     # 模型 A/B/C × 熔断 {原规则, 状态机, 无} × 6 窗口 × 2 成本 + 六个固定参考 + USDT 排行榜 → reports_out/brando_pa/phase7_usdt/
python -m brando_pa.cli usdt-signal                            # 最新已收盘日线下三模型的目标比例
python -m brando_pa.cli usdt-paper                             # 第七阶段前瞻模拟一次（资金配置；--verify / --replay）
# ---- 第八阶段（docs/27，真实行情 + 前瞻账户）----
python -m brando_pa.cli forward-data                           # 真实行情更新（来源自动回退、只保留已收盘 K 线、1h→1d 一致性、哈希链事件）
python -m brando_pa.cli forward-run [--loop --interval 600]    # 三个前瞻账户推进一次 / 常驻；bash scripts/brando_pa_forward.sh 供 cron
python -m brando_pa.cli forward-status                         # 状态摘要 + 事件链校验
python -m brando_pa.cli forward-replay --start 2025-09-01 --log-dir reports_out/brando_pa/phase8_forward_replay_2025Q4   # 独立回放（不是前瞻）
# ---- 第九阶段（docs/28，24 小时服务）----
python -m brando_pa.cli forward-service [--once]               # 后台服务主循环（由 deploy/ 下 launchd / systemd / Docker 管理）
python -m brando_pa.cli forward-health                         # 健康检查（退出码 0/1）
python -m brando_pa.cli forward-daily-report [--date YYYY-MM-DD]   # 每日 UTC 三账户报告
python -m brando_pa.cli forward-acceptance                     # 72 小时真实时间验收状态（不足 72h 为“进行中”）
python -m brando_pa.cli forward-data-audit --hours 48          # 实时跨来源行情审计（只读）
bash deploy/install_mac_brando_pa.sh                           # Mac：安装 launchd 常驻服务（见 docs/brando_pa_validation/phase9_forward/MAC_RUNBOOK.md）
bash scripts/brando_pa_forward_ctl.sh {status|health|logs|report|acceptance|restart|stop|start}
```
