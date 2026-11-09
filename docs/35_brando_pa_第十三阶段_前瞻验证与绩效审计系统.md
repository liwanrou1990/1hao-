# Brando Price Action 第十三阶段 · 中长期现货策略前瞻验证与绩效审计系统（2026-11-09）

> 类别：**前瞻模拟基础设施 + 冻结清单**。本阶段没有、也不可能有前瞻业绩：影子账户在 2026-11-09 02:39 UTC 才首次运行，此后的行情尚未发生。所有数字只来自已发生的记录；未到期窗口一律写“未到期”。
> 隔离：分支 `claude/brando-pa-phase13-forward`、代码 `src/brando_pa/pa13/`、输出 `reports_out/brando_pa/phase13_forward/`、行情仓 `data_store/brando_pa/phase13_bars/`、锁与 launchd 标签独立（`com.brando-pa.shadow13`）。Mac 三个模拟账户、策略参数、本金、持仓、日志、定时服务、72 小时验收均未触碰；真实交易禁用（隔离检查前后生产目录 29 个文件哈希不变）。本阶段**未部署到 Mac**。

## 1. 已完成内容（实际执行）

| # | 交付 | 状态 | 位置 |
|---|---|---|---|
| 1 | 策略冻结清单 | 完成：6 个影子策略的规则 / 参数 / 成本 / 数据与成交规则 / 隔离规则 / 起点规则 + 历史 B2 vs Mac B vs 影子 A 逐项比对 | `docs/brando_pa_validation/phase13_forward/freeze_manifest.{md,json}` |
| 2 | 独立前瞻模拟引擎 | 完成：`ShadowRunner` / `ShadowAccount` 复用第九阶段前瞻引擎（同一决策、成交、事件链、状态文件代码），只换账户集合、目录、行情仓与锁；F 增加波动跟踪状态 | `src/brando_pa/pa13/shadow.py` |
| 3 | 数据与订单审计模块 | 完成：复用 T1–T8 时间顺序审计、费用 / 净值只读审计、连续性验收；新增信号 / 成交延迟、停机缺口、每日净值、回撤与持续时间、vs BTC 持有差额、30/90/180/365 窗口（只评价已到期）、行为观察（主升 / 主跌段暴露、最长空仓、退出后 10 日内更高价买回）、未来日期断言 | `src/brando_pa/pa13/audit13.py` |
| 4 | 自动化测试 | 完成：第十三阶段 7 项 + 全套，见 §6 | `tests/test_brando_pa_pa13.py` |
| 5 | 只读 Dashboard | 完成：复用 Phase 11/12 静态 Plotly 页面技术；分区“前瞻模拟 / 历史回测 / 真实账户（无）” | `dashboard_phase13.html`（docs 为 CDN 版；`reports_out/.../phase13_forward/` 为内嵌版） |
| 6 | 每日 / 每周中文报告模板 | 完成：从真实记录生成，未到期写“未到期”，人工观察栏留空 | `src/brando_pa/pa13/report13.py`；样例 `cloud_first_run/daily_2026-11-08.md`、`weekly_2026-11-09.md` |
| 7 | Mac 安全部署步骤 | 完成脚本与说明，**未执行** | `deploy/install_mac_brando_pa_phase13.sh`、`scripts/brando_pa_shadow13_ctl.sh`，§5 |
| 8 | 90/180/365 天客观评价规则 | 完成（冻结于任何前瞻数据之前） | `docs/brando_pa_validation/phase13_forward/evaluation_rules.md` |
| 9 | 云端首次真实运行 | 完成一轮（OKX 实时行情，2026-11-09 02:39 UTC）：6 个账户建立，起点记录，0 信号 0 成交（下一次日线收盘 2026-11-10 00:00 UTC 之后才会有第一次决策） | `cloud_first_run/` |

## 2. Phase 12 结论复核（读取 `phase12_results.json`）

B2 期末 64,401.5 / 回撤 −25.5%；BTC SMA200 64,341.4 / −65.0%；BTC 持有 56,505.4 / −81.1%；26 组 PA 增强配置全部低于 B2——与引用一致。2018–2026 为多次查看的开发样本，不是盲测；历史表现不构成未来盈利证明。本阶段不再对该区间做任何参数调整。

## 3. 冻结的影子策略与“历史 B2 ≠ Mac B”核实

| 影子账户 | 规则 | 与 Mac 账户关系 |
|---|---|---|
| A_B2_state_machine | B_full_single 权重（both 50/50，单一 100%）+ 熔断状态机（−10% 限仓 50%，−20% 全卖，冷静 30×n 日，恢复 60 日，单日 −15% 紧急退出） | **与 Mac B_modelB_state_machine 同规则、同成本、同引擎代码**；差别只在起点时刻与独立数据仓 |
| B_BTC_SMA200 | BTC 100%，close > SMA200 ON，无熔断 | 与 Mac A_BTC_SMA200 同规则 |
| C/D/E 买入持有 | 首轮买入，不再交易（漂移再平衡关闭） | 新增对照 |
| F_B2_vol_trail | B2 + 3 ATR 波动跟踪：持有中收盘 < 最高收盘 − 3×ATR14 → 该资产目标 × 0.5；收盘 > 最近已确认摆动高点（k=3）→ 恢复；趋势 OFF 重置 | Phase 12 E_B2_vol_trail（全样本 58,364，增强组最高、回撤不变）的前瞻版，仅实验对照 |

成本：cost 账本单侧 15 bps（10 手续费 + 5 滑点）+ ideal 0 对照；成交：决策后下一整点之后按实时 Ask/Bid（≤ 120 s）或估算收盘，迟到 / 过期 / 错过规则同第九阶段。

**历史研究 B2（Phase 10/12 日线引擎）与 Mac 运行 B 账户不完全相同**：信号与风险状态机相同；但 ① Mac 多了“权重漂移 ≥ 10 pp 再平衡”，② 执行时点与价格（次日开盘 vs 决策后整点实时 Ask/Bid），③ 成本主口径（25 vs 15 bps），④ 数据来源（Binance 镜像 vs OKX 实时）不同。报告与页面中分别标为“历史 B2（日线研究引擎）”与“前瞻 B2（前瞻引擎）”。Phase 12 同口径 15 bps 下 B2 历史净收益为 +56,602，仅作对照，不与前瞻数字同列比较。

## 4. 前瞻数据现状（如实）

- 首次运行：2026-11-09 02:39:38 UTC（云容器 `vm`，OKX 实时行情，跨来源无偏差）。6 个账户现金各 10,000 USDT，暴露 0%，0 信号，0 成交。
- 第一次日线决策将在 2026-11-10 00:00 UTC 收盘后的首轮运行产生；买入持有账户 C/D/E 也在那一轮按规则首次买入（次整点实时 Ask）。**在此之前任何“成交 / 净值变化”都不存在，本报告与页面均未填写。**
- 云容器是临时环境：云端影子记录可能随容器回收丢失，仅用于验证管线；权威运行位置应是持续在线主机（见 §5）。`cloud_first_run/` 保存了首轮的事件链、状态、审计与报告样例。
- 窗口 30/90/180/365 天全部“未到期”。

## 5. Mac 安全部署步骤（未执行；须在 72 小时验收完成并得到你明确同意后）

权限与资源：与现有服务相同的用户 launchd 任务，新标签 `com.brando-pa.shadow13`；复用同一仓库 `.venv`；一个额外 Python 进程、约 100 MB 内存、约 1 MB/日磁盘、每 600 s 一次 OKX/Coinbase 公开接口请求；网络只需 www.okx.com 与 api.exchange.coinbase.com；不需要任何 API 密钥；真实交易无实现。
隔离检查（安装脚本内置，任一失败即退出）：严格 YAML 真实交易守卫；影子目录 / 行情仓 / feed 目录 / 锁 / launchd 标签与生产完全不同；生产目录哈希快照；安装脚本不写 `config/brando_pa.yaml`、不写生产 plist、不写 `phase8_forward`。
```bash
# 前提：Mac 已完成第九阶段 72 小时验收；已 fetch 到含本阶段代码的提交（先 upgrade-dry-run，再 upgrade，或在验收结束后再升级）
cd ~/1hao-
bash scripts/brando_pa_forward_ctl.sh acceptance "2026-11-08 12:23:00"     # 确认 72h 验收已到期并通过
PYTHONPATH=src .venv/bin/python -m brando_pa.cli phase13-isolation-check     # ok 必须为 true
BRANDO_SHADOW13_CONFIRM=I_AGREE bash deploy/install_mac_brando_pa_phase13.sh  # 你明确同意后才设置该变量
bash scripts/brando_pa_shadow13_ctl.sh status|health|audit|report|weekly|dashboard|isolation|logs
```
回滚 / 卸载：`launchctl bootout gui/$(id -u)/com.brando-pa.shadow13 && rm ~/Library/LaunchAgents/com.brando-pa.shadow13.plist`；影子目录可保留或整体删除，不影响生产账户。

## 6. 测试

`tests/test_brando_pa_pa13.py` 7 项：冻结规格与 Mac 规则一致、买入持有账户永不卖出且决策不早于日线收盘、F 波动跟踪状态减仓 / 恢复 / 重置、隔离（生产目录哈希不变、锁 / 标签 / 目录不同）、审计拒绝未来日期且未到期窗口不填数、报告与页面只用真实记录（不出现未来年份）、冻结清单含提交与比对 + Mac 脚本 bash 3.2 规则扫描。全套结果见 `docs/brando_pa_validation/phase13_forward/test_report.txt`。

## 7. 使用方法（云端或任何检出本分支的主机）

```bash
PYTHONPATH=src python -m brando_pa.cli phase13-freeze            # 冻结清单
PYTHONPATH=src python -m brando_pa.cli phase13-isolation-check   # 隔离检查
PYTHONPATH=src python -m brando_pa.cli phase13-service --once    # 单轮（或不带 --once 常驻，600 s 周期）
PYTHONPATH=src python -m brando_pa.cli phase13-audit             # 数据 / 订单审计 + 已到期窗口绩效（退出码 0/1）
PYTHONPATH=src python -m brando_pa.cli phase13-report [--weekly] # 日报 / 周报模板
PYTHONPATH=src python -m brando_pa.cli phase13-dashboard         # 只读页面
```
评价节奏：30 天只看质量项；90 天描述性评估且 n < 10 必须写“统计证据不足”；180 天风险与收益；365 天跨环境；通过条件见 `evaluation_rules.md` §4。

## 8. 尚未完成 / 不可能在本阶段完成

- 任何前瞻业绩（0 天数据）；30/90/180/365 天评价全部未到期。
- Mac 部署（等待 72 小时验收结束与你的同意）。
- 云端影子运行的持续性（容器临时），因此云端记录只作管线验证。
