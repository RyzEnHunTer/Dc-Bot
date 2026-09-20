@echo off
title DCC v1.1 Early ApexHunter Trading Bot
echo ==============================================================================
echo   STARTING DCC v1.1 EARLY APEXHUNTER BOT (TripleGuard + Daily KZ Paused)
echo ==============================================================================
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)
python live_bot.py --auto --strategy-version v1.1
pause
