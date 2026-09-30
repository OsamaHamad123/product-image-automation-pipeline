@echo off
title Product Image Automation Setup & Launcher
rem UTF-8 for every Python process started from here (cli_bridge.py, main.py)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo ============================================================
echo   Initializing Product Image Automation Pipeline Setup...
echo ============================================================
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0setup_and_launch.ps1"
if %errorlevel% neq 0 (
    echo.
    echo ❌ حدث خطأ أثناء تشغيل البرنامج.
    pause
)
