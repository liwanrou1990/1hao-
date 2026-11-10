#!/usr/bin/env bash
# Phase 21 independent forward-observation service (research only; 100,000 USDT VIRTUAL research capital; NO orders, real trading disabled).
# Runs ONLY from its own clone (default ~/brando_pa21_observer), own venv (.venv-pa21), own launchd label. Never writes into the production
# checkout (~/1hao-), never touches com.brando-pa.forward / com.brando-pa.shadow13 / the phase 14 dashboard. macOS bash 3.2 compatible.
# Env: BRANDO_PROD_ROOT (default ~/1hao-, read-only), BRANDO_PY3 (python >= 3.12 used to create .venv-pa21).
# Usage: bash scripts/brando_pa21_ctl.sh {preflight|install|once|start|stop|status|health|accept72 [since]|evaluate|guard|dashboard|logs [n]|rollback}
set -u
cd "$(dirname "${0}")/.."; HERE="$(pwd -P)"; LABEL="com.brando-pa.phase21-observer"; PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PROD="${BRANDO_PROD_ROOT:-${HOME}/1hao-}"; VENV="${HERE}/.venv-pa21"; PY="${VENV}/bin/python"; D="${HERE}/reports_out/brando_pa/phase21_forward"
PY3="${BRANDO_PY3:-python3}"; BASE="${D}/mac_baseline.json"; STARTED="${D}/service_started_at.txt"; export PYTHONPATH="${HERE}/src"
ok()   { echo "PASS  $*"; }
bad()  { echo "FAIL  $*"; FAILS=$((FAILS+1)); }
note() { echo "INFO  $*"; }
guard_not_prod() {
  P="$(cd "${PROD}" 2>/dev/null && pwd -P || echo "")"
  if [ -n "${P}" ] && [ "${P}" = "${HERE}" ]; then echo "REFUSED: this script must run from its own clone, not the production checkout ${PROD}"; exit 3; fi
}
guard_not_prod
case "${1:-status}" in
  preflight)
    FAILS=0; echo "== Phase 21 preflight (READ-ONLY) · $(date -u +%Y-%m-%dT%H:%M:%SZ) · clone ${HERE}"
    [ "$(uname -s)" = "Darwin" ] && ok "macOS $(sw_vers -productVersion 2>/dev/null)" || note "not macOS ($(uname -s)) - launchd steps unavailable"
    BR="$(git -C "${HERE}" rev-parse --abbrev-ref HEAD 2>/dev/null)"; case "${BR}" in claude/brando-pa-phase21-*) ok "branch ${BR} @ $(git -C "${HERE}" rev-parse --short HEAD)";; *) bad "unexpected branch ${BR}";; esac
    [ -z "$(git -C "${HERE}" status --porcelain --untracked-files=no)" ] && ok "clone clean" || bad "clone has local modifications"
    PV="$("${PY3}" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"; "${PY3}" -c 'import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null && ok "${PY3} ${PV}" || bad "Python >= 3.12 required (found ${PV:-none}); set BRANDO_PY3=/path/to/python3.12+ (e.g. Homebrew)"
    curl -sS -m 10 https://www.okx.com/api/v5/public/time >/dev/null 2>&1 && ok "OKX public API reachable" || bad "OKX public API not reachable (Coinbase is the fallback; check network)"
    curl -sS -m 10 https://api.exchange.coinbase.com/time >/dev/null 2>&1 && ok "Coinbase public API reachable" || note "Coinbase public API not reachable"
    if [ -d "${PROD}" ]; then ok "production checkout found ${PROD} @ $(git -C "${PROD}" rev-parse --short HEAD 2>/dev/null) ($(git -C "${PROD}" rev-parse --abbrev-ref HEAD 2>/dev/null))"; else note "production checkout ${PROD} not found (set BRANDO_PROD_ROOT)"; fi
    for L in com.brando-pa.forward com.brando-pa.shadow13; do launchctl list "${L}" >/dev/null 2>&1 && note "existing service ${L}: loaded (will NOT be touched)" || note "existing service ${L}: not loaded"; done
    launchctl list "${LABEL}" >/dev/null 2>&1 && bad "${LABEL} already loaded" || ok "${LABEL} not loaded"
    for X in reports_out/brando_pa/phase21_forward data_store/brando_pa/phase21_bars data_store/brando_pa/phase21_feed; do [ -e "${PROD}/${X}" ] && bad "phase 21 dir exists inside production checkout: ${PROD}/${X}" || true; done
    FREE_KB="$(df -k "${HERE}" | awk 'NR==2{print $4}')"; [ "${FREE_KB:-0}" -gt 2097152 ] && ok "free disk $((FREE_KB/1024)) MB" || bad "free disk < 2 GB"
    pmset -g 2>/dev/null | grep -E "^ *sleep " | head -1 | sed 's/^/INFO  power: /' || true
    note "72h acceptance needs the Mac awake: System Settings > Battery/Energy > prevent automatic sleeping on power adapter (manual, not changed by this script)"
    echo "== preflight: ${FAILS} failure(s)"; [ "${FAILS}" -eq 0 ] ;;
  install)
    set -e; echo "== install into ${HERE} (own venv, own data dirs). Nothing is loaded into launchd."
    [ -x "${PY}" ] || "${PY3}" -m venv "${VENV}"
    "${PY}" -m pip install -q --upgrade pip; "${PY}" -m pip install -q -r scripts/brando_pa21/requirements-pa21.txt
    "${PY}" -m pytest tests/test_brando_pa_phase21.py tests/test_brando_pa_phase21_launch.py -q -p no:cacheprovider
    "${PY}" -c "from brando_pa.pa21 import freeze21; v=freeze21.verify(); print('freeze', v); assert v['ok']"
    mkdir -p "${D}"
    if [ ! -f "${BASE}" ]; then "${PY}" -m brando_pa.pa21.macguard21 snapshot "${PROD}" "${BASE}"; else note "baseline exists, kept: ${BASE}"; fi
    "${PY}" -c "from brando_pa.pa21 import monitor21; import json,pathlib; pathlib.Path('${D}/drift_history.json').write_text(json.dumps(monitor21.drift_history(), ensure_ascii=False, indent=1))"
    "${PY}" -m brando_pa.pa21.stress21 >/dev/null
    mkdir -p "${HOME}/Library/LaunchAgents"
    cat > "${PLIST}" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key><array><string>${PY}</string><string>-m</string><string>brando_pa.pa21.service21</string><string>tick</string></array>
  <key>WorkingDirectory</key><string>${HERE}</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>${HERE}/src</string></dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${D}/service.log</string>
  <key>StandardErrorPath</key><string>${D}/service.log</string>
</dict></plist>
PLISTEOF
    plutil -lint "${PLIST}" 2>/dev/null || true
    "${PY}" -m brando_pa.pa21.service21 tick
    echo "== installed. plist written (NOT loaded): ${PLIST}. Next: bash scripts/brando_pa21_ctl.sh start" ;;
  once)      "${PY}" -m brando_pa.pa21.service21 tick ;;
  start)     [ -f "${PLIST}" ] || { echo "run install first"; exit 2; }
             launchctl bootstrap "gui/$(id -u)" "${PLIST}" 2>/dev/null || true; launchctl kickstart "gui/$(id -u)/${LABEL}"
             mkdir -p "${D}"; date -u +"%Y-%m-%d %H:%M:%S" >> "${STARTED}"; echo "started ${LABEL}; start time appended to ${STARTED}" ;;
  stop)      launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null && echo "stopped ${LABEL}" || echo "${LABEL} was not loaded" ;;
  status)    launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | grep -E "state|pid|last exit|runs" | head -6 || echo "launchd job not loaded (${LABEL})"
             "${PY}" -m brando_pa.pa21.service21 health ;;
  health)    "${PY}" -m brando_pa.pa21.service21 health ;;
  accept72)  S="${2:-$(head -1 "${STARTED}" 2>/dev/null)}"; [ -n "${S}" ] || { echo "no start time; pass it: accept72 'YYYY-MM-DD HH:MM'"; exit 2; }
             "${PY}" -m brando_pa.pa21.acceptance21 accept72 --since "${S}" --baseline "${BASE}" --prod-root "${PROD}" ;;
  evaluate)  "${PY}" -m brando_pa.pa21.acceptance21 evaluate ;;
  guard)     "${PY}" -m brando_pa.pa21.macguard21 compare "${BASE}" ;;
  dashboard) "${PY}" -m brando_pa.pa21.dashboard21 ;;
  logs)      tail -n "${2:-30}" "${D}/service.log" 2>/dev/null || echo "no service.log yet"; echo "--- runs"; tail -n 5 "${D}/runs.jsonl" 2>/dev/null | cut -c1-240 ;;
  rollback)  echo "== rollback: stop service, move plist to a backup folder, verify production untouched. Data in ${HERE} is kept (append-only records)."
             launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
             BK="${HOME}/brando_pa21_rollback_$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "${BK}"; [ -f "${PLIST}" ] && mv "${PLIST}" "${BK}/" || true
             echo "plist moved to ${BK}"; [ -f "${BASE}" ] && "${PY}" -m brando_pa.pa21.macguard21 compare "${BASE}" || echo "no baseline to compare"
             echo "To remove the observer entirely (manual): stop, then move ${HERE} away yourself. The production checkout ${PROD} was never modified." ;;
  *) echo "usage: ${0} {preflight|install|once|start|stop|status|health|accept72 [since]|evaluate|guard|dashboard|logs [n]|rollback}"; exit 2 ;;
esac
