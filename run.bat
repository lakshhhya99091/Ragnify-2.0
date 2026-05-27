@echo off
title Ragnify — AI Document Intelligence
color 0B

echo.
echo  ╔══════════════════════════════════════════════════════════════╗
echo  ║          Ragnify — AI Document Intelligence                 ║
echo  ║              Powered by GPT-4o + FAISS + RAG               ║
echo  ╚══════════════════════════════════════════════════════════════╝
echo.

:: Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

echo [1/3] Checking Python version...
python --version

echo.
echo [2/3] Installing dependencies (first run may take a few minutes)...
cd /d "%~dp0backend"
pip install -r requirements.txt --quiet

if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo [3/3] Starting Ragnify server...
echo.
echo  ┌──────────────────────────────────────────────────────────────┐
echo  │  Ragnify is starting at: http://localhost:8000              │
echo  │  Open your browser to: http://localhost:8000                │
echo  │                                                              │
echo  │  Press Ctrl+C to stop the server                           │
echo  └──────────────────────────────────────────────────────────────┘
echo.

:: Open browser after 3 seconds
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000"

:: Start the server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

pause
