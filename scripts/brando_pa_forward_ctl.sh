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
  report)  "$PY" -m brando_pa.cli forward-leaderboard; echo; "$PY" -m brando_pa.cli forward-daily-report ${2:+--date "$2"} > /dev/null && cat "$D/daily/$(ls "$D/daily" | grep '\.md$' | tail -1)"; echo; echo "完整 JSON：$D/leaderboard.json、$D/daily/*.json" ;;
  acceptance) "$PY" -m brando_pa.cli forward-acceptance ${2:+--local-start "$2"} ;;
  backup)  TS=$(date -u +%Y%m%dT%H%M%SZ); mkdir -p backups && tar -czf "backups/phase8_forward_$TS.tgz" "$D" data_store/brando_pa/forward_data_status.json data_store/brando_pa/forward_bars 2>/dev/null; echo "备份：backups/phase8_forward_$TS.tgz" ;;
  upgrade) # 安全升级：备份 → 停止服务（释放单实例锁）→ 拉取代码 → 依赖 → 只读检查 → 重新启动。不删除、不重置任何账户状态与日志。
           "$0" backup; launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true; sleep 3
           if [ -f "$D/service.lock" ] && command -v flock >/dev/null 2>&1; then :; fi
           pgrep -f "brando_pa.cli forward-service" >/dev/null && { echo "仍有 forward-service 进程在运行，拒绝升级"; exit 1; }
           git pull --ff-only && "$PY" -m pip install -q -r deploy/requirements-forward.txt
           "$PY" -m brando_pa.cli forward-timeline-audit >/dev/null && echo "时间顺序审计通过" || echo "警告：时间顺序审计未通过，请检查 $D/timeline_audit.md"
           launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null || true; launchctl kickstart "gui/$(id -u)/$LABEL"; sleep 15; "$PY" -m brando_pa.cli forward-health | head -c 600; echo ;;
  *) echo "用法：$0 {start|stop|restart|status|health|logs [n]|report [YYYY-MM-DD]|acceptance [local_start_utc]|backup|upgrade}"; exit 2 ;;
esac
