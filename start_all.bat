@echo off
title Product Image Automation Starter
echo ============================================================
echo   Launching Product Image Automation Pipeline Services
echo ============================================================
echo.
echo [1/2] Starting FastAPI Model Inference Server (Port 8001)...
start "FastAPI Inference Server" cmd /c "run_fastapi.bat"

echo [2/2] Starting Redis Google Sheets Sync Worker...
start "Redis Sheets Sync Worker" cmd /c "run_sync_worker.bat"

echo.
echo ============================================================
echo   All services launched! You can minimize the new windows.
echo   Do not close them while using the Laravel Dashboard.
echo ============================================================
echo.
pause
