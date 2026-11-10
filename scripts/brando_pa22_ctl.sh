#!/usr/bin/env bash
# Phase 22 real-buying / order-flow monitor (research only; NO orders; real trading disabled). Runs ONLY from its own clone
# (default ~/brando_pa22_orderflow), own venv (.venv-pa22), own launchd labels. Never touches ~/1hao-, ~/brando_pa21_observer,
# com.brando-pa.forward / com.brando-pa.shadow13 / com.brando-pa.phase21-observer. macOS bash 3.2 compatible. NOT deployed automatically.
# Usage: bash scripts/brando_pa22_ctl.sh {preflight|install|archive|run|start|stop|status|dashboard|rollback}
set -u
cd "$(dirname "${0}")/.."; HERE="$(pwd -P)"; LC="com.brando-pa.phase22-collector"; LD="com.brando-pa.phase22-daily"; LA="${HOME}/Library/LaunchAgents"
VENV="${HERE}/.venv-pa22"; PY="${VENV}/bin/python"; D="${HERE}/reports_out/brando_pa/phase22_orderflow"; export PYTHONPATH="${HERE}/src"; export PYTHONDONTWRITEBYTECODE=1; UIDN="$(id -u)"
for P in "${HOME}/1hao-" "${HOME}/brando_pa21_observer"; do Q="$(cd "${P}" 2>/dev/null && pwd -P || echo "")"; [ -n "${Q}" ] && [ "${Q}" = "${HERE}" ] && { echo "REFUSED: run from its own clone, not ${P}"; exit 3; }; done
pick_py3() { for c in "${BRANDO_PY3:-}" /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 python3; do [ -n "${c}" ] && command -v "${c}" >/dev/null 2>&1 && "${c}" -c 'import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null && { command -v "${c}"; return; }; done; echo ""; }
plist() {  # label, args..., interval-or-keepalive
  L="${1}"; shift; MODE="${1}"; shift; ARGS=""; for a in "$@"; do ARGS="${ARGS}<string>${a}</string>"; done
  if [ "${MODE}" = "keepalive" ]; then SCHED="<key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>60</integer>"; else SCHED="<key>StartInterval</key><integer>${MODE}</integer>"; fi
  cat > "${LA}/${L}.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>${L}</string><key>ProgramArguments</key><array><string>${PY}</string>${ARGS}</array>
<key>WorkingDirectory</key><string>${HERE}</string><key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>${HERE}/src</string><key>PYTHONDONTWRITEBYTECODE</key><string>1</string></dict>
${SCHED}<key>RunAtLoad</key><true/><key>ProcessType</key><string>Background</string><key>StandardOutPath</key><string>${D}/${L}.log</string><key>StandardErrorPath</key><string>${D}/${L}.log</string></dict></plist>
EOF
  plutil -lint "${LA}/${L}.plist" 2>/dev/null || true; }
case "${1:-status}" in
  preflight)
    F=0; echo "== [check] phase 22 preflight (READ-ONLY) clone ${HERE} @ $(git rev-parse --short HEAD)"
    [ -n "$(pick_py3)" ] && echo "PASS  python $(pick_py3)" || { echo "FAIL  python >= 3.12"; F=$((F+1)); }
    for U in https://www.okx.com/api/v5/public/time https://api.exchange.coinbase.com/time "https://static.okx.com/cdn/okex/traderecords/trades/daily/20240102/BTC-USDT-trades-2024-01-02.zip"; do curl -sS -m 15 -o /dev/null -I "${U}" && echo "PASS  ${U%%\?*}" || { echo "FAIL  ${U}"; F=$((F+1)); }; done
    for L in "${LC}" "${LD}"; do launchctl list "${L}" >/dev/null 2>&1 && { echo "FAIL  ${L} already loaded"; F=$((F+1)); } || echo "PASS  ${L} not loaded"; done
    [ -f "${HOME}/brando_pa21_observer/reports_out/brando_pa/phase21_forward/acceptance72.json" ] && echo "INFO  phase 21 72h acceptance result exists" || echo "WARN  phase 21 72h acceptance not finished: do not start phase 22 until it is"
    FREE_KB="$(df -k "${HERE}" | awk 'NR==2{print $4}')"; [ "${FREE_KB:-0}" -gt 31457280 ] && echo "PASS  free disk $((FREE_KB/1024)) MB (need > 30 GB headroom for one-time archive download)" || { echo "FAIL  free disk < 30 GB"; F=$((F+1)); }
    echo "== preflight: ${F} failure(s)"; [ "${F}" -eq 0 ] ;;
  install)
    set -e; PY3="$(pick_py3)"; [ -x "${PY}" ] || "${PY3}" -m venv "${VENV}"; "${PY}" -m pip install -q --upgrade pip; "${PY}" -m pip install -q -r scripts/brando_pa21/requirements-pa21.txt
    "${PY}" -m pytest tests/test_brando_pa_phase22.py -q -p no:cacheprovider; mkdir -p "${D}" "${LA}"
    plist "${LC}" keepalive -m brando_pa.pa22.collector22; plist "${LD}" 21600 -m brando_pa.pa22.daily22
    echo "== INSTALL_OK (plists written, NOT loaded). Next: archive (one-time, ~30-60 min), then start" ;;
  archive)   "${PY}" -m brando_pa.pa22.archive22 ;;
  run)       "${PY}" -m brando_pa.pa22.daily22 ;;
  start)     for L in "${LC}" "${LD}"; do launchctl bootstrap "gui/${UIDN}" "${LA}/${L}.plist" 2>/dev/null || true; done; launchctl list | grep phase22 ;;
  stop)      for L in "${LC}" "${LD}"; do launchctl bootout "gui/${UIDN}/${L}" 2>/dev/null && echo "stopped ${L}" || echo "${L} not loaded"; done ;;
  status)    launchctl list | grep phase22 || echo "not loaded"; cat "${D}/collector_status.json" 2>/dev/null | head -60 ;;
  dashboard) "${PY}" -m brando_pa.pa22.dashboard22 ;;
  rollback)  bash "${0}" stop; BK="${HOME}/brando_pa22_rollback_$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "${BK}"; for L in "${LC}" "${LD}"; do [ -f "${LA}/${L}.plist" ] && mv "${LA}/${L}.plist" "${BK}/"; done
             echo "plists moved to ${BK}; data kept in ${HERE}. Nothing else on this Mac was changed." ;;
  *) echo "usage: ${0} {preflight|install|archive|run|start|stop|status|dashboard|rollback}"; exit 2 ;;
esac
