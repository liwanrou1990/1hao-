# Nomad Replica after zero-root fix — fixed sample (round4)

- repo: https://github.com/nomad-xyz/monorepo
- commit: 0e02cc1f09d16f809f5d2d8f05abbeea6d1af04e
- paired vulnerable case: nomad_replica
- pragma (main file Replica.sol): 0.7.6
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - IMessageRecipient.sol -> packages/contracts-core/contracts/interfaces/IMessageRecipient.sol
  - Merkle.sol -> packages/contracts-core/contracts/libs/Merkle.sol
  - Message.sol -> packages/contracts-core/contracts/libs/Message.sol
  - NomadBase.sol -> packages/contracts-core/contracts/NomadBase.sol
  - Replica.sol -> packages/contracts-core/contracts/Replica.sol
  - TypeCasts.sol -> packages/contracts-core/contracts/libs/TypeCasts.sol
  - Version0.sol -> packages/contracts-core/contracts/Version0.sol

## Description

Fixed version (commit 0e02cc1, 2022-09-04) of the same files.
