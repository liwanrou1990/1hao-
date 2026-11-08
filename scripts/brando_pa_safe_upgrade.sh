#!/usr/bin/env bash
# brando_pa forward service - safe upgrade bootstrap (macOS bash 3.2 compatible; may run from /tmp; paths may contain spaces).
#   resolve repo -> git fetch -> checkout origin/<branch> into a temp worktree inside the repo -> run the NEW version's upgrader:
#   snapshot -> full backup + SHA256 verify -> stop service safely -> preflight / deps / new-version audit -> ff merge (data files saved & restored)
#   -> post-upgrade verification -> start -> health check.  Any failure: code untouched or old service restored, reason printed.
#   Never uses git reset / clean / force. Real trading stays disabled.
# Usage: bash brando_pa_safe_upgrade.sh [branch] [--dry-run]     Env: BRANDO_REPO=/path/to/1hao- (optional)
# NOTE: variables are always written as ${VAR} and never directly followed by non-ASCII text (bash 3.2 would merge a following multibyte char into the variable name -> unbound variable).
set -uo pipefail
BRANCH="${1:-claude/brando-pa-phase9-deploy}"; DRY_ARG="${2:-}"; DRY=""
case "${DRY_ARG}" in *dry*) DRY="--dry-run" ;; "") ;; *) echo "unknown argument: [${DRY_ARG}] (only --dry-run is accepted)"; exit 2 ;; esac
_is_repo() { [ -n "${1:-}" ] && git -C "${1}" rev-parse --show-toplevel >/dev/null 2>&1; }
REPO=""
if [ -n "${BRANDO_REPO:-}" ]; then
  # tolerate smart quotes / straight quotes pasted around the path, and a leading ~
  CAND="${BRANDO_REPO}"; CAND="$(printf '%s' "${CAND}" | sed -e 's/^[\"'"'"'“”‘’]*//' -e 's/[\"'"'"'“”‘’]*$//')"
  case "${CAND}" in "~"|"~/"*) CAND="${HOME}${CAND#\~}" ;; esac
  if _is_repo "${CAND}"; then REPO="$(git -C "${CAND}" rev-parse --show-toplevel)"; else echo "BRANDO_REPO=[${BRANDO_REPO}] is not a git repository"; exit 1; fi
else
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  if _is_repo "${SCRIPT_DIR}/.." && [ -f "$(git -C "${SCRIPT_DIR}/.." rev-parse --show-toplevel)/config/brando_pa.yaml" ]; then REPO="$(git -C "${SCRIPT_DIR}/.." rev-parse --show-toplevel)"
  elif _is_repo "${PWD}" && [ -f "$(git -C "${PWD}" rev-parse --show-toplevel)/config/brando_pa.yaml" ]; then REPO="$(git -C "${PWD}" rev-parse --show-toplevel)"
  elif _is_repo "${HOME}/1hao-"; then REPO="$(git -C "${HOME}/1hao-" rev-parse --show-toplevel)"
  fi
fi
[ -n "${REPO}" ] || { echo "brando_pa repository not found: cd into the project (e.g. ~/1hao-) or set BRANDO_REPO=/path/to/1hao-"; exit 1; }
cd "${REPO}" || exit 1
echo "repo: ${REPO} (HEAD $(git rev-parse --short HEAD)) branch: ${BRANCH} mode: ${DRY:-upgrade}"
PY="${BRANDO_PY:-${REPO}/.venv/bin/python}"; [ -x "${PY}" ] || PY=python3
git fetch origin "${BRANCH}" || { echo "git fetch failed - upgrade aborted (service untouched)"; exit 1; }
WT="${REPO}/.upgrade_worktree"; git worktree remove --force "${WT}" >/dev/null 2>&1 || true; rm -rf "${WT}"; git worktree prune >/dev/null 2>&1 || true
git worktree add --detach "${WT}" "origin/${BRANCH}" >/dev/null 2>&1 || { echo "cannot create temporary worktree - upgrade aborted (service untouched)"; exit 1; }
trap 'git -C "${REPO}" worktree remove --force "${WT}" >/dev/null 2>&1 || true' EXIT
PYTHONPATH="${WT}/src" "${PY}" -m brando_pa.forward.upgrade --repo "${REPO}" --branch "${BRANCH}" --worktree-src "${WT}/src" ${DRY:+${DRY}} ${BRANDO_NO_LAUNCHD:+--no-launchd} ${BRANDO_SKIP_PIP:+--skip-pip}
RC=$?
LAST="$(ls -t "${REPO}"/reports_out/brando_pa/phase8_forward/upgrade_report_*.json 2>/dev/null | head -1)"; [ -n "${LAST}" ] && echo "upgrade report: ${LAST}"
exit ${RC}
