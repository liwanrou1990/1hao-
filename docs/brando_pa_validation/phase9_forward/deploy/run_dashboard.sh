#!/usr/bin/env bash
# 启动 Dashboard（前台）。由 launchd/systemd 调用；默认只绑定 127.0.0.1。
set -u
cd "$(dirname "$0")/.."
[ -f "${SPOT90_ENV_FILE:-$HOME/.config/spot90/env}" ] && set -a && . "${SPOT90_ENV_FILE:-$HOME/.config/spot90/env}" && set +a
export PYTHONPATH=src
PY="${SPOT90_PYTHON:-$(pwd)/.venv/bin/python}"; [ -x "$PY" ] || PY=python3
exec "$PY" -m streamlit run src/spot90/dashboard/app.py --server.port "${SPOT90_PORT:-8501}" --server.address "${SPOT90_BIND:-127.0.0.1}" \
  --server.headless true --browser.gatherUsageStats false --server.enableXsrfProtection true --server.enableCORS false
