#!/usr/bin/env bash
# One-shot VPS installer for the DEX arbitrage continuous monitor (Linux/systemd).
# Read-only monitoring: no wallet, no signing, no real execution. Idempotent —
# safe to re-run (it refreshes the unit files and restarts the service).
#
# Usage (as root, from the repo root):
#   sudo APP_DIR=/opt/dao_audit DB_DIR=/var/lib/dexmon deploy/deploy.sh
#
# Override before running if needed:
#   APP_DIR   install location for the code (default /opt/dao_audit)
#   DB_DIR    writable state dir for the SQLite DB + logs (default /var/lib/dexmon)
#   RUN_USER  service account to create/run as (default dexmon)
#   POLL      block poll seconds (default 10)   RATE  per-scan rate limit s (default 2)
#   BASE_RPC_FALLBACK / ARBITRUM_RPC_FALLBACK  comma-separated fallback RPC URLs
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dao_audit}"
DB_DIR="${DB_DIR:-/var/lib/dexmon}"
RUN_USER="${RUN_USER:-dexmon}"
POLL="${POLL:-10}"
RATE="${RATE:-2}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

echo ">> installing from $SRC to $APP_DIR (user=$RUN_USER, db=$DB_DIR)"
[ "$(id -u)" = "0" ] || { echo "run as root (sudo)"; exit 1; }

# 1) service account (no login, no home shell)
id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"

# 2) code + state dirs
mkdir -p "$APP_DIR" "$DB_DIR"
# copy repo (exclude VCS/build noise); rsync if present else cp
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude .git --exclude reports_out --exclude out --exclude cache "$SRC"/ "$APP_DIR"/
else
  cp -a "$SRC"/. "$APP_DIR"/
fi
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR" "$DB_DIR"

# 3) python deps (system python3; pyyaml is the only hard runtime dep)
python3 -m pip install --quiet pyyaml || echo "WARN: pip install pyyaml failed; ensure PyYAML present"

# 4) foundry `cast` must be on PATH for the service; detect it
CAST_BIN="$(command -v cast || echo /root/.foundry/bin/cast)"
FOUNDRY_DIR="$(dirname "$CAST_BIN")"
echo ">> using cast at $CAST_BIN"

# 5) render systemd unit (env-substituted) and install
cat > /etc/systemd/system/dexmon.service <<UNIT
[Unit]
Description=DEX arbitrage continuous monitor (read-only, no execution)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
Environment=PYTHONPATH=$APP_DIR/tool
Environment=PATH=$FOUNDRY_DIR:/usr/local/bin:/usr/bin:/bin
# Optional fallback RPCs for failover (uncomment + set real endpoints):
# Environment=BASE_RPC_FALLBACK=${BASE_RPC_FALLBACK:-}
# Environment=ARBITRUM_RPC_FALLBACK=${ARBITRUM_RPC_FALLBACK:-}
ExecStart=/usr/bin/python3 -m dao_audit dex-run --db $DB_DIR/dex_monitor.db --poll $POLL --rate-limit $RATE
Restart=always
RestartSec=5
StandardOutput=append:$DB_DIR/dexmon.log
StandardError=append:$DB_DIR/dexmon.log
# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DB_DIR

[Install]
WantedBy=multi-user.target
UNIT

# 6) health check timer (every 5 min; logs + non-zero exit on failure)
cat > /etc/systemd/system/dexmon-health.service <<UNIT
[Unit]
Description=DEX monitor health check
[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$APP_DIR
Environment=PYTHONPATH=$APP_DIR/tool
Environment=PATH=$FOUNDRY_DIR:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 -m dao_audit dex-health --db $DB_DIR/dex_monitor.db --max-scan-age 600
UNIT
cat > /etc/systemd/system/dexmon-health.timer <<UNIT
[Unit]
Description=Run DEX monitor health check every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
UNIT

# 7) log rotation
cat > /etc/logrotate.d/dexmon <<ROT
$DB_DIR/dexmon.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
ROT

# 8) enable + (re)start
systemctl daemon-reload
systemctl enable --now dexmon.service
systemctl enable --now dexmon-health.timer
echo ">> done. status:  systemctl status dexmon --no-pager"
echo ">> logs:          journalctl -u dexmon -f    (or tail -f $DB_DIR/dexmon.log)"
echo ">> acceptance:    PYTHONPATH=$APP_DIR/tool python3 -m dao_audit dex-acceptance --db $DB_DIR/dex_monitor.db"
