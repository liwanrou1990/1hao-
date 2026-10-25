# V0.12 最终报告 — 真实历史合约 DAO 治理漏洞检测能力（第一轮盲测）

> 唯一目标：证明系统能否在**事先不知道漏洞位置与答案**的真实历史合约中自动识别类 X100 的治理
> 控制权失守与金库资金缺陷。方法与通过标准在采集/检测前写定并冻结（`eval/PREREGISTRATION.md`，
> git 提交早于本结果）。**结论只基于真实历史样本 + 独立评分，不以模拟合约成功率充当真实能力。**

## 1. 真实历史案例数量及来源

实际收录 **5 个真实案例**（全部为公开真实合约源码，带 git commit + 公开报告；**未使用**任何
VulnX100Treasury/MiniDAO 模拟合约）：

| 案例 | 类型 | 来源(commit) | 公开报告 |
|------|------|--------------|----------|
| Audius 治理接管 2022-07 | 漏洞(链) | AudiusProject/audius-protocol @01f924a7 | rekt.news/audius-rekt |
| Beanstalk 闪电贷治理 2022-04 | 漏洞(链) | BeanstalkFarms/Beanstalk @e9f49910 | rekt.news/beanstalk-rekt |
| Tornado 治理接管 2023-05 | 漏洞(链) | tornadocash/tornado-governance @6464973 | rekt.news/tornado-gov-rekt |
| Compound GovernorBravo | 安全对照 | compound-finance/compound-protocol @a3214f6 | 带时间锁+快照参考实现 |
| OpenZeppelin Governor | 安全对照 | OpenZeppelin/openzeppelin-contracts @11565e5 | 业界标准参考实现 |

**诚实声明**：真实「漏洞版+修复版+完整证据」案例远难凑满 10 个。本轮 **N_vuln=3 < 10**，按预注册
规则结果判为**探索性（样本不足）**：只报实测数字，**不宣称**系统具备或不具备普适真实世界能力。
不用普通漏洞/人工植入漏洞填充分母。扩充到 ≥10 为后续工作。

## 2. 盲测检测结果（引擎冻结 commit 798dd37，运行前校验哈希通过）

| 案例 | 自动发现 | 命中类别(定位正确) | 链识别 | 安全样本高危误报 |
|------|----------|---------------------|--------|------------------|
| Audius | 否 | — | — | — |
| Beanstalk | 否 | — | — | — |
| Tornado | 是 | VULN-3, VULN-4 | **是** | — |
| Compound(安全) | 否 | — | — | 0 |
| OpenZeppelin(安全) | 是 | — | — | **1 (VULN-4 relay 误报)** |

## 3. 每类治理漏洞识别率（定位正确 TP 口径，Wilson 95%CI）

| 类别 | TP | FN | FP | Recall | Precision |
|------|----|----|----|--------|-----------|
| VULN-1 席位/初始化接管 | 0 | 1 | 0 | 0.00 (CI 0–0.79) | n/a |
| VULN-2 投票权异常 | 0 | 2 | 0 | 0.00 (CI 0–0.66) | n/a |
| VULN-3 提案执行/Timelock | 1 | 2 | 0 | 0.33 (CI 0.06–0.79) | 1.00 |
| VULN-4 金库资金控制 | 1 | 2 | 1 | 0.33 (CI 0.06–0.79) | 0.50 |
| **总体** | **2** | **7** | **1** | **0.22 (CI 0.06–0.55)** | **0.67** |

FNR = 0.78 (CI 0.45–0.94)。

## 4. 组合治理风险链识别率

**1/3 = 0.33 (CI 0.06–0.79)**。仅 Tornado 被识别为完整链（VULN-3 任意 delegatecall 执行 + VULN-4
不受约束外部调用控制金库）；Audius、Beanstalk 的完整链未识别。单独统计，未以发现单一权限问题冒充。

## 5. 安全合约误报率

**FPR = 0.5 (CI 0.09–0.91)**：2 个安全对照中 OpenZeppelin Governor 被误报 1 处——把
`Governor.relay`（受 `onlyGovernance` 约束、只能经提案+时间锁执行的中继）标为 VULN-4 High。
这是真实误报（引擎自标 fp_likelihood=medium）。Compound GovernorBravo 无误报。

## 6. 漏报案例及技术原因

- **Audius VULN-1（初始化接管）漏报**：根因是**代理 slot-0 proxyAdmin 与实现 initialized 标志的
  存储槽冲突**——属跨合约存储布局语义，源码正则检测器结构上看不到；且合约用了 OZ `initializer`
  修饰符，恰好命中检测器的“缓解”负向签名而被抑制。源码级静态引擎的根本盲区。
- **Audius VULN-3/VULN-4 漏报**：执行器 `guardianExecuteTransaction`→`_executeTransaction` 的原始
  `.call.value()` 在内部函数中，未命中金库/执行检测器签名。
- **Beanstalk VULN-2/3/4 全漏报**：Solidity 0.7.6 Diamond facet，投票走 roots、执行走
  `LibDiamond.diamondCut` init delegatecall、emergencyCommit 自定义时间参数——均非 OZ/Compound 式
  命名与结构，现有签名（面向 X100 mock 的命名模式）完全未匹配。暴露**对非标准治理实现的过拟合**。
- **Tornado VULN-2 漏报**：投票权异常（实时 lockedBalance 无快照）未检出；仅检出 execute 的
  delegatecall（VULN-3/4）。

## 7. 修复前后识别对比

- **Audius**：修复是 OZ Initializable 存储布局补丁（patch 文件），**三份 .sol 字节不变**；源码级引擎
  在修复版产出与漏洞版**完全相同**（均为 0 发现）——它既没检出漏洞，也无法感知修复。如实列为盲区，
  不计为“修复版误报”。
- **Beanstalk**：修复为**删除链上治理**（迁 Snapshot），无补丁源码对照。
- **Tornado**：设计级漏洞，无源码修复（治理经事后重新投票在链上恢复）。
- 安全对照（Compound/OZ）充当“已是安全实现”的对照：Compound 正确零误报；OZ 出现 relay 误报。

## 8. 完整可复现测试证据

- 预注册与冻结标准：`eval/PREREGISTRATION.md`（提交 396b0a2，早于结果）。
- 引擎冻结：`eval/engine_freeze.json`（4 文件 SHA256 + commit 798dd37），`blind_run.py` 运行前校验通过。
- 盲测原始输出：`eval/results/round1/raw/*.json`（引擎只读 contracts/ 产出）。
- 独立评分：`eval/results/round1/scored.json`（`evaluate.py` 事后读 answers/）。
- 数据集与证据：`eval/dataset/<case>/`（真实源码 + PROVENANCE.md），答案 `eval/answers/`（独立存储）。
- 复现：`python3 eval/freeze_engine.py --check && python3 eval/blind_run.py && python3 eval/evaluate.py && python3 eval/make_report.py`。

### 关于四（自动化安全验证 / 隔离 EVM 复现）的诚实边界
这些真实事件均在 **以太坊主网**。本沙箱出站白名单仅可达 Base/Arbitrum L2 RPC；已实测
eth.llamarpc / publicnode / ankr / cloudflare-eth 均不可用（封锁/需 key/不提供历史 eth_call）。
因此**无法**在隔离 EVM 中以主网归档分叉在漏洞区块复现这些真实历史攻击；故**不对任何真实案例标记
“动态已验证”**，本轮能力结论**仅**基于静态盲测。此前 X100 模拟合约的隔离 EVM 复现是**另一回事**，
**不计入**真实世界能力。真实案例的动态复现需具备主网归档节点的环境，列为后续工作。

## 9. 明确结论：系统是否具备真实 DAO 治理风险检测能力？

**当前判定：FAIL / 未证明（探索性样本）。** 在 3 个真实历史治理失守案例上，冻结引擎盲测总体
Recall 仅 **0.22**、完整链识别 **1/3**、安全对照 FPR **0.5**，三项冻结通过标准**全部未达**。

- 系统**仅能**检出与其既有签名形态相符的模式（Tornado 的 execute 任意 delegatecall → VULN-3/4）。
- 系统**无法**检出：代理存储槽冲突式初始化接管（Audius VULN-1）、非标准实现的闪电贷治理 +
  diamondCut 任意 init（Beanstalk）、无快照投票权（VULN-2 普遍漏报）。暴露出**对 X100 式命名/结构的
  过拟合**与对真实多样实现的弱泛化，并在合法受控的中继接口上**误报**。
- 因此：**不能**基于本轮证据宣称系统具备真实世界 DAO 治理漏洞检测能力。模拟合约上的高成功率
  **不代表**真实能力。

下一步（预注册第 5 节）：**保留本轮原始结果**，仅依据上述漏报技术原因改进规则（语义化初始化/
存储冲突、通用任意外部调用/delegatecall、快照缺失、非 OZ 命名），并在**全新独立案例集**上复测，
**不在本 round1 集上反复调参后宣称成功**。
