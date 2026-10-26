# OlympusDAO Bophades OlympusGovernance (Code4rena 2022-08 audit findings) — vulnerable sample (round4)

- repo: https://github.com/code-423n4/2022-08-olympus
- commit: 2a0b515012b4a40076f6eac487f7816aafb8724a (audited snapshot published in the Code4rena contest repository)
- fix commit: none (no source-level fix commit in this repo; see notes)
- incident / disclosure date: 2022-08-25
- pragma (main file Governance.sol): 0.8.15
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - Governance.sol -> src/policies/Governance.sol
  - INSTR.sol -> src/modules/INSTR.sol
  - Kernel.sol -> src/Kernel.sol
  - KernelUtils.sol -> src/utils/KernelUtils.sol
  - VOTES.sol -> src/modules/VOTES.sol
  - VoterRegistration.sol -> src/policies/VoterRegistration.sol
- reports / references:
  - https://code4rena.com/reports/2022-08-olympus
  - https://github.com/code-423n4/2022-08-olympus-findings/issues/392
  - https://github.com/code-423n4/2022-08-olympus-findings/issues/239

## Description

Public audit findings on the Bophades governance policy: live-balance endorsements allow double counting, and supply-relative thresholds let anyone pass proposals before VOTES are minted.

## Verification notes

Read report.md of code-423n4/2022-08-olympus-findings (H-02 acknowledged, M-06 confirmed). The project's own repo (OlympusDAO/bophades) is not publicly clonable, so no fix commit / fixed sample.
