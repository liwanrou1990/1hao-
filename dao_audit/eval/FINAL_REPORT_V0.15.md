# V0.15 最终报告 — 历史编译器工具链 + Slither 只读审计基线 + Round 4 独立盲测

> 结论先行：**未证明检测能力。** Round 4（28 个全新案例）主评分轨 Recall **0/25**、完整治理风险链 **0/4**、
> 修复版区分 **0/10**。按冻结规则字面计算为 PARTIAL（仅 FPR 一项达标），按保守口径（不可分析的安全样本
> 不计为正确阴性）为 FAIL。两种口径下都不能宣称系统能识别真实治理缺陷。
> round1–3 原始结果与 V0.12–V0.14 报告均保留未改；round1–3 案例仅作开发集。

## 1. 本版实际完成了什么
1. **历史编译器工具链（真实可用）**：经 npm `solc`（官方 emscripten 构建）获取精确版本 0.4.10–0.8.x；
   ≤0.4.10 旧版输出转换为 standard-JSON；按案例从原仓库 commit 的 `git archive` 快照重建（无历史，
   防修复信息泄漏）；编译器版本优先取项目自身配置（truffle/hardhat/foundry/dapp/Dockerfile/CI），否则按
   pragma 搜索并记录每次尝试；npm 依赖取 lockfile，否则按 commit 日期解析范围；git 子模块按 gitlink 提交。
   每案记录编译器版本串、soljson SHA256、依赖版本与文件哈希、未解析导入、覆盖率。
   - 开发集 16/16 编译并载入 Slither IR（含 Parity 必须精确 0.4.10）。
   - Round 4：23/28 成功，**5 例 ANALYSIS_INCOMPLETE**（见 §4），全部保留在评分中。
   - **更正 V0.14**：V0.14 称“多版本 solc 在本环境不可行”是错误的——当时未尝试 npm 途径。
2. **Slither 数据依赖能力单测**（27 项，0.4.24/0.5.17/0.8.19 均通过）：确认 Slither 原生支持内部函数数据返回、
   局部别名、跨函数状态依赖、外部调用参数；**不支持** mapping 键依赖（`m[msg.sender]`）与控制依赖型内部返回。
   `perm/deps.py` 只补这两项。
3. **只读审计基线**（`analysis/baseline.py`）：仅用 Slither 内置能力。
   - 主评分轨：内置检测器 → VULN 类（unprotected-upgrade / tx-origin / protected-vars → VULN-1；suicidal /
     arbitrary-send-eth/erc20/erc20-permit / controlled-delegatecall → VULN-4）。
   - 探索轨：基于 `is_protected()`、条件读、数据依赖的信号，状态为 HEURISTIC_ONLY 或 INCONCLUSIVE，
     **不是漏洞结论**，不参与判定。
   - 开发集规则（round4 前固定）：只计案例自身合约文件内的结果；若到达该函数的所有入口都受时间/生命周期
     条件约束（Slither 无法评估 timelock），降为 INCONCLUSIVE。
4. **隔离**：独立评分器 `evaluate_v15.py`（旧 `evaluate.py` 未改）；`check_isolation.py` 静态检查（71 个
   round4 标识符未出现在检测/构建/运行代码中）与运行时检查（审计钩子记录检测进程打开的每个 eval/ 文件，
   28 次运行均只打开本案 contracts/ 与 BUILD.json）均 **PASS**。Round 4 标签由独立代理整理，我未读取。
5. **冻结**：`PREREGISTRATION_R4.md` 与 `engine_freeze.json`（文件 SHA256 + slither 0.11.6 / crytic-compile
   0.4.2 / node v22.22.0）在运行前同一提交（1cbb438）写定；只运行一次，无中途调参。

## 2. 开发集（round1–3，非盲测成绩）
| 轨道 | Recall | Precision | 安全/修复 FPR | 修复版区分 | 完整链 |
|------|--------|-----------|---------------|------------|--------|
| 主评分轨 | 0.12 (2/17) | 0.29 | 0.29 (2/7) | 0/3 | 0/4 |
| 含启发式 | 0.24 (4/17) | 0.07 | 0.86 | 1/3 | 1/4 |

不可分析 0/16。注意：Parity `kill` 的命中来自 Slither `suicidal`，原因是 Slither 看不到 `onlymanyowners`
内的 owner 检查（mapping 键缺口），**不是**理解了接管链。

## 3. Round 4 正式盲测结果（28 案：14 漏洞 / 10 修复 / 4 安全对照；4 个链式案例）
| 指标 | 主评分轨 | 含启发式（探索，不判定） |
|------|----------|--------------------------|
| 总体 Recall | **0.00 (0/25)**, CI 0–0.13 | 0.08 (2/25) |
| 总体 Precision | 0.00 (0/6) | 0.03 (2/69) |
| 普通权限风险检出（漏洞案例≥1 处定位正确） | **0/14** | 2/14（pickle_controller、wormhole_core_impl，均 VULN-4） |
| 安全/修复 FPR | 4/14 = 0.29（CI 0.12–0.55）；排除 2 个不可分析安全样本为 4/12 = 0.33 | 9/14 = 0.64 |
| 修复版区分率 | **0/10** | 0/10 |
| 完整治理风险链识别 | **0/4**（引擎未声明任何链） | 0/4 |

逐类（主轨）：VULN-1 0/4、VULN-2 0/3、VULN-3 0/2、VULN-4 0/16（另 6 个 FP）。
VULN-2/VULN-3 无 Slither 能力，召回 0 属预期，按“未覆盖”报告，不视为安全。

按来源（主轨，漏洞案例检出数/案例数）：真实攻击事件·已核实 0/6；真实攻击事件·来源不确定 0/2；
审计发现·已核实 0/3；审计发现·不确定 0/1；漏洞赏金·不确定 0/2。共 7 个案例来源标注为不确定。

误报（主轨）：Avatar.externalTokenTransferFrom（漏洞版与修复版同时出现）、LibAsset.transferFromERC20
（同上）、BondBaseTeller._handleTransfers、GnosisSafe.handlePayment —— 均为 Slither arbitrary-send-erc20 /
arbitrary-send-eth 对正常资产路径的触发，且漏洞版与修复版输出相同，说明基线无法区分修复。

### 判定（冻结标准，主评分轨）
- Recall ≥0.70 且 CI 下界 ≥0.50：**未达**（0.00）
- 完整链识别率 ≥0.50：**未达**（0/4）
- 安全 FPR ≤0.30：字面 0.29 **达标**；但其中 2 个安全样本未能分析，按保守口径 0.33 **未达**。预注册未写明
  不可分析安全样本如何计入 FPR，故两种结果都报告。
- **字面判定 PARTIAL（1/3），保守判定 FAIL（0/3）。** 两者实质相同：零检出、零链识别、零修复区分。

## 4. 不可分析样本（未剔除，原因均为构建限制）
| 案例 | 原因 |
|------|------|
| bond_fixed_expiry_teller | foundry 子模块路径 `lib/solmate/...` 未解析 |
| ens_governor（安全对照） | 选定编译器报 TypeError（版本选择不当） |
| olympus_governance | 重映射无尾斜杠导致路径拼接错误（`src/modulesINSTR.sol`），构建器缺陷 |
| sushi_route_processor2 / _fixed | `@openzeppelin` 依赖未能解析 |
这些是构建器缺陷，按预注册**未在盲测后修复重跑**。

## 5. 限制（必须明确，不得视为安全证明）
1. **完整权限语义引擎未建成。** 计划中的调用者/角色授权、初始化与代理存储、投票权来源、Timelock/执行条件、
   delegatecall/Yul、金库与跨合约关系图模块均未完成：开发过程中写入该引擎首个模块的步骤两次被安全检查中止，
   按要求未绕过。因此 A/B/C/D 跨合约权限图、“已证明的完整链”均**不存在**于本版。
2. `is_protected()` 含名称捷径（名为 `onlyOwner` 的修饰器即视为受保护），且把“msg.sender 作为任意调用参数”
   视为保护；自定义权限、一次性初始化守卫、代理存储冲突、Timelock 均无法判断 → 只能 INCONCLUSIVE。
3. Slither 检测器只覆盖少数权限/资产模式；VULN-2、VULN-3 完全未覆盖。
4. 启发式轨噪声极高（round4 精度 0.03），仅供人工审计排查线索。
5. 全部为**静态只读**分析；未做任何隔离 EVM 动态验证，未生成交易，未接触真实金库。
6. 开发集分诊规则基于 round1–3 观察制定（已在运行前冻结），属开发集信息，可能对其他数据有偏。

## 6. 复现与证据
- 原始输出：`eval/results/round4/raw/*.json`（含构建清单、编译器版本、检测进程打开的文件列表）；
  运行日志 `results/round4/run.log`；评分 `results/round4/scored_v15.json`；隔离检查 `results/round4/isolation_check.txt`。
- 开发集：`eval/results/dev15/`。
- 复现：`python3 eval/freeze_engine.py --check && python3 eval/blind_run.py --round round4 --engine baseline
  --dataset eval/dataset_r4 && python3 eval/check_isolation.py --dataset eval/dataset_r4 --round round4 &&
  python3 eval/evaluate_v15.py --round round4`
- DAO 审计模块、Foundry、DEX 系统全部保留未改；未连接真实第三方金库，未执行真实资金转移。

## 7. 下一步需要合规补充的能力（按对结果影响排序）
1. 构建器：子模块路径、无尾斜杠重映射、依赖解析、按 pragma 回退编译器版本（解决 5 例不可分析）。
2. mapping 键与控制依赖之上的授权判断（已有 `perm/deps.py` 与 27 项单测作为基础）。
3. 投票权快照与 Timelock 执行条件分析（VULN-2/3 目前零覆盖）。
4. 修复版区分：目前漏洞版与修复版输出完全相同，需要能识别新增守卫的分析。
任何新增能力都只能在新的独立数据集上验证；round4 自此只能作为开发集。
