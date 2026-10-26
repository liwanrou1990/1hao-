# Moonwell TemporalGovernor fast-track path not restricted to pause (Code4rena 2023-07 M-08) — vulnerable sample (round4)

- repo: https://github.com/moonwell-fi/moonwell-contracts-v2
- commit: 004c0f1920a873d64717a75393433a8e3daa358b (parent of the commit that adds whenPaused)
- fix commit: 4580996b7cdb831701c62d13042665161ac26ec4
- incident / disclosure date: 2023-07-24
- pragma (main file TemporalGovernor.sol): 0.8.19
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - ITemporalGovernor.sol -> src/core/Governance/ITemporalGovernor.sol
  - TemporalGovernor.sol -> src/core/Governance/TemporalGovernor.sol
- reports / references:
  - https://code4rena.com/reports/2023-07-moonwell
  - https://github.com/code-423n4/2023-07-moonwell-findings/issues/276

## Description

Audit finding M-08 (confirmed): the owner-only fast-track execution path that overrides the timelock is callable while the governor is not paused.

## Verification notes

Fix in 4580996b read: fastTrackProposalExecution gains the whenPaused modifier (commit message is unrelated: 'add mainnet mip json example configs').
