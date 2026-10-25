# MakerDAO DSChief 无快照投票(闪电贷治理风险) — 漏洞样本 (round2, 新项目)
- repo: https://github.com/dapphub/ds-chief (经典已部署设计)  commit: 4a833cdaeb66313da0fb16108771a52135d23a04
- fixed_commit: null (设计性风险,非单行bug;MakerDAO 以 DSChief v1.2 / makerdao/dss-chief 重写在链下缓解:
  free 需后一区块 require(block.number>last) + liftCooldown)
- 文件: src/chief.sol (DSChiefApprovals 被 DSChief 继承)
- 漏洞(VULN-2): 投票权重取自实时 deposits[msg.sender]/approvals,无历史区块快照; 经典版 free 无同块限制,
  借入/闪电贷 MKR 可单笔 lock→vote→lift→free→还 完成,零成本改写 hat(治理首脑)。
  函数: lock(49) 即时计权, vote(83/91) 以实时 deposits 赋权, lift(104) 仅凭实时 approvals 晋升 hat。
- 报告: B.Protocol 2020-10 闪电贷治理投票演示(非恶意); OpenZeppelin DSChief 分析 (子代理未复检域名)。
