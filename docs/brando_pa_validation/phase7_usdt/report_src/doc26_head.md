# 26 · Brando Price Action 第七阶段：BTC/ETH/USDT 纯现货动态资金配置与最终 USDT 利润

生成时间：{GEN_TS} UTC · 分支 `claude/brando-pa-phase7-usdt` · 基准锁定 `docs/brando_pa_validation/phase7_usdt/BASELINE_LOCK.md`（第六阶段提交 e659482）· 预先登记 `docs/brando_pa_validation/phase7_usdt/preregistration.md`（含登记后澄清一条，已披露）· 测试 {N_TESTS} 项通过（前六阶段 52 项 + 本阶段 3 项）

> 目标：在预设风险约束内尽可能提高期末 USDT 净资产与净利润。全部模型统一 USDT 计价、相同窗口 / 本金 / 成交机制 / 成本 / 资金约束；禁止负现金、杠杆、做空、借币、合约。三套资金配置模型 × 三种风险模式 × 六个固定参考 × 6 窗口 × 2 成本一次性全部运行并全部报告，没有挑选。真实交易接口保持禁用。**所有金额为 USDT，初始 10,000。**

## 0. 结论先行

{CONCLUSIONS}

## 一、本阶段做了什么（对应任务 §一～§十）

| 任务 | 实现 | 位置 |
|---|---|---|
| §一 统一 USDT 评价 | 逐根权益 = USDT + BTC×价 + ETH×价；净利润；**期末全部清仓、扣除该轮次手续费与滑点后的可实现 USDT**；按资产的 USDT 贡献（已实现毛利 − 手续费 + 未实现） | `src/brando_pa/usdt/engine.py: liquidation_value / _contrib`，`evaluation.py: usdt_row` |
| §二 固定参考 | BTC SMA200、ETH SMA200（第六阶段策略 A 原样）、买入持有 BTC / ETH / 等权、全程 USDT | `phase7.py: _ref_sma / _ref_bh` |
| §三 三套预登记模型 | A 等权趋势（50/50、50/USDT、USDT）；B 独立满额（50/50、100、100、USDT）；C 现金储备（40/40/20、80/20、80/20、USDT，80% 硬上限） | `config: usdt.models` |
| §四 PortfolioAllocator | 输入趋势 / 持仓 / USDT / 权益 / 风险决策 → 目标比例（和恒为 1）、先卖后买订单、预计成本、理由；再平衡触发 = 目标变化或漂移 ≥ 10pp 或超硬上限；最小再平衡金额 max(10 USDT, 0.5% 权益)；买入 ≤ 卖出后可用 USDT；全部决策写 `*_alloc_log.csv` | `allocator.py` |
| §五 风险层 | 复用第六阶段三种模式（原规则 / 有限状态机 / 无），全部运行；外部风险约束占位 30%（未批准，只标注） | `trendsys/risk_layer.py` |
| §六 成本 | 零成本 + 10 bps/5 bps；统计交易次数、手续费、滑点、换手率、扣费前后利润 | `engine.py` |
| §七 诊断 | 月度环境分类（±3% 阈值）× 各模型 USDT 盈亏；BTC/ETH 贡献；现金拖累 | `evaluation.py: regime_months` |
| §八 过拟合 | 预登记 + 代码锁定；窗口标签；无新数据；前瞻记录追加写入 + 哈希链 | `preregistration.md`，`paper.py` |
| §九 排行榜 | 统一列；满足约束者按期末清仓 USDT 排序；不满足者单独展示 | `phase7.py` |
| §十 交付 | 看板“USDT 利润与资产配置（第七阶段）”页；三资产仓位图、USDT 净值、贡献图、再平衡日志、排行、成本对照；`docs/brando_pa_validation/phase7_usdt/` | — |

## 二、数据与窗口（§八）

与第六阶段相同：train 2022-04→2024-06（训练，已观察）、valid 2024-07→2025-06（验证，已观察）、holdout 2025-07→（**已被观察的测试窗，不是样本外**）、locked_unused_1h 2022-01→03（真正未使用，3 个月，几乎全程空仓，无统计力）、full_1h 2022-04→（主窗口）、full_4h 2018-06→（长历史）。**没有新历史数据**；真正的新证据只来自前瞻记录。

