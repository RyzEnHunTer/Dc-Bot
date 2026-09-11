"""
Master System Orchestrator for DCC Institutional Trading Bot.
Single-command Python launcher that starts:
  1. Decoupled Live TradingView Chart Server (port 8085)
  2. Permanent Public Webpage Tunnel (calm-entrap-backfield.ngrok-free.dev)
  3. DCC Institutional Trading Engine (live_bot.py)

Handles automatic setup, VPS dependency verification, and clean process teardown.
Usage:
  python run_live_system.py                                  (Interactive Menu Mode)
  python run_live_system.py --auto                          (Auto Parity Mode)
  python run_live_system.py --auto --entry-mode bar_close --no-ema-gap-filter
  python run_live_system.py --no-tunnel                     (Local Browser Only)
"""

import os
import sys
import time
import shutil
import zipfile
import subprocess
import urllib.request
from typing import List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
PORT = 8085
PERMANENT_DOMAIN = "calm-entrap-backfield.ngrok-free.dev"
AUTHTOKEN = "3JAuWslWesEo1HJJPuYIvOI8mL4_61Lx9759CkK8zMC9BWoSf"
NGROK_EXE = os.path.join(BASE_DIR, "ngrok.exe")
NGROK_ZIP_URL = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"


def kill_existing_services():
    """Kills any dangling ngrok or chart server instances to prevent ERR_NGROK_334 and port conflicts."""
    try:
        import psutil
        curr_pid = os.getpid()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = proc.info.get('pid')
                if pid == curr_pid:
                    continue
                name = (proc.info.get('name') or '').lower()
                cmdline = " ".join(proc.info.get('cmdline') or []).lower()
                if 'ngrok' in name or ('live_server.py' in cmdline):
                    proc.kill()
            except Exception:
                pass
    except Exception:
        subprocess.run(["taskkill", "/F", "/IM", "ngrok.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_ngrok() -> str:
    """Ensures ngrok.exe is installed, up-to-date (v3.19+), and operational."""
    # 1. Local workspace executable
    if os.path.exists(NGROK_EXE):
        try:
            res = subprocess.run([NGROK_EXE, "version"], capture_output=True, text=True, timeout=5)
            ver_str = res.stdout.strip()
            # If older than 3.19, auto-update to latest
            if any(old_v in ver_str for old_v in [" 3.0.", " 3.1.", " 3.2.", " 3.3.", " 3.4.", " 3.5.", " 3.6.", " 3.7.", " 3.8.", " 3.9.", " 3.10.", " 3.11.", " 3.12.", " 3.13.", " 3.14.", " 3.15.", " 3.16.", " 3.17.", " 3.18."]):
                print(f"[*] Upgrading outdated ngrok agent ({ver_str})...")
                subprocess.run([NGROK_EXE, "update"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
            return NGROK_EXE
        except Exception:
            return NGROK_EXE

    # 2. System PATH
    found = shutil.which("ngrok")
    if found:
        return found

    # 3. Automatically download official release if on fresh VPS
    print("[*] Setting up ngrok.exe on VPS...")
    zip_path = os.path.join(BASE_DIR, "ngrok_temp.zip")
    try:
        urllib.request.urlretrieve(NGROK_ZIP_URL, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extract("ngrok.exe", BASE_DIR)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        try:
            subprocess.run([NGROK_EXE, "update"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        except Exception:
            pass
        print("[OK] ngrok.exe installed and updated successfully!")
        return NGROK_EXE
    except Exception as e:
        print(f"[!] Warning: Failed to download ngrok automatically ({e}). Continuing without tunnel.")
        return ""


def configure_authtoken(ngrok_bin: str):
    """Ensures user's permanent authtoken is registered."""
    try:
        subprocess.run(
            [ngrok_bin, "config", "add-authtoken", AUTHTOKEN],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
    except Exception as e:
        print(f"[!] Warning registering authtoken: {e}")


def set_process_priority(proc: subprocess.Popen, priority_str: str = "BELOW_NORMAL"):
    """Sets Windows process priority class to guarantee core bot execution is never starved."""
    if not proc or proc.poll() is not None:
        return
    try:
        import psutil
        p = psutil.Process(proc.pid)
        if priority_str == "BELOW_NORMAL":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        elif priority_str == "HIGH":
            p.nice(psutil.HIGH_PRIORITY_CLASS)
    except Exception:
        try:
            import ctypes
            val = 0x00004000 if priority_str == "BELOW_NORMAL" else 0x00000080
            handle = ctypes.windll.kernel32.OpenProcess(0x0200 | 0x0400, False, proc.pid)
            if handle:
                ctypes.windll.kernel32.SetPriorityClass(handle, val)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


def main():
    os.makedirs(LOGS_DIR, exist_ok=True)

    # Clean up any leftover background services from previous runs
    kill_existing_services()
    time.sleep(0.5)

    # Separate bot args from orchestrator args
    raw_args = sys.argv[1:]
    use_tunnel = True
    bot_args: List[str] = []

    for arg in raw_args:
        if arg == "--no-tunnel":
            use_tunnel = False
        else:
            bot_args.append(arg)

    server_proc = None
    tunnel_proc = None
    server_log = None
    tunnel_log = None

    try:
        # Step 1: Start Live Chart Server
        print("\n" + "=" * 80)
        print("  >>> STARTING DCC INSTITUTIONAL TRADING ENVIRONMENT <<<")
        print("=" * 80)
        print(f"[*] [1/3] Launching Live TradingView Chart Server on port {PORT} (Low CPU Priority)...")
        server_log = open(os.path.join(LOGS_DIR, "chart_server.log"), "w", encoding="utf-8")
        server_cmd = [sys.executable, os.path.join(BASE_DIR, "live_server.py"), "--port", str(PORT)]
        server_proc = subprocess.Popen(server_cmd, stdout=server_log, stderr=server_log)
        set_process_priority(server_proc, "BELOW_NORMAL")
        time.sleep(1.0)

        # Health check chart server
        if server_proc.poll() is not None:
            print("[ERROR] Live Chart Server failed to start! Details from logs/chart_server.log:")
            server_log.flush()
            if os.path.exists(os.path.join(LOGS_DIR, "chart_server.log")):
                with open(os.path.join(LOGS_DIR, "chart_server.log"), "r", encoding="utf-8") as f:
                    print(f.read())
            sys.exit(1)

        # Step 2: Start Permanent Web Tunnel (if enabled)
        tunnel_active = False
        if use_tunnel:
            ngrok_bin = ensure_ngrok()
            if ngrok_bin:
                configure_authtoken(ngrok_bin)
                print(f"[*] [2/3] Launching Permanent Web Tunnel -> https://{PERMANENT_DOMAIN} (Low CPU Priority)...")
                tunnel_log = open(os.path.join(LOGS_DIR, "tunnel.log"), "w", encoding="utf-8")
                tunnel_cmd = [ngrok_bin, "http", f"--url={PERMANENT_DOMAIN}", str(PORT)]
                tunnel_proc = subprocess.Popen(tunnel_cmd, stdout=tunnel_log, stderr=tunnel_log)
                set_process_priority(tunnel_proc, "BELOW_NORMAL")
                time.sleep(2.0)

                # Health check tunnel
                if tunnel_proc.poll() is not None:
                    tunnel_log.flush()
                    print(f"[!] Warning: Web tunnel did not start. Details from logs/tunnel.log:")
                    if os.path.exists(os.path.join(LOGS_DIR, "tunnel.log")):
                        with open(os.path.join(LOGS_DIR, "tunnel.log"), "r", encoding="utf-8") as f:
                            err_content = f.read().strip()
                            print(err_content if err_content else "Process exited unexpectedly.")
                    print("[!] Continuing in Local Browser mode only.")
                else:
                    tunnel_active = True
            else:
                print("[!] Skipping tunnel (ngrok executable not available).")
        else:
            print("[*] [2/3] Tunnel disabled (--no-tunnel flag detected).")

        # Step 3: Print Ready Status
        print("\n" + "*" * 80)
        print("  >>> LIVE MONITORING SYSTEM READY <<<")
        print("  * Local Browser:    http://127.0.0.1:" + str(PORT))
        if tunnel_active:
            print(f"  * Permanent Web:    https://{PERMANENT_DOMAIN}")
        print("*" * 80 + "\n")

        # Step 4: Execute DCC Live Bot in the foreground
        print(f"[*] [3/3] Launching DCC Institutional Bot Core...")
        bot_cmd = [sys.executable, os.path.join(BASE_DIR, "live_bot.py")] + bot_args
        subprocess.run(bot_cmd)

    except KeyboardInterrupt:
        print("\n\n[!] Shutdown signal received (Ctrl+C). Closing trading services...")
    except Exception as err:
        print(f"\n[ERROR] Unexpected error in system launcher: {err}")
    finally:
        # Graceful cleanup of background subprocesses
        print("[*] Shutting down background services...")
        if tunnel_proc and tunnel_proc.poll() is None:
            try:
                tunnel_proc.terminate()
                tunnel_proc.wait(timeout=2.0)
            except Exception:
                tunnel_proc.kill()

        if server_proc and server_proc.poll() is None:
            try:
                server_proc.terminate()
                server_proc.wait(timeout=2.0)
            except Exception:
                server_proc.kill()

        if server_log:
            server_log.close()
        if tunnel_log:
            tunnel_log.close()

        print("[OK] Background chart server and web tunnel stopped cleanly. Goodbye!\n")


if __name__ == "__main__":
    main()
