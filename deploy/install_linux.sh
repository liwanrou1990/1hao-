#!/usr/bin/env bash
# Linux 服务器（systemd）安装：用户级 systemd（无需 root；自动 enable-linger）或 --system 系统级。
# 用法：bash deploy/install_linux.sh [--system]      卸载：bash deploy/uninstall_linux.sh [--system]
set -euo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"; MODE="${1:-user}"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q pandas numpy scipy duckdb pyarrow httpx pyyaml joblib scikit-learn lightgbm streamlit plotly pytest
mkdir -p "$HOME/.config/spot90" logs
if [ ! -f "$HOME/.config/spot90/env" ]; then cp deploy/env.example "$HOME/.config/spot90/env"; chmod 600 "$HOME/.config/spot90/env"; fi
if [ "$MODE" = "--system" ]; then UNIT_DIR=/etc/systemd/system; SC="systemctl"; ENVF="$HOME/.config/spot90/env"; WANT=multi-user.target
else UNIT_DIR="$HOME/.config/systemd/user"; SC="systemctl --user"; ENVF="%h/.config/spot90/env"; WANT=default.target; mkdir -p "$UNIT_DIR"; fi
cat > "$UNIT_DIR/spot90-dashboard.service" <<U
[Unit]
Description=spot90 BTC/ETH risk dashboard (read-only)
After=network-online.target
StartLimitIntervalSec=0
[Service]
WorkingDirectory=$ROOT
EnvironmentFile=-$ENVF
Environment=SPOT90_PYTHON=$ROOT/.venv/bin/python
ExecStart=/bin/bash $ROOT/deploy/run_dashboard.sh
Restart=always
RestartSec=5
NoNewPrivileges=true
[Install]
WantedBy=$WANT
U
cat > "$UNIT_DIR/spot90-daily.service" <<U
[Unit]
Description=spot90 daily ingest + snapshot + healthcheck (read-only)
[Service]
Type=oneshot
WorkingDirectory=$ROOT
EnvironmentFile=-$ENVF
Environment=PATH=$ROOT/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash $ROOT/scripts/spot90_monitor_daily.sh
U
cat > "$UNIT_DIR/spot90-daily.timer" <<U
[Unit]
Description=Run spot90 daily update twice a day (UTC 00:40 and 04:10)
[Timer]
OnCalendar=*-*-* 00:40:00 UTC
OnCalendar=*-*-* 04:10:00 UTC
Persistent=true
RandomizedDelaySec=120
[Install]
WantedBy=timers.target
U
$SC daemon-reload
$SC enable --now spot90-dashboard.service spot90-daily.timer
if [ "$MODE" = "user" ]; then loginctl enable-linger "$(whoami)" 2>/dev/null || true; fi
echo "首次拉取数据与快照..."
PYTHONPATH=src .venv/bin/python -m spot90.cli ingest --no-crosscheck && PYTHONPATH=src .venv/bin/python -m spot90.monitor.snapshot || true
$SC --no-pager status spot90-dashboard.service | head -5 || true
$SC list-timers --no-pager | grep spot90 || true
echo "验证：curl -s http://127.0.0.1:${SPOT90_PORT:-8501}/_stcore/health → ok；手机访问见 deploy/REMOTE_ACCESS.md"
