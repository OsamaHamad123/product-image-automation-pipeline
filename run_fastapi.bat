@echo off
title FastAPI Image Automation Server
echo Starting FastAPI Image Automation Server on http://127.0.0.1:8001...
"C:\Users\OsamaHamad\AppData\Local\Programs\Python\Python314\python.exe" -m uvicorn fastapi_server:app --host 127.0.0.1 --port 8001
pause
