#!/usr/bin/env bash
# Standalone continuous runner (foreground). Ctrl-C to stop; resumes from DB.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
export PATH="/root/.foundry/bin:$PATH"
export PYTHONPATH="$HERE/tool:${PYTHONPATH:-}"
DB="${DEXMON_DB:-$HERE/reports_out/dex_monitor.db}"
exec python3 -m dao_audit dex-run --db "$DB" --poll "${POLL:-10}" --rate-limit "${RATE:-2}"
