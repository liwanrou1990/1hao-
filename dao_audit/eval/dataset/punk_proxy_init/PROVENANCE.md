# Punk Protocol 代理实现未保护 initialize (2021-08-10) — 漏洞样本 (round3, 新项目/代理升级架构)
- repo: https://github.com/PunkFinance/punk.protocol  vuln_commit: 6fcdda755a4b076e362d032ffe2b18850a6c208f (2021-08-09)
- fixed_commit: d68a58ddcb98a0641f0af44c31791ead99f4433f (2021-08-13)
- 文件: CompoundModel/Ownable/Forge/ForgeProxy/ForgeStorage/ModelStorage/OwnableStorage.sol (pragma >=0.5.0<0.9.0)
- 漏洞(VULN-1): 可升级实现 CompoundModel.initialize 为 public 且**无一次性守卫/无访问控制**,
  任意人可重指向策略(_forge/_cToken/_comp/_comptroller/_uRouterV2)到攻击者地址;Ownable.initialize 同样
  public 无守卫,经代理覆盖 OwnableStorage 夺管理权。接管后抽走池内稳定币(~$8.9M, VULN-4 下游)。
- 修复核验: fixed 版 CompoundModel.initialize 变为 `public initializer onlyCreator`(+constructor设creator),
  Ownable.initialize 变为 `public virtual initializer`。
- 报告: rekt.news/punk-protocol-rekt (子代理未复检域名,以仓库+前后commit为准证据)。
