@echo off
title DCC Institutional Trading Bot - v1.2 ApexHunter (100% Backtest Match)
echo ==============================================================================
echo   STARTING DCC BOT IN v1.2 APEXHUNTER PRODUCTION PARITY MODE
echo   Settings: v1.2 ApexHunter + Bar-Close Entry + 5M Sweep + Smart Killzone
echo ==============================================================================
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)
python live_bot.py --auto
pause
