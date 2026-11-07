#!/usr/bin/env bash
# brando_pa 定时任务（只读行情 + 模拟盘，不含真实下单）：ingest → signal → paper --once → export。
# * 并发锁（flock / mkdir 回退），幂等（K 线 upsert 去重；模拟盘按已处理 K 线时间戳续跑；订单号确定性去重）。
# * 日志 logs/brando_pa/YYYY-MM-DD.log；Webhook 只从环境变量 BRANDO_ALERT_WEBHOOK 读取，不打印。
# crontab 示例（每 15 分钟）： */15 * * * * /path/to/1hao-/scripts/brando_pa_daily.sh
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH=src
[ -f "${BRANDO_ENV_FILE:-$HOME/.config/brando_pa/env}" ] && set -a && . "${BRANDO_ENV_FILE:-$HOME/.config/brando_pa/env}" && set +a
mkdir -p logs/brando_pa data_store/brando_pa
LOG="logs/brando_pa/$(date -u +%F).log"; exec >>"$LOG" 2>&1
if command -v flock >/dev/null 2>&1; then exec 9>"data_store/brando_pa/.daily.flock"; flock -n 9 || { echo "$(date -u +%FT%TZ) 已有实例在运行，退出"; exit 9; }
else mkdir "data_store/brando_pa/.daily.lock" 2>/dev/null || { echo "$(date -u +%FT%TZ) 已有实例在运行（mkdir 锁），退出"; exit 9; }; trap 'rmdir data_store/brando_pa/.daily.lock 2>/dev/null' EXIT; fi
echo "== $(date -u +%FT%TZ) start"
rc=0
python3 -m brando_pa.cli ingest --timeframes 1w 1d 4h 15m || { echo "!! ingest 失败，使用已有数据"; rc=2; }
python3 -m brando_pa.cli signal || rc=3
python3 -m brando_pa.cli paper --once || rc=4
python3 -m brando_pa.cli export >/dev/null || rc=5
find logs/brando_pa -name '*.log' -mtime +60 -delete 2>/dev/null
echo "== $(date -u +%FT%TZ) end rc=$rc"; exit $rc
