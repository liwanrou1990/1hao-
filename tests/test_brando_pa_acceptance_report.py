"""第九阶段 Mac 验收优化：完整 JSON 可解析（日报/排行榜）、云端与本地验收记录分段、日线决策无重复无遗漏、账户资金一致、历史事件链不变、真实交易禁用。"""
import json
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from brando_pa.config import cfg, live_trading_allowed
from brando_pa.forward.acceptance import acceptance_status
from brando_pa.forward.accounts import ForwardRunner
from brando_pa.forward.audit import audit_timeline
from brando_pa.forward.report import daily_report, leaderboard
from brando_pa.trendsys.paper import _append, verify_chain
from tests.test_brando_pa_causality import _synth_oc, T
from tests.test_brando_pa_forward import _store, SYMS

REAL = Path("reports_out/brando_pa/phase8_forward")


def _drive(tmp_path, name="fw", days=("2024-08-02", "2024-08-03", "2024-08-04")):
    bars = _synth_oc(); store = _store(tmp_path / name, bars); r = ForwardRunner(cfg(), log_dir=tmp_path / name / "logs", store=store)
    r.run_once(now=T("2024-08-01 05:00"))
    for d in days:
        r.run_once(now=T(f"{d} 00:30")); r.run_once(now=T(f"{d} 01:10"))
    return r, store


def test_full_json_reports_parse_and_leaderboard_ranking(tmp_path):
    r, store = _drive(tmp_path); d = tmp_path / "fw" / "logs"
    rep = daily_report(d, "2024-08-03"); j = json.loads((d / "daily" / "2024-08-03.json").read_text())        # 完整 JSON 可解析
    assert set(j["accounts"]) == {"A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"} and j["category"].startswith("forward")
    lb = leaderboard(d); lj = json.loads((d / "leaderboard.json").read_text())
    cost = lj["cost"]; assert [x["rank"] for x in cost] == [1, 2, 3] and all(cost[i]["liquidation_net_profit_usdt"] >= cost[i + 1]["liquidation_net_profit_usdt"] for i in range(2))
    for x in cost + lj["ideal"]:
        w = x["weights"]; assert abs(w["BTC"] + w["ETH"] + w["USDT"] - 1) < 1e-6 and x["initial_usdt"] == 10000.0 and abs(x["liquidation_net_profit_usdt"] - (x["liquidation_usdt"] - 10000.0)) < 1e-9
        assert abs(x["equity_usdt"] - (x["cash_usdt"] + sum(x["holdings"][s] * x["prices"][s] for s in SYMS))) < 1e-6           # 资金一致：权益 = 现金 + 持仓 × 价
        gap = x["equity_usdt"] - (10000.0 + (x["realized_usdt"] or 0) + (x["unrealized_usdt"] or 0))
        assert (abs(gap) < 1e-6) if x["ledger"] == "ideal" else (-x["fees_paid_usdt"] - 1e-6 <= gap <= 1e-6)             # 零成本：已实现 + 未实现 = 净利润；含成本：差额只能是买入手续费
    md = (d / "leaderboard.md").read_text(); assert "名次" in md and "真实交易 API 禁用" in md


def test_acceptance_segments_cloud_vs_local(tmp_path):
    r, store = _drive(tmp_path); d = tmp_path / "fw" / "logs"
    # 把前 4 条 data_check 标成云容器主机、后面的标成 Mac 主机（事件链保持完整：重写为新链文件后校验）
    ev = [json.loads(l) for l in open(d / "runner_events.jsonl")]
    (d / "runner_events.jsonl").unlink()
    for i, e in enumerate(ev):
        e = {k: v for k, v in e.items() if k not in ("prev_hash", "hash", "recorded_at")}; e["host"] = "vm" if i < 3 else "macbook.local"; _append(d / "runner_events.jsonl", e)
    assert verify_chain(d / "runner_events.jsonl")["ok"]
    st = acceptance_status(d, 600, now=T("2024-08-04 03:00"))
    seg = st["segments"]; assert set(seg) == {"cloud_legacy", "local", "all"}
    assert seg["cloud_legacy"]["cycles_observed"] == 3 and seg["cloud_legacy"]["hosts"] == ["vm"] and seg["local"]["hosts"] == ["macbook.local"]
    assert seg["local"]["start_utc"].startswith("2024-08-03 00:30") and seg["local"]["status"] == "进行中" and seg["all"]["cycles_observed"] == len(ev)
    assert seg["local"]["coverage"] < 0.5 and seg["local"]["longest_gap_hours"] >= 1.0                                   # 真实缺口原样统计，不伪造周期
    st2 = acceptance_status(d, 600, now=T("2024-08-07 03:00"))                                                               # 本地段"过去"了 75 h 但只有 4 轮 → 未通过，不能靠起点掩盖
    assert st2["segments"]["local"]["status"] == "未通过"
    st3 = acceptance_status(d, 600, now=T("2024-08-04 03:00"), local_start="2024-08-02 00:30")                             # 显式 local_start 只能把云段提前截断，仍保留历史段
    assert st3["segments"]["local"]["start_utc"].startswith("2024-08-02 00:30") and "cloud_legacy" in st3["segments"]


def test_daily_decisions_no_duplicate_no_missing_and_chain_unchanged(tmp_path):
    r, store = _drive(tmp_path); d = tmp_path / "fw" / "logs"
    a = audit_timeline(d); assert a["ok"], a
    ev = [json.loads(l) for l in open(d / "A_BTC_SMA200" / "events.jsonl")]
    sig_ts = [e["signal_ts"] for e in ev if e["kind"] in ("signal", "missed_signal")]
    assert sig_ts == ["2024-08-02 00:00:00+00:00", "2024-08-03 00:00:00+00:00", "2024-08-04 00:00:00+00:00"]             # 每个日线收盘恰好一次决策
    # 同一时刻重复运行 / 重启不产生第二次决策
    before = hashlib.sha256((d / "A_BTC_SMA200" / "events.jsonl").read_bytes()).hexdigest(); n_before = sum(1 for _ in open(d / "A_BTC_SMA200" / "events.jsonl"))
    r.run_once(now=T("2024-08-04 01:20")); ForwardRunner(cfg(), log_dir=d, store=store).run_once(now=T("2024-08-04 01:30"))
    ev2 = [json.loads(l) for l in open(d / "A_BTC_SMA200" / "events.jsonl")]
    assert [e["signal_ts"] for e in ev2 if e["kind"] in ("signal", "missed_signal")] == sig_ts
    lines = (d / "A_BTC_SMA200" / "events.jsonl").read_text().splitlines()
    assert hashlib.sha256(("\n".join(lines[:n_before]) + "\n").encode()).hexdigest() == before                               # 历史事件一字未改，只追加
    assert verify_chain(d / "A_BTC_SMA200" / "events.jsonl")["ok"]
    # 遗漏检测：人为删除 state 的 last_daily_ts 以外的一天决策 → 审计报告 missing
    st = json.loads((d / "A_BTC_SMA200" / "state.json").read_text()); st["forward_start_utc"] = "2024-07-30 05:00:00+00:00"; (d / "A_BTC_SMA200" / "state.json").write_text(json.dumps(st))
    a2 = audit_timeline(d, write=False); assert any(v["rule"] == "T8-missing-daily-decision" for v in a2["accounts"]["A_BTC_SMA200"]["violations"])


@pytest.mark.skipif(not (REAL / "A_BTC_SMA200" / "events.jsonl").exists(), reason="无真实前瞻记录")
def test_real_forward_records_intact_and_audit_ok():
    assert all(verify_chain(REAL / n / "events.jsonl")["ok"] for n in ("A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"))
    a = audit_timeline(REAL, write=False); assert a["ok"], [v for x in a["accounts"].values() for v in x["open_violations"]]
    for n in ("A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"):
        st = json.loads((REAL / n / "state.json").read_text()); assert st["initial_usdt"] == 10000.0


def test_real_trading_remains_disabled():
    c = cfg(); assert c["execution"]["live_trading"] is False and not live_trading_allowed(c, cli_confirm=True)
    from brando_pa.paper.broker import LiveBroker
    with pytest.raises(Exception):
        LiveBroker(c).place_order({"symbol": "BTCUSDT", "side": "BUY", "qty": 0.001})
    assert c["usdt"]["initial_cash_usdt"] == 10000 and c["usdt"]["trend"]["sma"] == 200 and c["usdt"]["trend"]["confirm_days"] == 1
