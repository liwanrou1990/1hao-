#!/usr/bin/env bash
# brando_pa 前瞻服务日常操作（macOS launchd / 通用）：start | stop | restart | status | health | logs | report | acceptance
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
  report)  "$PY" -m brando_pa.cli forward-daily-report ${2:+--date "$2"} | head -c 2000; echo; ls "$D/daily" | tail -5 ;;
  acceptance) "$PY" -m brando_pa.cli forward-acceptance ;;
  *) echo "用法：$0 {start|stop|restart|status|health|logs [n]|report [YYYY-MM-DD]|acceptance}"; exit 2 ;;
esac
