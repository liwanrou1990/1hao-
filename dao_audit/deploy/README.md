# DEX 套利连续监控 — 部署指南（24–72h 常驻，v0.9 加固）

> 本 Claude 云会话**无法**保持 24–72h 常驻进程；以下为可直接部署到常驻 Linux VPS 的方案。
> 请勿把会话内的短验证跑当作连续监控。连续时长以监控 DB 的真实记录为准（`dex-acceptance`）。
> 只读监控：不连接钱包、不签名、不执行真实交易。

## 一键部署（systemd + 健康检查 + 日志轮转）
```bash
# 以 root 在仓库根目录运行；可用环境变量覆盖安装路径/账户/轮询参数
sudo APP_DIR=/opt/dao_audit DB_DIR=/var/lib/dexmon deploy/deploy.sh
```
`deploy.sh` 幂等，会：
1. 建立无登录系统账户 `dexmon`，拷贝代码到 `APP_DIR`、建立可写状态目录 `DB_DIR`；
2. 安装 `pyyaml`，探测 `cast`（报价所需）路径并写入服务 PATH；
3. 安装并启用 `dexmon.service`（崩溃自启 `Restart=always`，日志落 `DB_DIR/dexmon.log`，
   `ProtectSystem=strict` 仅 `DB_DIR` 可写）；
4. 安装 `dexmon-health.timer`（每 5 分钟健康检查）；
5. 安装 `/etc/logrotate.d/dexmon`（按天轮转、保留 14 份、压缩、`copytruncate`）。

## 手动运行（前台，调试用）
```bash
cd dao_audit
PYTHONPATH=tool python3 -m dao_audit dex-run \
  --db /var/lib/dexmon/dex_monitor.db --poll 10 --rate-limit 2
# 新区块触发增量扫描；SQLite 持久化；进程重启后自动从上次区块续跑。
```

## RPC 失败切换（failover）
每条链按顺序尝试多个 RPC：`config/dex.yaml` 的 `rpc`（主）+ `rpc_fallbacks:`（备），
再叠加环境变量 `<CHAIN>_RPC_FALLBACK`（逗号分隔，如 `BASE_RPC_FALLBACK`、`ARBITRUM_RPC_FALLBACK`）。
主节点连续失败→自动切换到健康的备用节点并记入 `failures` 表（`rpc_failover:<chain>`），
后续扫描沿用健康节点。运维在部署时提供真实备用端点（勿硬编码私钥/密钥）。
> 诚实说明：本仓库默认未内置备用端点；失败切换**逻辑**已单元测试覆盖，
> 但在本受限网络环境中无法对第二个线上端点做实链验证，需在你的 VPS 上以真实备用 RPC 验证。

## 健康检查与告警
```bash
PYTHONPATH=tool python3 -m dao_audit dex-health --db /var/lib/dexmon/dex_monitor.db --max-scan-age 600
# healthy=True → 退出码 0；不健康 → 退出码 3（systemd/cron/外部告警据此动作）
```
检查项：各链 RPC 可达（含失败切换）、DB 可写、最近一次扫描时效（`run_last` 年龄 ≤ 阈值；
无扫描记录时视为通过）。接告警可在 timer 的 `OnFailure=` 挂你的通知单元。

## 幂等与不漏/不重（重启安全）
- `blocks` 表 `PRIMARY KEY(chain, block)` + `INSERT OR IGNORE`：同一区块重复观测**不产生重复行**。
- 重启后 `_last_block` 从 `MAX(block)` 续跑，只扫 `block > last` 的新区块：**既不重复计数、也不重扫**。
- 停机期间的区块在验收报告中如实计入 `missed_block_ratio`（覆盖率），不会被谎称已扫描。
- 覆盖验证见单元测试 `tests/test_dex_runner.py::test_block_idempotency_no_double_count_and_resume`。

## 采集 24–72h 后出验收报告
```bash
PYTHONPATH=tool python3 -m dao_audit dex-acceptance --db /var/lib/dexmon/dex_monitor.db
# 输出 reports_out/dex_acceptance.{md,json}：真实连续时长、区块数、扫描次数、
# 正价差/正净收益/Fork验证次数、机会寿命分布、各路径净收益与容量、
# 各链漏块率/报价过期(区块)/平均计算耗时、延迟与失败率。
```
**验收流程**：部署→运行 ≥24h→`dex-acceptance`→核对 `continuous_seconds_observed ≥ 86400`、
`failure_rate` 低、各链 `missed_block_ratio` 与 `avg_staleness_blocks` 合理；仅当出现
`positive_net>0` 且通过完整双腿 fork 才进入交易验证，否则 **NO_TRADE**。

## 安全边界（不可更改）
- 不连接真实钱包、不签名、不执行真实交易；仅只读报价 + 本地 fork 模拟。
- 不降低手续费/滑点/MEV/失败成本标准制造盈利；无正净收益一律 NO_TRADE。
- 保留 DAO 治理安全审计模块的全部限制；仅分析授权合约。
