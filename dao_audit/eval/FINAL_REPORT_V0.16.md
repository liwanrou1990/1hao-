# V0.16 报告 — 检测能力审计、Round 4 失败分析与防御性审计流程

> 本版不启动新盲测。Round 1–4 历史成绩全部保留未改（round4 原始盲测输出 `results/round4/` 未动，
> 冻结记录另存 `results/round4/engine_freeze_round4.json`）。Round 4 的 28 个案例自此只作开发集。
> 本版所有数字都是**开发集**数字，不是盲测成绩；人工复核时标签可见，不计为检测能力。

## 0. 结论
- **能自动检测的范围很窄**：只有 Slither 内置的几类资产控制检测器（suicidal、arbitrary-send-eth/erc20、
  controlled-delegatecall）和 tx-origin。它们都有已记录的误报类别，只能作为“需复核的告警”。
- **投票权（B）与时间锁/执行（C）完全没有自动检测能力**，治理席位（A）只有依赖名称的 unprotected-upgrade
  和高噪声启发式信号。
- Round 4 的 25 个漏报里，12 个是“没有对应规则”，5 个是构建失败（其中 4 个是我的构建器缺陷，已修复），
  5 个是权限语义无法确定，3 个是只有启发式信号（证据不足）。6 个误报全部是检测器忽略了已存在的保护。
- 自动检测、人工复核、隔离环境确认三层分别计分；隔离环境确认本版**未执行**（0 项）。

## 1. 检测能力覆盖矩阵（详见 `v16/capability_matrix.json`）
工具：Slither 0.11.6 / crytic-compile 0.4.2 / node v22.22.0 / solc-js 精确版本（V0.16 起校验真实版本号）。
dev 实测 = 44 个开发案例（round1–4）上按定位规则计的 TP/FP。

| 维度 | 检查 | 工具 | 状态 | dev 实测 |
|------|------|------|------|----------|
| A 席位 | 可升级合约未保护的 initialize | unprotected-upgrade | **依赖名称**（需继承名为 Initializable 的合约；只认名为 initializer 的修饰器） | 0 TP / 0 FP |
| A | tx.origin 授权 | tx-origin | 自动 | 0 / 0 |
| A | 受注解保护的变量 | protected-vars | 不适用（需 `@custom:security` 注解） | — |
| A | 未保护函数写入被 msg.sender 检查的状态 | is_protected + 条件读（启发式） | 仅启发式 | 3 TP / 87 FP |
| A | 自定义 modifier、一次性初始化守卫、签名授权 | — | **未覆盖** | — |
| A | 代理/实现存储布局 | variable-order 打印器 | 仅事实 | — |
| B 投票权 | 快照/实时余额、重复计票、法定人数快照 | — | **未覆盖** | — |
| C 执行 | 有效时间锁、紧急路径绕过 | — | **未覆盖**（timestamp 检测器仅提示，时间条件≠时间锁） | — |
| D 金库 | 未保护 selfdestruct | suicidal | 自动（有已知误报类） | 1 / 0 |
| D | 向任意地址转 ETH | arbitrary-send-eth | 自动（有已知误报类） | 1 / 7 |
| D | 任意 from 的 transferFrom | arbitrary-send-erc20 | 自动（有已知误报类） | 1 / 6 |
| D | 可控目标 delegatecall | controlled-delegatecall | 自动（有已知误报类） | 1 / 5（V0.15 分诊全部降级） |
| D | 无 value 的任意低级调用 | 基线启发式（过程内） | 仅启发式 | 1 / 6 |
| D | 本应 internal 的公开资产辅助函数、未校验的合约参数 | — | **未覆盖** | — |

`is_protected()` 本身的两个已知缺陷：名为 `onlyOwner` 的修饰器直接视为受保护；任何把 msg.sender 作为调用
参数的函数也视为受保护。`all_conditional_solidity_variables_read` 不跟进内部函数里的 msg.sender 比较
（`require(isOwner())`），这正是 88mph `NFT.init` 漏检的原因。

## 2. Round 4 失败原因分析（详见 `v16/round4_failure_analysis.json`，含位置、编译器版本串、soljson 哈希）
### 2.1 漏报分类（25 项，每项唯一类别）
| 类别 | 数量 | 案例/函数 |
|------|------|-----------|
| 检测器无对应规则 | 12 | Bancor 三个公开辅助函数（转账以低级调用+编码选择器发出）；DAOstack newOrganization；LI.FI 两处任意调用；Llama 法定人数快照；Moonwell 紧急执行；Nomad initialize/process；Party 重复计票；Pickle swapExactJarForJar |
| 代码解析失败 | 5 | Bond redeem（依赖未在仓库记录）；Olympus 两项（重映射无尾斜杠）；Sushi 两项（monorepo 锁文件取错 OZ 版本） |
| 权限语义无法确定 | 5 | DAOstack sendEther（权限可经注册获得）；88mph init/mint/burn（owner 检查在内部函数里）；Wormhole initialize（自定义初始化守卫，unprotected-upgrade 依赖名称） |
| 证据不足（仅启发式） | 3 | Pickle add_liquidity（启发式命中）；Sandbox _burn（启发式命中但类别为 VULN-1）；Wormhole upgradeImplementation（controlled-delegatecall 命中，被 V0.15 时间分诊错误降级） |

按来源：真实攻击事件 15 项、审计发现 7 项、漏洞赏金 3 项（以项计）；来源不确定的案例（7 个）单独标注于 JSON。

### 2.2 无法分析（5 例 → 修复后 1 例）
| 案例 | 根因 | V0.16 处理 |
|------|------|-----------|
| ens_governor | npm `solc@0.8.7` 实际内含 **0.7.5** 编译器（官方正确包为 `0.8.7-fixed`），构建器未校验真实版本 | 已修：安装后校验版本串，不符则试 `-fixed`，仍不符则报错 |
| olympus_governance | foundry.toml 重映射缺尾斜杠，与 remappings.txt 合并未规范化 | 已修：规范化+去重 |
| sushi_route_processor2 / _fixed | monorepo yarn.lock 取了第一个条目（OZ 3.1.0），而包声明 4.8.0；缓存中残留旧版本链接 | 已修：先取最近 package.json 声明，再按该范围查锁文件；替换过期链接 |
| bond_fixed_expiry_teller | 该 commit 未记录 solmate 等子模块/锁定版本 | **保留 ANALYSIS_INCOMPLETE**（版本无法确定，不猜测） |
同一编译器缺陷还使 LI.FI 两个案例在盲测中偏离项目锁定的 0.8.7、改用 0.8.38 编译（编译成功，但违反精确版本锁定）。
其余 52 个已安装编译器版本均校验一致。新增 4 项工具链回归测试（`tests/perm/test_toolchain_fixes.py`），31/31 通过。

### 2.3 修复后开发集重跑（不计成绩）
| 集合 | 主轨 TP/FN/FP | 安全/修复样本出现高危告警 | 无法分析 |
|------|---------------|----------------------------|----------|
| round1–3（16 案） | 2 / 15 / 5 | 2/7 | 0 |
| round4（28 案） | 1 / 24 / 8 | 5/14 | 1 |
round4 新增的 1 个 TP：Sushi `uniswapV3SwapCallback`（arbitrary-send-erc20），且修复版不再告警——唯一一个
在位置级别区分了修复版的案例；但修复版仍有 `wrapNative` 告警，按严格口径修复区分仍为 0/10。
时间分诊规则对比：去掉该规则 +1 TP、+7 FP（dev），安全样本告警数不变；故不在 round4 上调参，规则保留并记录取舍。

## 3. 误报与漏报技术分类
**误报（6 条告警，4 个案例 + dev 重跑新增 1 类）**
| 告警 | 触发原因 | 实际存在的正常保护（证据） |
|------|----------|------------------------------|
| arbitrary-send-erc20 · Avatar.externalTokenTransferFrom（DAOstack 漏洞版+修复版） | 检测器不看访问控制 | `onlyOwner`（Avatar.sol:101），owner 为 Controller |
| arbitrary-send-erc20 · LibAsset.transferFromERC20（LI.FI 漏洞版+修复版） | 孤立分析 internal 库函数，不看调用点 | 两处调用都传 `msg.sender` 作为 from（CBridgeFacet.sol:62、LibSwap.sol:35） |
| arbitrary-send-erc20 · BondBaseTeller._handleTransfers（Bond 修复版） | 把非 msg.sender 的 from 一律视为任意 | from 为注册表返回的该市场 owner（BondBaseTeller.sol:184–218），是事先授权的做市方 |
| arbitrary-send-eth · GnosisSafe.handlePayment（Safe 安全对照） | 不建模签名授权 | private，仅在 `checkSignatures` 之后由 execTransaction 调用（GnosisSafe.sol:145），退款参数在签名哈希内 |
| arbitrary-send-eth · RouteProcessor2.wrapNative（dev 重跑，Sushi 两版） | 路由器按设计向路由指定接收方付款 | 金额来自调用者自己的输入 |
这些都是普通资产转移告警，**不计为治理金库漏洞**。

**漏报**见 §2.1；四类根因分别对应：缺规则（需新增分析能力）、构建（工具链）、权限语义（自定义守卫/内部函数条件/角色链）、
证据不足（只有启发式信号或分诊降级）。

## 4. 人工复核报告（详见 `v16/governance_check_report.json`、`v16/fix_diffs/*.diff`）
复核者可见标签与修复差异，因此只作为确认，**不计入自动检测**。
- **公开修复差异**（10 对，全部提取）：Bancor 三个辅助函数 public→internal；DAOstack 先缓存 nativeToken/
  nativeReputation 再检查；LI.FI 加 dexWhitelist 校验；Llama 加 `msg.sender == llamaCore`；Moonwell 紧急执行加
  `whenPaused`；Nomad 不再预批准零根并拒绝默认根；Party 加 `hasVoted` 记录；Sandbox `_burn` 改 internal；
  Sushi 回调不再信任池子提供的 from；Bond 校验 token 来自注册表。
- **重点检查**：自定义 modifier（DAOstack 方案权限、88mph onlyOwner 经内部函数）、代理/初始化保护（88mph init 无守卫、
  Nomad initialize 有守卫但信任零值、Wormhole 守卫读取逻辑合约自身存储）、有效时间锁（Moonwell 紧急路径）。
- **A/B/C/D 治理检查结果（28 案 × 4 问 = 112 格）**

| 层 | 证据充分·存在缺陷 | 证据充分·保护有效 | 证据不足 | 无法分析 | 未复核/未执行 |
|----|------------------|------------------|----------|----------|---------------|
| 自动（Slither 基线） | 0 | 0 | 108 | 4 | — |
| 人工复核 | 12 | 12 | 6 | — | 82 |
| 隔离环境确认 | 0 | 0 | 0 | — | 112（未执行） |
自动层按设计从不给出“证据充分”：Slither 的事实不能证明某个保护有效。

## 5. 现有工具实际可实现的防御性检查清单
可自动执行（结果均需人工复核后才能定性）：
1. 精确历史编译器构建 + 依赖/编译器版本/源码哈希记录；构建失败明确返回 ANALYSIS_INCOMPLETE。
2. Slither 资产控制检测器：suicidal、arbitrary-send-eth、arbitrary-send-erc20(-permit)、controlled-delegatecall、tx-origin。
3. 打印器事实收集（供人工复核）：vars-and-auth（每个函数写哪些状态、受哪些 msg.sender 条件约束）、modifiers、
   require、entry-points、variable-order（代理存储布局）、data-dependency。
4. 启发式线索（HEURISTIC_ONLY/INCONCLUSIVE）：未保护函数写入被 msg.sender 检查的状态；参数决定目标的低级调用。

只能人工完成：自定义 modifier 与一次性初始化守卫是否有效、代理初始化状态与存储冲突、签名/角色授权、投票权快照、
重复计票、法定人数、时间锁与紧急路径、跨合约权限链（A/B/C/D 链）。

## 6. 下一阶段开发与验收方案
1. **工具链**：保持版本校验；为未记录依赖的项目只报 ANALYSIS_INCOMPLETE，不猜版本。验收：dev 44 案中可编译率
   不下降，且每个编译器版本串与请求一致。
2. **降低已知误报**（只用 Slither 公开能力，不按函数名）：对 arbitrary-send 告警附加调用点上下文与修饰器事实，
   输出“告警 + 保护证据”，由人工定性。验收：dev 上 5 类已知误报都能附带对应保护证据。
3. **B/C 维度**：当前无自动能力，继续明确披露为“未覆盖”；如需补充，应以合规方式引入成熟的外部工具或经评审的规则，
   并先在 dev 集上验证。
4. **人工复核流程固定化**：每案 A/B/C/D 四问，按“证据充分·缺陷 / 证据充分·保护有效 / 证据不足 / 无法分析”记录，
   附代码位置；三层计分永远分开。
5. **新盲测的前提**：上述流程在 dev 集上稳定（构建、输出格式、复核表）后，再规划全新独立数据集；
   届时预注册分别报告自动层与人工层，不以人工结果充当自动检测能力。

## 7. 复现与证据
- `eval/v16/capability_matrix.json`、`round4_failure_analysis.json`（+ 生成脚本）、`governance_check_report.json`
  （+ 生成脚本）、`fix_diffs/*.diff`。
- 开发集重跑：`eval/results/dev16_r123/`、`eval/results/dev16_r4/`（每条输出含 `run_kind: dev`、`freeze_checked: false`）。
- 复现：`python3 eval/blind_run.py --dev --round dev16_r4 --engine baseline --dataset eval/dataset_r4 &&
  python3 eval/evaluate_v15.py --round dev16_r4 && python3 eval/v16/round4_failure_analysis.py &&
  python3 eval/v16/governance_check_report.py`
- DAO、Foundry、DEX 系统保留未改；全程只读静态分析，未生成交易，未接触任何真实金库。
