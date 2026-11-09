#!/usr/bin/env bash
# brando_pa 前瞻服务日常操作（macOS launchd / 通用）：start | stop | restart | status | health | logs | report | acceptance | audit
set -euo pipefail
cd "$(dirname "$0")/.."; LABEL="com.brando-pa.forward"; PY="${BRANDO_PY:-$PWD/.venv/bin/python}"; [ -x "$PY" ] || PY=python3
export PYTHONPATH=src
D=reports_out/brando_pa/phase8_forward
case "${1:-status}" in
  start)   launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null || true; launchctl kickstart "gui/$(id -u)/$LABEL" ;;
  stop)    launchctl bootout "gui/$(id -u)/$LABEL" ;;
  restart) launchctl kickstart -k "gui/$(id -u)/$LABEL" ;;
  status)  launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|pid|last exit" | head -5 || echo "launchd 任务未加载"; "$PY" -m brando_pa.cli forward-status | head -c 1500; echo ;;
  health)  "$PY" -m brando_pa.cli forward-health ;;
  logs)    tail -n "${2:-50}" "$D/service.log"; echo "--- alerts"; tail -n 10 "$D/alerts.jsonl" 2>/dev/null || true; echo "--- launchd"; tail -n 10 "$D/launchd.err.log" 2>/dev/null || true ;;
  report)  "$PY" -m brando_pa.cli forward-leaderboard; echo; { if [ -n "${2:-}" ]; then "$PY" -m brando_pa.cli forward-daily-report --date "$2"; else "$PY" -m brando_pa.cli forward-daily-report; fi; } > /dev/null && cat "$D/daily/$(ls "$D/daily" | grep '\.md$' | tail -1)"; echo; echo "完整 JSON：$D/leaderboard.json、$D/daily/*.json" ;;
  acceptance) if [ -n "${2:-}" ]; then "$PY" -m brando_pa.cli forward-acceptance --local-start "$2"; else "$PY" -m brando_pa.cli forward-acceptance; fi ;;
  audit)   if [ -n "${2:-}" ]; then "$PY" -m brando_pa.cli forward-ledger-audit --local-start "$2"; else "$PY" -m brando_pa.cli forward-ledger-audit; fi ;;   # 只读：费用/净值一致性/信号/验收/进程审计，结果写 reports_out/brando_pa/forward_audit/
  backup)  "$PY" - <<'PYB'
import sys; sys.path.insert(0, "src")
from pathlib import Path
from brando_pa.forward.upgrade import backup, LABEL
repo = Path(".").resolve(); fdir = repo / "reports_out/brando_pa/phase8_forward"
extra = [repo / "data_store/brando_pa/forward_bars", repo / "data_store/brando_pa/forward_data_status.json", repo / "data_store/brando_pa/data_events.jsonl", repo / "config/brando_pa.yaml", Path.home() / ".config/brando_pa/env", Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"]
print(backup(repo, fdir, [p for p in extra if p.exists()], repo / "backups"))
PYB
           ;;
  upgrade) bash scripts/brando_pa_safe_upgrade.sh "${2:-claude/brando-pa-phase9-deploy}" ;;      # 安全升级：见 scripts/brando_pa_safe_upgrade.sh（新版本升级器执行，备份校验/安全停服/预检/ff/核对/启动）
  upgrade-dry-run) bash scripts/brando_pa_safe_upgrade.sh "${2:-claude/brando-pa-phase9-deploy}" --dry-run ;;
  *) echo "用法：$0 {start|stop|restart|status|health|logs [n]|report [YYYY-MM-DD]|acceptance [local_start_utc]|audit [local_start_utc]|backup|upgrade [branch]|upgrade-dry-run}"; exit 2 ;;
esac
