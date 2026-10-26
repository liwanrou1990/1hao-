# Bond Protocol BondFixedExpiryTeller.redeem (OlympusDAO OHM bonds, 2022-10-21) — vulnerable sample (round4)

- repo: https://github.com/Bond-Protocol/bond-contracts
- commit: 2c0311249e2356bf1b5f27c08e7b6d5e5872cd7f (initial release commit; BondFixedExpiryTeller.sol unchanged until the fix)
- fix commit: cc252d658c1cdab2e56bde6d95cf03c9f357b49d
- incident / disclosure date: 2022-10-21
- pragma (main file BondFixedExpiryTeller.sol): 0.8.15
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - BondBaseTeller.sol -> src/bases/BondBaseTeller.sol
  - BondFixedExpiryTeller.sol -> src/BondFixedExpiryTeller.sol
  - CloneERC20.sol -> src/lib/CloneERC20.sol
  - ERC20BondToken.sol -> src/ERC20BondToken.sol
  - IBondFixedExpiryTeller.sol -> src/interfaces/IBondFixedExpiryTeller.sol
  - TransferHelper.sol -> src/lib/TransferHelper.sol
- reports / references:
  - https://medium.com/@Bond_Protocol/bond-protocol-incident-report-e0aa1dfdeab
  - https://www.theblock.co/news/markets/2022-10-21-hacker-drains-olympus-dao-178927
  - https://halborn.com/explained-the-olympusdao-hack-october-2022/

## Description

Missing validation that the redeemed bond token was issued by the teller let the attacker pass a fake token returning OHM as underlying and drain vesting OHM.

## Verification notes

Fix in cc252d6 read: redeem now requires token_ == bondTokens[underlying][expiry] and uses safeTransfer.
