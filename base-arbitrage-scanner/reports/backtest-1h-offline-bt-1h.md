# Historical backtest report — 1h (OFFLINE_FIXTURE)

- run_id: `offline-bt-1h`  mode: **backtest**  data source: **OFFLINE_FIXTURE**
- generated: 2026-10-25T01:16:05.385Z

> **WARNING: data_source=OFFLINE_FIXTURE.** This run did NOT use Base mainnet data. Numbers below exercise the pipeline on synthetic liquidity and carry no information about real arbitrage opportunities.
- sampling rule: every 60 blocks (120s) over 1h: blocks 51768481..51770281, 31 samples
- simulation: disabled (--no-sim)

## Scan coverage

| metric | value |
|---|---|
| blocks scanned | 31 |
| block range | 51768481 .. 51770281 |
| time range (UTC) | 2026-10-25T00:16:02.000Z .. 2026-10-25T01:16:02.000Z |
| verified pools (total / avg active per block) | 11 / 10.0 |
| routes per block (avg) | 48.0 |
| route×amount evaluations | 9441 |
| candidates (quote net > 0) | 207 |
| simulated | 0 |
| simulation passed | 0 |
| executable (FORK_ATOMIC pass AND net ≥ threshold) | 0 |
| Aave FLASHLOAN_PREMIUM_TOTAL (bps) | 5 |
| ETH/USDC used for gas (avg) | 3899.77 |

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
| theoretical spread | mid-price difference, zero fee / zero impact — NOT tradable | avg 189.3900 USD |
| quoted net profit | ExactInput quotes − flash premium − gas(L2+L1) − buffer | max 641.4044 / avg 34.7367 USD, 207 of 710 evaluations > 0 |
| simulated net profit | atomic path on fork/sim − gas − buffer | max n/a / avg n/a USD, 0 of 0 simulations passed |
| real executed profit | actual on-chain fills | **none — phase 1 sends no transactions** |

## Optimal borrow amount (all route×amount evaluations, per block aggregates)

| borrow USDC | evaluations | quotes ok | avg quoted net (USD) | max quoted net (USD) | blocks with best net > 0 | max simulated profit (USD, pre-gas) | top failure reasons |
|---|---|---|---|---|---|---|---|
| 1,000 | 1488 | 1488 | -4.6528 | 5.6494 | 7 / 31 | n/a | - |
| 10,000 | 1488 | 1488 | -81.0496 | 55.7447 | 7 / 31 | n/a | - |
| 50,000 | 1488 | 1488 | -1137.7740 | 249.2974 | 7 / 31 | n/a | - |
| 100,000 | 1488 | 1488 | -3918.0570 | 424.5432 | 6 / 31 | n/a | - |
| 500,000 | 1488 | 1488 | -62790.1903 | 617.3092 | 1 / 31 | n/a | - |
| 1,000,000 | 1488 | 1488 | -186862.0843 | -590.0286 | 0 / 31 | n/a | - |

Best single evaluation (ladder + golden-section optimizer): borrow **416,991.457 USDC** at block 51768481, quoted net 641.4044 USD, sim net n/a USD — USDC->WETH->USDC | AERODROME_CL WETH/USDC 0.04% ts100 => UNISWAP_V3 WETH/USDC 0.05% ts10

## Cost composition

| component | avg over evaluations (USD) | best evaluation (USD) |
|---|---|---|
| theoretical spread | 189.3900 | 2072.6080 |
| gross profit (quotes; fees+impact embedded) | 63.8039 | 1058.4554 |
| DEX fees (embedded) | 81.9707 | 376.0629 |
| price impact (embedded) | 43.8530 | 638.0897 |
| Aave flash premium | 14.5025 | 208.4957 |
| gas L2 execution | 0.0176 | 0.0151 |
| gas L1 data fee | 0.0445 | 0.0444 |
|   of which priority fee | 0.0020 | 0.0017 |
| execution buffer | 14.5025 | 208.4957 |
| total explicit cost | 29.0672 | 417.0510 |
| net profit | 34.7367 | 641.4044 |

Best evaluation: block 51768481, borrow 416,991.457 USDC — USDC->WETH->USDC | AERODROME_CL WETH/USDC 0.04% ts100 => UNISWAP_V3 WETH/USDC 0.05% ts10

## Failure distribution

**Quote stage (route×amount evaluations, stored subset):**

- ok: 710

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