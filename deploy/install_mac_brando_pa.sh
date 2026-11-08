#!/usr/bin/env bash
# brando_pa 前瞻服务 · macOS 安装（launchd 常驻，KeepAlive 自动恢复）。与仓库里 spot90 的 com.spot90.* 服务互不影响。
# 用法：bash deploy/install_mac_brando_pa.sh        卸载：bash deploy/uninstall_mac_brando_pa.sh
# 持久化目录（全部在仓库内，重启/升级不清空）：data_store/brando_pa/forward_bars（行情缓存）、reports_out/brando_pa/phase8_forward（账户状态、事件哈希链、心跳、日报、日志）
set -euo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"; LABEL="com.brando-pa.forward"; LA="$HOME/Library/LaunchAgents"
if [ "$(uname -s)" != "Darwin" ]; then echo "此脚本只在 macOS 上运行（当前 $(uname -s)）"; exit 1; fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt pyarrow duckdb httpx pyyaml streamlit plotly pytest
mkdir -p "$HOME/.config/brando_pa" reports_out/brando_pa/phase8_forward data_store/brando_pa/forward_bars
[ -f "$HOME/.config/brando_pa/env" ] || { cp deploy/.env.example "$HOME/.config/brando_pa/env"; chmod 600 "$HOME/.config/brando_pa/env"; }
WEBHOOK="$(grep -E '^BRANDO_ALERT_WEBHOOK=' "$HOME/.config/brando_pa/env" | cut -d= -f2- || true)"
# 真实交易硬性禁用：拒绝在配置里出现 live_trading: true
if grep -Eq '^\s*live_trading:\s*true' config/brando_pa.yaml; then echo "config/brando_pa.yaml 含 live_trading: true，拒绝安装"; exit 1; fi
echo "1) 行情与账户完整性检查（只读）..."
PYTHONPATH=src .venv/bin/python -m brando_pa.cli forward-data | head -c 600; echo
PYTHONPATH=src .venv/bin/python -m brando_pa.cli forward-status | head -c 400; echo
echo "2) 写入 launchd 配置 $LA/$LABEL.plist"
mkdir -p "$LA"
cat > "$LA/$LABEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$ROOT/.venv/bin/python</string><string>-m</string><string>brando_pa.cli</string><string>forward-service</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict>
    <key>PYTHONPATH</key><string>src</string><key>TZ</key><string>UTC</string><key>PYTHONUNBUFFERED</key><string>1</string>
    <key>BRANDO_ALERT_WEBHOOK</key><string>$WEBHOOK</string>
    <key>PATH</key><string>$ROOT/.venv/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$ROOT/reports_out/brando_pa/phase8_forward/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/reports_out/brando_pa/phase8_forward/launchd.err.log</string>
</dict></plist>
PL
plutil -lint "$LA/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$LA/$LABEL.plist"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 20
echo "3) 验证"
launchctl print "gui/$(id -u)/$LABEL" | grep -E "state|pid" | head -3 || true
PYTHONPATH=src .venv/bin/python -m brando_pa.cli forward-health | head -c 1200; echo
echo "睡眠：launchd 不能阻止系统睡眠；请执行 sudo pmset -a sleep 0 disksleep 0（台式/合盖外接电源）或保持 caffeinate -s 运行；见 docs/brando_pa_validation/phase9_forward/MAC_RUNBOOK.md"
echo "时钟：systemsetup -getusingnetworktime 应为 On；健康检查会记录与 OKX 服务器的时钟偏差"
echo "日常命令：bash scripts/brando_pa_forward_ctl.sh {status|health|logs|restart|stop|start|report}"
