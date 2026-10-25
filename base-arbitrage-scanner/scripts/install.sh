#!/usr/bin/env bash
# Install dependencies and compile the simulation contracts (offline, solc-js). Node >= 22.13 required (node:sqlite).
set -euo pipefail
cd "$(dirname "$0")/.."
node -e 'const [maj,min]=process.versions.node.split(".").map(Number); if (maj<22 || (maj===22&&min<13)) { console.error("Node >=22.13 required (node:sqlite), got", process.version); process.exit(1);} '
npm install
npm run compile-contracts
[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example — fill BASE_RPC_URL"; }
if command -v anvil >/dev/null 2>&1; then echo "anvil found: $(anvil --version | head -1)"; else echo "anvil not found: fork simulation will use 'hardhat node' (set FORK_PROVIDER=anvil after installing Foundry: curl -L https://foundry.paradigm.xyz | bash && foundryup)"; fi
npm run -s cli -- env-check || true
