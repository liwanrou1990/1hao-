# SushiSwap RouteProcessor2 approval drain (2023-04-09) — vulnerable sample (round4)

- repo: https://github.com/sushiswap/sushiswap
- commit: ab5297698b1284667684393b8b9add48ba979c00 (parent of the fix commit)
- fix commit: bb343a0c70ab070dbcb299ab993c25815e2d1083
- incident / disclosure date: 2023-04-09
- pragma (main file RouteProcessor2.sol): 0.8.10
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - IUniswapV3Pool.sol -> protocols/route-processor/interfaces/IUniswapV3Pool.sol
  - InputStream.sol -> protocols/route-processor/contracts/InputStream.sol
  - RouteProcessor2.sol -> protocols/route-processor/contracts/RouteProcessor2.sol
- reports / references:
  - https://rekt.news/sushi-yoink-rekt
  - https://blocksec.com/blog/8-sushi-swap-incident-a-clumsy-rescue-attempt-leads-to-a-series-of-copycat-attacks
  - https://github.com/sushiswap/sushiswap/commit/bb343a0c70ab070dbcb299ab993c25815e2d1083

## Description

Unvalidated pool address in the route plus a callback that transfers from a data-supplied address let attackers drain approvals given to the 4-day-old RouteProcessor2.

## Verification notes

Fix commit bb343a0c ('fix: RP2 approval abuse') read: swapUniV3 now pulls from msg.sender only and the callback no longer takes `from` from data.
