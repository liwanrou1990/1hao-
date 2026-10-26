# V0.14 最终报告 — 语义引擎升级 + 第三轮独立盲测（最终 PASS/FAIL）

> round1(f7764ed)/round2 原始盲测结果**永久保留未改**。round1+round2 全部案例已转为**开发集**。
> 本报告的最终能力结论以 **round3 全新独立集**为准（与 round1/round2 零项目重叠）。
> 不以模拟 X100 或开发集高分充当真实世界能力证明。**最终判定：FAIL / 未证明。**

## 1. V0.14 语义分析引擎

- `analysis/semantic.py`：继承感知跨文件调用图 + 访问控制语境 + 泛化汇点（含 0.5.x `.call.value()`
  与 **Yul/assembly `delegatecall`**）+ 泛化投票权（任意按地址索引实时权重）+ 结构化执行路径 +
  鲁棒 init 守卫（大小写/自定义命名）+ 时间锁执行器识别（排队哈希+经过延迟）。
- `analysis/compile_backend.py`（Phase 1）：Slither 0.11.6 **已安装**；但 solc 0.5/0.6/0.7 二进制被封
  (403)，历史合约依赖未 vendored → 编译级 Slither/AST **无法构建**这些合约，一律返回
  **ANALYSIS_INCOMPLETE**（不默认安全，满足 Phase 1.6）。**诚实边界**：所谓“集成 AST/Slither/CFG/
  污点”在本环境对历史数据集不可运行；实际交付的是**语义源码模型分析**（含调用图与数据流启发式），
  而非编译器级 CFG/污点。这是事实，不夸大。

## 2. 跨合约治理权限关系图（`governance_relation_graph`）

四维：A 席位(VULN-1) / B 投票权(VULN-2) / C 提案执行(VULN-3) / D 金库(VULN-4)。为每个 flagged 函数建
节点（合约.函数 + 门禁证据 + 调用链），并判定完整链 =（A∨B∨C）∧D。**单项风险与完整链分别统计，不混淆。**
开发集示例：Tornado/Beanstalk=完整链(B+C+D)，DODO=单项 A，Aave/Compound/OZ/Nouns=无。

## 3. 第三轮独立盲测（引擎冻结校验通过，跑一次，事后未调参）

数据集（全新、与 r1/r2 零重叠；真实源码+commit）：
| 案例 | 类型 | 标注类别 | 架构(新颖点) |
|------|------|----------|--------------|
| parity_wallet | 漏洞 | VULN-1+VULN-4 | 0.4.x 共享库+delegatecall 代理 |
| punk_proxy_init | 漏洞 | VULN-1 | 可升级实现 initialize behind proxy |
| polynetwork_bridge | 漏洞 | VULN-4 | 跨链 manager 无白名单低级 call |
| punk_proxy_init_fixed | 修复版 | — | initializer+onlyCreator |
| nouns_dao | 安全对照 | — | Bravo-fork + timelock + 快照 |

Build Finance 治理接管案例**源码不可得**（链上验证，GitHub 无源码，Etherscan 被封）→ 如实剔除，
不以 lookalike 替代（见 dataset/_UNAVAILABLE_CASES.md）。N_vuln=3 < 10 → **探索性（样本不足）**。

### 指标（独立评分，Wilson 95%CI）
| 指标 | 值 |
|------|-----|
| 总体 Recall | **0.60 (0.23–0.88)** |
| 总体 Precision | **0.23 (0.08–0.50)** |
| 安全/修复对照 FPR | **1.00 (0.34–1.0)**（2/2 安全样本均高危误报） |
| 完整治理风险链识别率 | **0/1 = 0.0** |
| 判定（冻结标准 3 项） | **0/3 → FAIL/UNPROVEN** |

逐类：VULN-1 Recall 0.67(tp2/fn1/fp4)；VULN-4 Recall 0.5(tp1/fn1/fp6)。

## 4. 单项检出 vs 完整链（分别报告）
- 单项命中：**Punk VULN-1 ✓**（可升级实现未保护 initialize——新架构，开发集未见）、
  **PolyNetwork VULN-4 ✓**（跨链 manager 无白名单低级 call——新架构）。这是**真实的跨架构泛化增益**。
- 完整链：Parity 的 A+D 链**未识别**（见下，两类均漏）→ 链识别 0/1。

## 5. 误报 / 漏报 / 不可分析（技术原因，供 round4；不得在 round3 调参）
- **FN Parity（整案漏，located=[]）**：`initWallet`/`kill` 分别被自定义修饰符 `only_uninitialized` /
  `onlymanyowners` 保护，命中了 V0.14 过宽的 `only\w+` 角色门禁启发式 → 被当作“已受保护”抑制；
  而引擎把 VULN-4 误报在 `Wallet.fallback/execute` 等**错误函数**（6 处 FP）。根因：`only\w+` 门禁过宽
  （把失效的一次性守卫 only_uninitialized 当作有效角色门禁）+ 代理 fallback 的 delegatecall 过度触发。
- **FP nouns_dao（安全对照）**：`NounsDAOLogicV4.initialize` 被报 VULN-1；其守卫（admin-by-proxy /
  timelock==0 式一次性检查）未被 init-guard 识别 → 误报。根因：init 守卫模式覆盖仍不全。
- **FP punk_proxy_init_fixed（修复版）**：修复加在实现 initialize，但引擎改报 `ForgeProxy.initialize`
  （代理自身 initialize）→ 修复版误报。根因：代理一次性部署 initialize 未与真实漏洞区分。
- **精度坍塌（0.23）**：Parity 6 处错位 VULN-4 + PolyNetwork `initGenesisBlock` 等额外 VULN-1。
- **ANALYSIS_INCOMPLETE**：全部 round3 合约的编译级 Slither 后端均 ANALYSIS_INCOMPLETE（solc 版本/依赖）。
- **修复前后区分**：Punk 修复版**未**正确区分（改报 ForgeProxy.initialize）；DODO（开发集）修复版正确区分。

## 5b. 动态验证（Phase 5，诚实边界）
各案漏洞版 vs 修复版差异在**源码+commit 级**核验（DODO 守卫 0→2；Punk initialize→initializer+onlyCreator；
Furucombo ban/halt 0→9；Parity 库自毁无源码修复）。完整隔离 EVM 动态复现需各项目 solc<0.8 工具链与依赖，
本环境不可得 → **未对任何真实案例标“动态已验证”**；仅既有 X100 机制在隔离 EVM 的演示另计，不充当真实能力。

## 6. 最终结论：系统能否跨架构识别真实治理安全缺陷？
**FAIL / 未证明。** round3（全新架构）总体 Recall 0.60、Precision 0.23、FPR 1.00、完整链 0/1、验收 0/3。

- **有真实但局部的进步**：V0.14 新增能检出两类此前完全无法处理的真实架构——可升级实现未保护
  initialize（Punk）与跨链 manager 无白名单低级 call（PolyNetwork）；开发集上 4 类历史 FN/FP 已修复。
- **但仍不具备可靠的跨架构能力**：对 0.4.x 共享库+自定义修饰符（Parity）整案漏检并错位误报；对正常
  治理控制（Nouns）与代理部署 initialize（Punk 修复版）误报；精度坍塌至 0.23；安全对照 FPR 100%。
- **跨轮一致的过拟合信号**：开发集 Recall 0.75，在 round3 独立集降到 0.60 且 FPR 升到 1.0——与 round1→
  round2 的模式一致。**因此不能宣称系统能跨不同代码实现可靠识别真实治理安全机制缺陷。**
- round4 改进方向（须在**再一个全新独立集**验证，不得在 round3 调参）：收紧 `only\w+` 门禁为“可证明有效
  约束”、区分代理部署 initialize 与实现 initialize、补全 init 守卫模式（timelock==0/admin-by-proxy）、
  用真实数据流而非函数定位近似来降低错位误报。

## 完整可复现证据
冻结 eval/engine_freeze.json（含 semantic.py+compile_backend.py，校验通过）；round3 原始
eval/results/round3/{raw,scored.json,REPORT.md}；数据集与证据 eval/dataset/<case>/PROVENANCE.md；
答案独立 eval/answers/；开发集(非成绩) eval/results/dev14/；保留 round1 eval/results/round1/ +
FINAL_REPORT_V0.12.md、round2 results/round2/ + FINAL_REPORT_V0.13.md。
复现：`python3 eval/freeze_engine.py --check && python3 eval/blind_run.py --round round3 --case <c> && python3 eval/evaluate.py --round round3 && python3 eval/make_report.py --round round3`。
DAO 安全模块与 DEX 系统全部功能保留未改。
