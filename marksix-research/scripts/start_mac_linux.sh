#!/usr/bin/env bash
# 一键启动（macOS / Linux）：创建虚拟环境 → 安装锁定依赖 → 初始化 → 启动中文界面
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  echo "创建虚拟环境 .venv ..."
  "$PY" -m venv .venv
fi
. .venv/bin/activate
if [ ! -f .venv/.deps_ok ] || [ requirements.txt -nt .venv/.deps_ok ]; then
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  touch .venv/.deps_ok
fi
python -m marksix --kind real init
python -m marksix --kind synthetic init
echo "启动界面：http://localhost:8501 （Ctrl+C 结束）"
python -m streamlit run app/main.py --browser.gatherUsageStats false
