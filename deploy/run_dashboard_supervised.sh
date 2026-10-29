#!/usr/bin/env bash
# 便携式守护（无 systemd/launchd 时使用）：Streamlit 退出后指数退避重启，日志 logs/dashboard_supervisor.log。
set -u
cd "$(dirname "$0")/.."
mkdir -p logs; echo $$ > logs/dashboard_supervisor.pid
backoff=2
while true; do
  echo "$(date -u +%FT%TZ) starting dashboard" >> logs/dashboard_supervisor.log
  bash deploy/run_dashboard.sh >> logs/dashboard.log 2>&1
  rc=$?
  echo "$(date -u +%FT%TZ) dashboard exited rc=$rc, restart in ${backoff}s" >> logs/dashboard_supervisor.log
  sleep "$backoff"; backoff=$(( backoff < 60 ? backoff * 2 : 60 ))
done
