@echo off
setlocal
title Pipeline Orchestrator V5 Launcher
REM 不在這裡設 PYTHONUTF8 / PYTHONIOENCODING — main.py 自己會用 sys.stdout.reconfigure
REM 把 stdout/stderr 強制改 utf-8。env var 鏈式設定容易沾到尾隨空白讓 Python
REM 的 preinitializing 階段直接炸掉（Fatal Python error: invalid PYTHONUTF8）

echo Starting Pipeline Orchestrator V5 in separate windows...
echo (V5 uses port 8004 / 3005 to avoid clashing with V1:8000 V2:8001 V3:8002 V4:8003)

echo [1/2] Starting Backend V5 (Port 8004)...
REM /k instead of /c keeps the window open if uvicorn crashes so you can read the error
REM 顯式清掉可能從父 process 繼承的 PYTHONUTF8（曾經設過沾到尾空白值讓 Python preinit 炸）
start "PO_Backend_V5" cmd /k "cd /d "%~dp0backend" && set "PYTHONUTF8=" && .venv\Scripts\uvicorn.exe main:app --host 0.0.0.0 --port 8004"

echo [2/2] Starting Frontend V5 (Port 3005)...
start "PO_Frontend_V5" cmd /k "cd /d "%~dp0frontend" && npx next dev --port 3005"

echo.
echo V5 startup commands issued.
echo   Frontend : http://localhost:3005
echo   Backend  : http://localhost:8004
echo.
echo ===== V5 First-time setup =====
echo 1. Backend venv：跟 V4 共用一份 venv 也可以（pywin32 已裝），但 V5 多了
echo    pywinauto / comtypes / python-pptx / jinja2 / markdown
echo    後端啟動時 auto_install_packages 會自動裝；或手動跑：
echo      cd backend
echo      .venv\Scripts\pip install pywinauto comtypes python-pptx jinja2 markdown
echo.
echo 2. Outlook 自動化節點需要：
echo    - Windows 桌面版 Outlook 已安裝 + 預設 profile 已設好
echo    - 跑這支 backend 的使用者帳號 = 設定 Outlook profile 的帳號
echo.
echo 3. Sandbox（如要用一般 Skill 節點走容器）：
echo    Container 名 pipeline-sandbox-v4 跟 V4 共用即可，不用 rebuild
echo.
pause
