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
