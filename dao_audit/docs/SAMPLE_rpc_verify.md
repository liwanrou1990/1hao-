# RPC 多节点验证报告

- 生成时间: 2026-10-25T06:11:31+00:00

## ethereum (expected chainId=1)

| 端点 | 成功率 | chainId | 匹配 | 区块 | 数据延迟s | 平均延迟ms | 错误 |
|------|--------|---------|------|------|-----------|------------|------|
| https://eth.llamarpc.com | 0% | None | ? | - | - | - | HTTP 525: <none> |
| https://cloudflare-eth.com | 0% | 1 | ✓ | - | - | - | rpc error: {'code': -32046, 'message': ' |
| https://rpc.ankr.com/eth | 0% | None | ? | - | - | - | rpc error: {'code': -32000, 'message': ' |

- 失败切换实际由: `全部失败` 提供 (status=UNAVAILABLE)
- 建议主用端点: `无健康端点`
- Alchemy 备用未激活：环境变量 ALCHEMY_ETH_URL 未设置（且需放行 *.g.alchemy.com）

## base (expected chainId=8453)

| 端点 | 成功率 | chainId | 匹配 | 区块 | 数据延迟s | 平均延迟ms | 错误 |
|------|--------|---------|------|------|-----------|------------|------|
| https://mainnet.base.org | 100% | 8453 | ✓ | 53059675 | 0 | 153 |  |

- 失败切换实际由: `https://mainnet.base.org` 提供 (status=OK)
- 建议主用端点: `https://mainnet.base.org`

## arbitrum (expected chainId=42161)

| 端点 | 成功率 | chainId | 匹配 | 区块 | 数据延迟s | 平均延迟ms | 错误 |
|------|--------|---------|------|------|-----------|------------|------|
| https://arb1.arbitrum.io/rpc | 100% | 42161 | ✓ | 518063299 | 1 | 179 |  |

- 失败切换实际由: `https://arb1.arbitrum.io/rpc` 提供 (status=OK)
- 建议主用端点: `https://arb1.arbitrum.io/rpc`
- Alchemy 备用未激活：环境变量 ALCHEMY_ARB_URL 未设置（且需放行 *.g.alchemy.com）
