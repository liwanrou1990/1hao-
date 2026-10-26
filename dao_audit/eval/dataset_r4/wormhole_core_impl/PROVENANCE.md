# Wormhole core bridge uninitialized implementation (Immunefi disclosure 2022-02-24) — vulnerable sample (round4)

- repo: https://github.com/wormhole-foundation/wormhole
- commit: 1175eb1315ef0bc03da55c3b61236e12953e81cb (last commit before the a055af14 Setup refactor; the deployed implementation 0x736D2A39... exposes initialize(address[],uint16,uint16,bytes32), which only exists in this pre-refactor code)
- fix commit: none (no source-level fix commit in this repo; see notes)
- incident / disclosure date: 2022-02-24
- pragma (main file Implementation.sol): ^0.8.0
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - BytesLib.sol -> ethereum/contracts/libraries/external/BytesLib.sol
  - Getters.sol -> ethereum/contracts/Getters.sol
  - Governance.sol -> ethereum/contracts/Governance.sol
  - GovernanceStructs.sol -> ethereum/contracts/GovernanceStructs.sol
  - Implementation.sol -> ethereum/contracts/Implementation.sol
  - Messages.sol -> ethereum/contracts/Messages.sol
  - Setters.sol -> ethereum/contracts/Setters.sol
  - State.sol -> ethereum/contracts/State.sol
  - Structs.sol -> ethereum/contracts/Structs.sol
  - Wormhole.sol -> ethereum/contracts/Wormhole.sol
- reports / references:
  - https://immunefi.com/blog/bug-fix-reviews/wormhole-uninitialized-proxy-bugfix-review/
  - https://medium.com/immunefi/wormhole-uninitialized-proxy-bugfix-review-90250c41a43a
  - https://github.com/immunefi-team/bugfix-reviews-pocs/tree/main/src/Wormhole

## Description

Bug-bounty disclosure (satya0x, USD 10M bounty). The UUPS-style core bridge implementation was left uninitialized after an earlier bugfix reverted its initialization. Anyone could call Implementation.initialize on the logic contract with their own guardian set, then pass Governance.submitContractUpgrade with a VAA they signed, making upgradeImplementation delegatecall into a contract that self-destructs the implementation. No funds were lost (patched same day by initializing the implementation on-chain).

## Verification notes

Read the Immunefi PoC (immunefi-team/bugfix-reviews-pocs, src/Wormhole/WormholeBugFixReview.sol) which calls initialize(guardians,0,0,0) then submitContractUpgrade on 0x736D2A39...; report text obtained via web search summaries (immunefi.com and medium.com are not reachable from this sandbox). The fix was operational (initializing the implementation), so there is no source-level fix commit and no fixed sample. Exact build commit of the deployed implementation is inferred from the initialize signature, not from a deployment record.
