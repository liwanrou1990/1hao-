# LI.FI CBridgeFacet with DEX whitelist — fixed sample (round4)

- repo: https://github.com/lifinance/contracts
- commit: d69b896281df944cd1ab1b2da409afb2e0a0ece4
- paired vulnerable case: lifi_cbridge_facet
- pragma (main file CBridgeFacet.sol): ^0.8.7
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - CBridgeFacet.sol -> src/Facets/CBridgeFacet.sol
  - DexManagerFacet.sol -> src/Facets/DexManagerFacet.sol
  - ICBridge.sol -> src/Interfaces/ICBridge.sol
  - ILiFi.sol -> src/Interfaces/ILiFi.sol
  - LiFiDiamond.sol -> src/LiFiDiamond.sol
  - LibAsset.sol -> src/Libraries/LibAsset.sol
  - LibBytes.sol -> src/Libraries/LibBytes.sol
  - LibDiamond.sol -> src/Libraries/LibDiamond.sol
  - LibStorage.sol -> src/Libraries/LibStorage.sol
  - LibSwap.sol -> src/Libraries/LibSwap.sol
  - LibUtil.sol -> src/Libraries/LibUtil.sol

## Description

Fixed version (commit d69b8962, 2022-03-21); also includes DexManagerFacet.sol and LibStorage.sol introduced by the fix.
