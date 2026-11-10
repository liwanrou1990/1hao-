"""冻结清单：固定观察基准 FIX25_Y 与三个对照的规则文本、参数、相关源文件 SHA-256。
每次前瞻运行前校验；不一致 → 拒绝运行（frozen_rule_violation）。清单只在本阶段建立时写一次（frozen_manifest.json，已提交）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import PROJECT_ROOT, cfg
from . import ACCOUNTS, BUDGET, FORWARD_START, RESEARCH_INITIAL_USDT, WARN

MANIFEST = Path(__file__).with_name("frozen_manifest.json")
FILES = ["src/brando_pa/pa20/alloc20.py", "src/brando_pa/pa20/execution20.py", "src/brando_pa/spot/ledger.py", "src/brando_pa/holding/engine.py",
         "src/brando_pa/pa18/variants18.py", "src/brando_pa/pa18/phase18.py", "src/brando_pa/trendsys/risk_layer.py", "src/brando_pa/trendsys/trend_layer.py",
         "src/brando_pa/usdt/engine.py", "src/brando_pa/pa21/forward21.py"]

RULES = {
    "FIX25_Y": {"text": "25% BTC + 75% USDT；起点收盘决策、次日开盘建仓；每年 1 月第一个交易日开盘再平衡回 25%；差额 < 5 USDT 不交易；不止损、不择时、回撤警报不交易", "weight": 0.25, "mode": "Y", "engine": "pa20.alloc20.AllocBacktester"},
    "B2_original": {"text": "BTC/ETH 各自收盘 > SMA200 持有、等权；原风险状态机（config trendsys.risk.state_machine）", "engine": "pa18.phase18.run_variant('V0_original')", "note": "日线研究引擎，次日开盘成交；与 Mac B 账户（实时报价成交）执行方式不同"},
    "SMA200": {"text": "BTC 收盘 > SMA200 持有，否则 USDT", "engine": "holding.engine.HoldingBacktester(mode=trend, sma=200)"},
    "BH_BTC": {"text": "起点买入 BTC 后持有", "engine": "holding.engine.HoldingBacktester(mode=buyhold)"},
}


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def current(root: Path = PROJECT_ROOT, c: dict | None = None) -> dict:
    c = c or cfg(); risk = c.get("trendsys", {}).get("risk", {}).get("state_machine", {})
    params = {"forward_start": FORWARD_START, "initial_usdt": RESEARCH_INITIAL_USDT, "accounts": list(ACCOUNTS), "fee_bps": 10.0, "base_slippage_bps": 5.0,
              "impact": {"Y": 1.0, "cap_bps": 100.0, "sigma_days": 20, "fallback_bps": 25.0}, "btc_step": 1e-5, "min_notional_usdt": 5.0, "sma": 200,
              "b2_risk_state_machine": risk, "warn_thresholds": list(WARN), "drawdown_budget": BUDGET, "rules": RULES}
    return {"params": params, "params_sha256": hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "files": {f: _sha(root / f) for f in FILES}}


def write_manifest(git_commit: str | None = None, frozen_at: str | None = None) -> dict:
    m = current() | {"frozen_at_utc": frozen_at, "preregistration_commit": git_commit, "preregistration": "docs/brando_pa_validation/phase21_forward/preregistration.md"}
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8"); return m


def verify(root: Path = PROJECT_ROOT, manifest: Path = MANIFEST, c: dict | None = None) -> dict:
    if not manifest.exists():
        return {"ok": False, "reason": "冻结清单不存在"}
    m = json.loads(manifest.read_text(encoding="utf-8")); cur = current(root, c)
    bad = [f for f, h in m["files"].items() if cur["files"].get(f) != h]
    pbad = cur["params_sha256"] != m["params_sha256"]
    return {"ok": not bad and not pbad, "changed_files": bad, "params_changed": pbad, "frozen_at_utc": m.get("frozen_at_utc"), "params_sha256": m["params_sha256"]}
