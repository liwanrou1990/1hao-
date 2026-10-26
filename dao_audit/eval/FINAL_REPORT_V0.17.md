# V0.17 验收报告 — 权限证据标准化、误报复核、覆盖边界与盲测准入

> 不启动新盲测，不修改 Round 1–4 任何原始成绩与冻结记录。本报告所有数字都是 44 个**开发案例**
> （round1–3 的 16 案 + round4 的 28 案）上的回归结果，不是盲测成绩。人工复核时标签可见，人工结论
> 单独计分，**不计入自动检出率**。全程只读静态分析，未生成交易，未接触任何真实金库。

## 0. 五个问题的直接回答
| 问题 | 结论 | 依据 |
|------|------|------|
| 自动检出率是否真正提高？ | **FAIL — 没有提高，反而下降** | 自动层 TP：V0.16 3/42 → V0.17 0/42 |
| 误报是否真正减少？ | **PARTIAL** | 自动层 FP 13 → 3；出现自动告警的安全/修复样本 7/21 → 1/21。但其中只有 2 条（LI.FI ×2）被数据流**证伪**；4 条经人工确认为误报（人工层）；其余转入 INCONCLUSIVE，**并未被证明是误报** |
| 25 项漏报解决了多少？ | **自动检出 0/25（FAIL）** | 构建层：5 项解析失败中 4 项现已可分析；人工队列层：2 项现进入复核队列（Sushi 回调、Wormhole 升级）；1 项仍只有启发式信号；21 项未检出 |
| B/C 治理风险是否已覆盖？ | **NOT_COVERED** | B（投票权/快照）6 项、C（时间锁/执行）5 项，自动层 0 检出，现有 Slither 能力无对应检查 |
| 是否具备启动全新独立盲测的条件？ | **NOT_READY** | 见 §8：自动召回 0、B/C 未覆盖、修复版严格区分 0/13 |

## 1. 权限证据标准化（`analysis/evidence.py`）
每条 Slither 检测器告警附带一份只读证据记录，事实全部来自 Slither 调用图、IR 与数据依赖
（另加 V0.15 已单测的两项缺口补充：mapping 键依赖、控制依赖型内部返回），**不使用任何名称匹配**：
- 到达该函数的所有 public/external 入口；
- 每个入口（含其修饰器与内部被调函数）中依赖 msg.sender 的 require/assert/if 条件及位置；
- 签名检查（ecrecover、ERC-1271 isValidSignature）；
- 入口“检查并写入同一状态”的事实（可能的一次性守卫，仅为事实）；
- transferFrom 类告警：`from` 是否来自调用者可提供的 address/bytes 参数，以及各调用点 `from` 是否为 msg.sender/this。

状态（互斥）：`REFUTED_BY_DATAFLOW`（告警前提在每个调用点都被数据依赖否定）、`PROTECTION_EVIDENCE` /
`PREMISE_UNCONFIRMED`（→ INCONCLUSIVE，人工复核队列）、`NO_PROTECTION_EVIDENCE`（→ 自动告警）、
`NOT_RESOLVED`（找不到函数 → 保守地留在自动告警）。**任何状态都不表示“安全”。**
V0.15 的“时间条件即降级”规则在 v17 档案中取消（时间条件不等于时间锁）；v15/v16 档案保持可复现。

## 2. 4 项误报复核（详见 `v17/REVIEW_QUEUE.md`）
| 告警 | 检测器 | 源码位置 | 实际访问控制/保护 | 误报原因 | V0.17 处理 |
|------|--------|----------|------------------|----------|-----------|
| Avatar.externalTokenTransferFrom（DAOstack 两版） | arbitrary-send-erc20 | Avatar.sol:95–103 | `onlyOwner`（Avatar.sol:101），owner 为 Controller；证据记录到 msg.sender 条件 | 检测器不考虑访问控制 | INCONCLUSIVE → 人工确认保护有效 |
| LibAsset.transferFromERC20（LI.FI 两版） | arbitrary-send-erc20 | LibAsset.sol:94–101 | internal；两处调用点 from 均为 msg.sender（CBridgeFacet.sol:62、LibSwap.sol:35） | 孤立分析内部库函数 | **REFUTED_BY_DATAFLOW**（自动、机械证据） |
| BondBaseTeller._handleTransfers（Bond 修复版） | arbitrary-send-erc20 | BondBaseTeller.sol:177–224（告警调用 L218） | from 为注册表返回的市场 owner，不来自任何调用者地址参数 | 把非 msg.sender 的 from 一律视为任意 | PREMISE_UNCONFIRMED → 人工确认 |
| GnosisSafe.handlePayment（Safe 对照） | arbitrary-send-eth | GnosisSafe.sol:196–213 | private；execTransaction 先 checkSignatures（L145，退款参数在签名哈希内）；setup 有一次性阈值检查 | 不建模签名授权 | INCONCLUSIVE → 人工确认 |
另：RouteProcessor2.wrapNative（Sushi 两版，dev 重跑新出现）为 INCONCLUSIVE，人工未能完全核实，**保持 INCONCLUSIVE**。

## 3. 为什么 V0.17 把全部历史自动真阳性降级（问题 4）
V0.16 的 3 个自动 TP 加 V0.15 被误降级的 Wormhole 告警，共 4 项，在 V0.17 全部落入复核队列：
| 告警 | 触发的证据 | 性质 |
|------|------------|------|
| Parity WalletLibrary.kill（suicidal） | 3 个 msg.sender 条件（onlymanyowners，经 mapping 键补充可见） | 保护**存在**，但被另一缺陷（任何人可初始化 owner）击穿 |
| Audius Governance._executeTransaction（arbitrary-send-eth） | guardianExecuteTransaction 有 msg.sender 条件；evaluateProposalOutcome 检查并写入提案状态 | 保护存在，被 guardian 可被重新初始化击穿 |
| Sushi uniswapV3SwapCallback（arbitrary-send-erc20） | `msg.sender == lastCalledPool` 条件 | 保护存在，但 lastCalledPool 来自调用者路由 |
| Wormhole Governance.upgradeImplementation（controlled-delegatecall） | 签名检查（守护者签名消息） | 保护存在，被逻辑合约守护者集合可被初始化击穿 |

结论：这 4 项都是**保护存在但被别处缺陷击穿**的真实漏洞——正是 V0.16 归类的“权限语义无法确定”。证据标准
（“存在 msg.sender 条件/签名检查即需人工判断”）判断正确：Slither 的事实无法证明保护是否有效，自动层不应把它们当
作已证实的告警，也不应当作安全。因此这是**转入人工复核，而不是排除误报**。V0.17 中真正被证伪的只有 LI.FI 的 2 条。
**不为提高自动召回率而取消该证据标准。**

开发中修正过一处证据规则缺陷：Sushi 回调的 `from` 由 `bytes` 参数解码而来，初版只把 address 参数视为“调用者可提供”，
错误地给出 PREMISE_UNCONFIRMED；现将 bytes 参数也计入，原因变为正确的 msg.sender 条件证据（分档不变）。

## 4. 25 项漏报逐项分析与处理计划（详见 `v17/MISS_PLAN_25.md`、`v17/miss_plan.json`）
| V0.16 类别 | 项数 | V0.17 状态 | 处理办法（可验证的验收条件） |
|------------|------|-----------|-------------------------------|
| 规则缺失 | 12 | 12 项均未检出 | 针对具体模式评估成熟外部静态规则集；**仅当**它在漏洞项上触发、且在修复版和安全对照上静默才采纳；否则保持未覆盖并写入人工清单 |
| 代码解析失败 | 5 | 4 项现可分析（之后 3 项未检出、1 项进入复核队列）；Bond 仍 ANALYSIS_INCOMPLETE | 依赖必须能从该 commit 的仓库核验，绝不猜版本；修复后按分析结果重新归类 |
| 权限语义不确定 | 5 | 5 项均未检出 | 使用 V0.17 证据记录进行人工复核；经验证的自动权限语义模块因平台安全检查中止开发 → 保持人工 |
| 证据不足 | 3 | 1 项进入复核队列（Wormhole 升级），1 项仅启发式（Pickle），1 项未检出（Sandbox：启发式信号仍落在正确函数 `_burn`，但类别为 VULN-1 而标签为 VULN-4，按定位规则不算检出） | 保留在人工队列，有带代码位置的复核结论才计入（只计人工层） |
**自动解决 0/25。** 构建层改善 4/5（属于工具链，不是检测能力）。

## 5. A/B/C/D 治理权限覆盖（详见 `v17/abcd_coverage.json`）
44 个开发案例共 42 个已标注缺陷项；自动、复核队列、人工三列**分开**，不相加：
| 维度 | 标注项 | 自动检出 V0.16 | 自动检出 V0.17 | V0.17 进入复核队列 | 复核队列确认为标注缺陷 | 人工复核格（round4，缺陷/保护有效） | 工具覆盖 |
|------|--------|----------------|----------------|--------------------|------------------------|-------------------------------------|----------|
| A 席位/管理员 | 9 | 0 | 0 | 0 | 0 | 3 / 2 | 部分（依赖名称的 unprotected-upgrade、tx-origin；启发式） |
| B 投票权/快照 | 6 | 0 | 0 | 0 | 0 | 2 / 2 | **未覆盖** |
| C 执行/时间锁 | 5 | 0 | 0 | 0 | 0 | 1 / 1 | **未覆盖** |
| D 金库/资产 | 22 | 3 | 0 | 4 | 4 | 6 / 7 | 部分（suicidal、arbitrary-send-*、controlled-delegatecall，均有已知误报类） |

每项检查的证据来源、覆盖与置信度：
| 维度 | 检查 | 证据来源 | 覆盖 | 风险结论方式 | 置信等级 |
|------|------|----------|------|--------------|----------|
| A | 未保护初始化（可升级合约） | Slither unprotected-upgrade | 依赖名称，仅命名为 Initializable/upgradeable 的合约 | 自动告警 + 证据记录 | 低 |
| A | tx.origin 授权 | Slither tx-origin | 自动 | 自动告警 | 中 |
| A | 自定义修饰器、一次性守卫、代理存储冲突、角色链 | 证据记录 + variable-order/vars-and-auth 打印器 | 仅事实 | 人工 | 人工判断 |
| B | 快照/实时余额、重复计票、法定人数 | — | **未覆盖** | 人工 | — |
| C | 有效时间锁、紧急路径、guardian 权限 | — | **未覆盖** | 人工 | — |
| D | selfdestruct、任意 ETH/ERC20、可控 delegatecall | Slither 检测器 + 证据记录 | 自动（含已知误报类） | 无保护证据 → 自动告警；有保护证据 → 人工 | 中（自动档）/人工 |
| D | 任意低级调用、应为 internal 的公开辅助函数、未校验合约参数 | 启发式 | 仅启发式/未覆盖 | 人工 | 低 |

## 6. B/C 未覆盖风险：成熟工具可支持的防御性检查与必须人工判断的部分
（只列出，不在本版实现；不绕过平台安全检查）
| 维度 | 可用的成熟防御性手段 | 能给出什么 | 仍需人工判断 |
|------|----------------------|------------|--------------|
| B | Slither 打印器 data-dependency / function-summary / inheritance-graph | 计票变量依赖哪些输入；是否基于已审计的投票权库（事实） | 快照区块选择是否正确、委托语义、法定人数来源与可重置性、重复投票记录 |
| B | Foundry 不变量/模糊测试（本地隔离、无价值测试代币） | 针对人工写定的性质（每人每提案只计一次、权重=快照余额）给出反例或通过 | 性质本身由审计人编写与评审 |
| C | Slither 打印器 vars-and-auth / require / modifiers；timestamp 检测器（仅提示） | 哪些函数在何种条件下写执行相关状态（事实） | 延迟是否覆盖所有执行路径、紧急/guardian 路径、时间锁管理员归属 |
| C | Foundry 不变量测试；solc 内置 SMTChecker（断言） | “eta 之前不能执行”等断言在本地的验证结果 | 断言与路径完整性由人工确认 |
上述手段都需要逐项目编写性质或复核，**不能**作为“自动检测 B/C 风险”的能力宣称。

## 7. 开发集回归（V0.16 vs V0.17，冻结后运行；详见 `v17/regression.json`）
| 集合 | 版本 | 自动 TP/FN/FP | 自动召回（95% CI） | 安全/修复样本出现自动告警 | 修复区分（严格 / 位置级） | 复核队列 TP/FP | 证伪 | 无法分析 |
|------|------|---------------|-------------------|---------------------------|----------------------------|----------------|------|----------|
| r1–3（16 案） | V0.16 | 2/15/5 | 0.12（0.03–0.34） | 2/7 | 0/3 / 0/3 | — | — | 0 |
| r1–3 | V0.17 | 0/17/3 | 0.00（0–0.18） | 1/7 | 0/3 / 0/3 | 2/5 | 0 | 0 |
| r4（28 案） | V0.16 | 1/24/8 | 0.04（0.01–0.20） | 5/14 | 0/10 / 1/10 | — | — | 1 |
| r4 | V0.17 | 0/25/0 | 0.00（0–0.13） | 0/14 | 0/10 / 1/10 | 2/10 | 2 | 1 |
r1–3 剩余 3 条自动告警均在 OpenZeppelin Governor 对照：2 条 NOT_RESOLVED（抽象合约中找不到可部署实例，保守留在自动档），
1 条 TimelockController._execute 为 NO_PROTECTION_EVIDENCE——其角色检查经修饰器传参进入内部函数，证据记录未跟进
这种参数绑定（已知局限，不改为“安全”）。
人工层（单独计分）：复核队列 19 项 = 标注缺陷 4、确认误报 4、INCONCLUSIVE 11；round4 治理检查表人工格：缺陷 12、保护有效 12。
隔离环境确认：0（未执行）。
工具链：dev17 全部 43 个编译单元的编译器版本串与请求一致（0 不符）；依赖、编译器、源码 SHA256 记录在每个原始输出的 `build` 字段；Bond 仍
ANALYSIS_INCOMPLETE（依赖在该 commit 不可核验，不猜版本）。单元/回归测试 31/31 通过。

## 8. 下一轮独立盲测准入评估：**NOT_READY**
| 准入条件 | 现状 | 判定 |
|----------|------|------|
| 自动层在开发集上有可测的召回（例如 ≥0.30） | 0/42 | FAIL |
| B、C 维度至少有部分自动覆盖 | 无 | FAIL |
| 修复版严格区分在开发集上 >0 | 0/13 | FAIL |
| 安全/修复样本自动告警率 ≤0.30 | 1/21 | PASS |
| 构建可分析率 ≥0.95 | 43/44 | PASS |
| 冻结、隔离、评分流程可复现 | 已具备 | PASS |
三项核心能力条件未达标。**不得宣称具备真实 X100 类漏洞的自动识别能力。** 当前系统的准确定位是：
“精确构建 + Slither 告警 + 标准化权限证据 + 人工复核队列”的辅助审计流程。

## 9. 冻结记录
- V0.17 引擎冻结：`eval/engine_freeze.json`（副本 `eval/v17/engine_freeze_v17.json`），包含 baseline.py、evidence.py、
  perm/deps.py、compiler/*、solcjs_std.js、blind_run.py、evaluate*.py、check_isolation.py、v17 报告脚本的 SHA256，
  以及 slither-analyzer 0.11.6、crytic-compile 0.4.2、node v22.22.0；冻结时 git commit 记录于文件内。
- 冻结文件内记录的 git commit 为冻结时的 HEAD（1f70b9b）；冻结所含文件随后在 V0.17 交付提交中一并提交，SHA256 以冻结文件为准。
- 冻结后 `freeze_engine.py --check` 通过，再运行 dev17 回归，回归后再次校验通过。
- Round 4 盲测冻结记录保留于 `results/round4/engine_freeze_round4.json`（及 git 1cbb438）。

## 10. 交付物索引
- 本报告；`v17/MISS_PLAN_25.md` + `miss_plan.json`（25 项漏报）；`v17/REVIEW_QUEUE.md` + `review_queue.json`（19 项复核队列）；
  `v17/abcd_coverage.json`；`v17/regression.json`；`analysis/evidence.py`（权限证据标准化）；
  原始输出 `results/dev17_r123/`、`results/dev17_r4/`。
- 复现：`python3 eval/freeze_engine.py --check && python3 eval/blind_run.py --dev --round dev17_r4 --engine baseline17
  --dataset eval/dataset_r4 && python3 eval/blind_run.py --dev --round dev17_r123 --engine baseline17 &&
  python3 eval/v17/review_queue.py && python3 eval/v17/coverage_and_plan.py`
