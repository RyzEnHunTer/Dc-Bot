@echo off
title DCC Institutional Trading Bot - v1.1 Early ApexHunter (100% Backtest Match)
echo ==============================================================================
echo   STARTING DCC BOT IN v1.1 EARLY APEXHUNTER PRODUCTION PARITY MODE
echo   Settings: v1.1 Early ApexHunter + Bar-Close Entry + TripleGuard + KZ Paused
echo ==============================================================================
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)
python live_bot.py --auto --strategy-version v1.1
pause
