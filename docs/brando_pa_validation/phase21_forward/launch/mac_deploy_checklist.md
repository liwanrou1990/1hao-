# 第二十一阶段 · Mac 独立前瞻观察部署清单（不自动执行）

> 用途：在 Mac 上用**独立目录**运行四个 100,000 USDT 虚拟研究账户的每日观察。只记录，不下单，真实交易禁用。
> 不修改：`~/1hao-`（Mac 三账户生产目录）、`com.brando-pa.forward`、`com.brando-pa.shadow13`、第十四阶段看板、B2 / SMA200 参数。
> 全部命令由你手动执行；脚本：`scripts/brando_pa21_ctl.sh`（macOS bash 3.2 兼容）。
> **逐条可复制的终端命令见 `mac_terminal_commands.md`**；本清单在其后增加的命令有 `dryrun`、`prodcheck`、`verify`、`restart-test`、`accept-begin`、`report`（72 小时验收的起点以 `accept-begin` 为准）。

## 0. 部署前检查（只读）

| # | 检查 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| 0.1 | 生产目录状态记录 | `git -C ~/1hao- rev-parse --short HEAD` | 记下结果，应为 `5a19020`（`claude/brando-pa-phase9-deploy`） |
| 0.2 | 现有服务在运行 | `launchctl list \| grep brando-pa` | 记下 forward / shadow13 的状态；本部署不改动它们 |
| 0.3 | Python | `python3 --version`；若低于 3.12，安装 Homebrew Python 后设 `export BRANDO_PY3=/opt/homebrew/bin/python3.12` | ≥ 3.12 |
| 0.4 | 防睡眠 | 系统设置 → 电池 / 能源 → 接电源时防止自动睡眠（手动） | 72 小时内不睡眠 |
| 0.5 | 克隆到独立目录 | `git clone --branch claude/brando-pa-phase21-launch "$(git -C ~/1hao- remote get-url origin)" ~/brando_pa21_observer` | 新目录，不是 `~/1hao-`、不是 worktree |
| 0.6 | 运行部署前检查 | `cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh preflight` | `preflight: 0 failure(s)` |

## 1. 安装（只在独立目录内）

`bash scripts/brando_pa21_ctl.sh install`

依次执行：
- 建立 `.venv-pa21` 并安装 7 个依赖；
- 运行第二十一阶段测试 15 项；
- 冻结校验；
- 对 `~/1hao-` 做**只读**基线快照，保存到 `reports_out/brando_pa/phase21_forward/mac_baseline.json`；
- 计算历史漂移与压力情景；
- 写入 `~/Library/LaunchAgents/com.brando-pa.phase21-observer.plist`（**不加载**）；
- 运行一次观察。

通过标准：脚本最后一行显示 `installed`，测试全部通过，`freeze {'ok': True ...}`。

## 2. 启动

`bash scripts/brando_pa21_ctl.sh start`

- launchd 每 3600 秒运行一次，不是常驻进程。
- 启动时间追加写入 `service_started_at.txt`，作为 72 小时验收的起点。
- 检查：`bash scripts/brando_pa21_ctl.sh status`，应显示心跳正常、`auto_trading: false`。

## 3. 日常

| 用途 | 命令 |
|---|---|
| 服务状态 | `bash scripts/brando_pa21_ctl.sh status` |
| 手动运行一次（不必要；服务会自动运行） | `bash scripts/brando_pa21_ctl.sh once` |
| 生成并查看只读页面 | `bash scripts/brando_pa21_ctl.sh dashboard`，然后 `open reports_out/brando_pa/phase21_forward/dashboard21_forward_center.html` |
| 评估计划进度 | `bash scripts/brando_pa21_ctl.sh evaluate` |
| 生产目录未受影响 | `bash scripts/brando_pa21_ctl.sh guard` |
| 日志 | `bash scripts/brando_pa21_ctl.sh logs 50` |

## 4. 停止

`bash scripts/brando_pa21_ctl.sh stop`：只卸载 `com.brando-pa.phase21-observer`。记录保留，再次 `start` 后漏掉的日期会补上并标“历史补记”。

## 5. 回滚

`bash scripts/brando_pa21_ctl.sh rollback`：
- 停止服务；
- 把 plist 移到 `~/brando_pa21_rollback_<时间>/`；
- 用基线核对 `~/1hao-` 未受影响。

数据不删除。要完全移除，手动把 `~/brando_pa21_observer` 移走即可。生产目录从未被写入，不需要恢复任何东西。

## 6. 72 小时验收

启动满 72 小时后运行 `bash scripts/brando_pa21_ctl.sh accept72`。必须全部通过：

| 条目 | 标准 |
|---|---|
| 已满 72 小时 | 当前时间 ≥ 起点 + 72 小时 |
| 运行覆盖 | 有成功运行的小时 ≥ 69 / 72 |
| 最大间隔 | 相邻运行 ≤ 3 小时 |
| 未处理异常 | 0 |
| 行情不可用 | ≤ 10%，且都有记录 |
| 每日记录 | 窗口内每个已收盘 UTC 日都有记录，类别为“实时观察” |
| 哈希链 | 每日记录、提醒、事件、运行、观察、漂移六条全部可验证 |
| 冻结与重放 | 冻结校验每次通过；重放不一致 0；冻结违规 0 |
| Mac 未受影响 | 三账户只追加文件的历史内容未改写；生产仓库 HEAD / 分支 / 未提交修改不变；初始本金配置 10,000 不变；forward / shadow13 加载状态不变；生产目录中没有第二十一阶段目录 |
| 无下单接口 | 源码扫描无下单 / 密钥接口 |

验收结果写入 `reports_out/brando_pa/phase21_forward/acceptance72.json`。

- 未通过：先 `stop`，把结果发给我分析，不要修改代码。
- 通过：仍需你确认才算正式上线。
