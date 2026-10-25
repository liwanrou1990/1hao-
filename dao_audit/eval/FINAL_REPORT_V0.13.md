# V0.13 最终报告 — 语义引擎改进 + 第二轮独立盲测

> round1 原始结果（Recall 22%、链识别 1/3、FPR 50%、验收 0/3）**永久保留未改**（commit f7764ed）。
> round1 五案已转为**开发集**（可调参，不再作独立盲测成绩）。本报告的能力结论以 **round2 全新独立集**为准。
> 不以模拟合约或开发集的高分充当真实世界能力证明。

## 1. V0.13 检测引擎改进（`analysis/semantic.py`）

从“函数名 + 正则”升级为**语义源码模型分析**：继承感知的跨文件/跨函数**调用图**（外部入口的“有效体”含其
递归调用的内部/基类函数）；**访问控制语境**（onlyGovernance+时间锁路由 / 一次性 init / 角色 / 两步 admin
交接视为约束，受保护的正常功能不判危）；**泛化汇点**（低级 call/delegatecall 含 0.5.x 语法 + Diamond
diamondCut）；**泛化投票权**（任意按地址索引实时余额/质押/roots 计票且无快照）；**结构化执行路径**（消费
投票结果 + 命中汇点 + 无真正 timelock）。每条结论带代码位置 + 门禁/调用链证据。

**诚实边界**：本环境无 Slither、仅 solc 0.8.28，历史合约 0.5–0.7 带依赖树不单独编译 → 这是**语义源码模型
分析器，不是编译级 AST / Slither 管线**（任务一所列 AST/Slither/调用图中，“编译级 AST 与 Slither”未实现，
已用源码级调用图与语义启发式替代并如实声明）。

## 2. 第一轮失败根因（详见 ROUND1_FAILURE_ANALYSIS.md）

命名过拟合（执行路径按名白名单）、无跨函数调用图（内部汇点漏归属）、活余额写死 balanceOf、未建访问控制
语境（onlyGovernance 误报）。开发集上改进后：Recall 0.22→0.67、FPR 0.5→0.0（Compound/OZ 清零）、
Tornado 3/3 链识别（**开发集 tuned-on 数字，非盲测成绩**，见 results/dev/）。

## 3. 第二轮独立盲测数据集（全新、未参与规则开发）

| 案例 | 类型 | 类别 | 来源(commit) |
|------|------|------|--------------|
| dodo_dvm | 漏洞 | VULN-1 未保护 init | DODOEX/contractV2 @94ba7fb (fix 01c5447) |
| dschief_governance | 漏洞 | VULN-2 无快照投票 | dapphub/ds-chief @4a833cd |
| furucombo_proxy | 漏洞 | VULN-4 不受约束 delegatecall | dinngo/furucombo-contract @e416ad1 (fix b096f10) |
| dodo_dvm_fixed | 修复版 | — | DODOEX/contractV2 @01c5447 |
| furucombo_proxy_fixed | 修复版 | — | dinngo/furucombo-contract @b096f10 |
| aave_governance_v2 | 安全对照 | — | aave/governance-v2 @d4e5ae0 |

与 round1（Audius/Beanstalk/Tornado/Compound/OZ）**零项目重叠**。N_vuln=3 < 10 → 按预注册仍为**探索性
（样本不足）**，只报实测数字，不宣称普适能力。覆盖 初始化/投票权/金库-代理 delegatecall 三类不同架构。

## 4. 第二轮原始结果（引擎冻结 freeze 校验通过，运行一次，事后未调参）

| 指标 | 值 (Wilson 95%CI) |
|------|-------------------|
| 总体 Recall | **0.333 (0.06–0.79)** |
| 总体 Precision | 0.333 (0.06–0.79) |
| 安全/修复对照 FPR | **0.667 (0.21–0.94)**（3 个安全样本中 2 个被高危误报） |
| 完整治理风险链识别率 | n/a（本轮无链式案例） |
| 验收（对照冻结标准 3 项） | **0/3 → FAIL/UNPROVEN** |

逐类：VULN-1 Recall 1.0（DODO ✓）；VULN-2 Recall 0.0（DSChief 漏）；VULN-4 Recall 0.0（Furucombo 漏）。

逐案：
- **dodo_dvm → VULN-1 命中 DVM.init ✓（TP）**：真正的泛化增益——与 X100 命名/结构不同的普通未保护 init 被检出。
- dschief_governance → **漏报（FN）**。
- furucombo_proxy → **漏报（FN）**。
- dodo_dvm_fixed → **误报（FP）**：修复版仍报 VULN-1。
- aave_governance_v2 → **误报（FP）**：VULN-4。
- furucombo_proxy_fixed → 无发现（正确）。

## 5. 误报 / 漏报清单与技术原因（供未来 round3，不得在 round2 上调参）

- **FN DSChief VULN-2**：投票权来自 `deposits[]` 映射；冻结引擎 live-weight 词表含 `deposited` 不含
  `deposits` → 漏。根因：活权重仍是关键字清单，未真正数据流分析。
- **FN Furucombo VULN-4**：汇点是**内联汇编** `delegatecall(gas,_to,...)`；引擎只认成员访问 `.delegatecall` →
  漏。根因：未解析 Yul/assembly 的低级调用。
- **FP dodo_dvm_fixed VULN-1**：修复守卫为 `require(!_DVM_INITIALIZED_)`（全大写标志）；引擎 init-guard
  正则 `[Ii]nitialized` 不匹配全大写 `INITIALIZED` → 未识别修复 → 修复版误报。根因：守卫识别大小写/命名脆弱。
- **FP aave ExecutorWithTimelock.executeTransaction VULN-4**：这是真正的时间锁执行器（带排队哈希+延迟），
  但引擎 `_is_timelock_exec` 的排队哈希/延迟特征未匹配 Aave 的实现 → 误报。根因：时间锁执行器识别不完整。

**修复前后区分准确率**：DODO 修复版**未**被区分（仍误报，0/1）；Furucombo 修复版被正确区分（1/1）；合计 1/2。

## 5b. 隔离 EVM 验证（五）——诚实边界
对 DODO/Furucombo，漏洞版与修复版的差异已在**源码 + commit 级**核验（DODO 守卫 0→2；Furucombo
halt/ban 0→9）。完整隔离 EVM 动态复现需各项目自身工具链（solc 0.6.x + 依赖树 / hardhat），本环境仅
solc 0.8.28、无法单独编译这些历史合约，故**未执行真实案例的隔离 EVM 动态复现**，不对任何真实案例标“动态
已验证”。仅使用本地隔离环境与无价值资产的既有 X100 机制演示**另计**，不充当真实世界能力。

## 6. 完整验收结论：系统能否跨架构识别真实 DAO 治理缺陷？

**判定：否 / 未证明（探索性）。** 在全新独立 round2 集上，冻结引擎总体 Recall **0.33**、FPR **0.667**、
验收 **0/3**。

- V0.13 相比 round1 有**真实但有限的泛化**：新增可检出“普通未保护初始化接管”（DODO VULN-1，与 X100
  命名无关），并消除了 round1 的 OZ onlyGovernance 误报（开发集 FPR 0.5→0.0）。
- 但在独立集上**仍不具备**跨架构能力：漏掉 `deposits` 式投票权（DSChief）、汇编 delegatecall（Furucombo），
  并在全大写 init 守卫（DODO 修复版）与真实时间锁执行器（Aave）上误报。
- **开发集 Recall 0.67 未能在独立集复现（降至 0.33）**——正是预注册警示的小样本过拟合；因此**不能**宣称
  系统已能跨不同代码架构识别真实 DAO 治理安全缺陷。改进方向明确（真数据流的权重/汇点分析、Yul 汇编调用、
  守卫识别鲁棒性、时间锁执行器识别），但须在**再一个全新独立集（round3）**上验证，**不得在 round2 上调参**
  后宣称成功。

## 完整可复现证据
- 冻结: eval/engine_freeze.json（含 semantic.py，校验通过）。
- 盲测原始: eval/results/round2/raw/*.json；评分 eval/results/round2/scored.json；报告 REPORT.md。
- 数据集+证据: eval/dataset/<case>/PROVENANCE.md；答案独立存 eval/answers/。
- 开发集(非成绩): eval/results/dev/。round1(保留): eval/results/round1/ + FINAL_REPORT_V0.12.md。
- 复现: `python3 eval/freeze_engine.py --check && python3 eval/blind_run.py --round round2 --case <c> && python3 eval/evaluate.py --round round2`。
