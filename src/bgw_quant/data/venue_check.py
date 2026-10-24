"""交易场所与数据匹配检查：每个资产的场所/标识/链/计价币/数据来源，以及库内数据是否与登记场所一致。

禁止混用：代币化美股的 playbook 数据必须是 venue=token 且 source 属于 {bitget_rwa:*, REAL_HISTORICAL:csv(声明 chain/contract), SIMULATED:token}；
native（Massive/交易所股价）只能作参考基准，不得进入事后路径搜索。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..config import asset_class_of, load_yaml

ALLOWED_TOKEN_SOURCE_PREFIXES = ("bitget_rwa:", "REAL_HISTORICAL:csv", "SIMULATED:token", "SIMULATED:crypto", "bitget_spot_public")


def load_venues() -> dict[str, Any]:
    try:
        return load_yaml("venues.yaml")
    except FileNotFoundError:
        return {"venues": {}, "assets": {}}


def source_matches_venue(symbol: str, source: str, chain: str | None, contract: str | None) -> tuple[bool, str]:
    """库内 bars 的 source/chain/contract 是否与资产登记场所相容。"""
    ac = asset_class_of(symbol)
    if not any(str(source).startswith(p) for p in ALLOWED_TOKEN_SOURCE_PREFIXES):
        return False, f"来源 {source} 不是代币/现货成交数据（疑似普通股价或未知来源）"
    if str(source).startswith("massive"):
        return False, "Massive 原生股价不得作为代币成交数据"
    if ac == "stock" and str(source).startswith("REAL_HISTORICAL:csv") and not (chain and contract):
        return False, "代币化美股的 CSV 导入必须声明 chain 与 contract（--chain/--contract），否则无法确认是哪条链上的哪个代币"
    if ac == "crypto" and str(source).startswith("bitget_spot_public"):
        return True, "交易所现货价格；成交渠道若为钱包 swap 则为成交估算"
    return True, "相容"


def venue_matrix(pipeline, day: date | None = None) -> list[dict]:
    cfg = load_venues()
    venues, assets = cfg.get("venues", {}), cfg.get("assets", {})
    out = []
    for sym in pipeline.all_symbols():
        a = assets.get(sym, {})
        cands = a.get("candidates") or ([{"venue": a.get("venue"), "token": a.get("identifier"), "chain": None, "contract": None}] if a.get("venue") else [])
        row = {"symbol": sym, "asset_class": asset_class_of(sym), "verification": a.get("verification", "unverified"),
               "venues": [{"venue": c.get("venue"), "venue_name": venues.get(c.get("venue"), {}).get("name"), "token": c.get("token"), "chain": c.get("chain"),
                           "contract": c.get("contract"), "quote": venues.get(c.get("venue"), {}).get("quote")} for c in cands],
               "exec_assumption": a.get("exec_assumption"), "data_in_store": None, "data_matches_venue": None, "note": None}
        if day is not None:
            b = pipeline.store.load_bars(sym, day, day, "token")
            if b.empty:
                row["data_in_store"] = "缺失"
            else:
                src, chain, contract = str(b["source"].iloc[0]), b["chain"].iloc[0], b["contract"].iloc[0]
                ok, note = source_matches_venue(sym, src, chain, contract)
                sim = bool((b["quality"].astype(int) & 128).any())
                row.update({"data_in_store": f"{src} chain={chain} contract={str(contract)[:12] if contract else None}" + ("（SIMULATED）" if sim else ""),
                            "data_matches_venue": ok, "note": note})
            nat = pipeline.store.load_bars(sym, day, day, "native")
            if not nat.empty:
                row["note"] = (row["note"] or "") + "；库内另有 native 原生股价（仅作基准，不进入事后路径）"
        out.append(row)
    return out
