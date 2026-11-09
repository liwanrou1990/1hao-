#!/usr/bin/env bash
# brando_pa Mac gated upgrade: pre-check -> dry-run -> HARD GATE on the upgrader's JSON report -> safe upgrade -> post-verify.
# Stops immediately (exit 1, service untouched) if any gate fails. Never resets git, never touches accounts, never enables live trading.
# macOS bash 3.2 compatible (always ${VAR}, ASCII output). Usage:
#   bash scripts/brando_pa_mac_upgrade_verified.sh [branch]
#   env: BRANDO_REPO=/path/to/1hao-   BRANDO_PY=python   BRANDO_LOCAL_START="2026-11-08 12:23:00"   BRANDO_SKIP_POSTCHECK=1 (tests only)
set -uo pipefail
BRANCH="${1:-claude/brando-pa-phase9-deploy}"
_is_repo() { [ -n "${1:-}" ] && git -C "${1}" rev-parse --show-toplevel >/dev/null 2>&1; }
REPO=""
if [ -n "${BRANDO_REPO:-}" ]; then
  CAND="${BRANDO_REPO}"; CAND="$(printf '%s' "${CAND}" | sed -e 's/^[\"'"'"'“”‘’]*//' -e 's/[\"'"'"'“”‘’]*$//')"
  case "${CAND}" in "~"|"~/"*) CAND="${HOME}${CAND#\~}" ;; esac
  _is_repo "${CAND}" && REPO="$(git -C "${CAND}" rev-parse --show-toplevel)" || { echo "BRANDO_REPO=[${BRANDO_REPO}] is not a git repository"; exit 1; }
else
  SD="$(cd "$(dirname "${0}")" && pwd)"
  if _is_repo "${SD}/.." && [ -f "$(git -C "${SD}/.." rev-parse --show-toplevel)/config/brando_pa.yaml" ]; then REPO="$(git -C "${SD}/.." rev-parse --show-toplevel)"
  elif _is_repo "${PWD}" && [ -f "$(git -C "${PWD}" rev-parse --show-toplevel)/config/brando_pa.yaml" ]; then REPO="$(git -C "${PWD}" rev-parse --show-toplevel)"
  elif _is_repo "${HOME}/1hao-"; then REPO="$(git -C "${HOME}/1hao-" rev-parse --show-toplevel)"; fi
fi
[ -n "${REPO}" ] || { echo "repository not found: set BRANDO_REPO=/path/to/1hao-"; exit 1; }
cd "${REPO}" || exit 1
PY="${BRANDO_PY:-${REPO}/.venv/bin/python}"; [ -x "${PY}" ] || PY="$(command -v python3)"
D="${REPO}/reports_out/brando_pa/phase8_forward"; OUT="${REPO}/reports_out/brando_pa/mac_check"; mkdir -p "${OUT}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"; LOG="${OUT}/upgrade_verified_${STAMP}.log"
exec > >(tee -a "${LOG}") 2>&1
LABEL="com.brando-pa.forward"; IS_MAC=0; [ "$(uname -s)" = "Darwin" ] && IS_MAC=1
LS="${BRANDO_LOCAL_START:-}"
stop_here() { echo; echo "[gate] STOP: ${1}"; echo "[gate] service untouched by this script; nothing upgraded. log=${LOG}"; exit 1; }
latest_report() { ls -t "${D}"/upgrade_report_*.json 2>/dev/null | head -1; }

echo "===== 1. PRE-CHECK  (utc $(date -u +%Y-%m-%dT%H:%M:%SZ), host $(hostname), is_mac=${IS_MAC})"
git fetch origin "${BRANCH}" || stop_here "git fetch failed (network?)"
HEAD0="$(git rev-parse HEAD)"; REMOTE="$(git rev-parse "origin/${BRANCH}")"
BEHIND="$(git rev-list --count "HEAD..origin/${BRANCH}")"; AHEAD="$(git rev-list --count "origin/${BRANCH}..HEAD")"
echo "repo=${REPO} HEAD=${HEAD0} origin/${BRANCH}=${REMOTE} behind=${BEHIND} ahead=${AHEAD}"
[ "${AHEAD}" -eq 0 ] || stop_here "local branch has ${AHEAD} unpushed commit(s): not a fast-forward; refusing (no reset/force)"
if [ "${IS_MAC}" -eq 1 ]; then launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | grep -E "state|pid" | head -3 || echo "[pre] launchd job not loaded"; fi
echo "[pre] commits to be applied:"; git log --oneline "HEAD..origin/${BRANCH}" | sed 's/^/    /'
echo "[pre] protected files touched by pending commits (must be empty):"
PROT="$(git diff --name-only "HEAD..origin/${BRANCH}" | grep -E '^config/brando_pa\.yaml$|src/brando_pa/forward/accounts\.py|src/brando_pa/spot/ledger\.py|src/brando_pa/trendsys/(trend_layer|risk_layer)\.py|src/brando_pa/usdt/(allocator|engine)\.py|reports_out/brando_pa/phase8_forward/' || true)"
if [ -n "${PROT}" ]; then echo "${PROT}" | sed 's/^/    /'; stop_here "pending commits touch strategy/config/ledger/account files; manual review required"; else echo "    (none)"; fi
SNAP_PRE="${OUT}/accounts_pre_${STAMP}.json"
"${PY}" - "${D}" "${SNAP_PRE}" <<'PYS'
import json, sys, pathlib
d, out = pathlib.Path(sys.argv[1]), sys.argv[2]; snap = {}
for a in sorted(p for p in d.iterdir() if p.is_dir() and (p / "state.json").exists()):
    st = json.loads((a / "state.json").read_text())
    snap[a.name] = {"initial_usdt": st.get("initial_usdt"), "forward_start_utc": st.get("forward_start_utc"), "last_daily_ts": st.get("last_daily_ts"), "pending": len(st.get("pending", [])), "processed": len(st.get("processed_orders", [])),
                    "ledgers": {k: {"cash": round(v["cash"], 8), "qty": {s: round(sum(l["qty"] for l in ls), 10) for s, ls in v.get("lots", {}).items()}, "fees_paid": round(v.get("fees_paid", 0.0), 8), "realized": round(v.get("realized", 0.0), 8)} for k, v in st["ledgers"].items()},
                    "events_lines": sum(1 for _ in open(a / "events.jsonl")) if (a / "events.jsonl").exists() else 0}
json.dump(snap, open(out, "w"), indent=1, ensure_ascii=False); print(json.dumps({k: {"cost_cash": v["ledgers"]["cost"]["cash"], "cost_qty": v["ledgers"]["cost"]["qty"], "events": v["events_lines"]} for k, v in snap.items()}, ensure_ascii=False))
PYS
[ "$?" -eq 0 ] || stop_here "could not snapshot accounts"
if [ "${BEHIND}" -eq 0 ]; then echo "[pre] already at origin/${BRANCH}; no upgrade needed"; UPGRADED=0; else

echo; echo "===== 2. DRY-RUN (non-intrusive: backup+verify, no service stop, no code change)"
bash scripts/brando_pa_safe_upgrade.sh "${BRANCH}" --dry-run; DRC=$?
REP="$(latest_report)"; [ -n "${REP}" ] || stop_here "no upgrade report written by dry-run"
echo "[gate] dry-run exit=${DRC} report=${REP}"
"${PY}" - "${REP}" <<'PYG'
import json, sys
r = json.load(open(sys.argv[1])); s = r.get("steps", {}); bad = []
if not r.get("ok"): bad.append("ok=false: " + str(r.get("reason")))
b = s.get("backup", {}); b.get("verified") is True and b.get("archive") or bad.append("backup not verified")
s.get("git_refs", {}).get("fast_forward_possible") is True or bad.append("fast-forward not possible")
s.get("local_modifications", {}).get("other") in ([], None) or bad.append("locally modified non-data files: %s" % s["local_modifications"]["other"][:5])
s.get("verify_unchanged", {}).get("diffs") == [] or bad.append("account snapshot changed during dry-run")
ta = s.get("timeline_audit_new_version"); (ta is None or ta.get("returncode") == 0) or bad.append("new-version timeline audit failed")
print(json.dumps({"backup": b.get("archive"), "n_files": b.get("n_files"), "verified": b.get("verified"), "old_head": s.get("git_refs", {}).get("old_head", "")[:7], "new_head": s.get("git_refs", {}).get("new_head", "")[:7], "data_files_to_restore": s.get("local_modifications", {}).get("data"), "gate_problems": bad}, ensure_ascii=False))
raise SystemExit(1 if bad else 0)
PYG
[ "$?" -eq 0 ] && [ "${DRC}" -eq 0 ] || stop_here "dry-run gate failed (see gate_problems above)"
echo "[gate] dry-run PASSED: verified backup exists (rollback = extract that .tgz), ff possible, accounts unchanged, new-version audit ok"

echo; echo "===== 3. SAFE UPGRADE"
bash scripts/brando_pa_safe_upgrade.sh "${BRANCH}"; URC=$?
REP2="$(latest_report)"; echo "[gate] upgrade exit=${URC} report=${REP2}"
"${PY}" - "${REP2}" <<'PYU'
import json, sys
r = json.load(open(sys.argv[1])); s = r.get("steps", {}); bad = []
r.get("ok") or bad.append("ok=false: " + str(r.get("reason")))
ff = s.get("fast_forward", {}); (ff.get("merged") is True) or bad.append("fast-forward not merged")
("restored_ok" not in ff or ff.get("restored_ok") is True) or bad.append("data files restore hash mismatch")
s.get("verify_after", {}).get("diffs") == [] or bad.append("post-upgrade account snapshot differs: %s" % s.get("verify_after", {}).get("diffs"))
st = s.get("start_service", {}); (st.get("started") is True or st.get("note")) or bad.append("service not started: %s" % st)
print(json.dumps({"reason": r.get("reason"), "fast_forward": {k: ff.get(k) for k in ("merged", "restored_ok", "new_head")}, "verify_after": s.get("verify_after"), "start_service": st, "health_after": s.get("health", {}).get("returncode"), "gate_problems": bad}, ensure_ascii=False))
raise SystemExit(1 if bad else 0)
PYU
[ "$?" -eq 0 ] && [ "${URC}" -eq 0 ] || { echo "[gate] UPGRADE REPORTED PROBLEMS - read ${REP2}; backups in ${REPO}/backups; the upgrader restores the old version itself on failure"; exit 1; }
UPGRADED=1; fi

echo; echo "===== 4. POST-VERIFY  (HEAD now $(git rev-parse --short HEAD))"
SNAP_POST="${OUT}/accounts_post_${STAMP}.json"
"${PY}" - "${D}" "${SNAP_POST}" "${SNAP_PRE}" <<'PYC'
import json, sys, pathlib
d, out, pre_p = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]; snap = {}
for a in sorted(p for p in d.iterdir() if p.is_dir() and (p / "state.json").exists()):
    st = json.loads((a / "state.json").read_text())
    snap[a.name] = {"initial_usdt": st.get("initial_usdt"), "forward_start_utc": st.get("forward_start_utc"), "last_daily_ts": st.get("last_daily_ts"), "pending": len(st.get("pending", [])), "processed": len(st.get("processed_orders", [])),
                    "ledgers": {k: {"cash": round(v["cash"], 8), "qty": {s: round(sum(l["qty"] for l in ls), 10) for s, ls in v.get("lots", {}).items()}, "fees_paid": round(v.get("fees_paid", 0.0), 8), "realized": round(v.get("realized", 0.0), 8)} for k, v in st["ledgers"].items()},
                    "events_lines": sum(1 for _ in open(a / "events.jsonl")) if (a / "events.jsonl").exists() else 0}
json.dump(snap, open(out, "w"), indent=1, ensure_ascii=False); pre = json.load(open(pre_p)); diffs = []
for n in set(pre) | set(snap):
    a, b = pre.get(n), snap.get(n)
    if not a or not b: diffs.append(f"{n}: missing"); continue
    for k in ("initial_usdt", "forward_start_utc", "last_daily_ts", "pending", "processed", "ledgers"):
        if a[k] != b[k] and not (k in ("last_daily_ts", "pending", "processed") and b["events_lines"] > a["events_lines"]):   # service may legitimately advance after restart (new events appended)
            diffs.append(f"{n}.{k}: {a[k]} -> {b[k]}")
    if b["events_lines"] < a["events_lines"]: diffs.append(f"{n}: events.jsonl shrank {a['events_lines']} -> {b['events_lines']}")
print("[post] capital/holdings/fees/realized/initial/forward_start identical:", "YES" if not diffs else "NO " + "; ".join(diffs))
raise SystemExit(1 if diffs else 0)
PYC
POST_ACC=$?
if [ "${BRANDO_SKIP_POSTCHECK:-}" = "1" ]; then echo "[post] BRANDO_SKIP_POSTCHECK=1 -> service/report checks skipped (tests)"; HRC=0; else
  if [ "${IS_MAC}" -eq 1 ]; then launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | grep -E "state|pid" | head -3 || echo "[post] launchd job not loaded"; fi
  HRC=1; WAIT_S="${BRANDO_HEALTH_WAIT_S:-90}"; i=0
  while :; do if bash scripts/brando_pa_forward_ctl.sh health >/dev/null 2>&1; then HRC=0; break; fi; [ "${i}" -ge "${WAIT_S}" ] && break; echo "[post] waiting for healthy heartbeat (${i}/${WAIT_S}s)..."; sleep 10; i=$((i + 10)); done
  echo "[post] health exit=${HRC}"; bash scripts/brando_pa_forward_ctl.sh health | head -40
  bash scripts/brando_pa_forward_ctl.sh status | head -20
  bash scripts/brando_pa_forward_ctl.sh report
  if [ -n "${LS}" ]; then bash scripts/brando_pa_forward_ctl.sh acceptance "${LS}"; bash scripts/brando_pa_forward_ctl.sh audit "${LS}" | tail -25
  else bash scripts/brando_pa_forward_ctl.sh acceptance; bash scripts/brando_pa_forward_ctl.sh audit | tail -25; fi
  # NOTE: never build an env assignment from a parameter expansion (${X:+VAR="${X}"}): the shell does not treat an expanded word as an
  # assignment, so with a value containing a space it became the command "BRANDO_LOCAL_START=2026-11-08 12:23:00" -> command not found.
  # Pass the variable unconditionally (empty = unset for the callee).
  if [ -f scripts/brando_pa_mac_check.sh ]; then BRANDO_REPO="${REPO}" BRANDO_PY="${PY}" BRANDO_LOCAL_START="${LS}" bash scripts/brando_pa_mac_check.sh "${BRANCH}" | tail -3; fi
fi
echo; echo "===== SUMMARY"
echo "upgraded=${UPGRADED} HEAD ${HEAD0:0:7} -> $(git rev-parse --short HEAD) (origin ${REMOTE:0:7})  accounts_identical=$([ "${POST_ACC}" -eq 0 ] && echo YES || echo NO)  health=$([ "${HRC}" -eq 0 ] && echo ok || echo NOT_OK)  log=${LOG}"
echo "real trading: disabled (config execution.live_trading must be false; upgrader refuses otherwise)"
[ "${POST_ACC}" -eq 0 ] && [ "${HRC}" -eq 0 ]
