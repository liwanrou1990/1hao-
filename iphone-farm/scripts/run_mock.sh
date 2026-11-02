#!/usr/bin/env bash
# 启动 iPhone-use 模拟服务器（SIMULATED，仅用于离线验证脚本），默认 30 台、端口 7390
cd "$(dirname "$0")/.."
PYTHONPATH=src python3 -m iphone_farm.mock_server --n "${1:-30}" --port "${2:-7390}" --latency-ms "${3:-0}"
