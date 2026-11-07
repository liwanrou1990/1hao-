#!/usr/bin/env bash
# 第八阶段前瞻模拟：在具备合法访问交易所公开行情条件的联网机器上运行。
# 用法：每小时（cron）或 systemd timer 调用一次；也可 --loop 常驻。
#   crontab 示例：7 * * * * cd /path/to/1hao- && bash scripts/brando_pa_forward.sh >> reports_out/brando_pa/phase8_forward/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
python -m brando_pa.cli forward-data                     # 1) 更新行情（自动重试、来源回退、只保留已收盘 K 线）
python -m brando_pa.cli forward-run --no-update          # 2) 推进前瞻账户（行情已在上一步更新）
python -m brando_pa.cli forward-status | head -c 400     # 3) 哈希链校验与状态摘要
