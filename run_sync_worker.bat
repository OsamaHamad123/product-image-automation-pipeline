@echo off
title Redis Write-Behind Sheets Worker
echo Starting Redis Write-Behind Sheets Worker...
".venv\Scripts\python.exe" sync_worker.py
pause
