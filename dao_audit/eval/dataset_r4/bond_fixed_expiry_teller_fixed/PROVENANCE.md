# Bond Protocol BondFixedExpiryTeller after token validation — fixed sample (round4)

- repo: https://github.com/Bond-Protocol/bond-contracts
- commit: cc252d658c1cdab2e56bde6d95cf03c9f357b49d
- paired vulnerable case: bond_fixed_expiry_teller
- pragma (main file BondFixedExpiryTeller.sol): 0.8.15
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - BondBaseTeller.sol -> src/bases/BondBaseTeller.sol
  - BondFixedExpiryTeller.sol -> src/BondFixedExpiryTeller.sol
  - CloneERC20.sol -> src/lib/CloneERC20.sol
  - ERC20BondToken.sol -> src/ERC20BondToken.sol
  - IBondFixedExpiryTeller.sol -> src/interfaces/IBondFixedExpiryTeller.sol
  - TransferHelper.sol -> src/lib/TransferHelper.sol

## Description

Fixed version (commit cc252d6, 2022-11-29, 'add updated contracts from zellic and sherlock audits').
