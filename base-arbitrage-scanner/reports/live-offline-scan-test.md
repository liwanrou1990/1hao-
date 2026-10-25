# Live scan report (OFFLINE_FIXTURE)

- run_id: `offline-scan-test`  mode: **live**  data source: **OFFLINE_FIXTURE**
- generated: 2026-10-25T01:08:17.520Z

> **WARNING: data_source=OFFLINE_FIXTURE.** This run did NOT use Base mainnet data. Numbers below exercise the pipeline on synthetic liquidity and carry no information about real arbitrage opportunities.
- simulation: FIXTURE_SEQUENTIAL (synthetic)
- blocks processed: 5

## Scan coverage

| metric | value |
|---|---|
| blocks scanned | 5 |
| block range | 51770047 .. 51770051 |
| time range (UTC) | 2026-10-25T01:08:14.000Z .. 2026-10-25T01:08:22.000Z |
| verified pools (total / avg active per block) | 11 / 11.0 |
| routes per block (avg) | 64.0 |
| route×amount evaluations | 2037 |
| candidates (quote net > 0) | 30 |
| simulated | 19 |
| simulation passed | 19 |
| executable (FORK_ATOMIC pass AND net ≥ threshold) | 0 |
| Aave FLASHLOAN_PREMIUM_TOTAL (bps) | 5 |
| ETH/USDC used for gas (avg) | 3909.35 |

Pools by DEX: AERODROME_CL=2, AERODROME_V2=3, UNISWAP_V3=6

<details><summary>pool list</summary>

- AERODROME_CL USDC/cbBTC 0.05% ts100 `0x0000000000000000000000229351722f56F0D0F3`
- AERODROME_CL WETH/USDC 0.04% ts100 `0x00000000000000000000001D9351722AFD8C15E4`
- AERODROME_V2 USDC/USDT stable 0.05% `0x0000000000000000000000209351722d99955315`
- AERODROME_V2 WETH/USDC volatile 0.30% `0x00000000000000000000001E9351722bDc39defb`
- AERODROME_V2 WETH/cbBTC volatile 0.30% `0x00000000000000000000002593517231F2fA1784`
- UNISWAP_V3 USDC/USDT 0.01% ts1 `0x00000000000000000000001f9351722CbaE79296`
- UNISWAP_V3 USDC/cbBTC 0.30% ts60 `0x0000000000000000000000219351722e78431BC8`
- UNISWAP_V3 WETH/USDC 0.05% ts10 `0x00000000000000000000001B935172294030986a`
- UNISWAP_V3 WETH/USDC 0.30% ts60 `0x00000000000000000000001C9351722a1EdE611D`
- UNISWAP_V3 WETH/USDT 0.05% ts10 `0x00000000000000000000002493517231144C4ed1`
- UNISWAP_V3 WETH/cbBTC 0.05% ts10 `0x00000000000000000000002393517230359e8FE2`

</details>

## Profit summary (three distinct notions)

| notion | definition | value |
|---|---|---|
| theoretical spread | mid-price difference, zero fee / zero impact — NOT tradable | avg 108.0505 USD |
| quoted net profit | ExactInput quotes − flash premium − gas(L2+L1) − buffer | max 140.8700 / avg 19.4489 USD, 30 of 99 evaluations > 0 |
| simulated net profit | atomic path on fork/sim − gas − buffer | max 140.8684 / avg 75.1805 USD, 19 of 19 simulations passed |
| real executed profit | actual on-chain fills | **none — phase 1 sends no transactions** |

## Optimal borrow amount

| borrow USDC | evaluations | avg quoted net | max quoted net | avg sim net | max sim net |
|---|---|---|---|---|---|
| 1,000 | 71 | -2.1183 | 2.3380 | 0.4456 | 0.8766 |
| 10,000 | 4 | 12.6934 | 22.6775 | 8.1173 | 8.1173 |
| 50,000 | 3 | 66.4350 | 93.5877 | 21.8911 | 21.8911 |
| 100,000 | 2 | 129.0964 | 137.5453 | 129.0948 | 137.5437 |

Best single evaluation (ladder + golden-section optimizer): borrow **125,487.186 USDC** at block 51770051, quoted net 140.8700 USD, sim net 140.8684 USD — USDC->cbBTC->WETH->USDC | AERODROME_CL USDC/cbBTC 0.05% ts100 => UNISWAP_V3 WETH/cbBTC 0.05% ts10 => AERODROME_CL WETH/USDC 0.04% ts100

## Cost composition

| component | avg over evaluations (USD) | best evaluation (USD) |
|---|---|---|
| theoretical spread | 108.0505 | 599.2223 |
| gross profit (quotes; fees+impact embedded) | 44.1185 | 266.3764 |
| DEX fees (embedded) | 35.8644 | 175.7597 |
| price impact (embedded) | 28.1877 | 157.0861 |
| Aave flash premium | 12.3257 | 62.7436 |
| gas L2 execution | 0.0181 | 0.0192 |
| gas L1 data fee | 0.0000 | 0.0000 |
|   of which priority fee | 0.0020 | 0.0021 |
| execution buffer | 12.3257 | 62.7436 |
| total explicit cost | 24.6696 | 125.5064 |
| net profit | 19.4489 | 140.8700 |

Best evaluation: block 51770051, borrow 125,487.186 USDC — USDC->cbBTC->WETH->USDC | AERODROME_CL USDC/cbBTC 0.05% ts100 => UNISWAP_V3 WETH/cbBTC 0.05% ts10 => AERODROME_CL WETH/USDC 0.04% ts100

## Failure distribution

**Quote stage (route×amount evaluations, stored subset):**

- ok: 99

**Per-block aggregated failure counters (all evaluations):**

- none

**Simulation stage:**

- none

**RPC / block failures:**

- none

## Verified positive-return opportunities

**None can exist in this run: data_source=OFFLINE_FIXTURE is synthetic.** The 0 rows flagged "executable" below only demonstrate the pipeline on synthetic liquidity.

---
Definitions: *theoretical spread* = displayed-price difference (never used to flag opportunities); *simulated profit* = output of the atomic flash-loan path simulation; *real profit* = none (no transactions sent in phase 1).