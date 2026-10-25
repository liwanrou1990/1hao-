# Aave Governance V2 (SECURE CONTROL, round2, 新项目无重叠)
- repo: https://github.com/aave/governance-v2
- commit: d4e5ae0c31824b9ec9aec4e06a3ee2c3c34ae431
- files: AaveGovernanceV2.sol, ExecutorWithTimelock.sol, GovernanceStrategy.sol
- role: 安全对照。快照投票(GovernanceStrategy.getVotingPowerAt 按历史区块) + ExecutorWithTimelock
  延时执行(delay/queue/execute+grace) + 提案状态机 + 角色门禁。预期无高危治理接管误报。
- 证据: Aave 治理为带快照+时间锁的参考实现;源码公开可核验(commit)。
