# 真实数据连接报告 (Phase 1)

- 生成时间: 2026-10-25T06:03:35+00:00
- 总体: **DEGRADED**  (7/8 OK)

| 检查项 | 分类 | HTTP | 延迟ms | 区块 | 数据延迟s | 错误 |
|--------|------|------|--------|------|-----------|------|
| RPC ethereum | UPSTREAM_FAIL | 525 | 597 | - | - | HTTP 525: <none> |
| RPC base | OK | 200 | 172 | 53059433 | 0 |  |
| RPC arbitrum | OK | 200 | 168 | 518061678 | 0 |  |
| DefiLlama price (current) | OK | 200 | 194 | - | - |  |
| DefiLlama price (historical) | OK | 200 | 302 | - | - |  |
| Snapshot governance | OK | 200 | 421 | - | - |  |
| CoinGecko ping | OK | 200 | 200 | - | - |  |
| CoinGecko market_chart (hist price+vol) | OK | 200 | 208 | - | - |  |

> 分类说明：NETWORK_POLICY_DENIED=出口代理 403（需改云环境网络权限）；API_AUTH=接口 401/403（需密钥/权限）；RATE_LIMITED=429 限速；UPSTREAM_FAIL=5xx 接口故障；STALE=数据过旧。网络不可用时不生成任何行情。