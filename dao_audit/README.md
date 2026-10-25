# X100 DAO 治理合约安全审计系统

> **防御性安全研究工具。** 仅分析用户提供或明确授权审计的合约；全部漏洞在**本地测试链（Foundry 内置 EVM / 本地 anvil）与模拟金库**上复现与验证；**不接触真实资产，不向主网发送任何交易**。
>
> `contracts/targets/` 下的合约是为授权审计编写的**研究靶标**，蓄意包含漏洞，**禁止部署到任何公网/主网**。

本系统以「X100 事件」为研究案例，检测 5 类 DAO 治理风险，产出**漏洞位置、严重程度、本地链测试证据、修复建议**，并给出修复版合约与回归验证。

## 检测的风险类别

| ID | 风险 | 严重程度 | PoC（复现） | 修复回归 |
|----|------|----------|-------------|----------|
| VULN-1 | 治理席位劫持 (seat hijacking) | Critical | `SeatHijackTest` | `SeatHijackFixedTest` |
| VULN-2 | 闪电贷投票操纵 (flash-loan voting) | Critical | `FlashLoanVoteTest` | `FlashVoteFixedTest` |
| VULN-3 | 治理时间锁缺失 (missing timelock) | High | `MissingTimelockTest` | `TimelockFixedTest` |
| VULN-4 | 金库权限过大 (over-privileged treasury) | Critical | `TreasuryOverprivilegeTest` | `TreasuryFixedTest` |
| VULN-5 | DEX 流动性池操纵 (spot-price oracle) | Critical | `DexOracleManipulationTest` | `OracleFixedTest` |

另有端到端攻击链 `X100KillChainTest`：闪电贷投票 + 无时间锁 + 金库过大权限，单笔近零成本清空模拟金库。

## 快速开始

```bash
cd dao_audit

# 1) 完整审计（默认用 Foundry 内置本地 EVM，确定性、无网络）
./run_audit.sh                 # 扫描 + 本地链复现 PoC/回归 + 生成报告
# 或指向本地 anvil 测试链：
./run_audit.sh anvil

# 2) 分步
export PATH=/root/.foundry/bin:$PATH
export PYTHONPATH=tool
python3 -m dao_audit list-rules                 # 列出检测规则
python3 -m dao_audit --project . scan           # 仅静态扫描授权合约
python3 -m dao_audit --project . verify         # 仅本地链运行 PoC/回归
python3 -m dao_audit --project . audit          # 完整审计 + 报告

# 3) 原生 Foundry 测试
forge test -vv                                  # 全部 13 个用例
forge test --match-contract X100KillChainTest -vvv

# 4) 工具自测
PYTHONPATH=tool python3 -m pytest tests -q
```

报告输出：`reports_out/x100_audit_report.{json,md}`。

## 授权与安全边界

审计范围由 `config/authorization.yaml` 控制，是系统的硬约束：

- **源码范围**：只有 `authorized_paths` 列出的目录内的 `.sol` 会被扫描；其他文件被 `AuthorizationError` 拒绝。
- **链范围**：任何 RPC 必须命中 `allowed_rpc_hosts`（仅 `127.0.0.1`/`localhost`）。指向主网/公网的 RPC、以及 `ETH_RPC_URL`/`FORK_URL` 环境变量中的非本地地址，都会被拒绝。
- **默认无网络**：不指定 `--rpc-url` 时，使用 Foundry 内置 EVM，根本不发起网络连接。
- **仅模拟资产**：所有代币、金库、流动性、闪电贷均为 `contracts/mocks/` 下的模拟物，无真实价值。

> 若要审计你自己的合约：把合约放入某个目录，在 `authorized_paths` 登记该目录，并按同样模式补充针对性的 PoC/回归测试。

## 目录结构

```
dao_audit/
├── foundry.toml              # 离线 solc、本地 EVM 配置
├── run_audit.sh              # 一键审计（inprocess | anvil）
├── config/authorization.yaml # 授权与本地链边界（硬约束）
├── contracts/
│   ├── targets/              # 授权研究靶标（蓄意脆弱，禁止部署）
│   ├── fixed/                # 修复版合约（修复建议的参考实现）
│   └── mocks/                # 模拟代币/闪电贷/DEX（本地链用）
├── test/                     # Foundry PoC（0x_*）与回归（Rx_*）
├── tool/dao_audit/           # Python 审计编排器
│   ├── rules.py              # 5 类风险规则库（含 SWC/CWE、修复）
│   ├── authorization.py      # 授权边界强制
│   ├── static_scan.py        # 启发式静态扫描
│   ├── runner.py             # 调用 forge 本地链复现
│   └── report.py             # JSON/Markdown 报告
├── tests/                    # 工具自测（pytest）
└── docs/                     # 方法论与 X100 案例分析
```

## 方法论

见 `docs/METHODOLOGY.md`（审计流程、每类风险的检测逻辑与修复原理）和
`docs/X100_CASE_STUDY.md`（X100 事件复盘与攻击链）。

每条结论都由**两重证据**支撑：静态签名命中给出**位置**，本地链 PoC 给出**可复现的攻击证据**；
修复版合约的回归测试证明**同一攻击对修复版失效**。
