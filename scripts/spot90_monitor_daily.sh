#!/usr/bin/env bash
# 每日更新（只读）：ingest → snapshot → healthcheck。日志按天写入 logs/spot90_monitor/YYYY-MM-DD.log，保留 60 天。
# 任一步失败：保留上一份快照、写 alerts.log、退出码非 0（供 cron 邮件 / 外部监控捕获）；可选 SPOT90_ALERT_WEBHOOK 推送。
# crontab 例（UTC 00:40，等待备用源 03:17 UTC 更新后可再跑一次 04:00）：
#   40 0 * * *  cd /path/to/1hao- && bash scripts/spot90_monitor_daily.sh
#   10 4 * * *  cd /path/to/1hao- && bash scripts/spot90_monitor_daily.sh
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH=src
mkdir -p logs/spot90_monitor
LOG="logs/spot90_monitor/$(date -u +%F).log"
exec >>"$LOG" 2>&1
echo "== $(date -u +%FT%TZ) start"
rc=0
python3 -m spot90.cli ingest --no-crosscheck || { echo "!! ingest 失败，使用已有数据"; rc=2; }
python3 -m spot90.monitor.snapshot || { echo "!! snapshot 失败，保留上一份快照"; rc=3; }
python3 -m spot90.monitor.healthcheck || { echo "!! healthcheck FAIL"; rc=$(( rc == 0 ? 4 : rc )); }
find logs/spot90_monitor -name '*.log' -mtime +60 -delete 2>/dev/null
echo "== $(date -u +%FT%TZ) end rc=$rc"
exit $rc
