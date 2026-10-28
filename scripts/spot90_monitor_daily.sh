#!/usr/bin/env bash
# 每日更新：拉取最新日线（Bitget 首选 → 备用源）→ 生成快照（含参考价与新鲜度）。失败时保留上一份快照并在日志中记录。
# crontab 例：  20 0 * * *  cd /path/to/1hao- && bash scripts/spot90_monitor_daily.sh >> logs/spot90_monitor.log 2>&1
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH=src
echo "== $(date -u +%FT%TZ) ingest"
python3 -m spot90.cli ingest --no-crosscheck || echo "ingest 失败（将用已有数据生成快照并标注延迟）"
echo "== $(date -u +%FT%TZ) snapshot"
python3 -m spot90.monitor.snapshot || { echo "snapshot 失败"; exit 1; }
echo "== $(date -u +%FT%TZ) done"
