# 真实数据连接报告 (Phase 1)

- 生成时间: 2026-10-25T05:27:34+00:00
- 总体: **NETWORK_POLICY_DENIED**  (0/8 OK)

| 检查项 | 分类 | HTTP | 延迟ms | 区块 | 数据延迟s | 错误 |
|--------|------|------|--------|------|-----------|------|
| RPC ethereum | NETWORK_POLICY_DENIED | - | 272 | - | - | URLError: <urlopen error Tunnel connection failed: 403 Forbi |
| RPC base | NETWORK_POLICY_DENIED | - | 246 | - | - | URLError: <urlopen error Tunnel connection failed: 403 Forbi |
| RPC arbitrum | NETWORK_POLICY_DENIED | - | 244 | - | - | URLError: <urlopen error Tunnel connection failed: 403 Forbi |
| DefiLlama price (current) | NETWORK_POLICY_DENIED | - | 214 | - | - | URLError: <urlopen error Tunnel connection failed: 403 Forbi |
| DefiLlama price (historical) | NETWORK_POLICY_DENIED | - | 245 | - | - | URLError: <urlopen error Tunnel connection failed: 403 Forbi |
| Snapshot governance | NETWORK_POLICY_DENIED | - | 263 | - | - | URLError: <urlopen error Tunnel connection failed: 403 Forbi |
| CoinGecko ping | NETWORK_POLICY_DENIED | - | 260 | - | - | URLError: <urlopen error Tunnel connection failed: 403 Forbi |
| CoinGecko market_chart (hist price+vol) | NETWORK_POLICY_DENIED | - | 221 | - | - | URLError: <urlopen error Tunnel connection failed: 403 Forbi |

## 被网络策略拒绝的主机

- `api.coingecko.com`
- `arb1.arbitrum.io`
- `coins.llama.fi`
- `eth.llamarpc.com`
- `hub.snapshot.org`
- `mainnet.base.org`

## 需要的云环境权限修改

云环境出口策略拒绝了这些主机（代理 CONNECT 403）。在会话标题栏的云环境菜单→Edit→Network access 中放宽级别，或将被拒主机加入 Allowed domains（保留 Allow package managers 勾选）。策略在容器启动时固定，改动后需开新会话生效。文档 https://code.claude.com/docs/en/cloud-environments#network-access

> 分类说明：NETWORK_POLICY_DENIED=出口代理 403（需改云环境网络权限）；API_AUTH=接口 401/403（需密钥/权限）；RATE_LIMITED=429 限速；UPSTREAM_FAIL=5xx 接口故障；STALE=数据过旧。网络不可用时不生成任何行情。