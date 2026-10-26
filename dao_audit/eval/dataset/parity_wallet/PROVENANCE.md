# Parity 多签 WalletLibrary 冻结 (2017-11-06) — 漏洞样本 (round3, 新项目/新架构/新版本)
- repo: https://github.com/paritytech/parity-ethereum  vuln_commit: 02d462e2636f1898df3e7556364260c594b112e6
- 文件: js/src/contracts/snippets/enhanced-wallet.sol (pragma ^0.4.9; WalletLibrary + 代理 Wallet 同文件)
- 漏洞: WalletLibrary 作为独立合约部署且从未初始化 → m_numOwners==0 → only_uninitialized 守卫形同虚设;
  任意人直接对库调用 initWallet/initMultiowned 成为唯一 owner(VULN-1),再 onlymanyowners 下调用
  kill→suicide 自毁共享库(VULN-4),使所有 delegatecall 该库的代理钱包永久冻结(~513k ETH)。
- fixed_commit: null(部署实例无源码修复可救;库已自毁,资金永久冻结)。
- 报告: parity.io 官方事后分析; paritytech/parity-ethereum issue #6995; OpenZeppelin "Parity Wallet Hack Reloaded"。
