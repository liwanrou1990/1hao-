#!/usr/bin/env bash
# Phase 21 independent forward-observation service (research only; 4 x 100,000 USDT VIRTUAL research accounts; NO orders, real trading disabled).
# Runs ONLY from its own clone (default ~/brando_pa21_observer), own venv (.venv-pa21), own launchd label. Never writes into the production
# checkout (~/1hao-), never starts/stops com.brando-pa.forward / com.brando-pa.shadow13 / the phase 14 dashboard. macOS bash 3.2 compatible.
# Env: BRANDO_PROD_ROOT (default ~/1hao-, read-only), BRANDO_PY3 (python >= 3.12 used to create .venv-pa21; auto-detected if unset).
# Usage: bash scripts/brando_pa21_ctl.sh {preflight|prodcheck|dryrun|install|start|verify|restart-test|accept-begin|report|accept72|
#                                         status|health|once|evaluate|guard|dashboard|logs [n]|stop|rollback}
set -u
cd "$(dirname "${0}")/.."; HERE="$(pwd -P)"; LABEL="com.brando-pa.phase21-observer"; PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PROD="${BRANDO_PROD_ROOT:-${HOME}/1hao-}"; VENV="${HERE}/.venv-pa21"; PY="${VENV}/bin/python"; D="${HERE}/reports_out/brando_pa/phase21_forward"
BASE="${D}/mac_baseline.json"; STARTED="${D}/service_started_at.txt"; ACC="${D}/acceptance_started_at.txt"; export PYTHONPATH="${HERE}/src"; export PYTHONDONTWRITEBYTECODE=1
UIDN="$(id -u)"
ok()   { echo "PASS  $*"; }
bad()  { echo "FAIL  $*"; FAILS=$((FAILS+1)); }
note() { echo "INFO  $*"; }
pick_py3() {
  if [ -n "${BRANDO_PY3:-}" ]; then echo "${BRANDO_PY3}"; return; fi
  for c in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.13 /usr/local/bin/python3.12 python3.13 python3.12 python3; do
    if command -v "${c}" >/dev/null 2>&1 && "${c}" -c 'import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null; then command -v "${c}"; return; fi
  done
  echo ""
}
P="$(cd "${PROD}" 2>/dev/null && pwd -P || echo "")"
if [ -n "${P}" ] && [ "${P}" = "${HERE}" ]; then echo "REFUSED: this script must run from its own clone, not the production checkout ${PROD}"; exit 3; fi
case "${1:-status}" in
  preflight)
    FAILS=0; echo "== [check] Phase 21 preflight (READ-ONLY) $(date -u +%Y-%m-%dT%H:%M:%SZ) clone ${HERE}"
    [ "$(uname -s)" = "Darwin" ] && ok "macOS $(sw_vers -productVersion 2>/dev/null)" || note "not macOS ($(uname -s)): launchd steps unavailable"
    BR="$(git -C "${HERE}" rev-parse --abbrev-ref HEAD 2>/dev/null)"; case "${BR}" in claude/brando-pa-phase21-*) ok "branch ${BR} @ $(git -C "${HERE}" rev-parse --short HEAD)";; *) bad "unexpected branch ${BR}";; esac
    [ -z "$(git -C "${HERE}" status --porcelain --untracked-files=no)" ] && ok "clone clean" || bad "clone has local modifications"
    PY3="$(pick_py3)"; [ -n "${PY3}" ] && ok "python for venv: ${PY3} ($("${PY3}" -V 2>&1))" || bad "no Python >= 3.12 found. Install: brew install python@3.12  (or set BRANDO_PY3=/path/to/python3.12)"
    curl -sS -m 10 https://www.okx.com/api/v5/public/time >/dev/null 2>&1 && ok "OKX public API reachable" || bad "OKX public API not reachable"
    curl -sS -m 10 https://api.exchange.coinbase.com/time >/dev/null 2>&1 && ok "Coinbase public API reachable (fallback)" || note "Coinbase public API not reachable"
    curl -sS -m 10 -o /dev/null https://pypi.org/simple/pandas/ 2>/dev/null && ok "PyPI reachable" || bad "PyPI not reachable (needed once for install)"
    if [ -d "${PROD}" ]; then ok "production checkout ${PROD} @ $(git -C "${PROD}" rev-parse --short HEAD 2>/dev/null) ($(git -C "${PROD}" rev-parse --abbrev-ref HEAD 2>/dev/null))"; else bad "production checkout ${PROD} not found (set BRANDO_PROD_ROOT)"; fi
    for L in com.brando-pa.forward com.brando-pa.shadow13; do launchctl list "${L}" >/dev/null 2>&1 && note "existing service ${L}: loaded (will NOT be touched)" || note "existing service ${L}: not loaded"; done
    launchctl list "${LABEL}" >/dev/null 2>&1 && bad "${LABEL} already loaded" || ok "${LABEL} not loaded"
    [ -f "${PLIST}" ] && note "plist already exists: ${PLIST}" || ok "no existing ${LABEL} plist"
    for X in reports_out/brando_pa/phase21_forward data_store/brando_pa/phase21_bars data_store/brando_pa/phase21_feed; do [ -e "${PROD}/${X}" ] && bad "phase 21 dir inside production checkout: ${PROD}/${X}" || true; done
    FREE_KB="$(df -k "${HERE}" | awk 'NR==2{print $4}')"; [ "${FREE_KB:-0}" -gt 2097152 ] && ok "free disk $((FREE_KB/1024)) MB" || bad "free disk < 2 GB"
    pmset -g 2>/dev/null | grep -E "^ *(sleep|displaysleep) " | sed 's/^ */INFO  power: /' || true
    note "72h acceptance needs this Mac awake and on power; change Energy settings yourself if 'sleep' above is not 0"
    echo "== preflight: ${FAILS} failure(s)"; [ "${FAILS}" -eq 0 ] ;;
  prodcheck)
    echo "== [check] existing Mac system (READ-ONLY; nothing in ${PROD} is written or executed)"
    echo "-- production git"; git -C "${PROD}" rev-parse --short HEAD; git -C "${PROD}" rev-parse --abbrev-ref HEAD; git -C "${PROD}" status --porcelain --untracked-files=no | head -20; echo "(empty line above = no tracked-file changes)"
    echo "-- launchd"; launchctl list 2>/dev/null | grep -E "brando-pa" || echo "no brando-pa launchd jobs listed"
    echo "-- three 10,000 USDT accounts (state.json + event hash chains, read-only)"
    PYX="${PY}"; [ -x "${PYX}" ] || PYX="$(pick_py3)"
    "${PYX}" -c "import pandas, yaml" 2>/dev/null || { echo "INFO  python ${PYX} lacks pandas/pyyaml; the account check runs after dryrun/install creates a venv"; exit 0; }
    "${PYX}" -m brando_pa.pa21.macguard21 accounts "${PROD}" ;;
  dryrun)
    set -e; echo "== [check] DRY RUN: temporary venv, tests, freeze check, plist preview. Writes NOTHING to launchd, ~/Library or ${PROD}."
    PY3="$(pick_py3)"; [ -n "${PY3}" ] || { echo "FAIL  no Python >= 3.12"; exit 1; }
    TV="$(mktemp -d "${TMPDIR:-/tmp}/brando_pa21_dryrun.XXXXXX")"; "${PY3}" -m venv "${TV}/venv"; "${TV}/venv/bin/python" -m pip install -q --upgrade pip; "${TV}/venv/bin/python" -m pip install -q -r scripts/brando_pa21/requirements-pa21.txt
    "${TV}/venv/bin/python" -m pytest tests/test_brando_pa_phase21.py tests/test_brando_pa_phase21_launch.py -q -p no:cacheprovider
    "${TV}/venv/bin/python" -c "from brando_pa.pa21 import freeze21; v=freeze21.verify(); print('freeze', v); assert v['ok']"
    "${TV}/venv/bin/python" -m brando_pa.pa21.macguard21 accounts "${PROD}"
    "${TV}/venv/bin/python" -m brando_pa.pa21.macguard21 snapshot "${PROD}" "${TV}/baseline_preview.json" >/dev/null && echo "baseline preview (temp only): ${TV}/baseline_preview.json"
    echo "-- plist that install WOULD write to ${PLIST} (not written now):"; echo "   Label=${LABEL}  Program=${PY} -m brando_pa.pa21.service21 tick  WorkingDirectory=${HERE}  StartInterval=3600  RunAtLoad=true  Log=${D}/service.log"
    echo "== DRYRUN_OK (temporary files in ${TV}; macOS clears ${TMPDIR:-/tmp} automatically)" ;;
  install)
    set -e; echo "== [install] into ${HERE} only (own venv, own data). Nothing is loaded into launchd."
    PY3="$(pick_py3)"; [ -n "${PY3}" ] || { echo "FAIL  no Python >= 3.12"; exit 1; }
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
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>${HERE}/src</string><key>PYTHONDONTWRITEBYTECODE</key><string>1</string></dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>${D}/service.log</string>
  <key>StandardErrorPath</key><string>${D}/service.log</string>
</dict></plist>
PLISTEOF
    plutil -lint "${PLIST}" 2>/dev/null || true
    "${PY}" -m brando_pa.pa21.service21 tick
    echo "== INSTALL_OK. plist written (NOT loaded): ${PLIST}" ;;
  start)
    [ -f "${PLIST}" ] || { echo "run install first"; exit 2; }
    launchctl list "${LABEL}" >/dev/null 2>&1 && { echo "${LABEL} already loaded"; exit 0; }
    launchctl bootstrap "gui/${UIDN}" "${PLIST}"; sleep 2; launchctl list "${LABEL}" >/dev/null 2>&1 || { echo "FAIL  ${LABEL} did not load"; exit 1; }
    mkdir -p "${D}"; date -u +"%Y-%m-%d %H:%M:%S" >> "${STARTED}"; echo "STARTED ${LABEL} (RunAtLoad runs one tick now, then every 3600 s). Start time appended to ${STARTED}" ;;
  verify)
    echo "== [verify] $(date -u +%Y-%m-%dT%H:%M:%SZ)"; LD=0
    if launchctl list "${LABEL}" >/dev/null 2>&1; then echo "PASS  launchd ${LABEL} loaded"; else echo "FAIL  launchd ${LABEL} not loaded"; [ "$(uname -s)" = "Darwin" ] && LD=1; fi
    "${PY}" -m brando_pa.pa21.service21 verify; RC=$?; [ "${RC}" -eq 0 ] && [ "${LD}" -eq 0 ] ;;
  restart-test)
    set -e; echo "== [verify] restart recovery test for ${LABEL} ONLY (existing services untouched)"
    launchctl list "${LABEL}" >/dev/null 2>&1 || { echo "FAIL  service not loaded; run start first"; exit 1; }
    N0="$(wc -l < "${D}/runs.jsonl" | tr -d ' ')"; L0=0; [ -f "${D}/daily_ledger.jsonl" ] && L0="$(wc -l < "${D}/daily_ledger.jsonl" | tr -d ' ')"; F0="$(launchctl list 2>/dev/null | grep -E 'com.brando-pa.(forward|shadow13)' || true)"
    launchctl bootout "gui/${UIDN}/${LABEL}"; sleep 3
    launchctl list "${LABEL}" >/dev/null 2>&1 && { echo "FAIL  still loaded after bootout"; exit 1; } || echo "PASS  unloaded (simulates logout / shutdown)"
    "${PY}" -c "import fcntl;f=open('${D}/service.lock','a+');fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);print('PASS  lock is free after stop')"
    grep -q "<key>RunAtLoad</key><true/>" "${PLIST}" && echo "PASS  plist in ~/Library/LaunchAgents with RunAtLoad=true: loads automatically at next login after a Mac restart" || { echo "FAIL  RunAtLoad missing"; exit 1; }
    launchctl bootstrap "gui/${UIDN}" "${PLIST}"; echo "INFO  re-loaded (simulates login after restart); waiting 120 s for the RunAtLoad tick ..."; sleep 120
    N1="$(wc -l < "${D}/runs.jsonl" | tr -d ' ')"; [ "${N1}" -gt "${N0}" ] && echo "PASS  new tick after reload (${N0} -> ${N1} runs)" || { echo "FAIL  no new tick after reload"; exit 1; }
    "${PY}" -m brando_pa.pa21.service21 verify
    F1="$(launchctl list 2>/dev/null | grep -E 'com.brando-pa.(forward|shadow13)' || true)"; L1=0; [ -f "${D}/daily_ledger.jsonl" ] && L1="$(wc -l < "${D}/daily_ledger.jsonl" | tr -d ' ')"
    [ "$(echo "${F0}" | awk '{print $3}')" = "$(echo "${F1}" | awk '{print $3}')" ] && echo "PASS  existing services' launchd labels unchanged" || echo "INFO  existing services list changed: before [${F0}] after [${F1}] (check whether they restarted on their own)"
    echo "INFO  daily ledger lines ${L0} -> ${L1} (a missed close is added once and marked by category, never duplicated)"
    "${PY}" -m brando_pa.pa21.macguard21 compare "${BASE}" >/dev/null && echo "PASS  production checkout unchanged vs baseline" || { echo "FAIL  production check failed: run guard"; exit 1; }
    echo "== RESTART_TEST_OK" ;;
  accept-begin)
    [ "$(uname -s)" = "Darwin" ] || { echo "REFUSED: real 72h acceptance must run on the Mac (this is $(uname -s))"; exit 1; }
    [ -f "${ACC}" ] && { echo "acceptance already started at $(head -1 "${ACC}") (not overwritten)"; exit 0; }
    launchctl list "${LABEL}" >/dev/null 2>&1 || { echo "FAIL  service not loaded"; exit 1; }
    "${PY}" -m brando_pa.pa21.service21 verify >/dev/null || { echo "FAIL  verify failed; fix before starting acceptance (bash scripts/brando_pa21_ctl.sh verify)"; exit 1; }
    { date -u +"%Y-%m-%d %H:%M:%S"; echo "host=$(hostname) commit=$(git -C "${HERE}" rev-parse --short HEAD) platform=$(uname -s)"; } > "${ACC}"
    echo "ACCEPTANCE_STARTED $(head -1 "${ACC}") UTC. Ends 72h later. Keep the Mac awake and on power. Check progress: bash scripts/brando_pa21_ctl.sh report" ;;
  report)
    echo "== [report] $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    launchctl print "gui/${UIDN}/${LABEL}" 2>/dev/null | grep -E "state =|pid =|last exit code|runs =" | head -5 || echo "service: launchd job not loaded"
    "${PY}" -m brando_pa.pa21.service21 verify | tail -8
    if [ -f "${ACC}" ]; then "${PY}" -m brando_pa.pa21.acceptance21 progress --since "$(head -1 "${ACC}")" --baseline "${BASE}" --prod-root "${PROD}"; else echo "acceptance: not started (run accept-begin)"; fi
    echo "-- rollback (only if needed): bash ${HERE}/scripts/brando_pa21_ctl.sh rollback" ;;
  accept72)
    S="${2:-$(head -1 "${ACC}" 2>/dev/null)}"; [ -n "${S}" ] || { echo "acceptance not started (run accept-begin)"; exit 2; }
    "${PY}" -m brando_pa.pa21.acceptance21 accept72 --since "${S}" --baseline "${BASE}" --prod-root "${PROD}" ;;
  once)      "${PY}" -m brando_pa.pa21.service21 tick ;;
  status)    launchctl print "gui/${UIDN}/${LABEL}" 2>/dev/null | grep -E "state|pid|last exit|runs" | head -6 || echo "launchd job not loaded (${LABEL})"; "${PY}" -m brando_pa.pa21.service21 health ;;
  health)    "${PY}" -m brando_pa.pa21.service21 health ;;
  evaluate)  "${PY}" -m brando_pa.pa21.acceptance21 evaluate ;;
  guard)     "${PY}" -m brando_pa.pa21.macguard21 compare "${BASE}" ;;
  dashboard) "${PY}" -m brando_pa.pa21.dashboard21 ;;
  logs)      tail -n "${2:-30}" "${D}/service.log" 2>/dev/null || echo "no service.log yet"; echo "--- runs"; tail -n 5 "${D}/runs.jsonl" 2>/dev/null | cut -c1-240 ;;
  stop)      launchctl bootout "gui/${UIDN}/${LABEL}" 2>/dev/null && echo "stopped ${LABEL}" || echo "${LABEL} was not loaded" ;;
  rollback)  echo "== rollback: stop ${LABEL}, move its plist to a backup folder, verify production untouched. Records in ${HERE} are kept."
             launchctl bootout "gui/${UIDN}/${LABEL}" 2>/dev/null || true
             BK="${HOME}/brando_pa21_rollback_$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "${BK}"; [ -f "${PLIST}" ] && mv "${PLIST}" "${BK}/" || true
             echo "plist moved to ${BK}"; [ -f "${BASE}" ] && "${PY}" -m brando_pa.pa21.macguard21 compare "${BASE}" || echo "no baseline to compare"
             echo "To remove the observer completely (manual): mv ${HERE} ${BK}/  (the production checkout ${PROD} was never modified)" ;;
  *) echo "usage: ${0} {preflight|prodcheck|dryrun|install|start|verify|restart-test|accept-begin|report|accept72|status|health|once|evaluate|guard|dashboard|logs [n]|stop|rollback}"; exit 2 ;;
esac
