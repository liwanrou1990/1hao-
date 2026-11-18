@echo off
REM One-click start (Windows): create venv, install pinned deps, init, launch UI
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo Creating virtual environment .venv ...
  %PY% -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat || goto :err
if not exist .venv\deps_ok (
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt || goto :err
  echo ok> .venv\deps_ok
)
python -m marksix --kind real init
python -m marksix --kind synthetic init
echo Open http://localhost:8501  (Ctrl+C to stop)
python -m streamlit run app\main.py --browser.gatherUsageStats false
goto :eof
:err
echo Startup failed. Please install Python 3.10-3.13 from python.org and retry.
pause
