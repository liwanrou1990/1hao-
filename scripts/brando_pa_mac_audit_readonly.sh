#!/usr/bin/env bash
# brando_pa Mac READ-ONLY audit helper (phase 14 audit, 2026-11-10). macOS bash 3.2 compatible.
# Prints: launchd state, service pid vs lock, heartbeat age, poll gaps > 15 min with timestamps, 72h coverage budget math,
# sleep/wake log around gaps, signal/missed/expired counts, ledger audit (--no-write). Writes NOTHING into the production dir.
# Usage: bash scripts/brando_pa_mac_audit_readonly.sh [LOCAL_START_UTC="2026-11-08 12:23:00"]
set -u
cd "$(dirname "${0}")/.."; LS="${1:-2026-11-08 12:23:00}"; D="reports_out/brando_pa/phase8_forward"; LABEL="com.brando-pa.forward"
PY="${BRANDO_PY:-${PWD}/.venv/bin/python}"; [ -x "${PY}" ] || PY=python3
echo "== host $(hostname) · $(date -u +%Y-%m-%dT%H:%M:%SZ) · HEAD $(git rev-parse --short HEAD 2>/dev/null) · branch $(git rev-parse --abbrev-ref HEAD 2>/dev/null) · READ-ONLY"
echo "== launchd"; launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | grep -E "state|pid|last exit|runs" | head -6 || echo "launchd job not loaded (or not macOS)"
echo "== process / lock / heartbeat"
pgrep -fl "brando_pa.cli forward-service" || echo "no forward-service process found"
[ -f "${D}/service.lock" ] && echo "lock file: $(cat "${D}/service.lock" 2>/dev/null) (content = pid written at lock time)"
"${PY}" - "${D}" "${LS}" <<'PYEOF'
import json, sys, pandas as pd
D, LS = sys.argv[1], sys.argv[2]; now = pd.Timestamp.now(tz="UTC")
h = json.load(open(f"{D}/health.json")); hb = pd.Timestamp(h["heartbeat_utc"]); print(f"heartbeat {hb} age {(now-hb).total_seconds()/60:.1f} min · pid {h.get('pid')} · n_cycles(this process) {h.get('n_cycles')} · last_ok_cycle {h.get('last_ok_cycle_at')} · consecutive_failures {h.get('consecutive_failures')}")
re_ = pd.read_json(f"{D}/runner_events.jsonl", lines=True); c = re_[re_.kind == "data_check"].copy(); c["t"] = pd.to_datetime(c["now"], utc=True); c = c.sort_values("t")
ls = pd.Timestamp(LS, tz="UTC"); loc = c[c.t >= ls]
print(f"== polls since local start {ls}: {len(loc)} data_check cycles · first {loc.t.min() if len(loc) else None} · last {loc.t.max() if len(loc) else None}")
src = loc.get("source", pd.Series(dtype=object)); print("data sources:", src.value_counts(dropna=False).to_dict() if len(src) else "n/a", "· ok share:", round(float(loc["ok"].astype(bool).mean()), 4) if len(loc) else None)
gaps = loc.t.diff().dt.total_seconds() / 60; g = pd.DataFrame({"end": loc.t, "gap_min": gaps.round(1)}); g["start"] = g["end"] - pd.to_timedelta(g["gap_min"], unit="m")
big = g[g.gap_min > 15]; print(f"== gaps > 15 min (count {len(big)}, total lost ≈ {((big.gap_min - 10) / 10).sum():.1f} cycles):"); print(big[["start", "end", "gap_min"]].to_string(index=False) if len(big) else "  none")
E = (now - ls).total_seconds() / 3600; exp = max(int(E * 6), 1); obs = len(loc); lost = exp - obs; tail = (now - loc.t.max()).total_seconds() / 3600 if len(loc) else E
print(f"== coverage now: {obs}/{exp} = {obs/exp:.1%} · elapsed {E:.2f} h · longest gap {max(gaps.max()/60 if len(gaps.dropna()) else 0, tail):.2f} h (incl. tail {tail:.2f} h)")
print(f"== 72h budget: lost {lost} cycles; max final coverage if zero downtime from now = {(432 - max(lost,0))/432:.2%} → {'can still reach 95%' if (432 - max(lost,0))/432 >= 0.95 else 'CANNOT reach 95% under the original criterion'}")
for a in ("A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"):
    ev = pd.read_json(f"{D}/{a}/events.jsonl", lines=True); k = ev["kind"].value_counts().to_dict(); st = json.load(open(f"{D}/{a}/state.json"))
    print(f"== {a}: events {len(ev)} · signal {k.get('signal',0)} missed {k.get('missed_signal',0)} order {k.get('order',0)} fill {k.get('fill',0)} expired {k.get('order_expired',0)} cancelled {k.get('order_cancelled',0)} · pending {len(st.get('pending',[]))} · last_daily {st.get('last_daily_ts')} · cash(cost) {st['ledgers']['cost']['cash']:.2f}")
PYEOF
echo "== sleep / wake log (last 3 days; shows whether gaps coincide with Mac sleep)"; (pmset -g log 2>/dev/null | grep -E "Sleep|Wake|DarkWake" | tail -40) || echo "pmset not available (not macOS)"
echo "== ledger audit (read-only, --no-write)"; PYTHONPATH=src "${PY}" -m brando_pa.cli forward-ledger-audit --log-dir "${D}" --local-start "${LS}" --no-write 2>/dev/null | tail -40
echo "== done (nothing written)"
