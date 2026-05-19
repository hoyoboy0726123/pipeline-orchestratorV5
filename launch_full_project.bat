@echo off
setlocal
title Pipeline Orchestrator Launcher
REM 一鍵啟動：首次執行會自動建立後端 venv、安裝前後端依賴、複製 .env 範本。
REM 之後執行只會啟動服務。需先安裝 Python 3.10+ 與 Node.js 18+。

echo ==================================================
echo  Pipeline Orchestrator - Launcher
echo ==================================================
echo.

REM ── 1. 後端虛擬環境 + 依賴 ────────────────────────────
if not exist "%~dp0backend\.venv\Scripts\python.exe" (
    echo [Setup] 建立後端虛擬環境 backend\.venv ...
    python -m venv "%~dp0backend\.venv"
    if errorlevel 1 (
        echo [X] 建立虛擬環境失敗。請先安裝 Python 3.10+ 並確認 python 在 PATH 上。
        pause
        exit /b 1
    )
    echo [Setup] 安裝後端依賴（首次需要幾分鐘）...
    "%~dp0backend\.venv\Scripts\python.exe" -m pip install -q -r "%~dp0backend\requirements.txt"
    if errorlevel 1 (
        echo [X] 後端依賴安裝失敗，請看上方錯誤訊息。
        pause
        exit /b 1
    )
)

REM ── 2. 後端 .env（沒有就從範本複製）──────────────────
if not exist "%~dp0backend\.env" (
    echo [Setup] 尚未設定 backend\.env，從範本複製一份 ...
    copy "%~dp0backend\.env.example" "%~dp0backend\.env" >NUL
    echo [!] 請編輯 backend\.env 填入 LLM API Key（Groq / Gemini）後，AI 功能才會運作。
)

REM ── 3. 前端依賴 ──────────────────────────────────────
if not exist "%~dp0frontend\node_modules" (
    echo [Setup] 安裝前端依賴（首次需要幾分鐘）...
    pushd "%~dp0frontend"
    call npm install
    set "NPM_ERR=%errorlevel%"
    popd
    if not "%NPM_ERR%"=="0" (
        echo [X] 前端依賴安裝失敗。請先安裝 Node.js 18+。
        pause
        exit /b 1
    )
)

echo.
echo [1/2] 啟動後端 (Port 8004) ...
REM /k 讓視窗在 uvicorn 崩潰時保持開啟、方便看錯誤
REM 顯式清掉可能從父 process 繼承的 PYTHONUTF8（main.py 自己會設 stdout 編碼）
start "PO_Backend" cmd /k "cd /d "%~dp0backend" && set "PYTHONUTF8=" && .venv\Scripts\uvicorn.exe main:app --host 0.0.0.0 --port 8004"

echo [2/2] 啟動前端 (Port 3002) ...
start "PO_Frontend" cmd /k "cd /d "%~dp0frontend" && npx next dev --port 3002"

echo.
echo ==================================================
echo  Pipeline Orchestrator 已啟動
echo    前端 : http://localhost:3002
echo    後端 : http://localhost:8004
echo ==================================================
echo.
echo 選用功能（需要時才裝）：
echo  - Skill 沙盒：隔離執行 AI 生成的程式碼。執行 sandbox\setup_sandbox.bat
echo    一次性安裝（需要 WSL；未安裝沙盒時 Skill 節點會 fallback 在本機跑）
echo  - Outlook 自動化節點：需 Windows 桌面版 Outlook + 已設定好的 profile
echo.
pause
