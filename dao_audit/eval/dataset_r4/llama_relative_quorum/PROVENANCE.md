# Llama LlamaRelativeQuorum unprotected validateActionCreation (Code4rena 2023-06 H-02) — vulnerable sample (round4)

- repo: https://github.com/llamaxyz/llama
- commit: 0f72fd894cf24109677ebcefd6891806b6068575 (parent of the fix commit)
- fix commit: c053b8654c38462f49642c6d67ee83a1d32c1e3e
- incident / disclosure date: 2023-06-06
- pragma (main file LlamaRelativeQuorum.sol): 0.8.19
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - Enums.sol -> src/lib/Enums.sol
  - ILlamaStrategy.sol -> src/interfaces/ILlamaStrategy.sol
  - LlamaCore.sol -> src/LlamaCore.sol
  - LlamaPolicy.sol -> src/LlamaPolicy.sol
  - LlamaRelativeQuorum.sol -> src/strategies/LlamaRelativeQuorum.sol
  - Structs.sol -> src/lib/Structs.sol
- reports / references:
  - https://code4rena.com/reports/2023-06-llama
  - https://github.com/code-423n4/2023-06-llama-findings/issues/62
  - https://github.com/llamaxyz/llama/commit/c053b8654c38462f49642c6d67ee83a1d32c1e3e

## Description

Audit finding H-02: the quorum-supply snapshot taken at action creation can be refreshed by any account, manipulating approval thresholds of pending actions.

## Verification notes

Fix commit c053b86 read: validateActionCreation reverts with OnlyLlamaCore unless msg.sender == llamaCore; commit message cites the C4 issue #62.
