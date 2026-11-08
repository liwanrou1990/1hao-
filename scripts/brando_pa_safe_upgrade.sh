#!/usr/bin/env bash
# brando_pa 前瞻服务 · Mac 安全升级引导（可在旧版本检出上运行）：
#   git fetch → 把 origin/<branch> 检出到临时 worktree → 用**新版本**的升级器执行：快照 → 完整备份+校验 → 安全停服务 → 预检/依赖/新版审计 → ff 合并（数据文件保存/放回）→ 升级后核对 → 启动 → 健康检查
# 任何一步失败：不改代码或恢复旧版本服务，并打印原因。不使用 git reset / clean / 强制覆盖。真实交易保持禁用。
# 用法：bash scripts/brando_pa_safe_upgrade.sh [branch] [--dry-run]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
BRANCH="${1:-claude/brando-pa-phase9-deploy}"; DRY="${2:-}"
PY="${BRANDO_PY:-$PWD/.venv/bin/python}"; [ -x "$PY" ] || PY=python3
git fetch origin "$BRANCH" || { echo "git fetch 失败，升级中止（服务未动）"; exit 1; }
WT="$PWD/.upgrade_worktree"; rm -rf "$WT"; git worktree prune >/dev/null 2>&1 || true
git worktree add --detach "$WT" "origin/$BRANCH" >/dev/null 2>&1 || { echo "无法创建临时 worktree，升级中止（服务未动）"; exit 1; }
trap 'git worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
PYTHONPATH="$WT/src" "$PY" -m brando_pa.forward.upgrade --repo "$PWD" --branch "$BRANCH" --worktree-src "$WT/src" ${DRY:+--dry-run}
RC=$?
echo "升级报告：$(ls -t reports_out/brando_pa/phase8_forward/upgrade_report_*.json 2>/dev/null | head -1)"
exit $RC
