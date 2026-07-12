@echo off
title Product Image Automation Setup & Launcher
echo ============================================================
echo   Initializing Product Image Automation Pipeline Setup...
echo ============================================================
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0setup_and_launch.ps1"
echo.
