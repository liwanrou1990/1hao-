# MS0 状态 — 经济安全研究原型（分支 `claude/econsec-research`）

## 已交付
1. **复用评估、模块架构、里程碑、MS2 验收预注册**：`ARCHITECTURE_AND_PREREGISTRATION.md`（在任何测试前写定）。
2. **M2 经济模型 + M3 候选性质生成原型**：`model.py`，输出 `model_own.json`。
   - 事实只来自 Slither IR（类型、调用、数据依赖、状态读写）；按结构分类（ERC20 ABI 形状、按地址索引的
     数值映射在资产移动函数中被写 → 记账变量；外部 view 结果流入保护流出的比较 → 估值依赖；入口中