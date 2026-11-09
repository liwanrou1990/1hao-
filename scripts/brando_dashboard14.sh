#!/usr/bin/env bash
# Brando Investment Intelligence Dashboard (Phase 14, read-only) - local start/stop. macOS bash 3.2 compatible (${VAR}, ASCII output).
# Usage: bash scripts/brando_dashboard14.sh start|stop|restart|status|open|logs   Env: BRANDO_DASH_PORT (default 8514), BRANDO_DASH_HOST (fixed 127.0.0.1)
# Isolation: separate venv .venv-dashboard14 (never touches the production .venv), separate pid/log dir reports_out/brando_pa/dashboard14_cache, GET-only API, host 127.0.0.1 only.
set -euo pipefail
cd "$(dirname "${0}")/.."; ROOT="${PWD}"; PORT="${BRANDO_DASH_PORT:-8514}"; HOST="127.0.0.1"
VENV="${ROOT}/.venv-dashboard14"; CACHE="${ROOT}/reports_out/brando_pa/dashboard14_cache"; PIDF="${CACHE}/dashboard.pid"; LOG="${CACHE}/dashboard.log"; mkdir -p "${CACHE}"
PYBASE="${BRANDO_PY_BASE:-$(command -v python3)}"
ensure_venv() {
  if [ ! -x "${VENV}/bin/python" ]; then echo "[dash14] creating venv ${VENV}"; "${PYBASE}" -m venv "${VENV}"; fi
  if ! "${VENV}/bin/python" -c "import fastapi, uvicorn, plotly, pandas, pyarrow" 2>/dev/null; then echo "[dash14] installing deploy/requirements-dashboard14.txt"; "${VENV}/bin/python" -m pip install -q -r deploy/requirements-dashboard14.txt; fi
}
is_running() { [ -f "${PIDF}" ] && kill -0 "$(cat "${PIDF}")" 2>/dev/null; }
case "${1:-status}" in
  start)
    if is_running; then echo "[dash14] already running pid=$(cat "${PIDF}") http://${HOST}:${PORT}/"; exit 0; fi
    ensure_venv
    PYTHONPATH=src nohup "${VENV}/bin/python" -m brando_pa.dashboard14.api --host "${HOST}" --port "${PORT}" >> "${LOG}" 2>&1 &
    echo $! > "${PIDF}"; sleep 2
    if is_running; then echo "[dash14] started pid=$(cat "${PIDF}") -> http://${HOST}:${PORT}/  (log ${LOG})"; else echo "[dash14] failed to start; see ${LOG}"; tail -20 "${LOG}"; exit 1; fi ;;
  stop)
    if is_running; then kill "$(cat "${PIDF}")" && echo "[dash14] stopped pid=$(cat "${PIDF}")"; else echo "[dash14] not running"; fi; rm -f "${PIDF}" ;;
  restart) "${0}" stop; "${0}" start ;;
  status)
    if is_running; then echo "[dash14] running pid=$(cat "${PIDF}") http://${HOST}:${PORT}/"; curl -fsS "http://${HOST}:${PORT}/healthz" >/dev/null 2>&1 && echo "[dash14] healthz ok" || echo "[dash14] healthz not responding"; else echo "[dash14] not running"; fi ;;
  open) command -v open >/dev/null 2>&1 && open "http://${HOST}:${PORT}/" || echo "open http://${HOST}:${PORT}/ in your browser" ;;
  logs) tail -n "${2:-50}" "${LOG}" 2>/dev/null || echo "no log yet" ;;
  *) echo "usage: ${0} start|stop|restart|status|open|logs [n]"; exit 2 ;;
esac
