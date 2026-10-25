# Furucombo Proxy 修复版 (SECURE/FIXED 对照, round2) — 修复前后区分
- repo: https://github.com/dinngo/furucombo-contract  commit: b096f10b4081f0cbc459cea75eddfe5e81d7b2e1
- 加 banned caller + halt 检查(isNotHalted/isNotBanned)。衡量引擎是否区分修复前后(此版本不应再报 VULN-4)。
