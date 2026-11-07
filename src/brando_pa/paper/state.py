"""模拟盘持久状态（JSON，原子写入）：账户、持仓、待执行、已处理 K 线、订单号集合（去重）、风控状态、交易记录文件。重启后从此恢复。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from spot90.atomic import atomic_write_json

from ..backtest.execution import Position


class PaperState:
    def __init__(self, path: Path, initial_equity: float):
        self.path = Path(path)
        self.data = {"created_at": datetime.now(timezone.utc).isoformat(), "initial_equity": initial_equity, "cash": initial_equity, "positions": [], "pending": {},
                     "last_bar_ts": {}, "order_ids": [], "seen_setups": [], "risk": {"day": None, "day_start_equity": initial_equity, "day_realized_pnl": 0.0, "halted_until_day": None, "halt_reasons": []},
                     "n_trades": 0, "realized_pnl": 0.0, "last_run": None, "errors": [], "mode": "paper"}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except json.JSONDecodeError:
                self.data["errors"].append({"ts": datetime.now(timezone.utc).isoformat(), "error": "状态文件损坏，已重新初始化"})

    @property
    def positions(self) -> list[Position]:
        return [Position.from_dict(d) for d in self.data["positions"]]

    @positions.setter
    def positions(self, ps: list[Position]) -> None:
        self.data["positions"] = [p.to_dict() for p in ps]

    def save(self) -> None:
        self.data["last_run"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.path, self.data, indent=2, default=str)

    def append_trade(self, log_file: Path, trade: dict) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(trade, ensure_ascii=False, default=str) + "\n")
        self.data["n_trades"] += 1
        self.data["realized_pnl"] += float(trade["net_pnl"])

    def append_event(self, log_file: Path, event: dict) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
