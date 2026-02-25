@echo off
title Itifaq Onboarding Platform

set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"

:: Activate virtual environment if present
if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call "venv\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call ".venv\Scripts\activate.bat"
) else (
    echo No virtual environment found — using system Python.
)

:: Check .env exists
if not exist ".env" (
    echo WARNING: .env file not found. Copy .env.example to .env and fill in your values.
    pause
    exit /b 1
)

echo Starting Flask app...
cd /d "%PROJECT_DIR%backend"
python app.py

pause
