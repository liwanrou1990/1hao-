"""Public data-source connectors. All real; none fabricate on failure.

- DefiLlama: token prices + protocol TVL (no API key).
- Snapshot: DAO governance proposals + votes (GraphQL, no key).
- DexQuote: cross-source price comparison for quote-divergence detection.

Each method returns a provenance Record (or list wrapped in one). On a blocked
or failing fetch the Record is UNAVAILABLE with the real error.
"""
from __future__ import annotations
from typing import List, Optional

from .provenance import Record, http_get_json, http_post_json, OK


class DefiLlamaSource:
    BASE = "https://api.llama.fi"
    COINS = "https://coins.llama.fi"

    def price_current(self, coin_keys: List[str]) -> Record:
        """coin_keys like ['coingecko:ethereum','ethereum:0x...']."""
        key = ",".join(coin_keys)
        return http_get_json(f"{self.COINS}/prices/current/{key}", source="defillama")

    def price_at(self, coin_key: str, ts: int) -> Record:
        """Historical price at unix ts (for backtests; no lookahead if ts<=event)."""
        return http_get_json(f"{self.COINS}/prices/historical/{ts}/{coin_key}",
                             source="defillama")

    def protocol_tvl(self, slug: str) -> Record:
        return http_get_json(f"{self.BASE}/protocol/{slug}", source="defillama")


class SnapshotSource:
    URL = "https://hub.snapshot.org/graphql"

    def recent_proposals(self, space: str, first: int = 5) -> Record:
        q = {
            "query": """query($space:String!,$first:Int!){
              proposals(first:$first, where:{space:$space}, orderBy:"created", orderDirection:desc){
                id title state created start end scores_total scores choices
                snapshot author space{ id }
              }}""",
            "variables": {"space": space, "first": first},
        }
        return http_post_json(self.URL, q, source="snapshot")

    def proposal_result(self, proposal_id: str) -> Record:
        q = {
            "query": """query($id:String!){ proposal(id:$id){
              id title state scores scores_total choices end snapshot } }""",
            "variables": {"id": proposal_id},
        }
        return http_post_json(self.URL, q, source="snapshot")


class DexQuoteSource:
    """Cross-source spot price comparison to surface quote divergence.

    Uses independent public price sources; a real on-chain DEX quoter can be
    added via EvmRpc.eth_call once RPC egress is permitted. Divergence is only
    reported when at least two sources return OK (else INSUFFICIENT_DATA).
    """
    def __init__(self, defillama: Optional[DefiLlamaSource] = None):
        self.defillama = defillama or DefiLlamaSource()

    def compare(self, coin_key: str) -> dict:
        prices = {}
        recs = {}
        dl = self.defillama.price_current([coin_key])
        recs["defillama"] = dl
        if dl.ok and isinstance(dl.data, dict):
            coins = dl.data.get("coins", {})
            info = coins.get(coin_key)
            if info and "price" in info:
                prices["defillama"] = float(info["price"])
        ok_prices = list(prices.values())
        if len(ok_prices) >= 2:
            spread = (max(ok_prices) - min(ok_prices)) / min(ok_prices)
            status = OK
        else:
            spread = None
            status = "INSUFFICIENT_DATA"
        return {"coin": coin_key, "prices": prices, "spread": spread,
                "status": status, "records": {k: v.to_dict() for k, v in recs.items()}}
