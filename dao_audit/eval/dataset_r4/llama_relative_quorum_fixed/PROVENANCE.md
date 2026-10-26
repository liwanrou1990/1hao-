# Llama LlamaRelativeQuorum with core-only validateActionCreation — fixed sample (round4)

- repo: https://github.com/llamaxyz/llama
- commit: c053b8654c38462f49642c6d67ee83a1d32c1e3e
- paired vulnerable case: llama_relative_quorum
- pragma (main file LlamaRelativeQuorum.sol): 0.8.19
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - Enums.sol -> src/lib/Enums.sol
  - ILlamaStrategy.sol -> src/interfaces/ILlamaStrategy.sol
  - LlamaCore.sol -> src/LlamaCore.sol
  - LlamaPolicy.sol -> src/LlamaPolicy.sol
  - LlamaRelativeQuorum.sol -> src/strategies/LlamaRelativeQuorum.sol
  - Structs.sol -> src/lib/Structs.sol

## Description

Fixed version (commit c053b86, 2023-06-23).
