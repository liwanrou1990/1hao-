# V0.18 fixed-version invariant checks (local isolated EVM, Foundry)

Only FIXED versions are exercised; the checks assert that the public fix holds
(unauthorized / out-of-state calls revert). Vulnerable versions are never run.
Compiler: exact project-pinned solc via the solc-js shim (`forge --use <shim>`).
Layout per case: `foundry.toml` (minimal project: src dir limited to the
governance contracts, libs from the full-tree snapshot), the test file, and
`result.json`. Reproduce: `setup_snapshot.sh <bare-repo> <commit> <dest>`
(full tree + submodules at gitlink commits + forge-std/lib/ds-test), symlink
`src` and `lib` into a minimal dir with the given foundry.toml, copy the test to
`v018test/`, then `forge test --use ~/.cache/dao_audit/shims/solc-<ver> --offline`.
