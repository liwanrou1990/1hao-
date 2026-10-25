# Furucombo Proxy 不受约束 delegatecall (2021-02-27) — 漏洞样本 (round2, 新项目)
- repo: https://github.com/dinngo/furucombo-contract (任务给的 furucombo/furucombo-contracts 不存在)
- vuln_commit: e416ad1baa1ec8dd6efbce98dd465a304ad3e818 (2020-12-24, 攻击时点前最后触碰 Proxy.sol)
- fixed_commit: b096f10b4081f0cbc459cea75eddfe5e81d7b2e1 (2021-04-07, 加 banned caller + halt 检查)
- 文件: contracts/{Proxy,Registry,HandlerBase,Storage,Config,...}.sol
- 漏洞(VULN-4): Proxy._exec(210) 以**内联汇编 delegatecall**(217) 调用 handler `_to`,仅 _isValid(注册与否)
  为门禁; proxy 持有用户 ERC20 授权且 delegatecall 在 proxy 自身存储/授权上下文执行,
  任意"已注册但攻击者可控"的 target 即以 proxy 全部权限运行 → 抽走用户授权资产。
  外部入口 batchExec(64)/execs(79)/fallback。VULN-1(注入实现)为次要。
- 修复核验: vuln Proxy halt/ban 引用 0 处, fixed 9 处(isNotHalted/isNotBanned/bannedAgents/fHalt)。
- 报告: rekt.news/furucombo-rekt/ ; Furucombo Medium 事后分析 ; PeckShield 预警(子代理未逐一复检)。
- 诚实: 汇编 delegatecall 非成员访问 .delegatecall 形式;源码非逐字节对齐链上部署字节码。
