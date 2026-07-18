@echo off
title FastAPI Image Automation Server
echo Starting FastAPI Image Automation Server on http://127.0.0.1:8001...
".venv\Scripts\python.exe" -m uvicorn fastapi_server:app --host 127.0.0.1 --port 8001
pause
