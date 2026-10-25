# Historical backtest report — 2h (OFFLINE_FIXTURE)

- run_id: `offline-bt-2h`  mode: **backtest**  data source: **OFFLINE_FIXTURE**
- generated: 2026-10-25T01:11:16.232Z

> **WARNING: data_source=OFFLINE_FIXTURE.** This run did NOT use Base mainnet data. Numbers below exercise the pipeline on synthetic liquidity and carry no information about real arbitrage opportunities.
- sampling rule: every 30 blocks (60s) over 2h: blocks 51766535..51770135, 121 samples
- simulation: FIXTURE_SEQUENTIAL (synthetic)

## Scan coverage

| metric | value |
|---|---|
| blocks scanned | 121 |
| block range | 51766535 .. 51770135 |
| time range (UTC) | 2026-10-24T23:11:10.000Z .. 2026-10-25T01:11:10.000Z |
| verified pools (total / avg active per block) | 11 / 10.0 |
| routes per block (avg) | 48.0 |
| route×amount evaluations | 36549 |
| candidates (quote net > 0) | 667 |
| simulated | 129 |
| simulation passed | 129 |
| executable (FORK_ATOMIC pass AND net ≥ threshold) | 0 |
| Aave FLASHLOAN_PREMIUM_TOTAL (bps) | 5 |
| ETH/USDC used for gas (avg) | 3900.02 |

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
| theoretical spread | mid-price difference, zero fee / zero impact — NOT tradable | avg 118.3660 USD |
| quoted net profit | ExactInput quotes − flash premium − gas(L2+L1) − buffer | max 721.8808 / avg 24.6178 USD, 667 of 2685 evaluations > 0 |
| simulated net profit | atomic path on fork/sim − gas − buffer | max 721.8797 / avg 137.9087 USD, 129 of 129 simulations passed |
| real executed profit | actual on-chain fills | **none — phase 1 sends no transactions** |

## Optimal borrow amount

| borrow USDC | evaluations | avg quoted net | max quoted net | avg sim net | max sim net |
|---|---|---|---|---|---|
| 1,000 | 2096 | -2.1948 | 4.9425 | 1.1159 | 1.9984 |
| 10,000 | 86 | 14.8727 | 48.4100 | 2.5504 | 6.6640 |
| 50,000 | 62 | 78.7671 | 225.2659 | 24.5655 | 40.3928 |
| 100,000 | 44 | 155.7842 | 408.4668 | 77.0019 | 127.1335 |
| 500,000 | 3 | 430.2468 | 581.4955 | n/a | n/a |

Best single evaluation (ladder + golden-section optimizer): borrow **292,504.271 USDC** at block 51768335, quoted net 721.8808 USD, sim net 721.8797 USD — USDC->WETH->USDC | AERODROME_CL WETH/USDC 0.04% ts100 => UNISWAP_V3 WETH/USDC 0.30% ts60

## Cost composition

| component | avg over evaluations (USD) | best evaluation (USD) |
|---|---|---|
| theoretical spread | 118.3660 | 2737.0731 |
| gross profit (quotes; fees+impact embedded) | 45.9016 | 1014.4002 |
| DEX fees (embedded) | 43.2193 | 993.8079 |
| price impact (embedded) | 29.4808 | 728.8650 |
| Aave flash premium | 10.6330 | 146.2521 |
| gas L2 execution | 0.0177 | 0.0151 |
| gas L1 data fee | 0.0000 | 0.0000 |
|   of which priority fee | 0.0020 | 0.0017 |
| execution buffer | 10.6330 | 146.2521 |
| total explicit cost | 21.2838 | 292.5194 |
| net profit | 24.6178 | 721.8808 |

Best evaluation: block 51768335, borrow 292,504.271 USDC — USDC->WETH->USDC | AERODROME_CL WETH/USDC 0.04% ts100 => UNISWAP_V3 WETH/USDC 0.30% ts60

## Failure distribution

**Quote stage (route×amount evaluations, stored subset):**

- ok: 2685

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