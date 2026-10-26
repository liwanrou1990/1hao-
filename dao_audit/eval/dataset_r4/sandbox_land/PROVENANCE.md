# The Sandbox LAND public _burn (2022-01/02) — vulnerable sample (round4)

- repo: https://github.com/thesandboxgame/sandbox-smart-contracts
- commit: fdf99fef15dcd68d18672e60e3a0a81804fdfc8e (parent of the fix commit; the deployed LAND v1 (0x50f54747..., 2019) has the same public _burn per SlowMist, exact deployment commit not verified)
- fix commit: 4239515728476cff55458b3ef53dfafc4a38fda0
- incident / disclosure date: 2022-02-08
- pragma (main file ERC721BaseToken.sol): 0.5.9
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - Admin.sol -> src/solc_0.5/contracts_common/BaseWithStorage/Admin.sol
  - ERC721BaseToken.sol -> src/solc_0.5/Land/erc721/ERC721BaseToken.sol
  - ERC721Events.sol -> src/solc_0.5/contracts_common/Interfaces/ERC721Events.sol
  - Land.sol -> src/solc_0.5/Land.sol
  - LandBaseToken.sol -> src/solc_0.5/Land/erc721/LandBaseToken.sol
  - MetaTransactionReceiver.sol -> src/solc_0.5/contracts_common/BaseWithStorage/MetaTransactionReceiver.sol
  - SuperOperators.sol -> src/solc_0.5/contracts_common/BaseWithStorage/SuperOperators.sol
- reports / references:
  - https://slowmist.medium.com/the-vulnerability-behind-the-sandbox-land-migration-2abf68933170
  - https://github.com/SunWeb3Sec/DeFiHackLabs/blob/main/src/test/2022-02/Sandbox_exp.sol

## Description

The LAND ERC721 base contract exposed _burn publicly. Sandbox announced a LAND migration on 2022-01-28; an on-chain burn of a non-owned LAND (tx 0x34516ee0..., 2022-02-08) confirmed exploitability. Fix commit changed _burn to internal.

## Verification notes

Fix commit 42395157 ('fix: restricted visibility of _burn method') read. Incident date uses the observed burn tx (DeFiHackLabs entry 20220208); the vulnerability announcement was 2022-01-28.
