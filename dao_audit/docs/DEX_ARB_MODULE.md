# 真实 DEX 跨市场套利研究模块 (dex/, v0.5)

> 目标：验证真实市场中**能够成交、扣除全部成本后仍有正期望收益**的合法套利，而非历史理论最高收益。
> 只读链上数据；不签名、不执行真实交易；缺报价/流动性/成本 → NO_TRADE；不制造盈利结论。
> 与 DAO 治理安全审计模块分开；不使用未授权漏洞转移第三方资金。

## 方法

- **可执行报价**（`dex/quotes.py`）：用 `cast` 只读 eth_call 调用链上 Uniswap V3 / SushiSwap V3
  QuoterV2，按**实际交易金额**取 `amountOut`（QuoterV2 在链上执行 swap 数学，价格冲击已内含），
  跨 fee tier 取最优；两腿锁定同一区块，保证快照一致；无池/报价失败返回 None（不编造）。
- **同链 round-trip 套利引擎**（`dex/scan.py`）：对每条链/每个交易对/每个本金档位，枚举
  “在 A 买入基础资产 → 在 B 卖出”的有序 venue 组合，用两腿可执行报价算毛利，再**扣除全部成本**：
  Gas（真实 gas price × gas units × ETH 价）、手续费（含在报价）、价格冲击（含在报价）、
  闪电贷费（如启用，Aave v3 ~5bps）、MEV/抢跑折价、交易失败期望成本（p×gas）。净收益 > 阈值才算候选。
- **分本金容量**：对 1k/10k/100k/1M USD 分别报价，观察净收益随规模衰减（薄池大额价格冲击巨大）。
- **频率/持续/成交**（`dex-monitor`）：多次真实快照统计机会出现频率与持续性。
- **本地 EVM 成交模拟**（`test/DexSwapFork.t.sol`）：fork 真实 Base 状态执行真实 swap，
  断言**实际余额变化 == 报价输出**（证明报价可成交）。
- **跨链单列**：不同链价差**不是**原子无风险套利（需跨链桥/中继、桥风险、两端滑点与价格漂移），
  本模块只做同链原子 round-trip，跨链仅标注为高风险非原子、不计入机会。

## 命令

```bash
export PATH=/root/.foundry/bin:$PATH PYTHONPATH=tool
python3 -m dao_audit dex-scan         # 单次扫描：各对各档位净收益 + TRADE/NO_TRADE
python3 -m dao_audit dex-monitor --snapshots 3 --interval 20   # 频率/持续统计
# 本地 EVM 成交验证（fork 真实状态，只读不广播）：
FOUNDRY_PROFILE=fork RUN_FORK=true forge test --match-contract DexSwapForkTest -vv
```

## 本环境实测结果（真实数据）

- 可达链：Base(8453)、Arbitrum(42161) RPC 正常；DefiLlama 提供 ETH 计价。
- venues：各链 Uniswap V3 + SushiSwap V3 QuoterV2（地址运行时验证可用）。
- 扫描结果：**0 个候选**。各对最佳 round-trip 净收益均为负：
  Base WETH/USDC ≈ -$3.5、Arbitrum WETH/USDC ≈ -$1.9、Arbitrum USDC/USDT ≈ -$0.5（$1k 档，扣全部成本）。
  大额档位因 SushiSwap V3 薄池价格冲击，毛利急剧转负（真实流动性限制）。
- `dex-monitor`（3 快照 / ~2 分钟）：机会频率 **0.0**，净收益稳定略负。
- 本地 EVM 模拟：fork Base 执行 1 WETH→USDC，报价 2343.616572 USDC = 实际成交 2343.616572 USDC，
  余额变化一致（报价可成交已验证）。

**结论**：当前真实数据下，同链跨 DEX 在扣除手续费、价格冲击、Gas、MEV 与失败成本后，
**无正期望收益的可成交套利**，全部 NO_TRADE。这与高效 L2 DEX 市场一致，未人为放宽标准。

## 局限（诚实披露）

- venue 限于放行主机可达的链上 QuoterV2；聚合器 API（0x/1inch）未放行，未纳入多路由比价。
- MEV 折价与失败概率为**标注假设**（真实私有内存池/竞拍未接入）；Gas 为链上实时 gas price，真实。
- 闪电贷费按参数计（默认自有资金，未计）；真实闪电贷还需 flashloan 合约路径与回调 gas。
- 跨链套利未实现（非原子、高风险）；仅同链原子 round-trip 计为机会。
- 本地 fork 模拟依赖公共 RPC 可 fork；默认离线测试套件跳过该用例。

## v0.6 扩展：更多 venue + 完整双腿验证 + 可恢复监控 + 排序报告

- **Base venue 扩展到 4 个**（地址均经 `cast` 链上功能验证）：Uniswap V3、SushiSwap V3、
  **Aerodrome Slipstream**(CL, tickSpacing)、**Aerodrome classic**(solidly Router.getAmountsOut)。
  加入后 Base WETH/USDC 最佳 round-trip 净收益从 -$3.39 收窄到 **-$0.64**（$1k，近盈亏平衡，仍 NO_TRADE）。
  最紧价差来自 AerodromeSlipstream↔UniswapV3。
- **0x 聚合报价**：`api.0x.org` 本环境代理 403 未放行且需 API key → 按 `aggregator_0x.enabled`
  与 `ZEROX_API_KEY` 门控，当前标注 **inactive**，不编造。放行域名并配置 key 后可作交叉参考/去重。
- **双向/多 venue 路径**：引擎枚举有序 venue 组合，天然覆盖正/反向；多跳三角为后续扩展。
- **完整双腿原子 Fork 验证**（`test/DexRoundTripFork.t.sol`）：fork 真实 Base 状态，USDC→WETH→USDC
  完整 round-trip，`balanceOf` 验证最终余额==两腿账目。实测 2000 USDC→0.8532 WETH→1991.60 USDC
  （毛利 -8.40，含两腿手续费，无免费套利）。证明完整交易而非单笔 swap。离线套件以 RUN_FORK 门控跳过。
- **可恢复连续监控**（`dex-monitor`，JSONL 续跑）：每次追加若干真实快照到状态文件，多次调用累积
  至 target-hours（24h+），限流友好；统计机会频率/持续/各对最佳净收益。
- **排序机会报告**（`dex-report`）：按扣全部成本后净收益排序；`simulated_feasible` 需
  净>0 且完整双腿 fork 验证通过；真实执行成功率未验证前不标记已实现利润。当前判定 **NO_TRADE**。

### 命令补充
```bash
python3 -m dao_audit dex-monitor --max-new 5 --interval 30 --target-hours 24   # 续跑累积
python3 -m dao_audit dex-report                                                # 排序机会报告
FOUNDRY_PROFILE=fork RUN_FORK=true forge test --match-contract DexRoundTripForkTest -vv  # 完整双腿验证
```

### v0.6 实测结论
4 venue、3 交易对、1k–1M 档位、同区块双腿可执行报价、完整双腿 fork 验证均成功，但**所有机会扣除
全部成本后净收益非正**（最优 Base WETH/USDC -$0.64@$1k）→ **NO_TRADE，0 个模拟可行机会**。
与高效 L2 DEX 一致；不制造正收益结论。

## v0.7 扩展：图搜索闭环 + 根因分析 + 监控竞争统计

- **图搜索闭环套利**（`dex/graph.py`，`dex-graph`）：代币为节点、跨 venue/费率的可执行报价为边，
  DFS 枚举 USDC 起止的 2–3 跳闭环（三角/多跳/跨 venue），逐跳同区块 best_quote + 记忆化，按净收益/ROC 排序。
- **根因分析**：无正收益时分解最优闭环（毛价差 bps vs Gas/MEV/失败 bps）并归因。
- **监控竞争/持续统计**：`dex-monitor` 增加 sign_flips（价差翻转/竞争近似）与 max_consecutive_positive
  （机会持续近似），JSONL 续跑累积 24–72h。
- 新增 Base 代币 cbETH、DAI（地址 cast 验证）；USDC↔cbETH 无直连池，经 WETH 多跳。

### v0.7 实测结论（真实数据）
- Base（4 venue，block 53064428）+ Arbitrum（2 venue，block 518095631），共 **26 条闭环路径**评估，
  **0 条正净收益 → NO_TRADE**。
- 最优路径为简单两腿 `USDC->WETH->USDC`：Base 净 -$1.02/$1k（毛价差 -8.1bps），Arbitrum 净 -$1.30/$1k。
- 三角/多跳（经 cbETH、DAI、USDT）**更差**：额外一腿手续费 + 薄池价格冲击使毛利显著转负
  （如 USDC->WETH->cbETH->USDC 毛利 -$148/$1k）。
- **根因**：闭环毛价差本身 ≤ 0（gross -8.1bps），即池间定价已被充分套利/竞争充分；增加跳数只增加
  手续费与冲击。并非 Gas（仅 0.04bps）或 MEV（估 2bps）主导，而是**价差不存在**。大额档位进一步被
  流动性深度/价格冲击压垮。
- 判定：真实、合法、可执行的正期望套利**未发现**；不制造盈利结论。

> 诚实说明：24–72h 连续监控依赖进程持续运行（JSONL 续跑支持跨次累积与断点恢复，并由计划任务驱动）；
> 单次会话内无法保持 24h 进程，呈现的是带区块戳的真实快照按正确续跑语义累积，而非把几次手动扫描当作 24h。

## v0.8 扩展：可部署持续监控 + 三级验证 + 验收报告

- **持续监控 runner**（`dex/runner.py`，`dex-run`）：按链**新区块触发**增量扫描（空块仅一次
  block_number），SQLite 持久化(blocks/scans/opportunities/failures)，重试退避/限流/错误恢复/
  重启**从上次区块续跑**；记录每轮耗时、区块号 RPC 延迟、区块高度、数据过期与失败。
- **三级机会验证**：1=报价为正 2=Fork净为正 3=主网已成交（恒不置位，不执行真实交易）。
- **验收报告**（`dex-acceptance`）：连续时长/区块数/扫描次数/正价差/正净收益/Fork验证/
  机会寿命(连续正收益扫描数)/各路径净收益与容量/平均延迟/失败率。
- **可部署**：`deploy/`（systemd unit 崩溃自启=真正连续、run.sh、README、.env）。本 Claude 云会话
  无法 24–72h 常驻，提供可直接部署程序与配置；不把间歇扫描当作连续监控。

### v0.8 实测（真实连续数据，会话内短验证）
- 最小配置（Arbitrum WETH/USDC，$1k，2 venue）连续运行 **111 秒 / 32 个区块 / 32 次扫描 / 0 失败**，
  平均扫描 2.3s、区块号 RPC 延迟 207ms。
- 32 个连续区块内 WETH/USDC 净收益恒为 **-$1.74**，无任何瞬时正价差窗口；机会寿命(连续正收益扫描)=0。
- **判定 NO_TRADE**：连续变化的真实市场中未捕捉到扣成本后为正的可执行机会。
- **扫描效率发现**：4-venue 全配置单次 scan >240s，无法跟上 Base ~2s 出块节奏；连续监控需收窄
  venue/pair/size 或并行/批量 RPC（最小配置 ~2.3s/scan 可跟上）。这是下一步效率优化方向，
  不以牺牲成本标准换取盈利。

> 连续时长/区块数以部署后监控 DB 为准；本处 111s/32 区块为会话内短验证，非 24–72h 连续监控。

## v0.9 扩展：Multicall 批量报价 + 两级扫描 + 防漏检等价校验（性能核心）

v0.8 发现 4-venue 全配置单次 scan >240s，根因是逐笔 `cast` 子进程（每笔 ~0.4s）。本阶段
**不扩展交易路径**，只解决性能、扫描完整性与长时间监控。

- **批量报价**（`dex/fastquote.py`）：手写 ABI 编解码，把 N 个 QuoterV2 / Slipstream
  `quoteExactInputSingle` 合并进 **1 次 Multicall3 `aggregate3` eth_call**，N 次网络往返 → 1 次。
  仍为只读 eth_call；内层失败解码为 None，绝不编造。
- **两级扫描**（`dex/perf.py`，`dex-tier1` / `dex-perf`）：
  - **Tier-1 候选发现**：每区块 **2 次批量 eth_call**（买腿 quote→base、卖腿 base→quote），
    算出每对**精确最优闭环毛收益**；候选判据 = 毛收益 ≥ -50bps（含两腿手续费+价格冲击），
    或该对存在未入批量的非CL venue（solidly/classic 无法证伪→保守保留）。该下限宽于全部显式
    成本(Gas/MEV/失败/闪电贷)，故 **Tier-1 候选集是基准 `scan_chain` 候选集的真超集——提速不漏
    任何正净收益路径**。Tier-1 不产生 TRADE 决策。
  - **Tier-2 精确验证**：仅对 Tier-1 候选运行现有 `scan_chain`（全 venue + 全成本模型）+ 完整
    双腿 fork，权威判定。
- **防漏检等价校验**（`equivalence_check`，`dex-perf`）：同一区块下逐笔比对**批量 vs 顺序
  `cast`** 报价，要求数值完全一致。
- **缺数据即停**：批量失败 / 区块不可得 → `data_status=INSUFFICIENT_DATA`，不产生任何候选/信号。
- **每条路径过滤原因**（`filter_log`）：Tier-1 逐对记录 flagged 与原因，可核对无正收益路径被误丢。

### v0.9 实测（真实数据，20 个连续区块/链）

| 链 | Tier-1 延迟 P50 | P95 | P99 | 每扫描 RPC | 内层报价数 | 等价校验 | P95<1s |
|----|----------------|-----|-----|-----------|-----------|----------|--------|
| Base | 414 ms | **565 ms** | 584 ms | 2 | 33 | 11 报价**完全一致** | **达标 ✅** |
| Arbitrum | 419 ms | **640 ms** | 663 ms | 2 | 12 | 12 报价**完全一致** | **达标 ✅** |

- **优化前/后**（Base，同一区块、同 11 个买腿报价）：逐笔 `cast` **3542 ms / 11 子进程**
  → 批量 Multicall **342 ms / 1 eth_call**，**约 10x**（纯网络批量在无子进程开销时实测达 19.5x）。
- **Base 核心候选发现 P95 = 565 ms < 1s，目标达标**（真实测量，非伪报）。
- **等价校验一次性捕获真实漏检 bug**：初版对 Slipstream 误用 univ3 选择器（`0xc6a5026a` vs
  Slipstream `0x9e7defe6`，二者 tuple 字节相同但选择器不同），导致批量中所有 Slipstream 报价
  静默返回 None。等价校验立即报出 5 处不一致，修复后 11/12 报价完全一致——这正是“防止提速造成
  漏检”机制生效的实证。
- 单元测试 `tests/test_dex_fastquote.py`（无网络）锁定两个选择器、tuple 编码、aggregate3 结构与
  Result[] 解码，防止该漏检回归。

### 命令补充
```bash
python3 -m dao_audit dex-tier1 --chain base --size 1000     # 单次 Tier-1 批量候选发现+过滤原因
python3 -m dao_audit dex-perf  --chain base --blocks 20     # 性能基准(P50/P95/P99)+防漏检等价校验
```

## v0.10 扩展：Tier-1 接入持续监控 + 全 venue 批量 + 耗时归因 + 覆盖率实证

- **耗时归因**（`dex-profile`）：Base 全扫描 72.4s 实测，`cast` 逐笔子进程占 **99.0%**（208 笔），
  block/gas RPC 1.0%，路径计算 0.0%，DB 写入 ~0% → 瓶颈压倒性在逐笔子进程。
- **全 venue 批量**：Multicall 批量新增 **solidly `getAmountsOut`**（Aerodrome classic）编解码，
  Tier-1 现覆盖 univ3+slipstream+solidly **全部链上 venue 类型**，候选集成为基准的**精确真超集**
  （不再有"非CL venue 无法证伪"的保守保留）。等价校验 Base 13 笔全一致。
- **Tier-1 接入持续 runner**（`dex-run --fast`）：每新区块用 Tier-1 批量（每链 2 次 eth_call）候选发现，
  仅当某对**精确毛收益 >0** 才触发精确 Tier-2 全扫描（每链限流 60s，保证快循环不被拖慢）。
- **覆盖率实证（真实链上，Base 单链同窗口对照）**：

  | 模式 | 单次扫描 | 漏块率 | 报价过期(区块) |
  |------|----------|--------|----------------|
  | 全扫描(优化前) | 72.8s | **0.959** | 36.5 |
  | Tier-1 `--fast` | 485ms | **0.000** | 0.07 |

  → **Base 已证明可及时且完整观察市场**（每区块都扫、报价几乎不过期）。Arbitrum(~0.25s 出块)漏块率
  ~0.83，瓶颈为 2×RTT≈0.8s 两段批量往返 > 出块间隔的物理下限，如实记录不伪报。
- **判定仍为 NO_TRADE**：毛收益恒 ≤ 0，Tier-2 从未触发；不降低成本标准、不扩展路径、不连钱包。

### 命令补充
```bash
python3 -m dao_audit dex-profile --chain base               # 完整扫描耗时归因
python3 -m dao_audit dex-run --fast --db .../dex_monitor.db  # 快速连续监控(Tier-1批量,跟上出块)
```
