#!/usr/bin/env python3
"""
===============================================================================
       DCC INSTITUTIONAL TRADING BOT - VPS & NEW SYSTEM SETUP WIZARD
===============================================================================
Automated environment configuration script for setting up the DCC Bot on
a new Windows VPS or local machine.

Performs:
  1. System & architecture compatibility audit (Python 64-bit, version check)
  2. Virtual environment (venv) creation & management
  3. Automated dependency installation (MetaTrader5, pandas, numpy, matplotlib)
  4. Configuration initialization (bot_accounts_config.json and .env)
  5. MetaTrader 5 terminal auto-detection
  6. End-to-end engine integrity verification

Usage:
  python setup_env.py             # Interactive setup with guided prompts
  python setup_env.py --auto       # Headless automated setup (ideal for VPS scripts)
  python setup_env.py --no-venv    # Install into current Python without creating venv
  python setup_env.py --test       # Run test suite after setup
  python setup_env.py --run        # Launch bot immediately after setup
===============================================================================
"""

import os
import sys
import shutil
import venv
import subprocess
import argparse
from pathlib import Path

# Safe encoding for Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
CONFIG_FILE = PROJECT_ROOT / "bot_accounts_config.json"
CONFIG_EXAMPLE = PROJECT_ROOT / "bot_accounts_config.example.json"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


class Colors:
    """ANSI color codes with Windows fallback."""
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        cls.CYAN = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.RED = ""
        cls.BOLD = ""
        cls.RESET = ""


# Auto-enable ANSI in Windows cmd/PowerShell if supported
if sys.platform == "win32":
    os.system("")


def print_banner():
    print(f"{Colors.CYAN}{Colors.BOLD}")
    print("=" * 78)
    print("       DCC INSTITUTIONAL TRADING BOT - ENVIRONMENT SETUP WIZARD")
    print("=" * 78)
    print(f"{Colors.RESET}  Automated setup for fresh Windows VPS or local trading workstations.")
    print("  Compatible with: Python 3.10, 3.11, 3.12 (64-bit AMD64)")
    print(f"{Colors.CYAN}" + "=" * 78 + f"{Colors.RESET}\n")


def check_python_compatibility() -> bool:
    """Verify Python version and architecture."""
    print(f"{Colors.BOLD}[Step 1/6] Auditing Python Environment & System Architecture...{Colors.RESET}")
    
    version = sys.version_info
    ver_str = f"{version.major}.{version.minor}.{version.micro}"
    is_64bit = sys.maxsize > 2**32
    arch_str = "64-bit (AMD64)" if is_64bit else "32-bit (x86)"
    
    print(f"  • Detected Python: {Colors.CYAN}{ver_str}{Colors.RESET} ({arch_str})")
    print(f"  • Operating System: {Colors.CYAN}{sys.platform}{Colors.RESET}")
    print(f"  • Python Executable: {sys.executable}")

    # Check minimum Python version
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print(f"\n{Colors.RED}[ERROR] Python 3.8 or higher is required. Detected: {ver_str}{Colors.RESET}")
        return False

    # Check 64-bit architecture (CRITICAL for MetaTrader 5)
    if not is_64bit:
        print(f"\n{Colors.RED}[FATAL ERROR] 32-bit Python detected!{Colors.RESET}")
        print("  MetaTrader 5 requires 64-bit Python (AMD64) to communicate with 'terminal64.exe'.")
        print("  Please uninstall 32-bit Python and install 64-bit Python 3.11 or 3.12 from:")
        print("  👉 https://www.python.org/downloads/windows/")
        print("  (Make sure to select 'Windows installer (64-bit)' and check 'Add python.exe to PATH')")
        return False

    if version.minor in (11, 12):
        print(f"  {Colors.GREEN}[OK] Optimal Python version for MetaTrader 5 production.{Colors.RESET}")
    elif version.minor >= 13:
        print(f"  {Colors.YELLOW}[INFO] Python {ver_str} detected. Ensure MT5 precompiled wheels support your build.{Colors.RESET}")
    else:
        print(f"  {Colors.YELLOW}[INFO] Python {ver_str} is supported (Recommended: 3.11 or 3.12).{Colors.RESET}")

    if sys.platform != "win32":
        print(f"  {Colors.YELLOW}[WARN] Non-Windows OS detected ({sys.platform}).{Colors.RESET}")
        print("         MetaTrader 5 live trading requires Windows. Data analysis/visualizer mode will still work.")
    else:
        print(f"  {Colors.GREEN}[OK] Native Windows OS detected for MetaTrader 5 IPC.{Colors.RESET}")

    print()
    return True


def get_venv_python(venv_dir: Path) -> Path:
    """Return path to Python binary inside virtual environment."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def setup_virtual_environment(venv_name: str, use_venv: bool, auto_mode: bool) -> Path:
    """Create or locate virtual environment, returning the target python executable."""
    print(f"{Colors.BOLD}[Step 2/6] Configuring Virtual Environment...{Colors.RESET}")
    venv_dir = PROJECT_ROOT / venv_name

    if not use_venv:
        print(f"  • Using current Python environment: {sys.executable}")
        print(f"  {Colors.GREEN}[OK] Virtual environment step bypassed (--no-venv).{Colors.RESET}\n")
        return Path(sys.executable)

    venv_py = get_venv_python(venv_dir)

    if venv_dir.exists() and venv_py.exists():
        print(f"  • Existing virtual environment found at: {Colors.CYAN}{venv_dir}{Colors.RESET}")
        print(f"  {Colors.GREEN}[OK] Using existing virtual environment.{Colors.RESET}\n")
        return venv_py

    print(f"  • Creating isolated virtual environment at: {Colors.CYAN}{venv_dir}{Colors.RESET}...")
    try:
        builder = venv.EnvBuilder(with_pip=True, clear=False)
        builder.create(venv_dir)
        
        if not venv_py.exists():
            print(f"{Colors.RED}[ERROR] Virtual environment was created, but python executable not found at: {venv_py}{Colors.RESET}")
            return Path(sys.executable)

        print(f"  {Colors.GREEN}[OK] Virtual environment created successfully.{Colors.RESET}\n")
        return venv_py
    except Exception as e:
        print(f"{Colors.YELLOW}[WARN] Could not create virtual environment: {e}{Colors.RESET}")
        print(f"       Falling back to system Python: {sys.executable}\n")
        return Path(sys.executable)


def install_dependencies(python_bin: Path, skip_install: bool = False) -> bool:
    """Upgrade pip and install dependencies from requirements.txt."""
    print(f"{Colors.BOLD}[Step 3/6] Installing Required Dependencies...{Colors.RESET}")

    if skip_install:
        print(f"  {Colors.YELLOW}[INFO] Package installation skipped (--skip-install).{Colors.RESET}\n")
        return True

    if not REQUIREMENTS_FILE.exists():
        print(f"{Colors.RED}[ERROR] requirements.txt not found at: {REQUIREMENTS_FILE}{Colors.RESET}")
        return False

    print(f"  • Upgrading pip...")
    try:
        subprocess.run(
            [str(python_bin), "-m", "pip", "install", "--upgrade", "pip"],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        print(f"  {Colors.YELLOW}[WARN] Could not upgrade pip (non-critical): {e}{Colors.RESET}")

    print(f"  • Installing packages from {REQUIREMENTS_FILE.name}...")
    cmd = [str(python_bin), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        for line in proc.stdout:
            line_str = line.strip()
            if line_str and not line_str.startswith("Requirement already satisfied"):
                print(f"    {line_str}")
        proc.wait()

        if proc.returncode != 0:
            print(f"\n{Colors.RED}[ERROR] Package installation exited with code {proc.returncode}.{Colors.RESET}")
            print("  Please check your internet connection or install manually with:")
            print(f"  {python_bin} -m pip install -r requirements.txt\n")
            return False

        print(f"  {Colors.GREEN}[OK] All dependencies successfully installed.{Colors.RESET}\n")
        return True
    except Exception as e:
        print(f"\n{Colors.RED}[ERROR] Failed to run pip: {e}{Colors.RESET}\n")
        return False


def setup_configuration_files(auto_mode: bool):
    """Ensure bot_accounts_config.json and .env exist, creating from templates if needed."""
    print(f"{Colors.BOLD}[Step 4/6] Initializing Configuration & Credentials...{Colors.RESET}")

    # 1. bot_accounts_config.json
    if CONFIG_FILE.exists():
        print(f"  • Account Configuration: {Colors.GREEN}[EXISTS]{Colors.RESET} ({CONFIG_FILE.name})")
    else:
        if CONFIG_EXAMPLE.exists():
            shutil.copyfile(CONFIG_EXAMPLE, CONFIG_FILE)
            print(f"  • Account Configuration: {Colors.CYAN}[CREATED]{Colors.RESET} Generated from {CONFIG_EXAMPLE.name}")
            print(f"    {Colors.YELLOW}👉 Remember to edit '{CONFIG_FILE.name}' with your actual MT5 Login and Server.{Colors.RESET}")
        else:
            print(f"  {Colors.YELLOW}[WARN] Neither {CONFIG_FILE.name} nor {CONFIG_EXAMPLE.name} found.{Colors.RESET}")

    # 2. .env
    if ENV_FILE.exists():
        print(f"  • Environment Variables: {Colors.GREEN}[EXISTS]{Colors.RESET} ({ENV_FILE.name})")
    else:
        if ENV_EXAMPLE.exists():
            shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
            print(f"  • Environment Variables: {Colors.CYAN}[CREATED]{Colors.RESET} Generated from {ENV_EXAMPLE.name}")
        else:
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.write("# DCC Institutional Trading Bot - Environment File\nOPENROUTER_API_KEY=\nDCC_VERSION=v1.2\n")
            print(f"  • Environment Variables: {Colors.CYAN}[CREATED]{Colors.RESET} Default .env created.")

    print(f"  {Colors.GREEN}[OK] Configuration files ready.{Colors.RESET}\n")


def scan_for_metatrader5():
    """Detect installed MetaTrader 5 terminal executables on the system."""
    print(f"{Colors.BOLD}[Step 5/6] Scanning for MetaTrader 5 Terminals...{Colors.RESET}")

    if sys.platform != "win32":
        print(f"  {Colors.YELLOW}[INFO] MT5 terminal scan skipped on non-Windows OS.{Colors.RESET}\n")
        return

    candidates = [
        Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
        Path(r"C:\Program Files\Exness MetaTrader 5\terminal64.exe"),
        Path(r"C:\Program Files\FTMO MetaTrader 5\terminal64.exe"),
        Path(r"C:\Program Files\ICMarkets MetaTrader 5\terminal64.exe"),
        Path(r"C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe"),
        Path(r"C:\Program Files\XM MetaTrader 5\terminal64.exe"),
        Path(r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe"),
    ]

    # Dynamic search in Program Files
    prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    if prog_files.exists():
        for mt_dir in prog_files.glob("*MetaTrader 5*"):
            t64 = mt_dir / "terminal64.exe"
            if t64.exists() and t64 not in candidates:
                candidates.append(t64)

    found = [p for p in candidates if p.exists()]

    if found:
        print(f"  {Colors.GREEN}[OK] Found {len(found)} MetaTrader 5 Terminal(s):{Colors.RESET}")
        for path in found:
            print(f"    • {Colors.CYAN}{path}{Colors.RESET}")
    else:
        print(f"  {Colors.YELLOW}[NOTICE] No standard 'terminal64.exe' found in default Program Files.{Colors.RESET}")
        print("           If MT5 is installed in a custom location, specify the path in 'bot_accounts_config.json'")
        print("           or simply have MetaTrader 5 open when launching the bot.")

    print()


def verify_installation(python_bin: Path, run_test_suite: bool = False) -> bool:
    """Verify required library imports and engine initialization."""
    print(f"{Colors.BOLD}[Step 6/6] Verifying System & DCC Engine Integrity...{Colors.RESET}")

    verify_script = """
import sys
results = []
try:
    import MetaTrader5 as mt5
    results.append(f"MetaTrader5: {mt5.__version__}")
except Exception as e:
    results.append(f"MetaTrader5: FAILED ({e})")

try:
    import pandas as pd
    results.append(f"pandas: {pd.__version__}")
except Exception as e:
    results.append(f"pandas: FAILED ({e})")

try:
    import numpy as np
    results.append(f"numpy: {np.__version__}")
except Exception as e:
    results.append(f"numpy: FAILED ({e})")

try:
    import matplotlib
    results.append(f"matplotlib: {matplotlib.__version__}")
except Exception as e:
    results.append(f"matplotlib: FAILED ({e})")

try:
    from dcc_engine import DCCEngine
    engine = DCCEngine("XAUUSD")
    results.append("DCCEngine: Initialized successfully")
except Exception as e:
    results.append(f"DCCEngine: FAILED ({e})")

print(" | ".join(results))
"""
    try:
        res = subprocess.run(
            [str(python_bin), "-c", verify_script],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"  {Colors.GREEN}[OK] Verification Output:{Colors.RESET}")
        for part in res.stdout.strip().split(" | "):
            status_color = Colors.GREEN if "FAILED" not in part else Colors.RED
            print(f"    • {status_color}{part}{Colors.RESET}")
        print()
    except subprocess.CalledProcessError as e:
        print(f"  {Colors.RED}[ERROR] Import verification failed:{Colors.RESET} {e.stderr or e.stdout}\n")
        return False

    if run_test_suite:
        print(f"  • Running DCC v1.2 Test Suite ({PROJECT_ROOT / 'v1.2' / 'test_v1_2_suite.py'})...")
        suite_path = PROJECT_ROOT / "v1.2" / "test_v1_2_suite.py"
        if suite_path.exists():
            test_proc = subprocess.run([str(python_bin), str(suite_path)])
            if test_proc.returncode == 0:
                print(f"  {Colors.GREEN}[OK] All test suite assertions passed!{Colors.RESET}\n")
            else:
                print(f"  {Colors.YELLOW}[WARN] Test suite exited with code {test_proc.returncode}.{Colors.RESET}\n")

    return True


def print_completion_summary(python_bin: Path, venv_name: str, use_venv: bool):
    """Print next steps and quick launch commands."""
    print(f"{Colors.GREEN}{Colors.BOLD}" + "=" * 78)
    print("                 SETUP COMPLETED SUCCESSFULLY! ")
    print("=" * 78 + f"{Colors.RESET}")
    print("\nYour DCC Institutional Trading Bot environment is configured and ready.\n")

    print(f"{Colors.BOLD}Quick Launch Commands:{Colors.RESET}")
    if use_venv and (PROJECT_ROOT / venv_name).exists():
        if sys.platform == "win32":
            activate_cmd = f"{venv_name}\\Scripts\\activate"
        else:
            activate_cmd = f"source {venv_name}/bin/activate"

        print(f"  1. Activate Virtual Environment:")
        print(f"     {Colors.CYAN}{activate_cmd}{Colors.RESET}")
        print(f"  2. Start Bot (Automatic Mode):")
        print(f"     {Colors.CYAN}python start_bot.py{Colors.RESET}  (or: {Colors.CYAN}{python_bin.name} live_bot.py --auto{Colors.RESET})")
        print(f"  3. Start Bot (Interactive Setup):")
        print(f"     {Colors.CYAN}python live_bot.py{Colors.RESET}")
    else:
        print(f"  • Start Bot (Automatic Mode):")
        print(f"     {Colors.CYAN}python start_bot.py{Colors.RESET}  (or: {Colors.CYAN}python live_bot.py --auto{Colors.RESET})")
        print(f"  • Start Bot (Interactive Setup):")
        print(f"     {Colors.CYAN}python live_bot.py{Colors.RESET}")

    print(f"\n{Colors.BOLD}Utilities:{Colors.RESET}")
    print(f"  • Run Diagnostics:   {Colors.CYAN}python tests/test_nightly_reconciler.py{Colors.RESET}")
    print(f"  • Launch Visualizer: {Colors.CYAN}python live_server.py{Colors.RESET}")
    print(f"  • Deploy to Vercel:  {Colors.CYAN}python deploy_visualizer.py{Colors.RESET}")
    print(f"\n{Colors.CYAN}" + "=" * 78 + f"{Colors.RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        description="DCC Institutional Trading Bot - VPS & Environment Setup Wizard"
    )
    parser.add_argument(
        "--auto", "-y", "--yes",
        action="store_true",
        help="Run non-interactively with standard defaults (recommended for automated VPS scripts)"
    )
    parser.add_argument(
        "--no-venv",
        action="store_true",
        help="Install directly into current Python environment without creating a virtualenv"
    )
    parser.add_argument(
        "--venv-name",
        default="venv",
        help="Name of virtual environment directory (default: 'venv')"
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip pip package installation (only set up configs and verify)"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run DCC test suite after setup"
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Start the trading bot (live_bot.py --auto) immediately after successful setup"
    )

    args = parser.parse_args()

    print_banner()

    # 1. Python & System Audit
    if not check_python_compatibility():
        sys.exit(1)

    # 2. Virtual Environment
    python_bin = setup_virtual_environment(
        venv_name=args.venv_name,
        use_venv=not args.no_venv,
        auto_mode=args.auto
    )

    # 3. Dependencies
    if not install_dependencies(python_bin, skip_install=args.skip_install):
        sys.exit(1)

    # 4. Configuration Files
    setup_configuration_files(auto_mode=args.auto)

    # 5. MetaTrader 5 Scanner
    scan_for_metatrader5()

    # 6. Verification
    if not verify_installation(python_bin, run_test_suite=args.test):
        sys.exit(1)

    # 7. Completion Summary
    print_completion_summary(
        python_bin=python_bin,
        venv_name=args.venv_name,
        use_venv=not args.no_venv
    )

    # Optional immediate launch
    if args.run:
        print(f"{Colors.BOLD}🚀 Launching DCC Trading Bot in Auto Mode...{Colors.RESET}\n")
        cmd = [str(python_bin), str(PROJECT_ROOT / "live_bot.py"), "--auto"]
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("\nBot stopped by user.")


if __name__ == "__main__":
    main()
