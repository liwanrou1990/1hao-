# 真实数据市场监控模块 (market/)

在审计系统之上新增的**事件驱动、真实数据**风险与交易机会分析模块。核心是一条
**完整性铁律**：结果只来自真实数据；源不可达→标 `UNAVAILABLE`→决策 `NO TRADE`；
**绝不以模拟行情替代真实市场**；不承诺固定收益；未经人工确认不连接钱包或执行真实交易。

## 架构

| 文件 | 职责 |
|------|------|
| `market/provenance.py` | 真实 HTTP/JSON-RPC；每条数据带 source/url/status/latency/http_code/block/timestamp/address/tx_hash；失败记真实错误，不编造 |
| `market/rpc.py` | 只读 EVM JSON-RPC（blockNumber/getBlock/getBalance/eth_call/getLogs），校验新鲜度(now−block.ts>上限→STALE)；不签名、不发交易 |
| `market/sources.py` | DefiLlama(价/TVL)、Snapshot(治理提案/投票)、跨源报价差；均真实，失败即 UNAVAILABLE |
| `market/costs.py` | 毛利/手续费/Gas/滑点/价格冲击/净利；任一真实输入缺失→INSUFFICIENT_DATA（不猜数） |
| `market/decision.py` | TRADE/NO TRADE：数据须 OK+新鲜、独立源达标、扣全部成本后净利>阈值；否则 NO TRADE 并给原因 |
| `market/backtest.py` | 事件驱动回测；严格防未来泄漏；训练/验证/样本外切分；净收益/胜率/回撤/次数/失败原因；无真实历史→INSUFFICIENT_DATA |
| `market/events.py` | 实时扫描编排：链健康+治理+金库+价格+机会→决策 |
| `market/dashboard.py` | 自包含 HTML 看板，只展示真实数据与其证据 |

配置 `config/market.yaml`（链 RPC、关注 DAO 的 Snapshot 空间与金库地址、代币、成本参数、阈值、新鲜度上限）。

## 命令

```bash
export PYTHONPATH=tool
python3 -m dao_audit market-scan        # 实时扫描→market_scan.json + market_dashboard.html
python3 -m dao_audit market-dashboard   # 由上次扫描重建看板
python3 -m dao_audit market-backtest    # 事件驱动回测（真实历史数据）
```

## 本环境现状（如实披露）

本云环境的**出口网络策略拒绝**所有市场/RPC/治理数据主机（CONNECT 403），仅放行
包管理器与 Anthropic API。因此当前：

- `market-scan`：三条链 RPC 与所有价格/治理源均 `UNAVAILABLE`（真实 403 错误已记录），
  全部机会判定 **NO TRADE**（数据不可得）。**未使用任何模拟行情**。
- `market-backtest`：Snapshot 不可达、0 事件，返回 **INSUFFICIENT_DATA**，不以模拟事件替代。

被拒主机示例：`eth.llamarpc.com`、`cloudflare-eth.com`、`rpc.ankr.com`、`mainnet.base.org`、
`arb1.arbitrum.io`、`api.llama.fi`、`coins.llama.fi`、`hub.snapshot.org`、`api.coingecko.com`。

### 如何启用真实数据

在云环境设置中调整 **Network access**（会话标题栏的云环境菜单 → Edit）：放宽访问级别，
或在 **Allowed domains** 加入上述主机（保留 Allow package managers 勾选）。步骤见
https://code.claude.com/docs/en/cloud-environments#network-access 。放行后，上述命令会
**立即**产出真实结果——连接器是真实实现，非桩代码。

## 安全边界（对应要求四）

- VULN-1..5 检测器仅用于**防御性**风险识别，与本交易模块**分开管理**（不同包、不同输出文件）。
- 不将未公开漏洞用于未授权资金提取或市场操纵。
- 交易信号为估计值、非收益承诺；执行真实交易需人工确认，本模块不签名、不自动下单。

## 测试

`tests/test_market.py`（9 项，不触网，确定性）：成本数学、缺输入→INSUFFICIENT_DATA、
UNAVAILABLE→NO TRADE、证据不足/净利非正→NO TRADE、仅全部通过才 TRADE、回测防未来泄漏与
切分、无价格→INSUFFICIENT_DATA、看板在无真实数据时显示 NO TRADE 横幅。
这些用例以已知合成数仅测**引擎算术**，绝不流入实时看板/扫描输出。

## v0.3：四阶段真实数据交易优势流程

| 阶段 | 命令 | 产物 | 当前状态(本环境) |
|------|------|------|------------------|
| 1 连接验证 | `market-netcheck` | connection_report.{md,json} | BLOCKED：9 主机全被网络策略拒绝(403) |
| 2 事件收益模型 | `market-edge` | edge_model.json, edge_report.json | INSUFFICIENT_DATA（Snapshot/CoinGecko 不可达） |
| 3 成本后决策 | `market-scan` | market_scan.json, market_dashboard.html | 全部 NO_TRADE（无确认 edge） |
| 4 模拟交易/验收 | `market-accept`(+paper) | acceptance_report.{md,json} | BLOCKED @Phase1（根因：云环境出口策略） |

### event-edge estimator（Phase 2，`market/edge.py`）

- 输入：真实 DAO 治理事件（Snapshot 提案结束时刻）+ 真实历史价/量（CoinGecko market_chart）。
- 支持多事件类型（按提案 state 分组）、多标的、多持仓窗口（默认 1d/3d/7d）。
- 每个 (事件类型×标的×窗口) 计算：前向收益分布、相对 ETH 的基准超额、胜率、
  成本调整收益、t 统计量与**自助法(bootstrap)95% 置信区间**。
- 严格时序：按时间切 train+validation / 样本外(OOS)；edge 参数只来自较早的样本内事件。
- 防未来泄漏：决策时价取 obs_ts≤事件时刻；实现收益用历史(已结算)价。
- **确认门**（全部满足才产出可用 edge）：样本内均值>0、显著(t≥1.65 或 CI 下界>0)、
  样本量≥min_n、**样本外成本调整后仍>0**。否则 edge_bps=None → 下游 NO_TRADE。

### 反滥用保证（对应要求）

- expected gross edge **只能**来自上述确认的 OOS 正 edge；代码中已删除任何硬编码正值/0 占位，
  无 edge 即 NO_TRADE（原因显式标注）。
- 不为生成 TRADE 而降低风险标准：阈值、显著性、成本扣减均在配置中显式，决策逐条给依据。
- 模拟交易(`market/paper.py`)只记录信号/模拟成交/执行偏差/净收益/回撤；**不连接钱包、不下单**，
  真实执行需人工确认。
- 验收(`market/accept.py`)为真实数据驱动的依赖门：上游无真实证据，下游一律 BLOCKED，
  不以模拟数据冒充通过。

### 恢复真实数据后的运行顺序

放行 9 个主机并开新会话后：
```bash
cd dao_audit && export PYTHONPATH=tool
python3 -m dao_audit market-netcheck   # Phase1 应转 OK
python3 -m dao_audit market-edge       # Phase2 建 OOS 确认 edge 模型
python3 -m dao_audit market-scan       # Phase3 成本后 TRADE/NO TRADE + 看板
python3 -m dao_audit market-accept     # Phase4 验收门
```
