#!/usr/bin/env bash
# brando_pa 前瞻服务 · Mac 安全升级引导（可从任意位置运行，例如 /tmp 下的副本；兼容带空格的路径）：
#   确定仓库根目录 → git fetch → 把 origin/<branch> 检出到仓库内临时 worktree → 用**新版本**升级器执行：
#   快照 → 完整备份+校验 → 安全停服务 → 预检/依赖/新版审计 → ff 合并（数据文件保存/放回）→ 升级后核对 → 启动 → 健康检查。
# 任何一步失败：不改代码或恢复旧版本服务，并打印原因。不使用 git reset / clean / 强制覆盖。真实交易保持禁用。
# 用法：bash brando_pa_safe_upgrade.sh [branch] [--dry-run]      环境变量 BRANDO_REPO 可显式指定仓库目录。
set -uo pipefail
BRANCH="${1:-claude/brando-pa-phase9-deploy}"; DRY_ARG="${2:-}"; DRY=""
case "$DRY_ARG" in *dry*) DRY="--dry-run" ;; "") ;; *) echo "未知参数：$DRY_ARG（只接受 --dry-run）"; exit 2 ;; esac
_is_repo() { [ -n "$1" ] && git -C "$1" rev-parse --show-toplevel >/dev/null 2>&1; }
REPO=""
if [ -n "${BRANDO_REPO:-}" ]; then _is_repo "$BRANDO_REPO" && REPO="$(git -C "$BRANDO_REPO" rev-parse --show-toplevel)" || { echo "BRANDO_REPO=$BRANDO_REPO 不是 Git 仓库"; exit 1; }
else
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  if _is_repo "$SCRIPT_DIR/.." && [ -f "$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel)/config/brando_pa.yaml" ]; then REPO="$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel)"
  elif _is_repo "$PWD" && [ -f "$(git -C "$PWD" rev-parse --show-toplevel)/config/brando_pa.yaml" ]; then REPO="$(git -C "$PWD" rev-parse --show-toplevel)"
  elif _is_repo "$HOME/1hao-"; then REPO="$(git -C "$HOME/1hao-" rev-parse --show-toplevel)"
  fi
fi
[ -n "$REPO" ] || { echo "找不到 brando_pa 仓库：请 cd 到项目目录（如 ~/1hao-）运行，或设置 BRANDO_REPO=/path/to/1hao-"; exit 1; }
cd "$REPO" || exit 1
echo "仓库目录：$REPO（HEAD $(git rev-parse --short HEAD)）；分支 $BRANCH ${DRY:+（演练）}"
PY="${BRANDO_PY:-$REPO/.venv/bin/python}"; [ -x "$PY" ] || PY=python3
git fetch origin "$BRANCH" || { echo "git fetch 失败，升级中止（服务未动）"; exit 1; }
WT="$REPO/.upgrade_worktree"; git worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$WT"; git worktree prune >/dev/null 2>&1 || true
git worktree add --detach "$WT" "origin/$BRANCH" >/dev/null 2>&1 || { echo "无法创建临时 worktree，升级中止（服务未动）"; exit 1; }
trap 'git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
PYTHONPATH="$WT/src" "$PY" -m brando_pa.forward.upgrade --repo "$REPO" --branch "$BRANCH" --worktree-src "$WT/src" ${DRY:+$DRY} ${BRANDO_NO_LAUNCHD:+--no-launchd} ${BRANDO_SKIP_PIP:+--skip-pip}
RC=$?
LAST="$(ls -t "$REPO"/reports_out/brando_pa/phase8_forward/upgrade_report_*.json 2>/dev/null | head -1)"; [ -n "$LAST" ] && echo "升级报告：$LAST"
exit $RC
