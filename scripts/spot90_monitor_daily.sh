#!/usr/bin/env bash
# 每日更新（只读）：ingest → snapshot → healthcheck。
# * 并发锁：同一时刻只允许一个实例（Linux 用 flock，macOS 用 mkdir 锁，锁目录 2 小时自动视为失效）。
# * 幂等：同一 UTC 日重复运行只覆盖当日快照/状态；日线 upsert 按时间戳去重。
# * 原子化：快照、状态、健康结果、parquet 均先写临时文件再 rename。
# * 日志：logs/spot90_monitor/YYYY-MM-DD.log，保留 60 天；密钥/Webhook 只从环境变量读取，不打印。
# 退出码：0 正常；2 ingest 失败（用旧数据继续）；3 snapshot 失败；4 健康检查 FAIL；9 已有实例在运行。
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH=src
[ -f "${SPOT90_ENV_FILE:-$HOME/.config/spot90/env}" ] && set -a && . "${SPOT90_ENV_FILE:-$HOME/.config/spot90/env}" && set +a
mkdir -p logs/spot90_monitor data_store/spot90
LOG="logs/spot90_monitor/$(date -u +%F).log"
exec >>"$LOG" 2>&1
LOCKDIR="data_store/spot90/.daily.lock"
acquire_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"data_store/spot90/.daily.flock"
    flock -n 9 || { echo "$(date -u +%FT%TZ) 已有实例在运行（flock），退出"; exit 9; }
  else
    if mkdir "$LOCKDIR" 2>/dev/null; then trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT; else
      if [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then rmdir "$LOCKDIR" 2>/dev/null && mkdir "$LOCKDIR" && trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT; else
        echo "$(date -u +%FT%TZ) 已有实例在运行（mkdir 锁），退出"; exit 9; fi
    fi
  fi
}
acquire_lock
echo "== $(date -u +%FT%TZ) start (webhook configured: $([ -n "${SPOT90_ALERT_WEBHOOK:-}" ] && echo yes || echo no))"
rc=0
python3 -m spot90.cli ingest --no-crosscheck || { echo "!! ingest 失败，使用已有数据"; rc=2; }
python3 -m spot90.monitor.snapshot || { echo "!! snapshot 失败，保留上一份快照"; rc=3; }
python3 -m spot90.monitor.healthcheck || { echo "!! healthcheck FAIL"; [ $rc -eq 0 ] && rc=4; }
find logs/spot90_monitor -name '*.log' -mtime +60 -delete 2>/dev/null
echo "== $(date -u +%FT%TZ) end rc=$rc"
exit $rc
