@echo off
rem UTF-8 for every Python process started from here (cli_bridge.py, main.py)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
powershell -ExecutionPolicy Bypass -File "%~dp0launch_desktop.ps1"
