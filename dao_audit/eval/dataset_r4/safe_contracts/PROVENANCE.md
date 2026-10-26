# Gnosis Safe v1.3.0 core — secure_control sample (round4)

- repo: https://github.com/safe-global/safe-contracts
- commit: 186a21a74b327f17fc41217a927dea7064f74604
- pragma (main file GnosisSafe.sol): >=0.7.0 <0.9.0
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - Enum.sol -> contracts/common/Enum.sol
  - EtherPaymentFallback.sol -> contracts/common/EtherPaymentFallback.sol
  - Executor.sol -> contracts/base/Executor.sol
  - FallbackManager.sol -> contracts/base/FallbackManager.sol
  - GnosisSafe.sol -> contracts/GnosisSafe.sol
  - GnosisSafeMath.sol -> contracts/external/GnosisSafeMath.sol
  - GnosisSafeProxy.sol -> contracts/proxies/GnosisSafeProxy.sol
  - GnosisSafeProxyFactory.sol -> contracts/proxies/GnosisSafeProxyFactory.sol
  - GuardManager.sol -> contracts/base/GuardManager.sol
  - IProxyCreationCallback.sol -> contracts/proxies/IProxyCreationCallback.sol
  - ModuleManager.sol -> contracts/base/ModuleManager.sol
  - OwnerManager.sol -> contracts/base/OwnerManager.sol
  - SecuredTokenTransfer.sol -> contracts/common/SecuredTokenTransfer.sol
  - SelfAuthorized.sol -> contracts/common/SelfAuthorized.sol
  - SignatureDecoder.sol -> contracts/common/SignatureDecoder.sol
  - Singleton.sol -> contracts/common/Singleton.sol
  - StorageAccessible.sol -> contracts/common/StorageAccessible.sol

## Description

Secure control: safe-global/safe-contracts tag v1.3.0 (core contracts and proxies; handlers/libraries/examples omitted).
