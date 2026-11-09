#!/usr/bin/env bash
# brando_pa Mac one-click READ-ONLY check (phase 9): environment, git version, launchd service, health, status, report,
# 72h acceptance, fee/NAV ledger audit. Writes a bundle to reports_out/brando_pa/mac_check/mac_check_<UTC>.{md,log}.
# Never stops/starts the service, never changes HEAD or account files. Compatible with macOS bash 3.2 (always ${VAR}, ASCII output).
# Usage: bash scripts/brando_pa_mac_check.sh [branch]      (env: BRANDO_REPO=/path/to/repo  BRANDO_PY=/path/to/python  BRANDO_LOCAL_START="YYYY-MM-DD HH:MM:SS")
set -euo pipefail
BRANCH="${1:-claude/brando-pa-phase9-deploy}"
LABEL="com.brando-pa.forward"

strip_quotes() { local v="${1}"; v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"; v="${v#$(printf '\xe2\x80\x9c')}"; v="${v%$(printf '\xe2\x80\x9d')}"; printf '%s' "${v}"; }
REPO="${BRANDO_REPO:-}"
if [ -n "${REPO}" ]; then REPO="$(strip_quotes "${REPO}")"; case "${REPO}" in "~"*) REPO="${HOME}${REPO#\~}";; esac; fi
if [ -z "${REPO}" ]; then
  SD="$(cd "$(dirname "${0}")" && pwd)"
  if [ -f "${SD}/../config/brando_pa.yaml" ]; then REPO="$(cd "${SD}/.." && pwd)"
  elif [ -f "${PWD}/config/brando_pa.yaml" ]; then REPO="${PWD}"
  elif [ -f "${HOME}/1hao-/config/brando_pa.yaml" ]; then REPO="${HOME}/1hao-"
  else echo "[check] ERROR: cannot find repo (set BRANDO_REPO=/path/to/1hao-)"; exit 2; fi
fi
cd "${REPO}"
PY="${BRANDO_PY:-${REPO}/.venv/bin/python}"; [ -x "${PY}" ] || PY="$(command -v python3)"
D="${REPO}/reports_out/brando_pa/phase8_forward"
OUT="${REPO}/reports_out/brando_pa/mac_check"; mkdir -p "${OUT}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"; MD="${OUT}/mac_check_${STAMP}.md"; LOG="${OUT}/mac_check_${STAMP}.log"
exec > >(tee -a "${LOG}") 2>&1
FAIL=0
sec() { echo; echo "===== ${1}"; }
run() { # run <title> <cmd...>  : never aborts the check; records exit code
  local t="${1}"; shift; sec "${t}"; set +e; "$@"; local rc=$?; set -e; echo "[check] exit=${rc} (${t})"; [ "${rc}" -eq 0 ] || FAIL=1; return 0; }

sec "ENVIRONMENT"
HOST="$(hostname)"; OS="$(uname -s)"
echo "host=${HOST} os=${OS} user=$(id -un) repo=${REPO} python=${PY} ($("${PY}" --version 2>&1)) utc_now=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
IS_MAC=0; if [ "${OS}" = "Darwin" ]; then IS_MAC=1; sw_vers 2>/dev/null || true; else echo "[check] NOTE: not macOS -> launchd checks skipped; this is NOT a Mac result"; fi

sec "GIT VERSION"
HEAD_SHA="$(git rev-parse HEAD)"; echo "branch=$(git rev-parse --abbrev-ref HEAD) head=${HEAD_SHA}"; git log --oneline -1
echo "modified tracked files: $(git status --porcelain | grep -c '^ M' || true)"
set +e; git fetch -q origin "${BRANCH}" 2>&1; FRC=$?; set -e
if [ "${FRC}" -eq 0 ]; then
  REMOTE_SHA="$(git rev-parse "origin/${BRANCH}")"; echo "origin/${BRANCH}=${REMOTE_SHA}"
  BEHIND="$(git rev-list --count "HEAD..origin/${BRANCH}")"; AHEAD="$(git rev-list --count "origin/${BRANCH}..HEAD")"; echo "behind=${BEHIND} ahead=${AHEAD}"
  [ "${BEHIND}" -gt 0 ] && { echo "[check] pending commits on origin (not yet applied here):"; git log --oneline "HEAD..origin/${BRANCH}" | sed 's/^/    /'; }
else REMOTE_SHA=""; BEHIND="?"; echo "[check] WARN: git fetch failed (offline?) -> using local code only"; fi
has() { git merge-base --is-ancestor "${1}" HEAD 2>/dev/null && echo "yes" || echo "no"; }
echo "applied: ledger_audit(22d58c3)=$(has 22d58c3)  F1/F3_reports(f2141f8)=$(has f2141f8)  live_guard_fix(6d72d49)=$(has 6d72d49)"

sec "LIVE TRADING GUARD"
"${PY}" - <<'PYG'
import re, yaml, os
c = yaml.safe_load(open("config/brando_pa.yaml", encoding="utf-8"))
lt = c.get("execution", {}).get("live_trading", None)
print("execution.live_trading =", repr(lt), "(type", type(lt).__name__ + ")", "| BRANDO_LIVE_CONFIRM set:", "BRANDO_LIVE_CONFIRM" in os.environ)
raise SystemExit(0 if lt is False and "BRANDO_LIVE_CONFIRM" not in os.environ else 1)
PYG
echo "[check] exit=$? (live guard; 0 = disabled as required)"

if [ "${IS_MAC}" -eq 1 ]; then
  run "LAUNCHD ${LABEL}" bash -c "launchctl print gui/\$(id -u)/${LABEL} 2>/dev/null | grep -E 'state|pid|last exit|runs' | head -6 || { echo 'launchd job NOT loaded'; exit 1; }"
  run "PROCESS" bash -c "pgrep -fl 'brando_pa.cli forward-service' || { echo 'no forward-service process'; exit 1; }"
  run "SLEEP SETTINGS" bash -c "pmset -g 2>/dev/null | grep -E '^ *(sleep|disksleep|displaysleep|hibernatemode)' || true"
fi
run "HEALTH"     bash scripts/brando_pa_forward_ctl.sh health
run "STATUS"     bash scripts/brando_pa_forward_ctl.sh status
run "LOGS (tail 30 + alerts)" bash scripts/brando_pa_forward_ctl.sh logs 30
run "REPORT (leaderboard + daily)" bash scripts/brando_pa_forward_ctl.sh report
LS_ARG=""; [ -n "${BRANDO_LOCAL_START:-}" ] && LS_ARG="${BRANDO_LOCAL_START}"
if [ -n "${LS_ARG}" ]; then run "ACCEPTANCE (local_start=${LS_ARG})" bash scripts/brando_pa_forward_ctl.sh acceptance "${LS_ARG}"; else run "ACCEPTANCE (local segment starts at first non-cloud run)" bash scripts/brando_pa_forward_ctl.sh acceptance; fi

# Fee / NAV ledger audit: use the newest code from origin in a detached worktree when local code is older (read-only; never switches HEAD)
sec "LEDGER AUDIT (read-only)"
AUDIT_SRC="${REPO}/src"
if [ -n "${REMOTE_SHA}" ] && [ "$(has f2141f8)" = "no" ]; then
  WT="${REPO}/.mac_check_worktree"; git worktree remove --force "${WT}" 2>/dev/null || true; rm -rf "${WT}"
  if git worktree add --detach -q "${WT}" "origin/${BRANCH}" 2>/dev/null; then AUDIT_SRC="${WT}/src"; echo "[check] local code lacks the audit tool -> using origin/${BRANCH} code from temporary worktree (read-only)"; fi
fi
set +e
if [ -n "${LS_ARG}" ]; then PYTHONPATH="${AUDIT_SRC}" "${PY}" -m brando_pa.cli forward-ledger-audit --log-dir "${D}" --out "${OUT}" --local-start "${LS_ARG}"
else PYTHONPATH="${AUDIT_SRC}" "${PY}" -m brando_pa.cli forward-ledger-audit --log-dir "${D}" --out "${OUT}"; fi
ARC=$?; set -e
echo "[check] exit=${ARC} (ledger audit; 0 = no issues)"; [ "${ARC}" -eq 0 ] || FAIL=1
[ -d "${REPO}/.mac_check_worktree" ] && { git worktree remove --force "${REPO}/.mac_check_worktree" 2>/dev/null || rm -rf "${REPO}/.mac_check_worktree"; }

sec "SUMMARY"
{
  echo "# brando_pa Mac check ${STAMP}"
  echo
  echo "- host: ${HOST} (${OS}; is_mac=${IS_MAC})"
  echo "- repo HEAD: ${HEAD_SHA}  origin/${BRANCH}: ${REMOTE_SHA:-fetch failed}  behind: ${BEHIND}"
  echo "- applied: ledger_audit=$(has 22d58c3) F1/F3_reports=$(has f2141f8) live_guard_fix=$(has 6d72d49)"
  echo "- overall: $([ "${FAIL}" -eq 0 ] && echo OK || echo "ATTENTION (see log)")"
  echo "- full log: ${LOG}"
  echo "- files: ${D}/health.json, ${D}/status.json, ${D}/leaderboard.md, ${D}/daily/, ${D}/acceptance_status.md, ${OUT}/ledger_audit_*.md"
  echo
  echo "## leaderboard"; cat "${D}/leaderboard.md" 2>/dev/null || echo "(missing)"
  echo; echo "## acceptance"; cat "${D}/acceptance_status.md" 2>/dev/null || echo "(missing)"
  echo; echo "## health.json"; echo '```'; cat "${D}/health.json" 2>/dev/null || echo "(missing)"; echo '```'
  echo; echo "## ledger audit"; LA="$(ls -t "${OUT}"/ledger_audit_*.md 2>/dev/null | head -1)"; [ -n "${LA}" ] && cat "${LA}" || echo "(missing)"
  echo; echo "Read-only check: no service stop/start, no HEAD change, no account file modified. Real trading disabled."
} > "${MD}"
echo "overall=$([ "${FAIL}" -eq 0 ] && echo OK || echo ATTENTION)  report=${MD}  log=${LOG}"
echo "[check] To apply pending code safely: bash scripts/brando_pa_forward_ctl.sh upgrade-dry-run && bash scripts/brando_pa_forward_ctl.sh upgrade"
exit "${FAIL}"
