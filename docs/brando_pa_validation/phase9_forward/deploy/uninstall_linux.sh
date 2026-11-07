#!/usr/bin/env bash
# 卸载 systemd 单元（代码与数据保留）
set -u
MODE="${1:-user}"
if [ "$MODE" = "--system" ]; then SC="systemctl"; D=/etc/systemd/system; else SC="systemctl --user"; D="${HOME:?}/.config/systemd/user"; fi
$SC disable --now spot90-dashboard.service spot90-daily.timer 2>/dev/null || true
for U in spot90-dashboard.service spot90-daily.service spot90-daily.timer; do rm -f -- "${D:?}/${U:?}"; done
$SC daemon-reload
echo "已移除 systemd 单元（数据与代码保留）"
