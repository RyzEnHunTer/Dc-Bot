@echo off
title DCC Institutional Trading Bot - 100% Backtest Match Mode
echo ==============================================================================
echo   STARTING DCC BOT IN 100% EXACT BACKTEST PARITY MODE
echo   Settings: Bar-Close Entry Mode + No EMA Gap Filter + 5M Liquidity Sweep
echo ==============================================================================
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)
python live_bot.py --auto --entry-mode bar_close --no-ema-gap-filter
pause
