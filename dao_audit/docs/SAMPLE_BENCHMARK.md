# 对照基准：检出率 / 误报率 / 漏报率

语料：4 个漏洞合约（正样本）, 9 个安全合约（负样本）, 共 13 个文件 × 5 类规则。

## 总体

- 检出率 (recall): **100.0%**  (TP=10, FN=0)
- 误报率 (FPR): **0.0%**  (FP=0, TN=55)
- 漏报率: **0.0%**
- 精确率 (precision): **100.0%**

## 分规则

| 规则 | TP | FP | FN | TN | 检出率 | 误报率 | 漏报率 | 精确率 |
|------|----|----|----|----|--------|--------|--------|--------|
| VULN-1 | 2 | 0 | 0 | 11 | 100% | 0% | 0% | 100% |
| VULN-2 | 2 | 0 | 0 | 11 | 100% | 0% | 0% | 100% |
| VULN-3 | 2 | 0 | 0 | 11 | 100% | 0% | 0% | 100% |
| VULN-4 | 2 | 0 | 0 | 11 | 100% | 0% | 0% | 100% |
| VULN-5 | 2 | 0 | 0 | 11 | 100% | 0% | 0% | 100% |

## 逐文件

| 文件 | 期望 | 实检 | 结果 |
|------|------|------|------|
| `VulnX100Governor.sol` | VULN-1,VULN-2,VULN-3 | VULN-1,VULN-2,VULN-3 | ✅ 一致 |
| `VulnX100Treasury.sol` | VULN-4 | VULN-4 | ✅ 一致 |
| `VulnOracleVault.sol` | VULN-5 | VULN-5 | ✅ 一致 |
| `SafeX100Governor.sol` | - | - | ✅ 一致 |
| `SafeX100Treasury.sol` | - | - | ✅ 一致 |
| `SafeOracleVault.sol` | - | - | ✅ 一致 |
| `SafeTWAPOracle.sol` | - | - | ✅ 一致 |
| `CheckpointToken.sol` | - | - | ✅ 一致 |
| `MockERC20.sol` | - | - | ✅ 一致 |
| `MockDEXPair.sol` | - | - | ✅ 一致 |
| `MockFlashLender.sol` | - | - | ✅ 一致 |
| `MiniDAO.sol` | VULN-1,VULN-2,VULN-3,VULN-4,VULN-5 | VULN-1,VULN-2,VULN-3,VULN-4,VULN-5 | ✅ 一致 |
| `SafeMiniDAO.sol` | - | - | ✅ 一致 |