@echo off
title Moodify - AI Emotion Music Player
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist .venv (
  echo Creating Python virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Installing required packages...
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Starting Moodify...
start "" http://127.0.0.1:5000
python app.py
pause
