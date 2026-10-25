# Beanstalk 闪电贷治理攻击 (2022-04-17) — 漏洞样本

- repo: https://github.com/BeanstalkFarms/Beanstalk
- vuln_commit: e9f49910e287e7a7afaa6db8f536b7194728b0af (2022-04-11, 黑客前最后一次默认分支提交;
  治理文件自 Omniscia 审计更新 ee4720c(2022-04-01) 起字节不变)
- fixed_commit: 9fb2ad7964821d238ea4574beeb235457b63e865 (2022-04-28, **删除**三个治理文件,
  治理迁至链下 Snapshot;无补丁源码可作对照)
- 文件: protocol/contracts/farm/facets/GovernanceFacet/{GovernanceFacet,Bip,VotingBooth}.sol + C.sol (Solidity 0.7.6, Diamond facet)
- 报告: rekt.news/beanstalk-rekt ; bean.money 事后分析 ; omniscia.io(事前审计方)
- 链: 闪电贷→Silo存Curve LP→单块获>2/3 roots(VULN-2 无快照)→emergencyCommit 1天应急无时间锁(VULN-3)
  →任意 diamondCut init delegatecall 清空 ~$182M(VULN-4)。
