# 审计方法论

本系统对每个治理风险采用**双重证据**：

1. **静态签名**（`tool/dao_audit/rules.py` + `static_scan.py`）——在授权源码中定位**漏洞位置**，并识别缓解模式（若存在则降权）。这是分流层，可能有误报，由动态证据裁决。
2. **本地链动态复现**（`test/` + `runner.py`）——在 Foundry 内置 EVM 或本地 anvil 上，用模拟资产真实执行攻击，PoC 通过即「漏洞已确认 (CONFIRMED)」。
3. **修复回归**（`contracts/fixed/` + `test/Rx_*`）——对修复版运行同一攻击，攻击失败即「修复已验证」。

状态语义：`CONFIRMED`（PoC 本地复现）> `SUSPECTED`（仅静态命中）> `NOT_OBSERVED`。

## 分类检测逻辑与修复原理

### VULN-1 治理席位劫持（Critical, CWE-284/665, SWC-105/118）
- **检测**：初始化/席位写入函数缺少 `onlyOwner` 与一次性 `initializer` 守卫。
- **本质**：`councilInitialized` 标志被写却从不校验 → 可重复初始化，攻击者将自己设为唯一席位。
- **修复**：一次性守卫 `require(!initialized)` + `onlyOwner`；席位变更走提案+时间锁；用 OZ `AccessControl`，部署后撤销部署者 admin。

### VULN-2 闪电贷投票操纵（Critical, CWE-682/840）
- **检测**：计票时读取 `balanceOf(msg.sender)` 实时余额，无历史快照。
- **本质**：票权=投票当刻余额 → 闪电贷在单笔交易内借币投票再归还，零成本达成法定票数。
- **修复**：ERC20Votes 检查点，`getPastVotes(voter, snapshotBlock)`，快照点设在提案创建区块之前；`totalSupply` 也用历史快照。

### VULN-3 治理时间锁缺失（High, CWE-362/841）
- **检测**：`execute` 在票数达标后直接调用金库，无 `queue`/`eta`/`minDelay`；投票期过短。
- **本质**：审批与执行之间零延迟 → 持币人/守护者无退出或否决窗口。
- **修复**：`TimelockController`：通过→`queue`(eta=now+minDelay)→`execute`(require now≥eta)；minDelay≥2 天；守护者可在窗内 `cancel`/`pause`。

### VULN-4 金库权限过大（Critical, CWE-284/862, SWC-105）
- **检测**：金库暴露 `execute(address,uint256,bytes)` 任意调用 + 无约束低级 `call`，唯一门禁是 governor 地址。
- **本质**：一笔提案即可 `transfer` 全部资产。
- **修复**：最小权限——代币流出走 `payToken`（收款白名单 + 按资产按周期额度）；运营调用限目标白名单；守护者熔断 `pause`；大额走多签+时间锁。

### VULN-5 DEX 流动性池操纵（Critical, CWE-20/682）
- **检测**：以 `spotPrice0()`/`getReserves()` 瞬时现货价为抵押品定价。
- **本质**：单区块内一笔大额兑换（常由闪电贷驱动）拉高现货价 → 超额借出。
- **修复**：抗操纵价源——Uniswap V2/V3 TWAP（窗口≥30min）或 Chainlink；加偏离阈值与陈旧性检查；禁止直接用现货价/储备计价。

## 为什么本地链就足以证明

这些漏洞是**合约逻辑缺陷**，与主网状态无关：票权来源、初始化守卫、执行延迟、权限约束、价格来源都在合约内部。在确定性本地 EVM 上用模拟资产复现，既充分又安全——无需、也禁止触碰真实资产或主网。
