#!/usr/bin/env bash
# 卸载 brando_pa 前瞻服务（只移除 launchd 任务；不删除任何账户状态、事件链、行情缓存与日报）
set -euo pipefail
LABEL="com.brando-pa.forward"; LA="$HOME/Library/LaunchAgents"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$LA/$LABEL.plist"
echo "已移除 launchd 任务 $LABEL；数据与记录保留在仓库目录（reports_out/brando_pa/phase8_forward, data_store/brando_pa/forward_bars）"
