# 最近扫描结果与阻塞项（更新于 2026-10-25 01:47 UTC）

## 状态：真实 Base 主网验证 **BLOCKED**

| 项目 | 结果 |
|---|---|
| 真实采样区块数 | **0** |
| 真实池数量 / 候选数 / fork 成功数 | **0 / 0 / 0**（未运行） |
| `VERIFIED_FORK` 机会 | **0**（未运行，非"验证为无"） |
| `LIVE_EXECUTED` | **0**（第一阶段设计上永不广播） |
| 24h / 168h 历史回测 | **未运行（BLOCKED）** |

### 阻塞原因诊断（`npm run -s cli -- rpc-check`，`reports/rpc-diagnosis.md`）

| 端点 | 分类 | 经代理 | 详情 |
|---|---|---|---|
| https://mainnet.base.org | **EGRESS_POLICY_DENIED** | 是 | 代理对 CONNECT 返回 `HTTP/1.1 403 Forbidden` |
| https://base-rpc.publicnode.com | **EGRESS_POLICY_DENIED** | 是 | 同上 |
| https://base.drpc.org | **EGRESS_POLICY_DENIED** | 是 | 同上 |

区分依据：
- 不是 DNS 问题：容器没有直接出站（绕开代理直连时连 `mainnet.base.org` 都无法解析，这是容器设计），所有流量必须走 `HTTPS_PROXY`。
- 不是服务商授权问题：403 来自代理网关的 CONNECT 响应，TLS 隧道从未建立，请求根本没有到达任何 RPC 服务商；同一代理对 `api.github.com` 返回 200，说明代理本身工作正常。
- 不是代码问题：`.env` 与环境变量中也没有任何 `BASE_RPC_URL`，即便有也会被同一策略拦截。
- 结论：**云环境出站网络策略拒绝**。解除方式：在会话标题栏云环境菜单 → Edit → Network access 放宽级别，或在 Allowed domains 加入你的 RPC 域名（保留"允许包管理器"）。代码没有也不会绕过该限制。

### 解除后一键运行

```bash
echo 'BASE_RPC_URLS=https://<your-archive-rpc>,https://mainnet.base.org' >> .env
npm run -s cli -- rpc-check && npm run discover
scripts/run_scan.sh                 # 实时扫描 + fork 原子模拟
scripts/run_backtest_full.sh        # 24h → 168h 回测 + CSV
npm test                            # 含 test/forkMainnet.integration.test.ts（真实链 fork 集成测试）
```

## 本轮新增（已通过测试，28 项：25 通过 + 3 项主网 fork 测试 BLOCKED 跳过）

- RPC 健康检查与故障切换：DNS / 代理 CONNECT / 源站状态码 / chainId / 区块高度 / archive 探测；分类 `OK | EGRESS_POLICY_DENIED | DNS_UNRESOLVED | PROVIDER_AUTH_DENIED | PROVIDER_RATE_LIMITED | WRONG_CHAIN | RPC_ERROR | UNREACHABLE`；健康端点按 archive、延迟排序进入 viem fallback。
- 四状态记录 `THEORETICAL_SPREAD / QUOTED_ESTIMATE / VERIFIED_FORK / LIVE_EXECUTED`（列 `opportunity_state`），报告按状态计数；合成数据最高只到 `QUOTED_ESTIMATE`。
- `opportunities` 表：同路线同金额连续采样合并去重，记录首末区块/时间、采样次数、跨度、最高状态、最佳金额；`invalidations` 表：上一块最佳候选在下一块复核，统计因状态变化/竞争失效次数。
- 报告新增：真实采样区块数、净利润分布直方图、预计交易成本（含 fork 实测 gas）、机会持续时间与失效、`VERIFIED_FORK` 表（区块、时间戳、池地址、路线、借款、成本、净利润）。
- `backtest`/`scan` 无健康 RPC 时退出码 2 并写 `reports/BLOCKED-rpc-diagnosis.md`（已实跑验证）。
- 安全开关 `LIVE_BROADCAST`/`AUTO_DEPLOY` 默认 false，置 true 拒绝启动。
- 新增 `test/forkMainnet.integration.test.ts`：有 RPC 时在真实链 fork 上验证部署、发现池、读取真实 Aave 费率、对最佳两跳路线做原子模拟并要求可解码结论（通过或 `Unprofitable` 差额）；fork 报价与引擎报价需一致。

## 离线流程验证（OFFLINE_FIXTURE，合成数据，不是市场，不可替代真实验证）

见 `reports/backtest-2h-offline-bt-2h.md`、`reports/live-offline-scan-test.md` 与本地 EVM 集成测试（`test/localEvm.integration.test.ts`：真实 Uniswap V3 字节码 + Mock Aave 上 10,000 USDC 原子路径通过、反向路线 `Unprofitable` 解码、大额 ROI 递减）。这些结果只证明流程与数学，`VERIFIED_FORK` 与 `LIVE_EXECUTED` 在其中恒为 0。
