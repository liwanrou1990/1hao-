# Bancor v0.6 BancorNetwork public token-transfer helpers (2020-06-18) — vulnerable sample (round4)

- repo: https://github.com/bancorprotocol/contracts-solidity
- commit: d4b1dc7b2e4d46a555b48ad34fd0fe235abad7b4 (parent of the fix commit = repo state of release 0.6.3 deployed 2020-06-16)
- fix commit: 47d8663d720d49aed55710039875070d514baca4
- incident / disclosure date: 2020-06-18
- pragma (main file TokenHandler.sol): 0.4.26
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - BancorNetwork.sol -> solidity/contracts/BancorNetwork.sol
  - ITokenHolder.sol -> solidity/contracts/utility/interfaces/ITokenHolder.sol
  - Owned.sol -> solidity/contracts/utility/Owned.sol
  - TokenHandler.sol -> solidity/contracts/utility/TokenHandler.sol
  - TokenHolder.sol -> solidity/contracts/utility/TokenHolder.sol
  - Utils.sol -> solidity/contracts/utility/Utils.sol
- reports / references:
  - https://blog.bancor.network/bancors-response-to-today-s-smart-contract-vulnerability-dc888c589fe4
  - https://blog.1inch.com/bancor-network-hack-2020
  - https://decrypt.co/32720/a-cryptocurrency-bug-put-545000-of-defi-funds-at-risk/

## Description

Bancor 0.6.x made TokenHandler.safeApprove/safeTransfer/safeTransferFrom public. BancorNetwork inherits them, so anyone could pull tokens from every wallet that had approved BancorNetwork. Bancor and front-running bots drained ~$545K on 2020-06-18.

## Verification notes

Fix commit 47d8663d read: CHANGELOG 0.6.4 'Fixed a security vulnerability that allowed any wallet to invoke the transfer functions'; the three helpers changed public -> internal.
