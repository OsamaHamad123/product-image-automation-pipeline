@echo off
setlocal EnableDelayedExpansion
title Product Image Automation Starter
rem UTF-8 for every Python process started from here (cli_bridge.py, main.py, sync_worker.py)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if "%REDIS_PORT%"=="" set REDIS_PORT=6379
echo ============================================================
echo   Launching Product Image Automation Pipeline Services
echo ============================================================
echo.
echo [1/2] Checking Cloud Services and Subscriptions...
".venv\Scripts\python.exe" verify_cloud_services.py
if !ERRORLEVEL! neq 0 (
    echo.
    echo ============================================================
    echo   WARNING: Critical cloud services failed validation check.
    echo   Please review the errors above before launching.
    echo ============================================================
    echo.
    set /p choice="Do you want to proceed with launching anyway? (Y/N): "
    if /i "!choice!" neq "Y" (
        echo Exiting...
        timeout /t 3
        exit /b 1
    )
)

echo.
echo [2/2] Sheets sync worker (only needed when Redis is running)...
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $a = $c.BeginConnect('127.0.0.1', [int]$env:REDIS_PORT, $null, $null); $ok = $a.AsyncWaitHandle.WaitOne(1000) -and $c.Connected; $c.Close(); if ($ok) { exit 0 } else { exit 1 } } catch { exit 1 }"
if !ERRORLEVEL! equ 0 (
    echo Redis detected on port %REDIS_PORT%: starting the Redis Google Sheets Sync Worker...
    start "Redis Sheets Sync Worker" cmd /c "run_sync_worker.bat"
) else (
    echo Redis not detected: sheet updates are written directly, no sync worker needed.
)

echo.
echo ============================================================
echo   The dashboard runs every action through cli_bridge.py
echo   (no FastAPI server). Start it with setup_and_launch.bat.
echo ============================================================
echo.
pause
