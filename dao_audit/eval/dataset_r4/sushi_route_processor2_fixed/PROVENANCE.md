# SushiSwap RouteProcessor2 after approval-abuse fix — fixed sample (round4)

- repo: https://github.com/sushiswap/sushiswap
- commit: bb343a0c70ab070dbcb299ab993c25815e2d1083
- paired vulnerable case: sushi_route_processor2
- pragma (main file RouteProcessor2.sol): 0.8.10
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - IUniswapV3Pool.sol -> protocols/route-processor/interfaces/IUniswapV3Pool.sol
  - InputStream.sol -> protocols/route-processor/contracts/InputStream.sol
  - RouteProcessor2.sol -> protocols/route-processor/contracts/RouteProcessor2.sol

## Description

Fixed version (commit bb343a0c, 2023-04-09).
