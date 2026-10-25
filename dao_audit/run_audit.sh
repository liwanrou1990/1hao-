#!/usr/bin/env bash
# X100 DAO governance audit — one-shot runner.
# Defensive/research use: authorized contracts only, LOCAL test chain only,
# simulated vaults, no real assets, no mainnet transactions.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
export PATH="/root/.foundry/bin:$PATH"
export FOUNDRY_PROFILE="${FOUNDRY_PROFILE:-offline}"   # sandbox: vendored solc; CI overrides to default
export PYTHONPATH="$HERE/tool:${PYTHONPATH:-}"

MODE="${1:-inprocess}"   # inprocess | anvil

echo "== forge build =="
forge build >/dev/null

if [[ "$MODE" == "anvil" ]]; then
  echo "== launching LOCAL anvil test chain (127.0.0.1:8545) =="
  anvil --silent --port 8545 >/tmp/anvil_audit.log 2>&1 &
  ANVIL_PID=$!
  trap 'kill $ANVIL_PID 2>/dev/null || true' EXIT
  sleep 3
  python3 -m dao_audit --project . audit --rpc-url http://127.0.0.1:8545
else
  # Default: forge's in-process local EVM (deterministic, no network).
  python3 -m dao_audit --project . audit
fi
