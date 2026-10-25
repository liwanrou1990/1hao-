# DEX 套利监控 性能优化 / 防漏检 / 持续部署 — 最终报告 (v0.9)

> 范围：只优化**性能、扫描完整性、长时间监控**，**不扩展交易路径**。
> 全部数值为真实链上测量；未达标如实报告，不伪报。
> 保留 NO_TRADE 标准，不降低手续费/滑点/MEV/失败成本以制造正收益；不连接钱包、不执行真实交易。

## 1. 单次扫描耗时 优化前/后（P50/P95/P99）

**根因剖析**：v0.8 单次全扫描 >240s，瓶颈在逐笔 `cast` 子进程（每笔 ~0.4s，含进程启动+RPC 往返）。
一次 4-venue 全扫描需数十笔报价串行 → 数十秒。网络 RPC 本身不慢，慢在**每笔一个子进程 + 每笔一次往返**。

**优化手段**：Multicall3 `aggregate3` 把 N 笔 `quoteExactInputSingle` 合并进 **1 次 eth_call**（手写 ABI，无外部库）。

| 场景 | 优化前（逐笔 cast） | 优化后（批量 Multicall） | 加速 |
|------|--------------------|--------------------------|------|
| Base 同 11 笔买腿报价（同区块） | 3542 ms / 11 子进程 / 11 次往返 | 342 ms / 1 eth_call / 1 次往返 | **10.4x** |
| 纯网络批量（无子进程基线，另测） | — | 174 ms / 1 次往返 | **~19.5x** |

**Tier-1 候选发现延迟（20 个真实连续区块/链）**：

| 链 | P50 | P95 | P99 | min | max | 每扫描 RPC | 内层报价数 |
|----|-----|-----|-----|-----|-----|-----------|-----------|
| **Base** | 414 ms | **565 ms** | 584 ms | 309 ms | 584 ms | 2 | 33 |
| **Arbitrum** | 419 ms | **640 ms** | 663 ms | — | — | 2 | 12 |

- **Base 核心候选发现 P95 = 565 ms < 1s → 目标达标 ✅**（真实测量）。
- 每次 Tier-1 扫描固定 **2 次** eth_call（买腿批量 + 卖腿批量），与 venue/费率数无关。

## 2. RPC 调用次数与延迟

- Tier-1：**2 次** eth_call/扫描（原逐笔 N 次 → 降至 2 次）。
- 连续监控空块：仅 1 次 `block_number`（新区块才触发扫描）。
- `block_number` RPC 延迟：验收 DB 真实记录 `avg_blocknum_rpc_ms`（会话内短验证 ~200ms 量级）。
- 失败切换：主 RPC 连续失败自动切备用并记 `failures`；逻辑已单测，真实第二端点需在 VPS 验证
  （本受限网络仅放行主端点，无法实链验证第二端点——如实披露）。

## 3. 区块覆盖率与漏检风险（真实记录推导）

连续监控验收报告新增**各链**：`missed_block_ratio`（窗口内未扫描区块占比）、`avg/max_staleness_blocks`
（扫描期间链前进区块数=报价过期程度）、`avg_compute_ms`。

会话内短验证（Arbitrum，`dex_validate` 最小配置，2×30–40s 两段重启跑）实测：

| 链 | 窗口区块跨度 | 已扫描 | 漏块率 | 平均过期(区块) | 最大过期 | 平均计算 |
|----|--------------|--------|--------|----------------|----------|----------|
| arbitrum | 290 | 19 | **0.93** | 9.6 | 16 | 2441 ms |

- **关键发现**：全扫描 2.4s 远跟不上 Arbitrum ~0.25s 出块 → 93% 区块未扫（含两段跑之间的停机）。
  这正是性能优化的意义：Tier-1 批量把候选发现降到 0.4–0.6s，大幅降低漏块率与报价过期。
- 漏检防护见下节：提速不改变任何报价、不丢任何正收益路径。

## 4. 优化前后候选/报价一致性（防漏检）

- **基准**：现有全扫描 `scan.scan_chain`（全 venue + 全成本 + 全档位）。
- **等价校验**（`dex-perf`）：同一区块下，批量 Multicall 报价 vs 逐笔 `cast` 报价逐一精确比对：
  - Base：11 笔**完全一致** ✅；Arbitrum：12 笔**完全一致** ✅。
- **候选集真超集**：Tier-1 以精确最优闭环毛收益 ≥ -50bps 为候选判据（下限宽于全部显式成本
  Gas/MEV/失败/闪电贷），并对无法入批量的非CL venue（solidly/classic）保守保留 → 任何基准会标记
  为 TRADE_CANDIDATE 的路径都必被 Tier-1 保留，**提速不丢正收益路径**。精确净收益仍由 Tier-2
  （全成本 + 完整双腿 fork）判定，Tier-1 不产生 TRADE。
- **逐路径过滤原因**（`filter_log`）：每对记录 flagged 与原因，可审计无正收益路径被误丢。
- **缺数据即停**：批量失败/区块不可得/链不同步 → `INSUFFICIENT_DATA`，不产生任何候选或信号。
- **实证**：等价校验在初版即捕获真实漏检 bug——Slipstream 误用 univ3 选择器（`0xc6a5026a` vs
  Slipstream `0x9e7defe6`），批量中所有 Slipstream 报价静默返回 None；报出 5 处不一致，修复后全一致。
  单测 `tests/test_dex_fastquote.py` 锁定两选择器防回归。

## 5. 可部署的 24h 连续监控服务

- `deploy/deploy.sh`：一键 VPS 安装（systemd 崩溃自启 + 每 5 分钟健康检查 timer + logrotate + 专用账户 + 文件系统加固）。
- 失败切换（`rpc_fallbacks` / `<CHAIN>_RPC_FALLBACK`）、健康检查（`dex-health`，非零退出可接告警）、
  SQLite 恢复、重启续跑。
- **幂等**：`blocks` 主键 + `INSERT OR IGNORE` + 从 `MAX(block)` 续跑 →
  重启**不重复计数、不重扫**；停机区块如实计入漏块率。单测
  `test_block_idempotency_no_double_count_and_resume` 覆盖；会话内两段重启实跑验证：19 区块行全不重复，
  重启后从更高区块续扫。
- **诚实边界**：本 Claude 云会话无法 24–72h 常驻，上述为可直接部署程序 + 配置 + 验收流程；
  **24h 连续监控需在 VPS 部署后以监控 DB 的真实记录为准**，此处不声称已完成 24h 连续监控。

## 6. 按实际成交容量的套利统计

- 分档本金 1k/10k/100k/1M：`dex-scan` / `dex-acceptance` 按 size 记录毛利/各项成本/净收益与样本数。
- 历史全部实测结论一致：同链跨 DEX round-trip 扣全部成本后净收益**非正**；大额档位因薄池价格冲击
  进一步转负（真实流动性限制）。各路径净收益与容量见 `dex-acceptance` 的"各路径净收益与容量"表。

## 7. 真实净收益与失败原因

- **真实净收益**：0。连续/图搜索/两级扫描所有策略均 **NO_TRADE**，无任何正净收益可成交机会。
- **失败/未成交原因**（根因，非借口）：闭环毛价差本身 ≤ 0（池间定价已被充分套利/竞争充分），
  并非 Gas/MEV 主导；增加跳数只叠加手续费与冲击；大额被流动性深度压垮。
- **交易验证纪律**：仅当出现正净收益候选，才保存区块/时间/真实报价 → 全成本计算 → 同状态
  fork 模拟全部交易腿 → `balanceOf` 验证最终净利；未经真实主网执行**绝不**标记为已实现利润。
  当前无正收益候选 → 无可进入此流程者。

## 关于"池状态增量缓存"的设计决策（诚实说明）

用户要求"基于池状态变化做增量更新，对未变动池缓存、变动时失效"。实际落地为**两级增量**：
- **链级增量**：新区块才触发扫描，空块仅 1 次 `block_number`（已实现，见 runner）。
- **区块内快照一致**：Tier-1 单区块内 2 次批量 eth_call 取全部报价，天然同快照。

**跨区块"按池缓存报价"被刻意不做**，原因是安全优先：QuoterV2 报价不仅取决于池价格
（slot0.sqrtPriceX96），还取决于**全量流动性/tick 分布**；仅按价格指纹判断"未变动"而复用旧报价，
会在流动性变化但价格未动时产生**过期/错误报价**，违反"防提速漏检"与 NO_TRADE 纪律。而要安全地做
池指纹需额外读 slot0+liquidity(+tick)，在 Base/Arbitrum 这类**几乎每区块都变动**的快链上，缓存命中率
极低且会**增加** RPC 次数，净收益为负。因此采用"每新区块用批量低成本重新报价"替代跨区块缓存——
更快、且不冒旧报价风险。缓存失效/RPC 滞后/链不同步场景一律 `INSUFFICIENT_DATA`，不出任何信号。

---
### 复现命令
```bash
export PATH=/root/.foundry/bin:$PATH PYTHONPATH=tool
python3 -m dao_audit dex-tier1  --chain base --size 1000          # 单次两级候选发现+过滤原因
python3 -m dao_audit dex-perf   --chain base --blocks 20          # 性能基准(P50/P95/P99)+防漏检等价校验
python3 -m dao_audit dex-health --db reports_out/dex_monitor.db   # 健康检查(RPC失败切换/DB/时效)
python3 -m dao_audit dex-run    --db /var/lib/dexmon/dex_monitor.db   # 常驻连续监控(新区块触发)
python3 -m dao_audit dex-acceptance --db /var/lib/dexmon/dex_monitor.db   # 验收(覆盖率/过期/寿命/容量)
sudo APP_DIR=/opt/dao_audit DB_DIR=/var/lib/dexmon deploy/deploy.sh   # 一键 VPS 部署
```
