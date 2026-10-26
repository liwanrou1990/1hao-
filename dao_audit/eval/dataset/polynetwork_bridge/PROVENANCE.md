# Poly Network 跨链桥接管 (2021-08-10, ~$611M) — 漏洞样本 (round3, 新架构:跨链manager)
- repo: https://github.com/polynetwork/eth-contracts  vuln_commit: 15d84b7f34d2068d7548d5385c3d43c37a7232ba (as-of-incident)
- fixed_commit: 1011bf8ab21c620129190b229cca268ebbdc2b94 (事后审计修订,非逐一补丁,如实标注)
- 文件: EthCrossChainManager/EthCrossChainData/EthCrossChainUtils + Utils/ZeroCopy* 等 (pragma ^0.5.0)
- 漏洞(VULN-4主): EthCrossChainManager.verifyHeaderAndExecuteTx→_executeCrossChainTx 行190
  `_toContract.call(...)` 的目标地址与方法选择器均取自攻击者可控的跨链消息,无白名单;
  攻击者构造消息调用 EthCrossChainData.putCurEpochConPubKeyBytes(onlyOwner,其owner正是Manager)
  替换共识keeper公钥→夺桥控制权(VULN-1 下游后果;该函数本身onlyOwner正确,故不单列为独立VULN-1漏洞,
  避免对正确受保护函数的不公平漏报记账)。
- 报告: rekt.news/SlowMist/BlockSec 对该事件的公开分析(子代理未复检URL)。
