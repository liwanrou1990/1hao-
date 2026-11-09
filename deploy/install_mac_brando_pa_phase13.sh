#!/usr/bin/env bash
# Phase 13 shadow forward service - Mac installer (NOT executed by the cloud session; run only after the phase-9 72h acceptance is finished and the owner explicitly agrees).
# Installs a SEPARATE launchd job com.brando-pa.shadow13 that runs `brando_pa.cli phase13-service` in the SAME repo checkout but with its own
# log dir (reports_out/brando_pa/phase13_forward), own market store (data_store/brando_pa/phase13_bars), own feed dir, own lock, own logs.
# It never touches reports_out/brando_pa/phase8_forward (the three production paper accounts), the production plist, or config/brando_pa.yaml.
# Requirements: macOS, python3 >= 3.11 with the repo venv already created by deploy/install_mac_brando_pa.sh (reuses .venv), network to www.okx.com / api.exchange.coinbase.com.
# Resources: one extra python process, ~100 MB RAM, ~1 MB/day disk, one HTTP poll per 600 s per source (same as the production service).
# Safety checks performed before installing: live trading disabled (strict YAML), production dir hash unchanged by a dry isolation check, labels/dirs/locks distinct.
set -euo pipefail
LABEL="com.brando-pa.shadow13"; PROD_LABEL="com.brando-pa.forward"
ROOT="$(cd "$(dirname "${0}")/.." && pwd)"; cd "${ROOT}"
PY="${ROOT}/.venv/bin/python"; [ -x "${PY}" ] || { echo "[shadow13] .venv missing: run deploy/install_mac_brando_pa.sh first"; exit 1; }
echo "[shadow13] repo=${ROOT} python=$("${PY}" --version 2>&1) head=$(git rev-parse --short HEAD)"
if [ "${BRANDO_SHADOW13_CONFIRM:-}" != "I_AGREE" ]; then echo "[shadow13] refusing: set BRANDO_SHADOW13_CONFIRM=I_AGREE to confirm the owner approved deploying the shadow service (after the 72h acceptance)."; exit 2; fi
export PYTHONPATH=src
# 1) live trading guard (same strict loader as the upgrader)
"${PY}" - <<'PYG'
from pathlib import Path
from brando_pa.forward.upgrade import check_live_trading_disabled
r = check_live_trading_disabled(Path("config/brando_pa.yaml")); print("[shadow13] live guard:", r["reason"]); raise SystemExit(0 if r["ok"] else 1)
PYG
# 2) isolation check (no cycle; paths/locks/labels + production hash snapshot)
"${PY}" -m brando_pa.cli phase13-isolation-check | tee /tmp/shadow13_isolation.json | "${PY}" -c "import sys,json; d=json.load(sys.stdin); print('[shadow13] isolation ok =', d['ok']); sys.exit(0 if d['ok'] else 1)"
# 3) dirs
mkdir -p reports_out/brando_pa/phase13_forward data_store/brando_pa/phase13_bars data_store/brando_pa/phase13_feed "${HOME}/Library/LaunchAgents"
# 4) plist (separate label, separate logs); never modifies the production plist
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
cat > "${PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key><array><string>${PY}</string><string>-m</string><string>brando_pa.cli</string><string>phase13-service</string></array>
  <key>WorkingDirectory</key><string>${ROOT}</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>src</string><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>${ROOT}/reports_out/brando_pa/phase13_forward/launchd.out.log</string>
  <key>StandardErrorPath</key><string>${ROOT}/reports_out/brando_pa/phase13_forward/launchd.err.log</string>
</dict></plist>
EOF
echo "[shadow13] plist written: ${PLIST} (production ${PROD_LABEL} untouched)"
# 5) first cycle in foreground (creates accounts at forward_start = now), then load the job
"${PY}" -m brando_pa.cli phase13-service --once
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "${PLIST}"; launchctl kickstart "gui/$(id -u)/${LABEL}"
sleep 5; launchctl print "gui/$(id -u)/${LABEL}" | grep -E "state|pid" | head -3
echo "[shadow13] installed. check: bash scripts/brando_pa_shadow13_ctl.sh status|health|audit|report|dashboard ; production job: launchctl print gui/\$(id -u)/${PROD_LABEL}"
