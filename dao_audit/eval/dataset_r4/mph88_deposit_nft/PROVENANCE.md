# 88mph v2 pool deposit NFT unprotected init (Immunefi disclosure 2021-06-07) — vulnerable sample (round4)

- repo: https://github.com/88mphapp/88mph-contracts
- commit: a4c48d61661ae3d8ce5aadfda6e4de27c4f07a9e (last commit touching contracts/NFT.sol in the v2 code line (EIP-1167 NFT clones))
- fix commit: none (no source-level fix commit in this repo; see notes)
- incident / disclosure date: 2021-06-07
- pragma (main file NFT.sol): 0.5.17
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - CloneFactory.sol -> contracts/libs/CloneFactory.sol
  - DInterest.sol -> contracts/DInterest.sol
  - NFT.sol -> contracts/NFT.sol
  - NFTFactory.sol -> contracts/NFTFactory.sol
- reports / references:
  - https://immunefi.com/blog/bug-fix-reviews/88mph-function-initialization-bugfix-review/
  - https://medium.com/immunefi/88mph-function-initialization-bug-fix-postmortem-c3a2282894d3
  - https://github.com/SunWeb3Sec/DeFiHackLabs/blob/main/src/test/2021-06/88mph_exp.sol

## Description

Whitehat disclosure: the deposit NFT contracts of three v2 pools exposed an unprotected init(); whoever called it became owner and could mint/burn users' deposit NFTs, which control withdrawal of the underlying deposits. 88mph paused the pools and returned funds.

## Verification notes

PoC read (calls init(address(this),...) on 0xF0b7DE03..., then burn(1)/mint(this,1)). Report via web-search summary. No source-level fix for the v2 NFT exists (v3 rewrite uses OZ initializer), so no fixed sample.
