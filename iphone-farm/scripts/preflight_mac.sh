#!/usr/bin/env bash
# 实机预检：USB 枚举、usbmux、蓝牙、iPhone-use 服务、电源/休眠设置。输出到 reports_out/preflight_<时间>.txt
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p reports_out
OUT="reports_out/preflight_$(date +%Y%m%d-%H%M%S).txt"
PORT="${IPHONE_USE_PORT:-7390}"
{
  echo "# preflight $(date)"; sw_vers; uname -m
  echo; echo "## USB 树中的 iPhone（system_profiler）"
  system_profiler SPUSBDataType 2>/dev/null | grep -E "iPhone|Serial Number|Location ID|Current Available|Current Required|Extra Operating" | sed 's/^ *//'
  echo; echo "## iPhone 数量（system_profiler）: $(system_profiler SPUSBDataType 2>/dev/null | grep -c 'iPhone')"
  echo; echo "## usbmux UDID 列表"
  if command -v idevice_id >/dev/null; then idevice_id -l; else echo "(未安装 libimobiledevice)"; fi
  echo; echo "## 每台的信任/名称/系统版本（ideviceinfo）"
  if command -v ideviceinfo >/dev/null; then
    for u in $(idevice_id -l 2>/dev/null); do
      echo "- $u: $(ideviceinfo -u "$u" -k DeviceName 2>&1) / $(ideviceinfo -u "$u" -k ProductType 2>&1) / iOS $(ideviceinfo -u "$u" -k ProductVersion 2>&1)"
    done
  fi
  echo; echo "## 蓝牙控制器"
  system_profiler SPBluetoothDataType 2>/dev/null | head -40
  echo; echo "## iPhone-use /status (端口 $PORT)"
  curl -s --max-time 5 "http://127.0.0.1:$PORT/status" || echo "(iPhone-use 未运行)"
  echo; echo; echo "## 休眠设置（群控期间应禁止休眠：sudo pmset -a sleep 0 disksleep 0; caffeinate -dims &）"
  pmset -g | grep -E "sleep|displaysleep" || true
  echo; echo "## 打开文件数上限（建议 ulimit -n 10240）"; ulimit -n
} | tee "$OUT"
echo "已保存 $OUT"
