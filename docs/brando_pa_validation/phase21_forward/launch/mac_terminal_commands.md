# 第二十一阶段 · Mac 终端部署命令（按顺序逐条复制执行）

**规则：**
- 每一步只复制一个代码块，执行完先看结尾的标记。
- 出现 `FAIL`、`REFUSED`、`STOP`、`ERROR`，或者没有出现预期标记，**立即停止，不要继续下一步**，把整段输出发给我。
- 所有命令只在 `~/brando_pa21_observer` 中写入文件（外加 `~/Library/LaunchAgents/com.brando-pa.phase21-observer.plist`）。
- 对 `~/1hao-` 只读：不启动、不停止 `com.brando-pa.forward` / `com.brando-pa.shadow13`，也不写入任何文件。
- 没有任何下单代码，真实交易保持禁用。

时间：观察起点是 2026-11-11 这根 UTC 日线，第一条记录要到 **2026-11-12 00:00 UTC（北京时间 11-12 08:00）** 之后才会出现。在此之前账本显示 0 天是正常的。

---

## A. 检查（只读 + 演练）

### A1 记录现有系统状态（只读）

```bash
git -C ~/1hao- rev-parse --short HEAD; git -C ~/1hao- rev-parse --abbrev-ref HEAD; git -C ~/1hao- status --porcelain --untracked-files=no | head; launchctl list | grep brando-pa; echo A1_DONE
```

预期：
- `5a19020` 和 `claude/brando-pa-phase9-deploy`；
- 中间没有文件修改行；
- 列出现有的 `com.brando-pa.forward`（以及已部署时的 `com.brando-pa.shadow13`）；
- 最后是 `A1_DONE`。

### A2 克隆到独立目录（如果目录已存在会停止）

```bash
if [ -e ~/brando_pa21_observer ]; then echo "STOP: ~/brando_pa21_observer already exists"; else git clone --branch claude/brando-pa-phase21-launch "$(git -C ~/1hao- remote get-url origin)" ~/brando_pa21_observer && echo A2_DONE; fi
```

预期：`A2_DONE`。

### A3 核实提交、部署脚本和测试报告

```bash
cd ~/brando_pa21_observer && git log --oneline -1 && git merge-base --is-ancestor e4f54c8 HEAD && grep -h "passed, 0 failed" docs/brando_pa_validation/phase21_forward/test_report*.txt | cut -c1-40 && bash -n scripts/brando_pa21_ctl.sh && echo A3_COMMIT_OK
```

预期：
- 第一行是我在对话中给出的提交号；
- 能看到三份测试报告（225 / 234 / 最终一份）都是 `passed, 0 failed`；
- 最后是 `A3_COMMIT_OK`。

### A4 部署前检查（只读）

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh preflight
```

预期：最后一行 `== preflight: 0 failure(s)`。

- 如果提示没有 Python ≥ 3.12：先执行 `brew install python@3.12`，再重新执行 A4。
- 如果 `INFO  power` 一行里 `sleep` 不是 0：到“系统设置 → 电池 / 能源”中手动设置接电源时不自动睡眠，脚本不会替你修改。

### A5 演练（临时环境）与三账户只读核对

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh dryrun 2>&1 | tee ~/brando_pa21_dryrun.log | tail -25
```

预期：
- 中间有 `MAC_ACCOUNTS_OK`：三个账户都找到，初始本金 10,000，事件哈希链完整；
- 最后是 `== DRYRUN_OK`；
- 本步不写 launchd、不写 `~/Library`、不写 `~/1hao-`。

---

## B. 安装（只在独立目录内）

### B1 安装

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh install 2>&1 | tee ~/brando_pa21_install.log | tail -8
```

预期：最后一行 `== INSTALL_OK. plist written (NOT loaded) ...`。

本步会建立 `.venv-pa21`、运行测试、校验冻结规则、对 `~/1hao-` 做只读基线、写 plist（不加载），并运行一次观察。

### B2 安装后核对现有系统未受影响

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh prodcheck | tail -3 && bash scripts/brando_pa21_ctl.sh guard
```

预期：`MAC_ACCOUNTS_OK`，然后 `"ok": true` 且 `"problems": []`。

---

## C. 启动与核对

### C1 启动独立定时观察任务

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh start
```

预期：`STARTED com.brando-pa.phase21-observer`。

### C2 等待首次运行后核对行情、账本、哈希链、页面

```bash
cd ~/brando_pa21_observer && sleep 120 && bash scripts/brando_pa21_ctl.sh verify
```

预期：
- `launchd`、`heartbeat`、`freeze`、`hash_chains`、`market_data`、`ledger`、`dashboard` 各项都是 `PASS`；
- 最后是 `VERIFY_OK`；
- 2026-11-12 08:00（北京时间）之前，`ledger` 显示 `awaiting_first_close`、0 天，属于正常。

### C3 打开只读页面

```bash
open ~/brando_pa21_observer/reports_out/brando_pa/phase21_forward/dashboard21_forward_center.html
```

预期：页面顶部写着“研究本金 · 100,000 USDT（虚拟）……不是真实资产”和“Mac 运行账户 · 10,000 USDT × 3（未改动）”，页面上没有任何按钮。

### C4 重启恢复测试（只卸载并重新加载本服务，约 2.5 分钟）

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh restart-test
```

预期：每项都是 `PASS`，最后是 `== RESTART_TEST_OK`。现有服务不会被卸载或重载。

### C5（可选）真正重启 Mac

本服务是 LaunchAgent，重启后需要**你登录**才会自动加载。真正重启会同时中断现有三账户服务，所以是否执行由你决定；不做也不影响验收。如果执行，登录后运行：

```bash
cd ~/brando_pa21_observer && sleep 120 && launchctl list | grep brando-pa && bash scripts/brando_pa21_ctl.sh verify && bash scripts/brando_pa21_ctl.sh guard
```

预期：三个标签都在，出现 `VERIFY_OK`，并且 `"ok": true`。

---

## D. 真实 72 小时本机验收

### D1 开始验收（只能在 Mac 上执行，记录起点，不可覆盖）

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh accept-begin
```

预期：`ACCEPTANCE_STARTED <UTC 时间>`。

此后 72 小时内保持 Mac 接电源、不睡眠、不合盖。验收只认这台 Mac 上产生的运行记录（运行平台必须为 darwin、且来自同一主机），云端模拟结果不会被计入。

### D2 随时查看进度（服务状态、观察起点、验收进度、问题清单、回滚方法）

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh report
```

看以下字段：
- `hours_elapsed` / `hours_remaining`：已过和剩余小时；
- `n_ticks`：运行次数，应约等于已过小时数；
- `n_days_recorded`、`observation_categories`：应为“实时观察”；
- `status`：应为“进行中，当前无问题”；
- `issues`：应为空。

### D3 满 72 小时后正式判定

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh accept72; bash scripts/brando_pa21_ctl.sh guard
```

预期：`"verdict": "通过"`，11 项全部是“通过”，然后 `"ok": true`。结果保存在 `reports_out/brando_pa/phase21_forward/acceptance72.json`。

- **未通过**：执行 E1 停止服务，把输出发给我，不要修改代码。
- **通过**：仍需你确认才算正式上线。

---

## E. 停止与回滚

### E1 只停止本服务（记录保留，可再次 `start`）

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh stop
```

### E2 回滚

停止服务，把 plist 移到 `~/brando_pa21_rollback_<时间>/`，并核对 `~/1hao-` 未受影响。

```bash
cd ~/brando_pa21_observer && bash scripts/brando_pa21_ctl.sh rollback
```

如果要彻底移除，按回滚输出最后一行的提示，手动把 `~/brando_pa21_observer` 移到备份目录。`~/1hao-` 从未被修改，不需要恢复。
