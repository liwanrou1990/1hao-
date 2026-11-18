@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
call .venv\Scripts\activate.bat
python -m pytest -q %*
