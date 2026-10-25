#!/usr/bin/env bash
# Full historical validation: 24h backtest first, then the 168h (7-day) extension. Both pin every read to the sampled block.
# Exits 2 with reports/BLOCKED-rpc-diagnosis.md when no healthy Base RPC is reachable (synthetic data is never substituted).
set -euo pipefail
cd "$(dirname "$0")/.."
SAMPLE="${SAMPLE:-30}"           # blocks between samples (30 = one sample per minute on Base)
TS=$(date -u +%Y%m%dT%H%M%SZ)
npm run -s cli -- rpc-check
npm run -s cli -- backtest --hours 24  --sample "$SAMPLE" --run "bt24h-$TS"
npm run -s cli -- backtest --hours 168 --sample "$SAMPLE" --run "bt168h-$TS"
npm run -s cli -- export-csv --run "bt24h-$TS"  --out "reports/csv-bt24h-$TS"
npm run -s cli -- export-csv --run "bt168h-$TS" --out "reports/csv-bt168h-$TS"
echo "reports: reports/backtest-24h-bt24h-$TS.md  reports/backtest-168h-bt168h-$TS.md"
