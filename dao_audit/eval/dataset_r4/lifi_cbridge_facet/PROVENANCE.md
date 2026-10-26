# LI.FI CBridgeFacet unchecked swap call (2022-03-20) — vulnerable sample (round4)

- repo: https://github.com/lifinance/contracts
- commit: 7b41d7d3306f213480b167e9487071d1c959d4fb (last commit before the fix (2022-03-17))
- fix commit: d69b896281df944cd1ab1b2da409afb2e0a0ece4
- incident / disclosure date: 2022-03-20
- pragma (main file CBridgeFacet.sol): ^0.8.7
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - CBridgeFacet.sol -> src/Facets/CBridgeFacet.sol
  - ICBridge.sol -> src/Interfaces/ICBridge.sol
  - ILiFi.sol -> src/Interfaces/ILiFi.sol
  - LiFiDiamond.sol -> src/LiFiDiamond.sol
  - LibAsset.sol -> src/Libraries/LibAsset.sol
  - LibBytes.sol -> src/Libraries/LibBytes.sol
  - LibDiamond.sol -> src/Libraries/LibDiamond.sol
  - LibSwap.sol -> src/Libraries/LibSwap.sol
  - LibUtil.sol -> src/Libraries/LibUtil.sol
- reports / references:
  - https://blog.li.fi/20th-march-the-exploit-e9e1c5c03eb9
  - https://blocksecteam.medium.com/li-fi-attack-a-cross-chain-bridge-vulnerability-no-its-due-to-unchecked-external-call-c31e7dadf60f

## Description

The diamond's CBridge facet let anyone execute arbitrary calls through the swap step; the attacker called transferFrom on tokens approved to LI.FI.

## Verification notes

Fix commit d69b8962 read: adds DexManagerFacet + LibStorage.dexWhitelist and requires approveTo/callTo to be whitelisted before LibSwap.swap in each facet.
