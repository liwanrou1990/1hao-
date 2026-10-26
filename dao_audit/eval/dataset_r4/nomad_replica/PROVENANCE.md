# Nomad bridge Replica zero trusted root (2022-08-01) — vulnerable sample (round4)

- repo: https://github.com/nomad-xyz/monorepo
- commit: 2b80ba83755a43e046c65d303d087094b58e8da9 (last commit touching Replica.sol before the fix (2022-07-13); the exploited implementation was deployed in the June 2022 upgrade with the same initialize/acceptableRoot logic)
- fix commit: 0e02cc1f09d16f809f5d2d8f05abbeea6d1af04e
- incident / disclosure date: 2022-08-01
- pragma (main file Replica.sol): 0.7.6
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - IMessageRecipient.sol -> packages/contracts-core/contracts/interfaces/IMessageRecipient.sol
  - Merkle.sol -> packages/contracts-core/contracts/libs/Merkle.sol
  - Message.sol -> packages/contracts-core/contracts/libs/Message.sol
  - NomadBase.sol -> packages/contracts-core/contracts/NomadBase.sol
  - Replica.sol -> packages/contracts-core/contracts/Replica.sol
  - TypeCasts.sol -> packages/contracts-core/contracts/libs/TypeCasts.sol
  - Version0.sol -> packages/contracts-core/contracts/Version0.sol
- reports / references:
  - https://immunefi.com/blog/bug-fix-reviews/hack-analysis-nomad-bridge-august-2022/
  - https://www.certik.com/resources/blog/28fMavD63CpZJOKOjb9DX3-nomad-bridge-exploit-incident-analysis
  - https://halborn.com/explained-the-nomad-hack-august-2022/
  - https://github.com/nomad-xyz/monorepo/commit/0e02cc1f09d16f809f5d2d8f05abbeea6d1af04e

## Description

A routine upgrade initialized the Replica with committedRoot = 0x00. Because initialize sets confirmAt[_committedRoot] = 1 and acceptableRoot treats any root with non-zero confirmAt as valid, every never-proven message (messages[hash] == 0x00) passed the !proven check in process(). Copy-paste attackers drained ~$190M.

## Verification notes

Fix commit 0e02cc1 ('fix: zero root is not acceptableRoot') read: it guards confirmAt write in initialize and makes acceptableRoot return false for LEGACY_STATUS_NONE. Reports read via web-search summaries (CertiK/Halborn/GoPlus).
