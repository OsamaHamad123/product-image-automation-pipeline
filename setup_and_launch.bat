@echo off
rem no bare ampersand in the title: cmd would run the rest as a command
title Product Image Automation - Setup and Launch
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
