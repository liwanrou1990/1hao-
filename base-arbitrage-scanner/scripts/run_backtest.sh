#!/usr/bin/env bash
# Historical backtest. Usage: scripts/run_backtest.sh [--hours 24] [--sample 30] [--max-blocks N] [--offline]
# Default: last 24h sampled every 30 blocks; run again with --hours 168 to extend to 7 days once 24h completed.
set -euo pipefail
cd "$(dirname "$0")/.."
exec npm run -s cli -- backtest "${@:---hours 24}"
