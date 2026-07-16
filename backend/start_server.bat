@echo off
cd /d %~dp0

if not exist venv (
    echo Cannot find venv folder. Please run the setup steps in README first.
    pause
    exit /b
)

call "%~dp0venv\Scripts\activate.bat"

if errorlevel 1 (
    echo Failed to activate venv.
    pause
    exit /b
)

if not exist cert.pem (
    echo Generating local HTTPS certificate for camera access on phones...
    python generate_cert.py
)
if not exist key.pem (
    echo Generating local HTTPS certificate for camera access on phones...
    python generate_cert.py
)

echo Starting server with HTTPS...
start "" https://localhost:8000/admin

uvicorn main:app --reload --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem

pause
