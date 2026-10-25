# BLOCKED: Base mainnet RPC unavailable

- checked at: 2026-10-25T01:47:12.386Z
- consequence: no real-data scan/backtest was run. Synthetic data is NOT used as a substitute (use --offline explicitly for pipeline tests; its output is labeled OFFLINE_FIXTURE).

| endpoint | class | via proxy | detail |
|---|---|---|---|
| https://mainnet.base.org | **EGRESS_POLICY_DENIED** | yes | proxy refused CONNECT mainnet.base.org:443 → HTTP/1.1 403 Forbidden (cloud environment network policy; add the host to Allowed domains) |

- EGRESS_POLICY_DENIED: the cloud environment network policy refused the tunnel. Fix in the environment settings (Network access → Allowed domains), not in code. Nothing reached the RPC provider.
