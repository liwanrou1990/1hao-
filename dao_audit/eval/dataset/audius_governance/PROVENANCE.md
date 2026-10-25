# Audius 治理接管 (2022-07-23) — 漏洞样本

- repo: https://github.com/AudiusProject/audius-protocol
- vuln_commit: 01f924a7762659141d3b32491b81ca6e0f63c822 (fixed 的父提交)
- fixed_commit: 1e04215d4ad8251d77a35dd75c8908d4c5cf3738 (PR#3584 "[C-739] Patch OZ Initializable")
- **重要诚实说明**: 修复在 `eth-contracts/patches/@openzeppelin+upgrades+2.8.0.patch`(改 OZ Initializable
  存储布局,消除与代理 slot-0 proxyAdmin 冲突)，**三份 .sol 在漏洞版与修复版字节完全相同**。即：
  源码级静态引擎**结构上看不到**此修复(修复不在它解析的 .sol 内)。故本案不设 .sol 层“修复对照”，
  并将此列为静态源码引擎的已知盲区。
- 文件: eth-contracts/contracts/{Governance,InitializableV2,AudiusAdminUpgradeabilityProxy}.sol
- 报告: rekt.news/audius-rekt/ ; decrypt.co/105913 (官方 blog.audius.co 事后分析域名已失效,仅存档)
- 链: 代理 slot-0/实现 initialized 存储冲突→重复 initialize 装 guardian(VULN-1)→guardian 无时间锁
  执行(VULN-3)→Governance 托管金库,恶意调用转出 ~18.5M AUDIO(VULN-4)。
