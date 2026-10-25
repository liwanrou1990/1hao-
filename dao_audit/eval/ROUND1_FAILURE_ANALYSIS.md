# 第一轮盲测失败根因分析 (V0.13 输入)

> V0.12 round1 原始结果**永久保留不改**（Recall 22%、链识别 1/3、FPR 50%、验收 0/3）。
> 本文件分析技术失败原因。**自此 round1 的 5 个案例被标记为"开发集(DEV)"**：允许用于改进引擎，
> **不再**作为独立盲测成绩；真实能力以 round2 全新独立集为准。

逐案根因（对照真实源码行号，证据可核验）：

## Audius（漏报 VULN-1/3/4，全漏）
- **VULN-1 初始化接管**：`Governance.initialize`(L187) 带 OZ `initializer` 修饰符 →
  `has_init_guard` 判为“已缓解”而抑制。真实漏洞是**代理 slot-0 proxyAdmin 与实现 initialized 标志
  存储槽冲突**使该守卫失效——属**存储布局语义**，源码正则无法感知。根因：缺代理存储布局分析。
- **VULN-3 执行无时间锁**：执行入口名为 `guardianExecuteTransaction`(L718)，内部执行器
  `_executeTransaction`(L970)。检测器 `_EXEC_NAME_RE` 仅白名单 `execute|_execute|...|run$`，
  两个名字都不匹配 → 漏。根因：**按函数名匹配执行路径（过拟合命名）**。
- **VULN-4 任意外呼**：低级 `.call` 在**内部**函数 `_executeTransaction` 里；检测器只看
  `is_externally_callable()` 的函数自身 body → 内部函数的调用被忽略。根因：**无跨函数调用图**。

## Beanstalk（漏报 VULN-2/3/4，全漏）
- **VULN-2 投票权**：`vote`(L69) 用 `balanceOfRoots(msg.sender)` 计票；检测器 `_LIVE_BAL_RE`
  只认 `balanceOf(`/`getVotes(` → 漏。根因：**活余额识别写死为特定函数名**，不识别自定义计票(roots/stake)。
- **VULN-3 应急执行**：`emergencyCommit`(L180)/`commit`(L169) 名字不在执行白名单 → 漏。同“命名过拟合”。
- **VULN-4 任意 init delegatecall**：`propose`(L35) 存攻击者 init 地址/calldata，真正 delegatecall 在
  `LibDiamond.diamondCut` 内（跨库文件）；检测器只认 propose 自身 body 里的 `.call/.delegatecall` → 漏。
  根因：**不识别 Diamond `diamondCut(init,data)` 这类间接任意外呼 + 无跨文件调用图**。

## OpenZeppelin（误报 VULN-4，安全样本）
- `Governor.relay`(L654) 为 `onlyGovernance`，源码注释明确“onlyGovernance 调用只能经成功提案达成”。
  检测器未把 `onlyGovernance`（经治理+时间锁路由）视为有效约束 → 对合法受控中继误报 High。
  根因：**缺访问控制语境建模**（违反 II.6：受 onlyGovernance/角色/时间锁保护的正常功能不应简单标危）。

## 共性根因 → V0.13 改进方向
1. 用**继承感知的跨函数/跨文件调用图**替代“只看外部函数自身 body”。
2. 用**结构**（消费投票结果 + 对外调用/delegatecall/diamondCut）识别执行路径，替代函数名白名单。
3. **访问控制语境**：onlyGovernance / 角色 / 时间锁路由 / 一次性 init 视为约束；受此保护的正常功能
   不判危（修复 OZ 误报）；仅当约束缺失或可被廉价获取才判危。
4. **泛化投票权**：任何“按地址索引的余额/质押/roots 实时读取参与计票且无逐提案快照”均属 VULN-2。
5. **泛化任意外呼**：`.call/.delegatecall` 到参数/存储地址、`diamondCut(init,data)` 均视为任意外呼，
   按是否有白名单/额度/治理+时间锁约束定级。
6. 每条结论带**代码位置 + 权限/调用关系证据**（哪个修饰符门禁、经哪条内部调用链到达危险汇点）。
7. 存储槽冲突式初始化接管（Audius VULN-1）仍是源码级难点：加启发式信号，但如实承认可能仍漏。
