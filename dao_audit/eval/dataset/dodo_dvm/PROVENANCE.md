# DODO DVM 未保护初始化 (2021-03-08) — 漏洞样本 (round2, 新项目)
- repo: https://github.com/DODOEX/contractV2
- vuln_commit: 94ba7fbd21adddcc7ecdcea467dd8efd961098ec (fix 的父提交, 2021-03-04)
- fixed_commit: 01c544780291a5acc3e2be4980493e63065fb200 (2021-03-09 "fix", 加 _DVM_INITIALIZED_ 守卫)
- 文件: contracts/DODOVendingMachine/impl/{DVM,DVMStorage,DVMVault,DVMFunding,DVMTrader}.sol
- 漏洞: DVM.init 为 external 且**无一次性守卫、无访问控制**(未继承 InitializableOwnable/未 initOwner)，
  任意账户可对已部署池重复 init，用伪 token 覆盖 _BASE_TOKEN_/_QUOTE_TOKEN_ 后抽走真实储备。
  字节核验: 漏洞版 DVM.sol 含守卫 0 处, 修复版含 2 处。
- 报告: rekt.news/dodo-rekt ; blog.peckshield.com/2021/03/09/DODO (子代理未复检域名存活, 以仓库+fix commit为准证据)
- VULN-1 根因(DVM.init); VULN-4 为下游资金影响。
