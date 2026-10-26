# round3 无法取得源码的候选案例（如实记录，不以lookalike/植入漏洞凑数）

- **Build Finance DAO 治理接管 (2022-02-09)**：真实被利用的 BUILD 治理/铸币合约
  (0x6e36556b…) 与投票合约仅存在于链上(Etherscan 验证)，**GitHub 无公开 Solidity 源码**
  (build-finance 组织的公开仓库 metric-contracts/basis-gold-protocol 等均非被利用合约，
  团队亦声明攻击者未控制 Metric)。本环境 Etherscan 出站被封(403)，无法取回链上源码。
  故**不纳入 round3**(拒绝用 lookalike 替代)。VULN-1/2/3+VULN-4 类别真实但映射到未公开的链上合约。
