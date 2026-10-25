# RPC diagnosis (2026-10-25T01:47:01.671Z)

| endpoint | class | via proxy | detail |
|---|---|---|---|
| https://mainnet.base.org | **EGRESS_POLICY_DENIED** | yes | proxy refused CONNECT mainnet.base.org:443 → HTTP/1.1 403 Forbidden (cloud environment network policy; add the host to Allowed domains) |
| https://base-rpc.publicnode.com | **EGRESS_POLICY_DENIED** | yes | proxy refused CONNECT base-rpc.publicnode.com:443 → HTTP/1.1 403 Forbidden (cloud environment network policy; add the host to Allowed domains) |
| https://base.drpc.org | **EGRESS_POLICY_DENIED** | yes | proxy refused CONNECT base.drpc.org:443 → HTTP/1.1 403 Forbidden (cloud environment network policy; add the host to Allowed domains) |

- EGRESS_POLICY_DENIED: the cloud environment network policy refused the tunnel. Fix in the environment settings (Network access → Allowed domains), not in code. Nothing reached the RPC provider.
