# V0.18 阶段报告 — 防御性审计：漏报核查、修复版安全不变量验证、能力边界

> 分支 `claude/v018-dev`（自 V0.17 冻结 `f0e4cd2`）。V0.17 冻结文件、Round 1–4 全部原始结果与
> `PREREGISTRATION_V018.md` 未改。本阶段**未开发**权限语义引擎（此前四次被平台安全检查中止，不重试、不改写），
> 未启动 Round 5。所有数字来自开发集，自动 / 人工 / 隔离环境验证 / 无法分析**分别计数，不相加**。
> 只在本地隔离 EVM 与已公开的历史源码上验证；**只运行修复版**，不运行漏洞版，不生成任何资金提取或攻击交易。

## 0. 已验证 vs 未实现
| 项目 | 状态 |
|------|------|
| 25 项漏报的分类、构建失败原因、检测器覆盖核查 | 已完成，分类一致（0 处矛盾）；`v18/miss_classification_check.json` |
| 10 对漏洞/修复版的安全不变量与修复证据 | 已完成：隔离 EVM 验证 3 对、编译 IR 验证 2 对、仅差异复核 5 对；`v18/fixed_version_invariants.json` |
| 隔离 EVM 中的修复版不变量测试（精确历史编译器） | 已完成 3 案 7 项测试全部通过；`v18/invariants/` |
| 自动检出率提升 | **未实现**（引擎未变，仍为 V0.17 冻结版：自动 TP 0/42） |
| B/C 自动覆盖 | **未实现**（B、C 仍为未覆盖；本阶段 B、C 的不变量只在人工编写的测试中验证） |
| A/B/C/D 跨合约权限关系自动分析 | **未实现**（被安全检查中止的工作，不重试） |
| Round 5 准入 | **NOT_READY**（按 `PREREGISTRATION_V018.md` 五项标准，无一项由自动层达成） |

## 1. 25 项漏报核查（`v18/miss_classification_check.json`）
- 分类不变：规则缺失 12、构建失败 5、权限语义不确定 5、证据不足 3；每项与盲测时的构建状态逐一核对，**0 处矛盾**。
- 构建失败 5 项的原因与现状：ENS（npm `solc@0.8.7` 实际内含 0.7.5，V0.16 已修，现以真实 0.8.7 编译）；Olympus（重映射
  缺尾斜杠，已修，0.8.15）；Sushi ×2（monorepo 锁文件取错 OZ 版本，已修，0.8.10）；Bond（该 commit 未记录 solmate/clones
  依赖版本，**仍 ANALYSIS_INCOMPLETE，不猜版本**）。
- 检测器覆盖：VULN-2、VULN-3 的 11 项（含 Olympus 2 项）没有任何 Slither 检测器对应；VULN-1 的 4 项只有依赖名称的
  unprotected-upgrade / tx-origin；VULN-4 的 10 项中，现有 arbitrary-send / suicidal / controlled-delegatecall 只覆盖
  其中 2 项的模式（且因存在保护证据进入复核队列，不是自动确认）。

## 2. 修复版安全不变量验证（`v18/fixed_version_invariants.json`、`v18/invariants/`）
每对给出：维度、不变量、修复差异证据行、可验证方式、V0.17 自动层是否区分、本阶段验证层。
| 漏洞案例 | 维度 | 安全不变量 | 修复证据（diff） | 验证层 | 结果 |
|----------|------|------------|------------------|--------|------|
| moonwell_temporal_governor | C | 快速执行需 owner **且**处于暂停态 | `onlyOwner whenPaused` | 隔离 EVM，solc 0.8.19 精确 | 2/2 PASS |
| llama_relative_quorum | B | 法定人数快照只能由 LlamaCore 重写 | `msg.sender != llamaCore → revert OnlyLlamaCore` | 隔离 EVM，0.8.19 | 1/1 PASS（在逻辑合约上验证，llamaCore 未初始化；已初始化路径走同一检查但未执行） |
| nomad_replica | A+D | 零根不被预批准、零根永不可接受、初始化一次性 | `if (_committedRoot != 0)`、`LEGACY_STATUS_NONE` 拒绝 | 隔离 EVM，0.7.6 | 4/4 PASS（未构造伪造消息，只验证根接受谓词与初始化状态） |
| sandbox_land | D | `_burn` 不可外部调用 | public→internal | 编译 IR（精确编译器）可见性 | VERIFIED |
| bancor_network | D | 三个资产辅助函数不可外部调用 | public→internal | 编译 IR 可见性 | VERIFIED |
| bond_fixed_expiry_teller | D | redeem 只接受本 teller 登记的债券代币 | `token_ != bondTokens[..] → revert` | 仅差异复核 | 需市场 fixture，未运行 |
| lifi_cbridge_facet | D | swap 的 callTo/approveTo 必须在白名单 | `dexWhitelist` 双重检查 | 仅差异复核 | 需 diamond 存储 fixture，未运行 |
| party_veto_proposal | B | 每成员每提案最多计一次 | `hasVoted` 记录与检查 | 仅差异复核 | 需 Party fixture，未运行 |
| sushi_route_processor2 | D | 回调只从路由的 msg.sender 拉取资金 | `require(from == msg.sender)` | 仅差异复核 | 需池 fixture，未运行 |
| daostack_ucontroller | A | 注册前一次读取、再检查再写入 | 缓存 nativeToken/nativeReputation | 仅差异复核 | 调用顺序复核 |
- **自动层对修复版的区分**：V0.17 引擎在 10 对中只有 Sushi 1 对在位置级别区分（漏洞版回调有告警、修复版无），其余 9 对
  自动层漏洞版与修复版都**没有**告警（Bond 不可分析）。修复版区分仍主要靠人工与隔离测试，不是自动能力。
- 工具链事实：Foundry 1.5.1 能通过 `--use` 使用 solc-js 精确版本 shim（整项目输入会触发 wasm 内存越界，需最小化
  编译范围）；子模块按 gitlink 提交物化（含 forge-std 内嵌 ds-test）；每案 `result.json` 记录仓库、commit、子模块 SHA、
  编译器版本串、forge 版本、测试结果与局限。

## 3. 各层计数（开发集，V0.17 冻结引擎未变）
| 层 | 数值 | 说明 |
|----|------|------|
| 自动检出（CONFIRMED 相当） | 0/42 标注项 | 引擎未变（`v17/regression.json`） |
| 自动告警 FP | 3（均在 OZ Governor 对照；1/21 安全样本） | 未变 |
| 复核队列（INCONCLUSIVE） | 19 项：标注缺陷 4、确认误报 4、INCONCLUSIVE 11 | 人工层（`v17/REVIEW_QUEUE.md`） |
| 隔离 EVM 验证（仅修复版） | 3 案 7 测试 PASS | 本阶段新增，验证“修复有效”，不是检出能力 |
| 编译 IR 验证 | 2 案（可见性变化） | 本阶段新增 |
| 无法分析 | 1/28（round4）、0/16（round1–3） | Bond |
| 完整治理链自动识别 | 0/8 链式案例 | 未变 |

## 4. 每项检测的工具局限与人工复核要求（摘要）
- Slither 资产检测器（D）：忽略修饰器与调用点上下文、不建模签名授权、看不到编码为低级调用的转账 → 每条告警需按
  V0.17 证据记录人工复核（入口、msg.sender 条件、签名检查）。
- 初始化/升级（A）：unprotected-upgrade 依赖合约与修饰器名称；自定义守卫、代理存储冲突、角色链无自动判断 → 人工。
- 投票权/快照/法定人数（B）、时间锁/紧急路径/执行授权（C）：无自动检测；可用本阶段方法为修复版编写不变量测试，
  但性质必须由审计人针对项目编写，不能推广为自动能力。
- 构建层：依赖必须可从该 commit 核验；不可核验即 ANALYSIS_INCOMPLETE。

## 5. 安全限制记录
本阶段未触发新的安全限制。未尝试任何被中止的权限语义引擎步骤。

## 6. 复现
`eval/v18/invariants/README.md`（快照、子模块、最小 forge 工程、`forge test --use <shim>`）；
`python3 eval/v18/...` 脚本无（核查与证据由本阶段一次性脚本生成，结果 JSON 已入库）；
V0.17 回归：`python3 eval/blind_run.py --dev --round dev17_r4 --engine baseline17 --dataset eval/dataset_r4`。
