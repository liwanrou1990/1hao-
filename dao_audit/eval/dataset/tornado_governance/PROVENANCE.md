# Tornado Cash Governance 接管 (2023-05-20) — 漏洞样本

- repo: https://github.com/tornadocash/tornado-governance
- vuln_commit: 6464973b108102b70efafa29e55da69bfb43b7b9 (master HEAD; 漏洞逻辑在 v1 Governance 基类，
  被部署的 v3 GovernanceStakingUpgrade 原样继承；on-chain 部署字节码不在仓库内)
- v3 引入 commit: 74b6021aa7e45a565a6e9ab27b6e04f1eb9256ce (2022-03-07)
- fixed_commit: 无源码修复（设计级漏洞：对 CREATE2 可变目标的任意 delegatecall；治理经事后重新投票
  在链上恢复，而非 Solidity 补丁）。故本案**仅有漏洞版、无修复版对照**，如实记录。
- 抽取文件: Governance.sol, Delegation.sol, Configuration.sol, LoopbackProxy.sol,
  GovernanceVaultUpgrade.sol, GovernanceGasUpgrade.sol, GasCompensator.sol, GovernanceStakingUpgrade.sol
- 公开报告: rekt.news/tornado-gov-rekt ; halborn.com/blog/post/explained-the-tornado-cash-hack-may-2023 ;
  hackernoon.com/inside-the-governance-hack-of-tornado-cash
- 事件: 攻击者提案以 CREATE2 部署→selfdestruct→同地址重部署恶意字节码；Governance.execute 对该地址
  delegatecall（仅 isContract 检查），在 Governance 存储上下文伪造 lockedBalance(~1.2M 票)，夺取治理控制权。
