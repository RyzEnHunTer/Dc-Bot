#!/usr/bin/env python3
"""
===============================================================================
       DCC INSTITUTIONAL TRADING BOT - PYTHON RUNNER & LAUNCHER
===============================================================================
Cross-platform launcher for DCC Live Bot.
Automatically detects and uses the local virtual environment (venv) if present,
otherwise falls back to the current Python interpreter.

Usage:
  python start_bot.py              # Starts bot in automatic mode (--auto)
  python start_bot.py --interactive # Prompts for account configuration
  python start_bot.py [any args]   # Forwards arguments directly to live_bot.py
===============================================================================
"""

import os
import sys
import subprocess
from pathlib import Path

# Safe encoding for Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent
LIVE_BOT_SCRIPT = PROJECT_ROOT / "live_bot.py"


def find_python_executable() -> str:
    """Find virtual environment Python or fallback to current interpreter."""
    venv_candidates = [
        PROJECT_ROOT / "venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / "venv" / "bin" / "python",
        PROJECT_ROOT / ".venv" / "bin" / "python",
    ]
    for candidate in venv_candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def main():
    print("=" * 78)
    print("      STARTING DCC v1.2 APEXHUNTER BOT (Institutional Dual-Gear)")
    print("=" * 78)

    python_exe = find_python_executable()
    print(f"[*] Python Runtime: {python_exe}")

    # Build command line
    user_args = sys.argv[1:]
    if not user_args:
        # Default to auto mode with saved accounts if no arguments provided
        cmd_args = ["--auto"]
    elif "--interactive" in user_args:
        cmd_args = [arg for arg in user_args if arg != "--interactive"]
    else:
        cmd_args = user_args

    cmd = [python_exe, str(LIVE_BOT_SCRIPT)] + cmd_args
    print(f"[*] Command: {' '.join(cmd)}\n")

    try:
        proc = subprocess.run(cmd)
        sys.exit(proc.returncode)
    except KeyboardInterrupt:
        print("\n[INFO] Bot stopped gracefully by user.")
    except Exception as e:
        print(f"\n[ERROR] Failed to start bot: {e}")
        if sys.platform == "win32":
            try:
                input("\nPress Enter to exit...")
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
