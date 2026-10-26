# The Sandbox LAND with internal _burn — fixed sample (round4)

- repo: https://github.com/thesandboxgame/sandbox-smart-contracts
- commit: 4239515728476cff55458b3ef53dfafc4a38fda0
- paired vulnerable case: sandbox_land
- pragma (main file ERC721BaseToken.sol): 0.5.9
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - Admin.sol -> src/solc_0.5/contracts_common/BaseWithStorage/Admin.sol
  - ERC721BaseToken.sol -> src/solc_0.5/Land/erc721/ERC721BaseToken.sol
  - ERC721Events.sol -> src/solc_0.5/contracts_common/Interfaces/ERC721Events.sol
  - Land.sol -> src/solc_0.5/Land.sol
  - LandBaseToken.sol -> src/solc_0.5/Land/erc721/LandBaseToken.sol
  - MetaTransactionReceiver.sol -> src/solc_0.5/contracts_common/BaseWithStorage/MetaTransactionReceiver.sol
  - SuperOperators.sol -> src/solc_0.5/contracts_common/BaseWithStorage/SuperOperators.sol

## Description

Fixed version (commit 42395157, 2021-12-28).
