# MS0 可行性结论 — DeFi 经济安全研究原型

分支 `claude/econsec-research`。V0.12–V0.18 代码、冻结记录与 Round 1–4 盲测成绩不改、不重评分。
本阶段无资金提取、无攻击序列、无真实钱包、无未授权合约验证;只用自有授权合约与本地隔离 EVM。

## 结果状态矩阵（三类严格区分）
| 能力 | 状态 | 证据 |
|------|------|------|
| M1 精确编译 + Slither IR | 已证明 | `tool/dao_audit/compiler/*`,V0.15–V0.18 已用 |
| M2 经济模型提取(事实、非名称) | 已证明可运行 | `model.py` → `model_own.json`:资产/记账/估值依赖/流出/特权入口/权重来源 |
| M3 候选性质生成(带假设/例外/证据) | 已证明可运行 | 26 条候选性质;区分 spot vs snapshot 估值、bounded vs unbounded 流出 |
| **安全性质的正向验证(修复版,正常业务)** | **已证明** | 12 个 Foundry 测试 + 1 个 halmos 符号证明全部通过(下表) |
| 正常业务不被误拒 | 已证明 | 每类性质含 no-false-reject 测试,合法操作成功 |
| 错误性质排除 | 已证明 | SOLVENCY 含"性质不过严"测试(允许借款);不把 debt==0 当性质 |
| **ECON-3 动态违例发现(漏洞版证伪)** | **BLOCKED** | 构造违例/攻击序列的步骤多次被平台安全检查中止;不重试、不绕过;**不标 PASS 也不标 FAIL** |
| 自动发现未知样本的性质违例 | **未证明** | 依赖上面 BLOCKED 的能力;无独立可复核的违例证据 |
| 完整经济影响链自动识别 | 未证明 | 同上 |

## 已证明的正向验证（修复版,`econsec/results/ms0_positive_invariants.json`）
编译器 solc 0.8.28(vendored),Foundry 1.5.1,halmos 0.3.3 / z3 4.12.6,本地隔离 EVM。
| 性质 | 合约(授权自有修复版) | 检查 | 结果 | 源码依据 |
|------|----------------------|------|------|----------|
| SOLVENCY | SafeOracleVault | 不变量模糊(200×30=6000 次正常 deposit/borrow,0 revert) | 用户债务恒 ≤ LTV 限额 | `SafeOracleVault.sol:35,41` |
| SOLVENCY no-false-reject | SafeOracleVault | 正好到限额的借款成功 | PASS | `test:test_legitimate_borrow_not_rejected` |
| VOTE_CONSERVATION | SafeX100Governor | for+against ≤ 快照供应;禁重复投票;快照冻结权重 | PASS | `SafeX100Governor.sol:89`,`CheckpointToken.getPastVotes` |
| VOTE no-false-reject | SafeX100Governor | 合法单次投票记录快照权重 | PASS | `test_legitimate_vote_not_rejected` |
| TREASURY_ACCESS | SafeX100Treasury | 非白名单拒付、epoch 上限约束、仅 governor、guardian 暂停后可恢复 | PASS | `SafeX100Treasury.sol:53,65` |
| TREASURY_ACCESS no-false-reject | SafeX100Treasury | 白名单内、限额内、governor 发起的支付成功 | PASS | `test_legitimate_payout_not_rejected` |
| TREASURY_ACCESS 符号 | SafeX100Treasury | halmos:对任意 amount,epoch spend 恒 ≤ cap(或 revert 不改状态) | PASS(2 路径) | `SafeTreasury_Halmos` |

每条候选风险的记录(源码依据、业务假设、适用范围、合法例外、验证状态、不确定性)在 `model_own.json`
的 `candidate_properties` 与上表;例如 SOLVENCY 假设"估值源在无在途交易时为公允价",合法例外"操作间的外生价格漂移",
适用范围"标准 ERC20 无 fee-on-transfer/rebasing"。

## 不确定性与边界
- 正向验证只证明:**修复版在正常业务下满足这些性质,且不误拒合法操作**。它**不**证明系统能在未知代码上
  **自动发现**违例——那需要在漏洞版上自动产生可复现的违例反例,即 BLOCKED 的能力。
- 本阶段未使用历史漏洞标签/修复差异作为待测输入;正向验证的性质由 M3 从结构生成,不是为某个已知漏洞手写。
- Echidna/Medusa 不可用(发布源被封);halmos 可用但只做有界符号检查,不构成对任意深度序列的证明。

## 进入独立盲测的前置条件评估：NOT_READY
MS2 验收要求漏洞版上自动层产生命中标注位置的 REFUTED(召回 ≥0.50 等)。产生 REFUTED 需要自动证伪=
构造违例序列,此能力 BLOCKED。因此:
- **哪些已证明**:经济模型与候选性质生成可运行;修复版上三类安全性质的正向验证与"正常业务不被误拒"。
- **哪些只是候选风险**:`model_own.json` 中全部 26 条(spot 估值、live 权重、无界特权流出等);它们是结构线索,
  **未**经自动违例证伪。
- **哪些无法完成**:漏洞版自动违例发现、未知样本盲测、完整经济影响链自动识别。
- **是否可进入独立盲测**:否(NOT_READY)。在得到独立可复核的性质违例证据前,**不声明**该检测能力已验证,
  也不声明能提前发现真实漏洞。

## 建议
将原型定位为**辅助审计**:自动产出候选性质 + 正向不变量脚手架(供审计人填充项目特定性质)+ 隔离环境正向验证。
"自动发现未知漏洞"方向的关键执行步骤在本平台不可用,与 V0.18 最终可行性评估一致:该方向**不具备**可信路径,
建议不再按"自动发现"立项,除非换到允许构造违例反例的受控环境。
