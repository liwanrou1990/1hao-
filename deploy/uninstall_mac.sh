#!/usr/bin/env bash
# 卸载 launchd 任务（代码与数据保留）
set -u
LA="${HOME:?}/Library/LaunchAgents"
for L in com.spot90.dashboard com.spot90.daily; do
  launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true
  rm -f -- "${LA:?}/${L:?}.plist"
done
echo "已移除 launchd 任务（数据与代码保留）"
