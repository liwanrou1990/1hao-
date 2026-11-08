#!/usr/bin/env bash
# brando_pa 前瞻服务 · macOS 安装（launchd 常驻，KeepAlive 自动恢复）。与仓库里 spot90 的 com.spot90.* 服务互不影响。
# 用法：bash deploy/install_mac_brando_pa.sh        卸载：bash deploy/uninstall_mac_brando_pa.sh
# 持久化目录（全部在仓库内，重启/升级不清空）：data_store/brando_pa/forward_bars（行情缓存）、reports_out/brando_pa/phase8_forward（账户状态、事件哈希链、心跳、日报、日志）
set -euo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"; LABEL="com.brando-pa.forward"; LA="$HOME/Library/LaunchAgents"
if [ "$(uname -s)" != "Darwin" ]; then echo "此脚本只在 macOS 上运行（当前 $(uname -s)）"; exit 1; fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r deploy/requirements-forward.txt     # 最小依赖；完整研究环境另行 pip install -r requirements.txt
mkdir -p "$HOME/.config/brando_pa" reports_out/brando_pa/phase8_forward data_store/brando_pa/forward_bars
[ -f "$HOME/.config/brando_pa/env" ] || { cp deploy/.env.example "$HOME/.config/brando_pa/env"; chmod 600 "$HOME/.config/brando_pa/env"; }
WEBHOOK="$(grep -E '^BRANDO_ALERT_WEBHOOK=' "$HOME/.config/brando_pa/env" | cut -d= -f2- || true)"
# 真实交易硬性禁用：用与服务相同的 YAML 解析严格检查 execution.live_trading（必须是布尔 false；重复键/解析失败/非布尔/true 一律拒绝安装）
PYTHONPATH=src .venv/bin/python - <<'PYG' || { echo "live trading guard refused installation"; exit 1; }
from pathlib import Path
from brando_pa.forward.upgrade import check_live_trading_disabled
g = check_live_trading_disabled(Path("config/brando_pa.yaml")); print("live_trading guard:", g["ok"], g["reason"]); raise SystemExit(0 if g["ok"] else 1)
PYG
# 0) 恢复已有前瞻记录（只在本机还没有账户状态时，从仓库内的审计副本恢复；绝不覆盖已有记录）
RESTORE_SRC="docs/brando_pa_validation/phase9_forward/forward_logs"; DST="reports_out/brando_pa/phase8_forward"
if [ ! -f "$DST/A_BTC_SMA200/state.json" ] && [ -f "$RESTORE_SRC/A_BTC_SMA200/state.json" ]; then
  echo "0) 本机无账户状态，从 $RESTORE_SRC 恢复前瞻记录（事件链、状态、日报）..."; cp -Rn "$RESTORE_SRC"/. "$DST"/
  PYTHONPATH=src .venv/bin/python -m brando_pa.cli forward-status | head -c 300; echo
elif [ -f "$DST/A_BTC_SMA200/state.json" ]; then
  echo "0) 检测到本机已有前瞻记录，保留不动（事件 $(wc -l < "$DST/A_BTC_SMA200/events.jsonl") 条）"
fi
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
