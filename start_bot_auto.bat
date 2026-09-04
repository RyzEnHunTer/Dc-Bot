@echo off
title DCC Institutional Trading Bot - Auto Mode
echo Starting DCC Bot in Auto Mode (Saved Account Settings)...
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)
python live_bot.py --auto
pause
