# Mac 安全更新步骤（已在运行的 Mac；不删除、不重置账户与日志；绝不 reset / clean / 强制覆盖；不会双实例）

一次性引导（当前 Mac 检出还是旧版本，先只取新版本的升级引导脚本；脚本可放在 /tmp，仓库目录按 BRANDO_REPO → 脚本所在仓库 → 当前目录 → ~/1hao- 解析，路径可含空格）：
```bash
cd ~/1hao-
git fetch origin claude/brando-pa-phase9-deploy
git show origin/claude/brando-pa-phase9-deploy:scripts/brando_pa_safe_upgrade.sh > /tmp/brando_safe_upgrade.sh
BRANDO_REPO="$HOME/1hao-" bash /tmp/brando_safe_upgrade.sh claude/brando-pa-phase9-deploy --dry-run   # 演练：快照 → 完整备份 + 校验 → 安全停服务 → 恢复服务；不改代码
BRANDO_REPO="$HOME/1hao-" bash /tmp/brando_safe_upgrade.sh claude/brando-pa-phase9-deploy             # 正式升级
```
（修复记录：① 旧版引导脚本用 `dirname "$0"/..` 推断仓库，从 /tmp 运行时得到 `fatal: not a git repository`；② macOS 自带 bash 3.2 把 `$REPO（` 解析成变量名 `REPO`+多字节首字节 → `REPO?: unbound variable`。现在：仓库目录显式解析；脚本内变量一律写 `${VAR}` 且不紧跟非 ASCII 字符，输出改为 ASCII；`BRANDO_REPO` 两侧若被粘贴成智能引号会自动去掉；`--dry-run` 允许长破折号；回归测试覆盖以上两次真实报错。）

`--dry-run` 的实际行为：快照 → 完整备份并校验 → 记录服务/锁状态（**不停止服务**）→ git fetch → 本地修改分类与 fast-forward 可行性 → 新版本只读时间顺序审计 → 复核账户快照不变 → 输出结论。**不改代码、不装依赖、不写账户、不停服务**。只有演练输出 "dry-run 通过" 且账户数据一致，才执行正式升级。
以后：`bash scripts/brando_pa_forward_ctl.sh upgrade`（同一引导脚本）。

升级器（`src/brando_pa/forward/upgrade.py`，由新版本代码在临时 worktree 中执行）保证：
1. 备份 `backups/brando_pa_forward_backup_<UTC>.tgz` + `.manifest.json`（SHA256）：三账户 state.json / events.jsonl / corrections.jsonl、日报、排行榜、心跳、告警、service.log、runner_events、forward_bars 行情仓、forward_data_status.json、data_events.jsonl、config/brando_pa.yaml、~/.config/brando_pa/env、launchd plist；备份后解包逐文件比对哈希，失败即禁止升级。
2. 停服务：`launchctl bootout` 后等待 `service.lock` 可获取且无 `forward-service` 进程（最多 90 s），否则中止。
3. Git：只允许 fast-forward；本地修改的被跟踪**数据文件**（data_store/**、reports_out/**）先原样保存、合并后字节级放回并校验；本地修改的**代码**文件 → 中止不覆盖；本地有未推送提交 → 中止。
4. 依赖安装与**新版本**时间顺序审计在切换代码之前完成；失败 → 恢复旧版本服务并报告原因；审计不通过不启动新版本。
5. 升级后核对：每个账户现金、各批次持仓数量、挂单数、已处理订单数、last_daily_ts、forward_start、事件链条数与末尾哈希、纠错链、runner_events 链全部相同；任一不同 → 不启动，提示用备份核对。
6. 真实交易守卫：用与服务相同的 YAML SafeLoader 严格读取 `execution.live_trading`，要求布尔 false；true、字符串、重复键、键缺失、解析失败或 `BRANDO_LIVE_CONFIRM` 已设置一律拒绝（2026-11-08 修复：旧守卫是文本扫描，被行尾注释里的 "true" 误触发）。升级器不触碰任何策略参数。
每一步写入 `reports_out/brando_pa/phase8_forward/upgrade_log.jsonl` 与 `upgrade_report_<UTC>.json`。

macOS 兼容性：锁用 `fcntl.flock`（macOS 原生支持；同一目录第二个实例立即退出）；哈希用 Python `hashlib`（不依赖 sha256sum）；进程检查用 `pgrep -f`；服务管理用 `launchctl bootout/bootstrap/kickstart`；`KeepAlive` 在升级期间因 bootout 而不会重启进程。

# Mac 最简部署步骤（首次安装，在持续在线的 Mac 终端执行）

```bash
# 1. 克隆（或已有仓库则拉取）并切到部署分支
git clone https://github.com/liwanrou1990/1hao- && cd 1hao-
git checkout claude/brando-pa-phase9-deploy          # 当前应为提交 fb1c577 之后的最新提交
# 2. 安装：venv + 最小依赖（deploy/requirements-forward.txt）+ 恢复仓库内的前瞻记录副本（只在本机无记录时，cp -n 不覆盖）+ 行情/账户检查 + launchd 常驻 + 首轮验证
bash deploy/install_mac_brando_pa.sh
# 3. 检查
bash scripts/brando_pa_forward_ctl.sh status     # launchd 状态 + 三账户 + 哈希链
bash scripts/brando_pa_forward_ctl.sh health     # 退出码 0 = 健康
bash scripts/brando_pa_forward_ctl.sh logs 50    # 服务日志 / 告警
bash scripts/brando_pa_forward_ctl.sh acceptance # 72 小时真实时间验收（起点 = 首次运行）
```
前提：macOS 可访问 www.okx.com（K 线 + ticker）与 api.exchange.coinbase.com；`python3` 为 3.11+；不要让 Mac 睡眠（`sudo pmset -a sleep 0` 或 `caffeinate -s`）。真实下单无实现、禁用（安装脚本检测到 `live_trading: true` 会拒绝安装）。

# Mac 运行手册（brando_pa 前瞻服务 · launchd）

> 本文件由 Claude 云容器生成；**云容器不是 Mac，没有也不可能在你的 Mac 上执行安装**。以下脚本已提交到 Git，需你在持续在线的 Mac 上执行。

## 安装 / 启动 / 停止 / 重启 / 状态 / 日志 / 健康
```bash
git clone <repo> 1hao- && cd 1hao-    # 或 git pull 到 bcb6645 之后的提交
bash deploy/install_mac_brando_pa.sh   # venv + 依赖 + 行情与账户完整性检查 + launchd（RunAtLoad + KeepAlive）+ 首轮验证
bash scripts/brando_pa_forward_ctl.sh status      # launchd 状态 + 三账户状态 + 哈希链
bash scripts/brando_pa_forward_ctl.sh health      # 健康检查（退出码 0/1）：最新 K 线年龄、最近成功轮询、进程存活、数据源、账户净值、风险状态、最新日志时间、连续失败次数、时钟偏差（startup_check.json）
bash scripts/brando_pa_forward_ctl.sh logs 100    # service.log 最近 100 行 + 告警 + launchd stderr
bash scripts/brando_pa_forward_ctl.sh report      # 生成/查看每日 UTC 三账户 USDT 报告
bash scripts/brando_pa_forward_ctl.sh acceptance  # 72 小时真实时间验收状态（不足 72h 固定“进行中”）
bash scripts/brando_pa_forward_ctl.sh restart | stop | start
bash deploy/uninstall_mac_brando_pa.sh            # 只移除 launchd 任务，不删除任何记录
```
直接命令：`launchctl print gui/$(id -u)/com.brando-pa.forward`、`tail -f reports_out/brando_pa/phase8_forward/service.log`、`cat reports_out/brando_pa/phase8_forward/health.json`。

## 持久化目录（重启/升级不清空；只追加）
- `data_store/brando_pa/forward_bars/`：行情缓存（历史预热 + 实时追加，每根 K 线带 source）
- `reports_out/brando_pa/phase8_forward/<账户>/state.json`、`events.jsonl`（哈希链）、`health.json`、`startup_check.json`、`alerts.jsonl`、`daily/`、`service.log`（10 MB × 5 轮转）、`service.lock`（单实例锁）
- 建议每日备份 `reports_out/brando_pa/phase8_forward` 与 `data_store/brando_pa/forward_data_status.json`（例如 Time Machine 或 rsync）。

## 成交时序规则（2026-11-08 第二次修正后 + 报价精度）
- 处理订单时实时获取 OKX/Coinbase 现货 Bid/Ask（BUY 用 Ask、SELL 用 Bid，保存报价时间戳与来源）；报价年龄 > 120 s 不用；无新鲜报价时用最新已收盘 1h 收盘价作为**估算**（明确标记 estimated，年龄 ≤ 70 min）；都没有 → fill_paused，等待或过期，不用历史价格。网络要求新增：允许访问 www.okx.com 与 api.exchange.coinbase.com 的 ticker 接口（与 K 线同主机）。
- **前瞻成交 = 引擎实际处理时刻**，按当时可观察的最新已收盘 1h 收盘价成交（price_time ≥ exec_after > decision_time）；超过 exec_after 1 h → filled_late；超过 3 h → expired；断线无新价格 → pending。绝不按处理时刻之前某根 K 线的开盘价回填。回放模式另行标注。

### 旧说明（已被上面规则取代，保留以便审计）
- 日线收盘 → 服务下一轮形成决策（decision_time = 运行时钟）→ 订单提交 → 执行时隙 = 决策之后第一根**开盘**的 1h K 线 → 该 K 线收盘后按其开盘价记账（fill 事件含 decision_time / execution_time / execution_price_observed_at / fill_recorded_at）。
- 任何开盘不晚于决策时刻的 K 线绝不成交（cancelled_invalid）；执行 K 线收盘后超过宽限期仍未记账（服务离线）→ order_expired；收盘后超过宽限期才首次处理的日线 → missed_signal。
- 审计：`PYTHONPATH=src .venv/bin/python -m brando_pa.cli forward-timeline-audit`（退出码 0/1）；纠错需先停止服务：`... forward-timeline-audit --repair`，只写 corrections.jsonl 与 order_cancelled 事件，不改原始记录。

## 费用 / 净值一致性只读审计（2026-11-08 新增）
```bash
bash scripts/brando_pa_forward_ctl.sh audit "2026-11-08 12:23:00"     # 只读：逐笔费用算术、事件链重放 vs state/nav、恒等式、费用明细、信号重复/错过/因果、72h 分段、锁/进程；结果写 reports_out/brando_pa/forward_audit/
```
口径说明（审计结论见 docs/29）：权益 = 现金 + 持仓 × 最新已收盘 1h 收盘价；**清仓余额 = 权益 − 持仓市值 × 15 bps（假设卖出滑点 5 bps + 手续费 10 bps，尚未发生）**；买入成本（5 bps 滑点 + 10 bps 手续费）已在成交时从现金扣除一次，不重复；权益 − 初始 = 已实现 + 未实现 − 买入手续费。前瞻实时成交以 Ask/Bid 为参考价再加 5 bps 滑点（比预登记略保守，半价差约 0.01–0.05 bps）。

## 日线更新语义
- `last_daily_ts` 是**最后已处理日线的开盘时刻**（UTC 日期）；其收盘 = +1 天。例如 `last_daily_ts = 2026-11-07 00:00` 表示 2026-11-08 00:00 UTC 收盘的日线已处理（状态里另有 `last_processed_daily_close_utc` 与 `next_daily_close_utc`）。
- 每个日线收盘只决策一次（signal 或 missed_signal）；同一时刻重复运行或重启不会再次决策；审计规则 T8 检查无重复、无遗漏。

## 单实例与重复订单
- `service.lock` 用 `flock` 独占；第二个实例（含 `forward-service --once`）立即退出。
- 订单号 `账户:账本:标的:信号日:方向:序号` 去重；成交只发生一次；重启后从 `state.json` 恢复挂单与已处理订单。

## 已知运行限制（无法由程序保证，需你在 Mac 上处理）
| 项目 | 行为 | 处理 |
|---|---|---|
| 系统睡眠 | launchd 任务随系统睡眠而暂停；期间不会轮询，错过的日线信号恢复后只记 `missed_signal`，挂单超过宽限期作废 `order_expired`，**不会回填成交** | 台式机 / 外接电源：`sudo pmset -a sleep 0 disksleep 0`；或保持 `caffeinate -s` 运行；合盖需外接显示器或 `pmset -a disablesleep 1`（有风险，慎用） |
| 网络断开 | 来源重试 + 回退（OKX → Coinbase → …）；全部失败则本轮不新增订单并告警；恢复后自动补齐 K 线 | 无需操作；连续失败 ≥ 3 轮会告警 |
| 时钟 | 未收盘 K 线判定依赖本机 UTC 时间；`startup_check.json` 与健康检查记录与 OKX 服务器的时钟偏差 | `systemsetup -getusingnetworktime` 应为 On |
| 重启 | `RunAtLoad` 开机自启（需用户登录会话；LaunchAgent 在登录后生效）；`KeepAlive` 崩溃后 30 s 重启 | 若要无登录运行，改用 `/Library/LaunchDaemons`（需 root，路径与权限自行调整） |
| macOS 更新 / 强制重启 | 同上；期间错过的信号按 missed 处理 | — |
| Binance / Bybit | 在受限地区返回 451 / CloudFront 封锁；程序不绕过 | 保持 OKX 主、Coinbase 备；Coinbase BTC-USDT 低流动性小时会有空缺 K 线（审计中可见 1–2 个缺口/48h），作为备用时质量门控会检查 |

## 72 小时真实时间可用性验收协议
1. 安装并启动后，记录起点：`bash scripts/brando_pa_forward_ctl.sh acceptance`（首次运行写入 `acceptance_status.json`，状态“进行中”）。
2. 每天（UTC）运行一次 `acceptance` 与 `health`，把输出追加到 `docs/brando_pa_validation/phase9_forward/acceptance_log/`（文件名 `YYYY-MM-DD.json`），记录：心跳覆盖率、数据故障、错过信号、过期订单、重启次数、告警。
3. 满 72 小时后 `acceptance` 的状态才会变为“通过/未通过”（覆盖率 ≥ 95% 且最长中断 ≤ 2 h 为通过）。**在此之前任何“验收成功”的说法都是无效的。**
4. 当前状态：**未开始**（服务尚未在持续在线主机上启动；云容器内只做过单轮运行与故障注入测试）。
