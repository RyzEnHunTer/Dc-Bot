@echo off
setlocal enabledelayedexpansion

echo ===============================================================================
echo            INSTITUTIONAL DCC TRADING BOT - VPS SETUP & BUILD SCRIPT
echo ===============================================================================
echo.

:: 1. Check Python Installation
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.11 or 3.12 (64-bit) from https://www.python.org/downloads/
    echo IMPORTANT: Make sure to check "Add python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [1/4] Checking Python version...
python -c "import sys; print(f'Detected Python {sys.version.split()[0]} ({sys.maxsize > 2**32 and \"64-bit\" or \"32-bit\"})')"
echo.

:: 2. Create Virtual Environment
if not exist "venv" (
    echo [2/4] Creating virtual environment (venv)...
    python -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [2/4] Virtual environment (venv) already exists.
)
echo.

:: 3. Upgrade Pip and Install Dependencies
echo [3/4] Installing required libraries from requirements.txt...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Package installation failed! Check your internet connection.
    pause
    exit /b 1
)
echo [OK] All dependencies successfully installed.
echo.

:: 4. Verify MetaTrader 5 Connectivity
echo [4/4] Verifying MetaTrader 5 module...
python -c "import MetaTrader5 as mt5; print(f'[OK] MetaTrader 5 Python Library Version: {mt5.__version__}')"
if %ERRORLEVEL% neq 0 (
    echo [WARN] MetaTrader 5 module could not be verified. Ensure MT5 Desktop Terminal is installed.
)
echo.

echo ===============================================================================
echo                     VPS SETUP COMPLETED SUCCESSFULLY!
echo ===============================================================================
echo.
echo Quick Launch Instructions:
echo   - To start bot interactively:    Double-click 'start_bot.bat'
echo   - To start bot in auto mode:     Double-click 'start_bot_auto.bat'
echo   - To run live diagnostics:       python tests/test_mt5_live_order.py
echo.
pause
