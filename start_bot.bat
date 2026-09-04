@echo off
title DCC Institutional Trading Bot - Interactive Menu
echo Starting DCC Bot in virtual environment...
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)
python live_bot.py
pause
