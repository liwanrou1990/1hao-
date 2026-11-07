#!/usr/bin/env bash
# macOS（Apple Silicon/Intel）安装：venv + 依赖 + launchd 常驻 Dashboard（KeepAlive 自动恢复）+ 每日两次更新（StartCalendarInterval）。
# 用法：bash deploy/install_mac.sh        卸载：bash deploy/uninstall_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"
if command -v brew >/dev/null 2>&1 && ! brew list libomp >/dev/null 2>&1; then echo "提示：LightGBM 需要 libomp：brew install libomp（仅训练/回测用，监测不依赖）"; fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q pandas numpy scipy duckdb pyarrow httpx pyyaml joblib scikit-learn lightgbm streamlit plotly pytest
mkdir -p "$HOME/.config/spot90" logs
if [ ! -f "$HOME/.config/spot90/env" ]; then cp deploy/env.example "$HOME/.config/spot90/env"; chmod 600 "$HOME/.config/spot90/env"; fi
LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA"
cat > "$LA/com.spot90.dashboard.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.spot90.dashboard</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ROOT/deploy/run_dashboard.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>SPOT90_PYTHON</key><string>$ROOT/.venv/bin/python</string><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$ROOT/logs/dashboard.log</string><key>StandardErrorPath</key><string>$ROOT/logs/dashboard.err.log</string>
</dict></plist>
PL
cat > "$LA/com.spot90.daily.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.spot90.daily</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ROOT/scripts/spot90_monitor_daily.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$ROOT/.venv/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key><array>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>10</integer></dict>
  </array>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$ROOT/logs/daily.launchd.log</string><key>StandardErrorPath</key><string>$ROOT/logs/daily.launchd.err.log</string>
</dict></plist>
PL
# launchd 的 StartCalendarInterval 用本机本地时间；北京时间 08:40 / 12:10 ≈ UTC 00:40 / 04:10（备用源 03:17 UTC 更新后第二次运行拿到最新日线）。其他时区请自行换算。
plutil -lint "$LA/com.spot90.dashboard.plist" "$LA/com.spot90.daily.plist"
for L in com.spot90.dashboard com.spot90.daily; do
  launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LA/$L.plist"
done
launchctl kickstart -k "gui/$(id -u)/com.spot90.dashboard"
echo "首次拉取数据与快照（约 1 分钟）..."
PYTHONPATH=src .venv/bin/python -m spot90.cli ingest --no-crosscheck && PYTHONPATH=src .venv/bin/python -m spot90.monitor.snapshot || true
sleep 5; launchctl print "gui/$(id -u)/com.spot90.dashboard" | grep -E "state|pid" | head -3 || true
echo "验证 Dashboard：curl -s http://127.0.0.1:${SPOT90_PORT:-8501}/_stcore/health   （期望输出 ok）"
echo "验证每日任务：launchctl print gui/$(id -u)/com.spot90.daily | grep -A6 'calendar'；手动触发：launchctl kickstart gui/$(id -u)/com.spot90.daily"
echo "手机访问：见 deploy/REMOTE_ACCESS.md（Tailscale Serve）。"
