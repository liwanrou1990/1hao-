"""交易员训练模式：隐藏未来K线，逐根推进，交易员给出 买/卖/持有 决策；结束后对照 A（事后最优）与 AI/规则策略并计分。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import pandas as pd

from ..costs.model import CostModel
from ..replay.executable import ExecutableReplay
from ..replay.hindsight import optimal_trades


@dataclass
class TrainingState:
    session_id: str
    trader: str
    symbol: str
    day: str
    capital_usd: float
    cost_profile: str
    cursor: int = 0                      # 已揭示到第几根（含）
    position: int = 0
    pending_action: str | None = None    # 本根收盘下达的指令，下根开盘执行
    decisions: list[dict] = field(default_factory=list)
    finished: bool = False
    score: dict | None = None
    data_is_simulated: bool = True
    warmup_bars: int = 30                # 开局先展示的K线数


class TrainingSession:
    def __init__(self, bars: pd.DataFrame, cost: CostModel, state: TrainingState):
        self.bars = bars.reset_index(drop=True)
        self.cost = cost
        self.state = state
        if state.cursor == 0:
            state.cursor = min(state.warmup_bars, len(self.bars) - 1)

    @classmethod
    def start(cls, bars: pd.DataFrame, cost: CostModel, trader: str, capital_usd: float, cost_profile: str, warmup: int = 30):
        sim = bool((bars["quality"].astype(int) & 128).any())
        st = TrainingState(uuid.uuid4().hex[:12], trader, str(bars["symbol"].iloc[0]),
                           str(pd.to_datetime(bars["ts_ny"]).dt.date.iloc[0]), capital_usd, cost_profile,
                           data_is_simulated=sim, warmup_bars=warmup)
        return cls(bars, cost, st)

    # ---- 只暴露已揭示的K线 ----
    def visible_bars(self) -> pd.DataFrame:
        return self.bars.iloc[: self.state.cursor + 1]

    def decide(self, action: str) -> dict:
        """交易员在当前已揭示的最后一根收盘时下达 buy/sell/hold；在下一根开盘执行。"""
        action = action.lower()
        assert action in ("buy", "sell", "hold")
        if self.state.finished:
            raise RuntimeError("训练已结束")
        self.state.pending_action = action
        return {"cursor": self.state.cursor, "pending": action}

    def advance(self) -> dict:
        """揭示下一根K线，并执行上一根收盘时的指令。"""
        if self.state.finished:
            raise RuntimeError("训练已结束")
        if self.state.cursor >= len(self.bars) - 1:
            return self.finish()
        i = self.state.cursor + 1
        act = self.state.pending_action or "hold"
        exec_px = float(self.bars["open"].iloc[i])
        rec = {"idx": i, "ts_ny": self.bars["ts_ny"].iloc[i], "action": act, "position_before": self.state.position}
        if act == "buy" and self.state.position == 0:
            self.state.position = 1
            rec["exec_price"] = exec_px
        elif act == "sell" and self.state.position == 1:
            self.state.position = 0
            rec["exec_price"] = exec_px
        else:
            rec["action"] = "hold"
        rec["position_after"] = self.state.position
        self.state.decisions.append(rec)
        self.state.pending_action = None
        self.state.cursor = i
        last = i == len(self.bars) - 1
        if last:
            return self.finish()
        return {"cursor": i, "bar": self._bar(i), "position": self.state.position, "finished": False}

    def _bar(self, i: int) -> dict:
        b = self.bars.iloc[i]
        return {"time": int(pd.Timestamp(b["ts"]).timestamp()), "ts_ny": b["ts_ny"], "open": float(b["open"]), "high": float(b["high"]),
                "low": float(b["low"]), "close": float(b["close"]), "volume": float(b["volume"]) if pd.notna(b["volume"]) else 0.0}

    # ---- 计分：把交易员决策转成目标仓位序列，用同一引擎与成本模型复盘 ----
    def trader_signals(self) -> pd.Series:
        sig = pd.Series(0, index=self.bars.index, dtype=int)
        pos = 0
        for i in range(len(self.bars)):
            # 决策记录的 exec 在 i 根开盘，意味着 i-1 根收盘发出信号
            for d in self.state.decisions:
                if d["idx"] == i and d["action"] in ("buy", "sell") and "exec_price" in d:
                    pos = 1 if d["action"] == "buy" else 0
                    sig.iloc[i - 1] = pos
            sig.iloc[i] = pos
        # 日终空仓由引擎强制
        return sig

    def finish(self, ai_signals: pd.Series | None = None, rule_strategies: list | None = None) -> dict:
        if self.state.finished and self.state.score:
            return self.state.score
        rep = ExecutableReplay(self.cost, self.state.capital_usd)

        class _S:
            name = "交易员"
            params = {}

            def compute(self_inner, df):
                return self.trader_signals()
        trader = rep.run(self.bars, _S())
        hind = optimal_trades(self.bars, self.cost, self.state.capital_usd)
        comp = {"trader": {"net_return": trader.net_return, "n_trades": trader.n_trades, "cost_usd": trader.total_cost_usd,
                           "trades": trader.trades},
                "hindsight": {"net_return": hind.net_return, "n_trades": hind.n_trades}}
        if ai_signals is not None:
            class _AI:
                name = "AI"
                params = {}

                def compute(self_inner, df):
                    return ai_signals.reindex(df.index).fillna(0).astype(int)
            ai = rep.run(self.bars, _AI())
            comp["ai"] = {"net_return": ai.net_return, "n_trades": ai.n_trades}
        if rule_strategies:
            comp["rules"] = {s.name: rep.run(self.bars, s).net_return for s in rule_strategies}
        capture = trader.net_return / hind.net_return if hind.net_return > 0 else None
        grade = _grade(trader.net_return, hind.net_return, comp.get("ai", {}).get("net_return"))
        score = {"comparison": comp, "capture_ratio_vs_hindsight": capture, "grade": grade,
                 "n_decisions": len([d for d in self.state.decisions if d["action"] != "hold"]),
                 "finished_at": datetime.now(timezone.utc).isoformat(), "data_is_simulated": self.state.data_is_simulated}
        self.state.finished = True
        self.state.score = score
        return score

    def to_json(self) -> str:
        return json.dumps(asdict(self.state), ensure_ascii=False, default=str)


def _grade(trader: float, hind: float, ai: float | None) -> str:
    if trader > 0 and (ai is None or trader >= ai):
        return "A：正收益且不低于 AI"
    if trader > 0:
        return "B：正收益但低于 AI"
    if trader == 0:
        return "C：未交易（在无优势日可能是正确选择）"
    return "D：负收益（请对照理论最优与成本明细复盘）"
