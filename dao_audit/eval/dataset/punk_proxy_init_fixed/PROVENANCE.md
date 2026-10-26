# Punk Protocol 修复版 (SECURE/FIXED 对照, round3) — 修复前后区分
- repo: https://github.com/PunkFinance/punk.protocol  commit: d68a58ddcb98a0641f0af44c31791ead99f4433f
- CompoundModel.initialize→`public initializer onlyCreator`; Ownable.initialize→`public initializer`。
  预期引擎不再报 VULN-1(修复生效)。
