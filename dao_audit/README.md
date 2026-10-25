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

## v0.2：可复用分析引擎 + 真实合约审计

本版把 5 类检测从"仅验证预置靶标"升级为**可复用的静态分析 + 权限分析引擎**，可审计
你**拥有或明确授权**的真实 Solidity 合约：

- **结构化模型** (`tool/dao_audit/analysis/solmodel.py`)：把任意 .sol 解析为合约/函数/
  修饰符/状态变量（可见性、应用的修饰符、函数体、行号），检测基于结构而非纯正则。
- **检测器** (`analysis/detectors.py`)：5 类风险各一个结构化检测器 + 权限分析
  （特权函数是否缺门禁/初始化守卫、是否暴露任意调用）。每条发现带
  **位置 / 根本原因 / 触发条件 / 影响范围 / 误报判断 / 修复建议 / 置信度**。
- **导入真实合约**：`import` 需显式授权声明（记录到 `config/imported_attestations.jsonl`），
  只接受你声明拥有/授权审计的合约。
- **对照基准** (`benchmark.py`)：安全 vs 漏洞合约语料，输出**检出率/误报率/漏报率**混淆矩阵。
- **泛化验证**：`contracts/imported/MiniDAO.sol`（脆弱）与 `SafeMiniDAO.sol`（安全）使用与
  案例完全不同的命名（ballot/steward/enact），证明引擎不依赖预置词汇。

### 新增命令

```bash
export PATH=/root/.foundry/bin:$PATH PYTHONPATH=tool
# 导入我拥有/授权的真实合约并分析（必须声明授权来源）
python3 -m dao_audit import /path/to/MyGovernor.sol --attested-by "you@org" --note "owned"
# 分析全部授权合约（静态+权限，真实合约无需 PoC）→ 详细报告
python3 -m dao_audit analyze --prefix myproject
# 安全/漏洞对照基准：检出率/误报率/漏报率（可设阈值，CI 用）
python3 -m dao_audit benchmark --min-recall 1.0 --max-fpr 0.0
```

# 金库资金流转验证：本地 EVM 真实执行 5,000,000 USDC 从金库→接收地址
python3 -m dao_audit fundflow      # 运行资金流转测试，生成带真实 trace/gas 的验证报告
```

# 独立复核（干净环境重建 + 全量测试 + 证据 + SHA256 校验）
python3 -m dao_audit recheck       # forge clean→build→全量测试；落盘原始 JSON/trace/断言/SHA256
sha256sum -c reports_out/SHA256SUMS.txt   # 任何人可独立校验证据文件未被改动
```

> `recheck` 从干净环境重跑，用 `balanceOf` 实际状态、解码的 ERC20 Transfer 事件、
> decimals 与 5e24 原始单位换算独立确认资金流转真实、可重复、可审计；输出
> `reports_out/INDEPENDENT_VERIFICATION.md` 与 `SHA256SUMS.txt`，样例见
> `docs/SAMPLE_INDEPENDENT_VERIFICATION.md`。

### 金库资金流转验证（核心验收）

`test/FundFlow_X100.t.sol` 在本地隔离 EVM 上，用链上状态断言证明 5,000,000 模拟 USDC
**确实**从 DAO 金库转移到测试接收地址（逐漏洞 VULN-1..4 + 组合攻击链）；
`test/FundFlow_X100_Fixed.t.sol` 用修复版证明同样攻击被阻止、金库余额保持 5,000,000。
`python3 -m dao_audit fundflow` 从**真实 Foundry 执行结果**（通过/失败、gas、console 日志、
`-vvvv` 完整 trace）生成报告，失败如实标注，绝不伪造：

- 核心断言：金库初始 5,000,000 → 最终 0；接收地址 +5,000,000；资产总量守恒。
- 报告：`reports_out/fundflow_verification.{md,json}`；完整 trace：`reports_out/fundflow_trace_{vuln,fixed}.txt`
  （含 `VulnX100Treasury → receiver value 5e24` 的真实 Transfer 事件）。
- 样例见 `docs/SAMPLE_FUNDFLOW_REPORT.md`。

> 真实合约默认只做静态+权限分析（标 `STATIC_ONLY`）。要**本地链动态确认**，按 `test/`
> 模式为其手写针对性 Foundry PoC。局限见 `docs/LIMITATIONS.md`。

### 当前基准结果（自建语料，13 文件：4 漏洞 / 9 安全）

| 指标 | 值 |
|------|----|
| 检出率 (recall) | 100% (TP=10, FN=0) |
| 误报率 (FPR) | 0% (FP=0, TN=55) |
| 漏报率 | 0% |
| 精确率 | 100% |

数值仅代表在此小型自建语料上的表现，不等于对任意真实合约的泛化精度（见 LIMITATIONS）。

## 持续集成 (GitHub Actions)

`.github/workflows/dao-audit.yml`：push/PR 触发，装 Foundry → `forge test`（本地链 PoC/回归）
→ pytest（工具自测）→ `audit`（生成报告）→ `benchmark`（检出回归即 fail）→ 上传 `reports_out/`
产物。CI 用默认 profile（联网下载 pin 的 solc）；本仓库沙箱用 `FOUNDRY_PROFILE=offline`
（内置 solc 二进制，不联网）。

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
│   ├── mocks/                # 模拟代币/闪电贷/DEX（本地链用）
│   └── imported/             # 导入的授权真实合约（+ 授权声明记录）
├── test/                     # Foundry PoC（0x_*）与回归（Rx_*）
├── tool/dao_audit/           # Python 审计编排器
│   ├── analysis/             # ★ 可复用分析引擎
│   │   ├── solmodel.py       #   Solidity 结构化模型（括号匹配解析）
│   │   └── detectors.py      #   5 类结构化检测器 + 权限分析
│   ├── rules.py              # 5 类风险规则库（含 SWC/CWE、PoC/修复映射）
│   ├── authorization.py      # 授权边界强制 + 真实合约导入/授权声明
│   ├── static_scan.py        # 检测器适配层（兼容旧接口）
│   ├── runner.py             # 调用 forge 本地链复现
│   ├── report.py             # 聚合 + 详细发现报告（JSON/Markdown）
│   └── benchmark.py          # 安全/漏洞对照基准（检出率/误报率/漏报率）
├── tests/                    # 工具自测（pytest）
└── docs/                     # 方法论与 X100 案例分析
```

## 方法论

见 `docs/METHODOLOGY.md`（审计流程、每类风险的检测逻辑与修复原理）和
`docs/X100_CASE_STUDY.md`（X100 事件复盘与攻击链）。

每条结论都由**两重证据**支撑：静态签名命中给出**位置**，本地链 PoC 给出**可复现的攻击证据**；
修复版合约的回归测试证明**同一攻击对修复版失效**。
