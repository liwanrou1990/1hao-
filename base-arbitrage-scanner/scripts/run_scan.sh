#!/usr/bin/env bash
# Live per-block scan with leaderboard. Usage: scripts/run_scan.sh [--blocks N] [--offline] [--no-sim]
set -euo pipefail
cd "$(dirname "$0")/.."
exec npm run -s cli -- scan "$@"
