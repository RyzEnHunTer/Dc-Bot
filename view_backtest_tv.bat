@echo off
title TradingView Backtest Visualizer - DCC Strategy
cd /d "%~dp0"
echo ===============================================================================
echo            STARTING TRADINGVIEW BACKTEST & TRADE GEOMETRY VISUALIZER
echo ===============================================================================
echo.
echo Launching local server on port 8088...
start "" http://localhost:8088/visualizer/index.html
echo.
echo Visualizer open in your browser!
echo Press Ctrl+C in this window to stop the server when done.
echo.
python -m http.server 8088
