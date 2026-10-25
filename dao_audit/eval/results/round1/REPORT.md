# 盲测结果报告 (round1) — 真实历史合约 DAO 治理漏洞检测能力

> 引擎冻结后盲测，答案由独立评分器 evaluate.py 事后比对。见 eval/PREREGISTRATION.md（通过标准先于结果写定）。
> 仅基于真实历史样本与独立评分；不以模拟合约成功率作为真实世界能力证明。

- 案例数: 5（漏洞样本 3，安全对照 2，完整风险链案例 3）
- **结论性**: False  —  结论性要求 N_vuln>=10 且链式案例>=3；否则标注为探索性(样本不足)，只报实测数字，不宣称普适能力。

## 一、总体指标（定位正确的 TP 口径，含 Wilson 95% CI）

- Recall（召回）: 0.2222 (95%CI 0.0632–0.5474)  FNR: 0.7778 (95%CI 0.4526–0.9368)
- Precision（精确率）: 0.6667 (95%CI 0.2077–0.9385)
- 安全对照 FPR: 0.5 (95%CI 0.0945–0.9055)（1/2 安全样本出现高危治理误报）
- 完整治理风险链识别率: 0.3333 (95%CI 0.0615–0.7923)（1/3）

## 二、各治理漏洞类别识别率

| 类别 | TP | FN | FP | Recall(95%CI) | Precision(95%CI) |
|------|----|----|----|---------------|------------------|
| VULN-1 席位/初始化接管 | 0 | 1 | 0 | 0.0 (95%CI 0.0–0.7935) | n/a |
| VULN-2 投票权异常 | 0 | 2 | 0 | 0.0 (95%CI 0.0–0.6576) | n/a |
| VULN-3 提案执行/Timelock | 1 | 2 | 0 | 0.3333 (95%CI 0.0615–0.7923) | 1.0 (95%CI 0.2065–1.0) |
| VULN-4 金库资金控制 | 1 | 2 | 1 | 0.3333 (95%CI 0.0615–0.7923) | 0.5 (95%CI 0.0945–0.9055) |
| VULN-5 预言机操纵 | 0 | 0 | 0 | n/a | n/a |

## 三、逐案结果

| 案例 | 项目 | 类型 | 自动发现 | 命中类别 | 链识别 | 高危误报(安全样本) |
|------|------|------|----------|----------|--------|--------------------|
| audius_governance | audius | vulnerable | 否 | — | — | 0 |
| beanstalk_governance | beanstalk | vulnerable | 否 | — | — | 0 |
| compound_governance | compound | secure_control | 否 | — | — | 0 |
| openzeppelin_governor | openzeppelin | secure_control | 是 | — | — | 1 |
| tornado_governance | tornado_governance | vulnerable | 是 | VULN-3,VULN-4 | 是 | 0 |

## 四、判定（对照预注册冻结标准）

- 冻结标准满足项: 0/3 — {'recall>=0.70 & CIlo>=0.50': False, 'chain_rate>=0.50': False, 'fpr<=0.30': False}
- **判定: FAIL/UNPROVEN**

> 样本量未达结论性阈值（N_vuln≥10 且链式≥3）→ 结果为**探索性**，只报实测数字，不宣称系统具备或不具备普适的真实世界治理漏洞检测能力。

## 五、漏报与误报（技术原因，供第二轮改进）

- 漏报 audius_governance: 未定位 ['VULN-1', 'VULN-3', 'VULN-4']（原因见 round2 改进记录）
- 漏报 beanstalk_governance: 未定位 ['VULN-2', 'VULN-3', 'VULN-4']（原因见 round2 改进记录）
- 误报 openzeppelin_governor(安全样本): [{'rule_id': 'VULN-4', 'contract': 'Governor', 'function': 'relay', 'line': 655, 'severity': 'High'}]
- 漏报 tornado_governance: 未定位 ['VULN-2']（原因见 round2 改进记录）