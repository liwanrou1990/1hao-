# X100 事件案例研究

> **范围说明**：本文把「X100 事件」作为一个**治理攻击模式的研究案例**来复盘。文中的
> 合约（`contracts/targets/`）是为授权审计**重建的研究靶标**，用于在本地测试链上
> 复现这些模式，不是任何第三方生产系统的副本。目的是防御：理解攻击链并给出修复。

## 攻击链概述

典型的治理接管事件（X100 案例所代表的模式）把若干独立缺陷串成一条近零成本的资金
链路。本仓库用 `X100KillChainTest` 在本地链完整复现：

```
(可选) 席位劫持 [VULN-1]
        │  attacker 重新初始化 council，获得特权席位
        ▼
提交恶意提案（permissionless propose）
        │  提案内容：treasury.execute(USDC, transfer(attacker, 全部))
        ▼
闪电贷借入治理代币 [VULN-2]
        │  单笔交易内：borrow → castVote(YES, weight=实时余额) → repay
        │  以实时余额计票，达成法定票数，攻击者净代币成本≈0
        ▼
投票期结束，立即执行 [VULN-3]
        │  无时间锁：审批与执行间零延迟，守护者/持币人来不及反应
        ▼
金库任意调用清空资产 [VULN-4]
        │  无目标白名单/额度上限 → 一次调用转走全部 USDC
        ▼
结果：攻击者以近零成本清空金库
```

`VULN-5`（DEX 现货价预言机操纵）是同一事件里常见的并行放大器：若金库/借贷模块用
AMM 现货价给抵押品定价，攻击者可用闪电贷拉高价格后超额借出，`VulnOracleVault` +
`DexOracleManipulationTest` 复现了这一点。

## 本地链复现结果（实测）

| 步骤 | 测试 | 观测证据 |
|------|------|----------|
| 席位劫持 | `SeatHijackTest` | 任意地址重新初始化后成为 council，并可改写 treasury 指针 |
| 闪电贷投票 | `FlashLoanVoteTest` | 零持仓攻击者投出 500k YES，超过 20% 法定票数，事后仍零持仓 |
| 无时间锁 | `MissingTimelockTest` | 投票结束后立即执行，延迟 < 1 小时安全窗 |
| 金库过大权限 | `TreasuryOverprivilegeTest` | 单笔提案转走 5,000,000 模拟 USDC，金库归零 |
| DEX 操纵 | `DexOracleManipulationTest` | 借贷能力从公允 8,000 被抬高至 161,621（>20x） |
| 完整攻击链 | `X100KillChainTest` | 近零成本清空 5,000,000 模拟 USDC |

全部为**模拟资产**，运行于 Foundry 本地 EVM / 本地 anvil（chain-id 31337）。

## 修复后结果（回归）

| 修复 | 测试 | 结果 |
|------|------|------|
| 一次性+owner 初始化 [FIX-1] | `SeatHijackFixedTest` | 重新初始化被 revert，攻击者无法获席位 |
| 历史快照票权 [FIX-2] | `FlashVoteFixedTest` | 闪电贷余额票权=0 |
| 时间锁 [FIX-3] | `TimelockFixedTest` | 执行被 `timelock` 阻断，存在≥2 天反应窗 |
| 最小权限金库 [FIX-4] | `TreasuryFixedTest` | 全额清空被拒；仅限额白名单支付可行；守护者可熔断 |
| TWAP 预言机 [FIX-5] | `OracleFixedTest` | 单区块操纵对 TWAP 无效，借贷能力守在公允 ~8,000 |

## 防御要点（供治理团队核对清单）

1. 票权一律取历史快照，永不取实时余额。
2. 所有特权初始化一次性 + 明确 owner/role，部署后撤销部署者权限。
3. 提案执行必须经时间锁，并赋予守护者在窗内 cancel/pause 的能力。
4. 金库最小权限：白名单收款 + 分资产分周期额度 + 熔断 + 大额多签。
5. 任何链上定价用抗操纵价源（TWAP/外部喂价 + 偏离/陈旧性检查），禁用现货价。
