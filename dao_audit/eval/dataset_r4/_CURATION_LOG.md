# Round 4 curation log

Curator rules applied: real public incidents or disclosed audit / bug-bounty findings only; source must be
on GitHub at an identifiable commit (byte-identical copies, verified by `make_build_manifests.py`, 0 unmatched);
no look-alike, planted or edited code; hard-excluded projects (Audius, Beanstalk, Tornado Cash, Compound,
OpenZeppelin, DODO, MakerDAO/dapphub, Furucombo/dinngo, Aave, Parity, Punk, Poly Network, Nouns, Build Finance)
not used. The detector (`tool/`) was not read or run.

Sandbox limitations: rekt.news, immunefi.com, medium.com and etherscan were not fetchable; report contents were
checked through web-search summaries, the DeFiHackLabs PoC repository (cloned), the Immunefi bugfix-review PoC
repository (cloned), Code4rena findings repositories (report.md, cloned) and the fix commits themselves.

## Accepted (vulnerable, with fixed pair where a source-level fix commit exists)

| case | source of truth | fixed sample |
|---|---|---|
| wormhole_core_impl | Immunefi bugfix review + PoC (2022-02 disclosure) | none (operational fix) |
| nomad_replica | Nomad hack 2022-08-01; fix commit 0e02cc1 | nomad_replica_fixed |
| bancor_network | Bancor 2020-06-18 incident; fix commit 47d8663d (CHANGELOG 0.6.4) | bancor_network_fixed |
| sandbox_land | SlowMist analysis + on-chain burn; fix commit 42395157 | sandbox_land_fixed |
| pickle_controller | Pickle 2020-11-21 hack; DeFiHackLabs/evil-jar PoC | none (no fix commit for this controller) |
| lifi_cbridge_facet | LI.FI post-mortem 2022-03-20; fix commit d69b8962 | lifi_cbridge_facet_fixed |
| mph88_deposit_nft | Immunefi 88mph init bugfix review (2021-06) + PoC | none (v2 NFT never patched in source; v3 rewrite) |
| sushi_route_processor2 | Sushi RP2 2023-04-09 hack; fix commit bb343a0c | sushi_route_processor2_fixed |
| daostack_ucontroller | DAOstack Genesis Alpha hijack 2026-09 (DeFiHackLabs PoC, trace-derived); fix commit e6f7582 | daostack_ucontroller_fixed |
| bond_fixed_expiry_teller | Bond Protocol / OlympusDAO 2022-10-21 incident report; fix in cc252d6 | bond_fixed_expiry_teller_fixed |
| olympus_governance | Code4rena 2022-08 Olympus H-02 / M-06 | none (project repo private) |
| party_veto_proposal | Code4rena 2023-04 Party H-08; fix commit 48300a8 | party_veto_proposal_fixed |
| llama_relative_quorum | Code4rena 2023-06 Llama H-02; fix commit c053b86 | llama_relative_quorum_fixed |
| moonwell_temporal_governor | Code4rena 2023-07 Moonwell M-08; fix in 4580996b | moonwell_temporal_governor_fixed |

## Accepted (secure controls)

| case | reason |
|---|---|
| uniswap_governance | Uniswap/governance v1.0.2 as deployed (GovernorAlpha/Timelock/UNI); no known vulnerability |
| ens_governor | ensdomains/governance mainnet-deployment commit (ENSGovernor/ENSToken/TimelockController); no known vulnerability |
| safe_contracts | Gnosis Safe v1.3.0 core; heavily audited, no known vulnerability at tag |
| lido_voting | Lido aragon-apps Voting app-voting-v3.0.0-1; snapshot voting, no known vulnerability at tag |

## Rejected / not usable (reason)

- Temple DAO StaxLPStaking migrateStake (2022-10): real, but StaxLPStaking is not in TempleDAO/temple history; no public Stax repo -> unavailable.
- Ronin bridge initializeV3 not called (2024-08): root cause is an upgrade/deployment omission (initializer never invoked), not a defect visible in source -> rejected.
- Multichain/AnySwap anySwapOutUnderlyingWithPermit (2022-01): exploited contract is AnyswapV4Router, which is not in anyswap/anyswap-v1-core (only V3/V5/V6 routers; Multichain stated V3 Router was safe). Substituting another router file would not be the exploited source -> unavailable.
- Dexible selfSwap (2023-02): no public GitHub source located -> unavailable.
- Socket gateway WrappedTokenSwapperImpl (2024-01): repo SocketDotTech/socket-ll-contracts is not public -> unavailable.
- Seneca Chamber performOperations (2024-02): no public GitHub source located -> unavailable.
- Visor Finance RewardsHypervisor (2021-12): no public repo found for the exploited contract (VisorFinance / GammaStrategies) -> unavailable.
- Rikkei Finance setOracleData (2022-04): no public GitHub source located; also oracle-driven loss -> unavailable.
- Yam Finance rebase/governance bug (2020-08): root cause is a math/rebase bug (excluded class) -> rejected.
- Yam Finance dormant-DAO takeover (2026-09): low-participation governance capture through rules working as written (no source defect) -> rejected.
- True Seigniorage Dollar governance takeover (2021-03): no public repo located; participation-based capture -> unavailable.
- Fortress Loans governance + oracle (2022-05): no public GitHub source located; loss path through oracle manipulation -> unavailable.
- Swerve governance attempt (2021): Vyper contracts and a governance-participation event, no Solidity defect -> rejected.
- KiloEx (2025-04): no public GitHub source located -> unavailable.
- Kinto uninitialized proxy (2025-07): front-run of non-atomic proxy initialization at deployment (operational), not an in-source defect -> rejected.
- Safemoon public burn (2023-03): the upgraded SafeMoon v2 implementation is not in SafeMoonProtocol/SafeMoon.sol (only 2021 v1 code) -> unavailable.
- Unizen (2024-03): no public GitHub source located -> unavailable.
- LI.FI GasZipFacet (2024-07): real and on GitHub, but same project as the accepted 2022 LI.FI case; skipped to avoid project duplication.
- Prisma Finance MigrateTroveZap (2024-03): zap contract not in prisma-fi/prisma-contracts -> unavailable.
- Hedgey ClaimCampaigns (2024-04): hedgey-finance/ClaimCampaigns history starts 2024-10 (post-exploit rewrite); vulnerable version not present -> unavailable.
- Rubic RubicProxy routerCallNative (2022-12): Cryptorubic/multi-proxy-rubic history starts 2023-01 (post-incident); exploited proxy not present -> unavailable.
- Qubit QBridge deposit (2022-01): bridge contracts not in PancakeBunny-finance/qubit-finance -> unavailable.
- Arbitrum Inbox re-initialization bounty (2022-09): could not verify the exact source-level mechanism and commit (write-up unreachable; candidate commit 88e7111 only removes postUpgradeInit logic) -> rejected for label accuracy.
- Superfluid ctx injection (2022-02): user-identity spoofing in calldata context; mapping to the four classes ambiguous -> not curated.
- Exactly Protocol DebtManager (2023-08): user impersonation via unvalidated market combined with share-price manipulation; mixed root cause -> rejected.
- Enjin Crypto Items registry re-initialization (2026-08): registry/platform source not public (enjin/erc-1155 is only the reference implementation) -> unavailable.
- X100 Senate same-block governance capture (2026-10): source verified only on Sourcify, no GitHub repository -> unavailable.
- StrongBlock abandoned Governor (2026-08) and Term Finance Aragon capture (2026-08): governance capture by cheap/thin voting supply, contracts behaving as written -> rejected.
- Startale ERC-7579 transient init flag (2026-09): no public GitHub repository located -> unavailable.
- Renegade uninitialized proxy (2026-05): Stylus (Rust) implementation, not Solidity -> rejected.
- Pike Finance uninitialized proxy (2024-04): no public GitHub source located -> unavailable.
- SuperRare RareStakingV1 (2025-07): no public GitHub source located -> unavailable.
- Cork Protocol hook access control (2025-05): source repository not located/verified -> unavailable.
- Curio DAO (2024-03): ds-chief fork, overlaps excluded MakerDAO/dapphub code -> rejected.
- Arcade.xyz C4 2023-07 M-02 (vote-power manipulation): only theoretical for the configured GSC vault -> rejected.
- Party C4 2023-04 H-02 (flash-loan crowdfund majority): acknowledged as configuration-dependent (executionDelay 0) -> rejected; H-08 used instead.
- Olympus C4 2022-08 H-03 (TRSRY front-runnable setApprovalFor): not a governance/permission root cause for this dataset -> not labeled.
- Gitcoin governance (control): Compound GovernorAlpha fork; Uniswap governance chosen instead.
- Curve DAO VotingEscrow (control): Vyper, no .sol files -> rejected.
