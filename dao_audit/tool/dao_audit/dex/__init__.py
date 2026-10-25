"""Real DEX cross-market arbitrage research (read-only, Base/Arbitrum).

Executable quotes via on-chain QuoterV2 (cast eth_call), same-block two-leg
round trips, full cost accounting, capacity by capital size. No simulated
prices; missing quote/cost -> NO_TRADE. No signing or real execution.
Kept separate from the DAO governance security modules.
"""
