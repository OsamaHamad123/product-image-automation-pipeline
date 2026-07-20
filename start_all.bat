@echo off
title Product Image Automation Starter
echo ============================================================
echo   Launching Product Image Automation Pipeline Services
echo ============================================================
echo.
echo [1/3] Checking Cloud Services and Subscriptions...
".venv\Scripts\python.exe" verify_cloud_services.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo ============================================================
    echo   WARNING: Critical cloud services failed validation check!
    echo   Please review the errors above before launching.
    echo ============================================================
    echo.
    set /p choice="Do you want to proceed with launching anyway? (Y/N): "
    if /i "%choice%" neq "Y" (
        echo Exiting...
        timeout /t 3
        exit /b 1
    )
)

echo.
echo [2/3] Starting FastAPI Model Inference Server (Port 8001)...
start "FastAPI Inference Server" cmd /c "run_fastapi.bat"

echo.
echo [3/3] Starting Redis Google Sheets Sync Worker...
start "Redis Sheets Sync Worker" cmd /c "run_sync_worker.bat"

echo.
echo ============================================================
echo   All services launched! You can minimize the new windows.
echo   Do not close them while using the Laravel Dashboard.
echo ============================================================
echo.
pause
