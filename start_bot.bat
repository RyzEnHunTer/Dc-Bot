@echo off
title DCC v1.2 ApexHunter Trading Bot
echo ==============================================================================
echo   STARTING DCC v1.2 APEXHUNTER BOT (Dual-Gear Institutional Engine)
echo ==============================================================================
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)
python live_bot.py --auto
pause
