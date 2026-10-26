# Pickle Finance ControllerV4 swapExactJarForJar evil jars (2020-11-21) — vulnerable sample (round4)

- repo: https://github.com/pickle-finance/protocol
- commit: 024e89e70272d6320445cbb491c3fe2ffed41b88 (last commit touching controller-v4.sol before the exploit)
- fix commit: none (no source-level fix commit in this repo; see notes)
- incident / disclosure date: 2020-11-21
- pragma (main file controller-v4.sol): ^0.6.7
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - controller-v4.sol -> src/controller-v4.sol
  - curve.sol -> src/proxy-logic/curve.sol
  - jar-converter.sol -> src/interfaces/jar-converter.sol
  - jar.sol -> src/interfaces/jar.sol
  - strategy-base.sol -> src/strategies/strategy-base.sol
  - strategy-cmpd-dai-v2.sol -> src/strategies/compound/strategy-cmpd-dai-v2.sol
  - strategy.sol -> src/interfaces/strategy.sol
- reports / references:
  - https://rekt.news/pickle-finance-rekt
  - https://slowmist.medium.com/slowmist-a981b3a2a3dc
  - https://github.com/banteg/evil-jar
  - https://github.com/SunWeb3Sec/DeFiHackLabs/blob/main/src/test/2020-11/Pickle_exp.sol

## Description

The attacker passed fake jars and CurveProxyLogic.add_liquidity calldata to ControllerV4.swapExactJarForJar, draining the pDAI jar's strategy (~$19.7M).

## Verification notes

PoC (DeFiHackLabs, based on samczsun/banteg evil-jar) read: swapExactJarForJar(FakeJar(CDAI), FakeJar(CDAI), 0, 0, targets, datas) with CURVE_LOGIC.add_liquidity selectors. No source-level fix commit in this repo (the controller was replaced; swapExactJarAndJar removed only in 2021-05), so no fixed sample.
