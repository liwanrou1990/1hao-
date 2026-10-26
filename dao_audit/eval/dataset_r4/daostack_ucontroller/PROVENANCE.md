# DAOstack UController newOrganization hijack of Genesis Alpha (2026-09) — vulnerable sample (round4)

- repo: https://github.com/daostack/arc
- commit: 14acc6f93c4ec27fe71d11aedda04652c7095ca2 (parent of the e6f7582 fix (2019-01-31); the on-chain UController 0xD5bEe5D9... is unverified, version inferred from the double-read behaviour reproduced in the PoC)
- fix commit: e6f7582392829c4964b77b6710b47ed2fd074cc1
- incident / disclosure date: 2026-09-26
- pragma (main file UController.sol): ^0.5.2
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - Avatar.sol -> contracts/controller/Avatar.sol
  - ControllerInterface.sol -> contracts/controller/ControllerInterface.sol
  - DAOToken.sol -> contracts/controller/DAOToken.sol
  - GlobalConstraintInterface.sol -> contracts/globalConstraints/GlobalConstraintInterface.sol
  - SchemeRegistrar.sol -> contracts/universalSchemes/SchemeRegistrar.sol
  - UController.sol -> contracts/controller/UController.sol
  - UniversalScheme.sol -> contracts/universalSchemes/UniversalScheme.sol
  - UniversalSchemeInterface.sol -> contracts/universalSchemes/UniversalSchemeInterface.sol
- reports / references:
  - https://github.com/SunWeb3Sec/DeFiHackLabs/blob/main/src/test/2026-09/DAOstack_exp.sol
  - https://etherscan.io/tx/0xcfff7b060a7fb881f4bd8d2498dbe4e09454f7afaf5328fc12b59812f6737cc1
  - https://github.com/daostack/arc/commit/e6f7582392829c4964b77b6710b47ed2fd074cc1

## Description

Governance takeover: through the shared UController the attacker registered a fake avatar whose getters return a decoy on the first read and the victim's real Reputation afterwards, became a 0x1F scheme over the victim's Reputation, minted 20,000 REP, passed a SchemeRegistrar proposal installing itself on the real avatar and drained it with sendEther.

## Verification notes

Root cause taken from the DeFiHackLabs PoC header (trace-derived; contracts unverified on Etherscan). Fix commit e6f7582 ('fix uController newOrganization', 2019-02-07) read: reads nativeToken/nativeReputation once into locals so check and storage use the same address; later commits 41ff46d/c0baf59 additionally require token/reputation ownership and an actors registry. Mapping the deployed bytecode to this exact commit is an inference (unverified source).
