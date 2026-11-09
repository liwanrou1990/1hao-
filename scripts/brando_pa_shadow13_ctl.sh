#!/usr/bin/env bash
# Phase 13 shadow forward service control (independent from com.brando-pa.forward). macOS bash 3.2 compatible: ${VAR} everywhere, ASCII output.
# Usage: bash scripts/brando_pa_shadow13_ctl.sh {once|start|stop|restart|status|health|audit|report|weekly|dashboard|isolation|logs [n]}
set -euo pipefail
cd "$(dirname "${0}")/.."; LABEL="com.brando-pa.shadow13"; PY="${BRANDO_PY:-${PWD}/.venv/bin/python}"; [ -x "${PY}" ] || PY="$(command -v python3)"
export PYTHONPATH=src
D="reports_out/brando_pa/phase13_forward"
case "${1:-status}" in
  once)      "${PY}" -m brando_pa.cli phase13-service --once ;;
  start)     launchctl bootstrap "gui/$(id -u)" "${HOME}/Library/LaunchAgents/${LABEL}.plist" 2>/dev/null || true; launchctl kickstart "gui/$(id -u)/${LABEL}" ;;
  stop)      launchctl bootout "gui/$(id -u)/${LABEL}" ;;
  restart)   launchctl kickstart -k "gui/$(id -u)/${LABEL}" ;;
  status)    launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | grep -E "state|pid|last exit" | head -5 || echo "launchd job not loaded (${LABEL})"; cat "${D}/health.json" 2>/dev/null | head -40 || echo "no health.json yet" ;;
  health)    "${PY}" - <<'PYH'
import json, sys, pandas as pd
from pathlib import Path
p = Path("reports_out/brando_pa/phase13_forward/health.json")
if not p.exists(): print("no heartbeat yet"); sys.exit(1)
h = json.loads(p.read_text()); age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(h["heartbeat_utc"])).total_seconds()
print(json.dumps({"heartbeat_age_s": round(age), "n_cycles": h.get("n_cycles"), "consecutive_failures": h.get("consecutive_failures"), "last_ok_cycle_at": h.get("last_ok_cycle_at"), "ok": age < 2 * h.get("interval_s", 600) and h.get("consecutive_failures", 0) < 3}, ensure_ascii=False, indent=1))
sys.exit(0 if age < 2 * h.get("interval_s", 600) else 1)
PYH
             ;;
  audit)     "${PY}" -m brando_pa.cli phase13-audit ;;
  report)    "${PY}" -m brando_pa.cli phase13-report ;;
  weekly)    "${PY}" -m brando_pa.cli phase13-report --weekly ;;
  dashboard) "${PY}" -m brando_pa.cli phase13-dashboard ;;
  isolation) "${PY}" -m brando_pa.cli phase13-isolation-check ;;
  logs)      tail -n "${2:-50}" "${D}/service.log" 2>/dev/null || echo "no service.log yet"; echo "--- alerts"; tail -n 10 "${D}/alerts.jsonl" 2>/dev/null || true ;;
  *) echo "usage: ${0} {once|start|stop|restart|status|health|audit|report|weekly|dashboard|isolation|logs [n]}"; exit 2 ;;
esac
