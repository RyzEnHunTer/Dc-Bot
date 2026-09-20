#!/usr/bin/env python3
"""
===============================================================================
        DCC INSTITUTIONAL STRATEGY - CLOUD VISUALIZER DEPLOYER
===============================================================================
Deploys ONLY the static backtest website (HTML + Data + Lightweight Charts)
to the cloud so anyone can view it 24/7 without you running any local server.

NOTE: Your trading bot code, MT5 engines, and private account configs remain
      100% PRIVATE and are NEVER uploaded.
===============================================================================
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# Safe encoding for Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Paths
BASE_DIR = Path(__file__).resolve().parent
VISUALIZER_DIR = BASE_DIR / "visualizer"
INDEX_HTML = VISUALIZER_DIR / "index.html"
DATA_JSON = VISUALIZER_DIR / "data.json"
CHARTS_JS = VISUALIZER_DIR / "lightweight-charts.standalone.production.js"

def print_banner():
    print("\n" + "=" * 78)
    print("      DCC INSTITUTIONAL VISUALIZER - CLOUD DEPLOYMENT WIZARD")
    print("=" * 78)
    print("  Host your TradingView Backtest Website online 24/7 (Free Cloud Hosting)")
    print("  Safe & Isolated: ONLY the static visualizer is deployed.")
    print("  All bot code, Python engines, and account configs remain 100% local.")
    print("=" * 78 + "\n")

def check_files():
    """Verify that all essential website files exist."""
    print("[*] [1/3] Checking visualizer files...")
    
    if not VISUALIZER_DIR.exists():
        print(f"[!] Error: Visualizer directory not found at: {VISUALIZER_DIR}")
        return False
        
    missing = []
    for path, name in [
        (INDEX_HTML, "index.html (UI & Access Control)"),
        (DATA_JSON, "data.json (Backtest Trades & Candles)"),
        (CHARTS_JS, "lightweight-charts.standalone.production.js (Charting Engine)")
    ]:
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"   [OK] {name} [{size_mb:.2f} MB]")
        else:
            print(f"   [X] Missing: {name}")
            missing.append(name)
            
    if missing:
        print(f"\n[!] Error: Cannot deploy. Missing files: {', '.join(missing)}")
        return False
        
    print("   [OK] All website assets verified.\n")
    return True

def find_npx():
    """Locate npx executable on Windows / Unix."""
    npx = shutil.which("npx")
    if npx:
        return npx
        
    candidates = [
        Path(r"D:\nodejs\npx.cmd"),
        Path(r"D:\nodejs\npx"),
        Path(r"C:\Program Files\nodejs\npx.cmd"),
        Path(r"C:\Program Files\nodejs\npx"),
        Path(os.environ.get("APPDATA", "")) / "npm" / "npx.cmd"
    ]
    for c in candidates:
        if c.exists():
            return str(c)
            
    return None

def is_vercel_logged_in(npx_cmd):
    """Check if the user is already authenticated with Vercel."""
    try:
        res = subprocess.run(
            [npx_cmd, "-y", "vercel", "whoami"],
            capture_output=True,
            text=True,
            shell=True
        )
        return res.returncode == 0
    except Exception:
        return False

def login_vercel(npx_cmd):
    """Run interactive Vercel login."""
    print("-" * 78)
    print("🔐 VERCEL AUTHENTICATION REQUIRED")
    print("   A login prompt will now start. Select 'Continue with GitHub' or 'Email'.")
    print("   Your browser will open automatically to complete the verification.")
    print("-" * 78 + "\n")
    
    cmd = f'"{npx_cmd}" -y vercel login'
    return subprocess.call(cmd, cwd=str(VISUALIZER_DIR), shell=True) == 0

def deploy_to_vercel(temporary=False):
    """Deploy the visualizer directory directly to Vercel."""
    print("[*] [2/3] Preparing Vercel Cloud Deployment...")
    
    npx_cmd = find_npx()
    if not npx_cmd:
        print("[!] Error: Node.js / npx was not found on your system.")
        print("    Please install Node.js from https://nodejs.org or use the GitHub method.")
        return False

    print(f"   [OK] Found npx at: {npx_cmd}")
    print(f"   [OK] Target deployment directory: {VISUALIZER_DIR}")

    # Check login status if not temporary
    if not temporary:
        if not is_vercel_logged_in(npx_cmd):
            print("\n[!] No active Vercel session detected. Initiating one-time login...")
            if not login_vercel(npx_cmd):
                print("\n[!] Login was cancelled or failed.")
                print("    Tip: You can select option [2] to deploy without an account (temporary)!")
                return False
            print("\n[OK] Successfully authenticated with Vercel!\n")

    print("\n" + "-" * 78)
    print("🚀 Starting Vercel Deployment...")
    print("   • When asked: 'Set up and deploy?', press Enter (Y).")
    print("   • When asked: 'Which scope?', press Enter.")
    print("   • When asked for project name, press Enter or type: dcc-visualizer")
    print("-" * 78 + "\n")

    # Command to run inside VISUALIZER_DIR
    if temporary:
        cmd = f'"{npx_cmd}" -y vercel deploy --temporary'
    else:
        cmd = f'"{npx_cmd}" -y vercel --prod'
    
    try:
        returncode = subprocess.call(cmd, cwd=str(VISUALIZER_DIR), shell=True)
        
        print("\n" + "=" * 78)
        if returncode == 0:
            print("[SUCCESS] Your visualizer is now live on Vercel's global cloud network!")
            print("Anyone in the world can open the link 24/7 with zero hosting on your PC.")
            print("\nSharing Links:")
            print(" - Public View (Restricted): https://<your-project>.vercel.app")
            print(" - VIP Member View:          https://<your-project>.vercel.app?key=DCC2026")
        else:
            print(f"[!] Vercel process exited with code {returncode}.")
            print("    If you prefer, you can also deploy via GitHub: https://vercel.com")
        print("=" * 78 + "\n")
        return returncode == 0
    except Exception as e:
        print(f"\n[!] Execution error: {e}")
        return False

def show_render_instructions():
    """Print step-by-step instructions for Render."""
    print("\n" + "=" * 78)
    print("            HOW TO HOST ON RENDER (100% FREE STATIC SITE)")
    print("=" * 78)
    print("""
1. Log in to your Render dashboard: https://dashboard.render.com
2. Click the blue 'New +' button in the top right -> Choose 'Static Site'.
3. Connect your GitHub repository: 'RyzEnHunTer/Dc-Bot'
4. In the settings, configure:
     * Name:              dcc-visualizer (or any name you prefer)
     * Branch:            main
     * Root Directory:    visualizer   <-- (* CRITICAL: Only hosts this folder!)
     * Build Command:     (leave completely blank)
     * Publish Directory: .            (or leave blank)
5. Click 'Create Static Site'.

Render will deploy the website in ~30 seconds and give you a permanent URL:
  https://dcc-visualizer.onrender.com
""")
    print("=" * 78 + "\n")

def run_local_preview():
    """Run local preview server."""
    import webbrowser
    import http.server
    import socketserver
    
    PORT = 8088
    os.chdir(str(BASE_DIR))
    
    print(f"\n[*] Starting local preview at http://localhost:{PORT}/visualizer/index.html ...")
    webbrowser.open(f"http://localhost:{PORT}/visualizer/index.html")
    print("Press Ctrl+C to stop local preview.\n")
    
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nLocal preview stopped.")

def main():
    print_banner()
    
    if not check_files():
        sys.exit(1)
        
    # Check CLI arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("--vercel", "-v", "--prod"):
            deploy_to_vercel(temporary=False)
            return
        elif arg in ("--temp", "--temporary"):
            deploy_to_vercel(temporary=True)
            return
        elif arg in ("--render", "-r"):
            show_render_instructions()
            return
        elif arg in ("--preview", "-p"):
            run_local_preview()
            return
        elif arg in ("--help", "-h"):
            print("Usage: python deploy_visualizer.py [--vercel | --temp | --render | --preview]")
            return

    # Interactive Menu
    print("Please choose your preferred deployment option:")
    print("  [1] Deploy to Vercel Cloud (Permanent Production Link)")
    print("  [2] Deploy to Vercel Instant (Temporary link - No login required)")
    print("  [3] View instructions for Render Static Site (via GitHub)")
    print("  [4] Test / Preview locally in browser (port 8088)")
    print("  [5] Exit")
    print()
    
    try:
        choice = input("Enter choice (1-5) [default: 1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nExiting.")
        sys.exit(0)
        
    if choice in ("", "1"):
        deploy_to_vercel(temporary=False)
    elif choice == "2":
        deploy_to_vercel(temporary=True)
    elif choice == "3":
        show_render_instructions()
    elif choice == "4":
        run_local_preview()
    elif choice == "5":
        print("Goodbye.")
        sys.exit(0)
    else:
        print("Invalid choice. Running default Vercel deployment...")
        deploy_to_vercel(temporary=False)

if __name__ == "__main__":
    main()
