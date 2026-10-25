# DODO DVM 修复版 (SECURE/FIXED 对照, round2) — 修复前后区分测试
- repo: https://github.com/DODOEX/contractV2  commit: 01c544780291a5acc3e2be4980493e63065fb200
- 已加 _DVM_INITIALIZED_ 一次性守卫的 DVM.init。预期: 引擎不应再在 DVM.init 报 VULN-1(修复生效)。
- 用于衡量"修复前后版本区分准确率"。
