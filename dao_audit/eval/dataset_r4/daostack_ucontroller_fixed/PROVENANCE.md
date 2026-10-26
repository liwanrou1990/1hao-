# DAOstack UController after newOrganization single-read fix — fixed sample (round4)

- repo: https://github.com/daostack/arc
- commit: e6f7582392829c4964b77b6710b47ed2fd074cc1
- paired vulnerable case: daostack_ucontroller
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

## Description

Fixed version (commit e6f7582, 2019-02-07).
