"""
DCC Strategy Institutional Live & Demo MT5 Trading Bot
Architecture:
1. Dual-Asset Monitoring: XAUUSD (Gold) & NAS100 (Nasdaq) concurrently
2. Max 1 active trade per asset (Max 2 total portfolio positions)
3. Dead-Hour Protection: Skips 09:00 and 13:00 UTC trap hours
4. Pre-Armed Detection: Detects setup on Candle [t-1] (5 mins before entry)
5. Zero-Latency Tick Monitoring: Streams ticks during the final 2 minutes of Candle [t]
6. Zero-Allocation Float Math: No pandas inside tick loops (sub-microsecond updates)
7. Robust Tick & Spread Sanitizer: Defends against bad ticks, None values, and spread spikes
8. Twin-Ticket Architecture: Simultaneous Ticket A (50% TP1) + Ticket B (50% Runner)
9. Breakeven Automator: Moves Runner SL to (Entry + Spread) the millisecond Ticket A fills
10. Broker Filling Mode Auto-Negotiation: Dynamically chooses FOK/IOC based on symbol specs
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
import json
import os
import queue
import sys
import threading
import time as pytime
from typing import Any, Dict, List, Optional, Tuple, Union

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from dcc_engine import DCCEngine, SignalType, TradeSignal
from mt5_data import MT5DataProvider
from news_engine import NewsEngine
from notifier import NotificationManager
from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich import box

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(width=78, force_terminal=True, legacy_windows=False)


@dataclass
class SymbolConfig:
    symbol: str
    tp1_rr: float
    tp2_rr: float
    atr_sl_multiplier: float
    adx_min: float
    max_allowed_spread: float
    magic_number: int


CONFIGS: Dict[str, SymbolConfig] = {
    "XAUUSD": SymbolConfig(
        symbol="XAUUSD",
        tp1_rr=1.4,
        tp2_rr=2.2,
        atr_sl_multiplier=0.9,
        adx_min=15.0,
        max_allowed_spread=0.65,  # Max 65 cents spread on Gold
        magic_number=20260901,
    ),
    "NAS100": SymbolConfig(
        symbol="NAS100",
        tp1_rr=1.5,
        tp2_rr=2.0,
        atr_sl_multiplier=1.0,
        adx_min=15.0,
        max_allowed_spread=7.0,   # Max 7.0 pts spread on Nasdaq (accommodates 5.1-6.5 broker spread variations)
        magic_number=20260902,
    )
}


@dataclass
class PreArmedState:
    is_armed: bool = False
    direction: int = 0          # 1 = BUY, -1 = SELL
    armed_bar_time: Optional[datetime] = None
    target_close_time: Optional[datetime] = None
    prev_ema9: float = 0.0
    prev_ema20: float = 0.0
    h1_bias: int = 0
    h1_e20: float = 0.0
    h1_adx: float = 0.0
    h1_atr: float = 0.0
    vwap_5m: float = 0.0
    sl_distance: float = 0.0
    tp1_distance: float = 0.0
    tp2_distance: float = 0.0
    # Pre-calculated order specs
    projected_lots: float = 0.0
    partial_lots: float = 0.0
    runner_lots: float = 0.0

    @property
    def direction_str(self) -> str:
        return "BUY" if self.direction == 1 else "SELL" if self.direction == -1 else "ARMED"

    @property
    def signal(self):
        return self

    @property
    def signal_type(self):
        class _SignalTypeProxy:
            def __init__(self, val):
                self.value = val
        return _SignalTypeProxy(self.direction_str)


@dataclass
class ActiveTwinPosition:
    symbol: str
    direction: str
    entry_price: float
    entry_spread: float
    entry_time: datetime
    ticket_a: int               # 50% TP1
    ticket_b: int               # 50% Runner
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    lots_a: float = 0.0
    lots_b: float = 0.0
    be_sl: float = 0.0
    ticket_a_closed: bool = False
    runner_moved_to_be: bool = False
    pnl_a: float = 0.0
    pnl_b: float = 0.0


class MarketAuditLogger:
    """
    Forensic Market Calculation Logger.
    Records every 5M candle close calculation for XAUUSD & NAS100:
    - OHLC, 5M EMA9, 5M EMA20, 5M VWAP, EMA Gap & Compression Ratio
    - 1H Bias (+1/-1/0), 1H EMA9, 1H EMA20, 1H ADX, 1H ATR
    - 5M Swing High/Low, 2H Swing High/Low
    - 5M Liquidity Sweep Confluence (Swept level, pts swept, status)
    - Arming Decision & Exact Rejection Reason
    - Planned Order Parameters (Lots, SL, TP1, TP2)
    Saves to:
    1. logs/market_calculations_YYYYMMDD.csv (structured data for 1-to-1 backtest comparison)
    2. logs/market_surveillance_YYYYMMDD.log (human-readable event stream)
    """
    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.log_dir = os.path.join(base_dir, "logs")
        else:
            self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.csv_headers = [
            "timestamp_utc", "timestamp_ist", "symbol",
            "open", "high", "low", "close", "volume",
            "ema9_5m", "ema20_5m", "ema_gap", "ema_gap_ratio_atr", "vwap_5m",
            "bias_1h", "ema9_1h", "ema20_1h", "adx_1h", "atr_1h", "h1_ema20_level",
            "swing_low_5m", "swing_high_5m", "swing_low_2h", "swing_high_2h",
            "sweep_detected", "sweep_level", "swept_pts",
            "decision", "reason",
            "planned_entry", "planned_sl", "planned_tp1", "planned_tp2", "planned_lots"
        ]

    def log_candle(self, data: Dict):
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        csv_path = os.path.join(self.log_dir, f"market_calculations_{date_str}.csv")
        txt_path = os.path.join(self.log_dir, f"market_surveillance_{date_str}.log")

        file_exists = os.path.exists(csv_path)
        try:
            with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.csv_headers)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(data)
        except Exception as e:
            pass

        try:
            log_line = (
                f"[{data.get('timestamp_utc', '')} UTC | {data.get('timestamp_ist', '')} IST] "
                f"{data.get('symbol', '')} | C: {data.get('close', 0.0):.2f} | "
                f"EMA9: {data.get('ema9_5m', 0.0):.2f} | EMA20: {data.get('ema20_5m', 0.0):.2f} | "
                f"Gap: {data.get('ema_gap', 0.0):.2f} | Bias: {data.get('bias_1h', 0)} | "
                f"ADX: {data.get('adx_1h', 0.0):.1f} | ATR: {data.get('atr_1h', 0.0):.2f} | "
                f"SwL_5M: {data.get('swing_low_5m', 0.0):.2f} | SwH_5M: {data.get('swing_high_5m', 0.0):.2f} | "
                f"Sweep: {data.get('sweep_detected', False)} | Status: {data.get('decision', '')} "
                f"({data.get('reason', '')})\n"
            )
            with open(txt_path, mode="a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass


CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_accounts_config.json")


def prompt_circuit_breakers(daily_dd: float, max_dd: float, default_auto: bool = True) -> Tuple[float, float]:
    """
    Configures Circuit Breakers with either:
    1. Auto-configure (-1.0% safety cushion before hard DD limits)
    2. Custom manual entry with strict validation: Circuit Breaker MUST be strictly less than Hard DD Limit.
    """
    auto_daily_cb = round(max(0.1, daily_dd - 1.0), 2)
    auto_max_cb = round(max(0.1, max_dd - 1.0), 2)

    print("\n--- CIRCUIT BREAKER SAFETY CONFIGURATION ---")
    print(f"Hard Account Drawdown Limits: Daily DD: {daily_dd:.1f}% | Max Total DD: {max_dd:.1f}% (Broker / Prop Firm Rule)")
    print("The Circuit Breaker halts the bot BEFORE reaching your hard limits to protect your capital from breaching.")
    print(f"  [1] Auto-configure (-1.0% safety cushion) -> Daily CB: {auto_daily_cb:.1f}%, Max CB: {auto_max_cb:.1f}% (Recommended)")
    print("  [2] Enter manual Circuit Breaker values (Custom)")

    choice = input("Select option [1-2] (Press Enter for [1]): ").strip()
    if not choice or choice == "1":
        print(f"\n[AUTO-CONFIGURED] Circuit Breakers set to: Daily CB: {auto_daily_cb:.1f}% | Max CB: {auto_max_cb:.1f}% (-1.0% safety buffer before hard limits)")
        return auto_daily_cb, auto_max_cb

    # Manual Input with Strict Validation (CB MUST be strictly less than DD limit!)
    while True:
        d_cb_in = input(f"Enter Daily Circuit Breaker % [Must be < {daily_dd:.1f}%, Default: {auto_daily_cb:.1f}%]: ").strip()
        if not d_cb_in:
            chosen_daily_cb = auto_daily_cb
            break
        try:
            val = float(d_cb_in.rstrip('%').strip())
            if val <= 0:
                print("[ERROR] Daily Circuit Breaker must be greater than 0%. Please try again.")
                continue
            if val >= daily_dd:
                print(f"[ERROR] Invalid! Circuit Breaker ({val:.1f}%) cannot be greater than or equal to Daily DD Limit ({daily_dd:.1f}%). Circuit Breaker must be strictly less than the hard limit to protect it! Please try again.")
                continue
            chosen_daily_cb = round(val, 2)
            break
        except ValueError:
            print("[ERROR] Please enter a valid numeric percentage.")

    while True:
        m_cb_in = input(f"Enter Max Total Circuit Breaker % [Must be < {max_dd:.1f}%, Default: {auto_max_cb:.1f}%]: ").strip()
        if not m_cb_in:
            chosen_max_cb = auto_max_cb
            break
        try:
            val = float(m_cb_in.rstrip('%').strip())
            if val <= 0:
                print("[ERROR] Max Circuit Breaker must be greater than 0%. Please try again.")
                continue
            if val >= max_dd:
                print(f"[ERROR] Invalid! Circuit Breaker ({val:.1f}%) cannot be greater than or equal to Max Total DD Limit ({max_dd:.1f}%). Circuit Breaker must be strictly less than the hard limit to protect it! Please try again.")
                continue
            chosen_max_cb = round(val, 2)
            break
        except ValueError:
            print("[ERROR] Please enter a valid numeric percentage.")

    print(f"\n[CONFIGURED] Circuit Breakers set to: Daily CB: {chosen_daily_cb:.1f}% | Max CB: {chosen_max_cb:.1f}%")
    return chosen_daily_cb, chosen_max_cb


def get_current_trading_day_start_utc(dt: datetime) -> datetime:
    """Returns 00:00:00 UTC of the active trading day. On Saturday (w=5) and Sunday (w=6),
    persists Friday's 00:00:00 UTC so Friday's deals, PnL, and CB status persist until Monday 00:00 UTC."""
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    w = dt.weekday()
    if w == 5:
        return day_start - timedelta(days=1)
    elif w == 6:
        return day_start - timedelta(days=2)
    return day_start


def get_daily_starting_equity(balance: float) -> float:
    """Calculates the active trading day starting equity anchored to balance minus closed deals today.
    On Saturday and Sunday, rolls back to Friday to persist starting equity and drawdown calculations.
    Falls back to balance if MT5 is unavailable or history cannot be read."""
    try:
        now_utc = datetime.now(timezone.utc)
        active_day_start_utc = get_current_trading_day_start_utc(now_utc)
        start_ts = int(active_day_start_utc.timestamp())
        end_ts = int(pytime.time() + 86400)
        today_deals = mt5.history_deals_get(start_ts, end_ts)
        closed_pnl_today = 0.0
        if today_deals:
            for d in today_deals:
                if getattr(d, "entry", None) == 1:
                    closed_pnl_today += float(getattr(d, "profit", 0.0) + getattr(d, "commission", 0.0) + getattr(d, "swap", 0.0))
        return max(0.01, float(balance) - closed_pnl_today)
    except Exception:
        return balance


class AccountConfigManager:
    """Persistent storage for per-account Risk, Daily DD, Max DD, Circuit Breakers, and High-Water Mark."""
    def __init__(self, config_path: str = CONFIG_FILE_PATH):
        self.config_path = config_path
        self.accounts: Dict[str, Dict] = self._load()

    def _load(self) -> Dict[str, Dict]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] Failed to load config: {e}. Starting fresh.")
        return {}

    def save(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.accounts, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Could not save config to {self.config_path}: {e}")

    def get_or_setup_account(self, account_info, auto_defaults: bool = False) -> Dict:
        acc_id = str(account_info.login)
        if acc_id in self.accounts:
            cfg = self.accounts[acc_id]
            needs_save = False
            if "notifications" not in cfg:
                cfg["notifications"] = {
                    "active_platform": "none",
                    "telegram_bot_token": "",
                    "telegram_chat_id": "",
                    "discord_webhook_url": ""
                }
                needs_save = True
            if "use_liquidity_sweep" not in cfg:
                cfg["use_liquidity_sweep"] = True
                needs_save = True
            if "use_news_shield" not in cfg:
                cfg["use_news_shield"] = True
                needs_save = True
            if "use_ema_gap_filter" in cfg:
                del cfg["use_ema_gap_filter"]
                needs_save = True
            if "entry_mode" not in cfg:
                cfg["entry_mode"] = "bar_close"
                needs_save = True
            if "strategy_version" not in cfg:
                cfg["strategy_version"] = "v1.2"
                needs_save = True
            if "daily_cb_pct" not in cfg:
                cfg["daily_cb_pct"] = round(max(0.1, cfg.get("daily_dd_limit_pct", 4.0) - 1.0), 2)
                needs_save = True
            if "max_cb_pct" not in cfg:
                cfg["max_cb_pct"] = round(max(0.1, cfg.get("max_total_dd_pct", 8.0) - 1.0), 2)
                needs_save = True
            # Lifecycle migration for existing accounts
            if "account_lifecycle" not in cfg:
                cfg["account_lifecycle"] = "challenge"
                needs_save = True
            if "challenge_steps" not in cfg:
                cfg["challenge_steps"] = 2
                needs_save = True
            if "current_phase" not in cfg:
                cfg["current_phase"] = 1
                needs_save = True
            if "phase_1_target_pct" not in cfg:
                cfg["phase_1_target_pct"] = 8.0
                needs_save = True
            if "phase_2_target_pct" not in cfg:
                cfg["phase_2_target_pct"] = 5.0
                needs_save = True
            if "phase_start_balance" not in cfg:
                cfg["phase_start_balance"] = round(float(account_info.balance), 2)
                needs_save = True
            if "challenge_risk_pct" not in cfg:
                cfg["challenge_risk_pct"] = 1.30
                needs_save = True
            if "funded_risk_pct" not in cfg:
                cfg["funded_risk_pct"] = 1.0
                needs_save = True
            if "use_custom_phase_risk" not in cfg:
                cfg["use_custom_phase_risk"] = False
                needs_save = True
            # Dynamic sync of active risk per trade
            if cfg.get("use_custom_phase_risk", False):
                active_risk_pct = cfg.get("challenge_risk_pct", 1.25) if cfg.get("account_lifecycle") == "challenge" else cfg.get("funded_risk_pct", 1.0)
            else:
                active_risk_pct = 1.00
            expected_risk_dec = round(active_risk_pct / 100.0, 4)
            if cfg.get("risk_per_trade") != expected_risk_dec:
                cfg["risk_per_trade"] = expected_risk_dec
                needs_save = True

            # Track peak high-water mark
            if account_info.equity > cfg.get("high_water_mark", 0.0):
                cfg["high_water_mark"] = round(float(account_info.equity), 2)
                needs_save = True
            if needs_save:
                self.save()
            return cfg

        # NEW ACCOUNT DETECTED!
        print("\n" + "=" * 80)
        print(f"  >>> [NEW MT5 ACCOUNT DETECTED: {acc_id} ({account_info.server})] <<<")
        print("=" * 80)
        print(f"Equity: ${account_info.equity:,.2f} | Balance: ${account_info.balance:,.2f} | Leverage: 1:{account_info.leverage}")
        print("Let's configure the Account Lifecycle, Targets, Risk, and Circuit Breakers:\n")

        if auto_defaults:
            lifecycle = "challenge"
            ch_steps = 2
            curr_phase = 1
            p1_target = 8.0
            p2_target = 5.0
            ch_risk = 1.25
            funded_risk = 1.0
            start_bal = round(float(account_info.balance), 2)
            daily_dd = 4.0
            daily_cb = 3.0
            max_dd = 8.0
            max_cb = 7.0
            use_custom_phase_risk = False
            print(f"  [Auto-Assigned Lifecycle] Mode: 2-Step Challenge (Phase 1: {p1_target}%, Phase 2: {p2_target}%)")
            print(f"  [Auto-Assigned Risk] Challenge Risk: {ch_risk}% (Optimal Speed) | Funded Risk: {funded_risk}%")
            print(f"  [Auto-Assigned Defaults] Daily DD Limit: {daily_dd}%, Max DD Limit: {max_dd}%")
            print(f"  [Auto-Configured Circuit Breakers] Daily CB: {daily_cb}% | Max CB: {max_cb}% (-1.0% safety cushion before hard limits)")
        else:
            print("Select Account Lifecycle Type:")
            print("  [1] Prop Firm Challenge / Evaluation (Default)")
            print("  [2] Live Funded / Personal Account")
            l_choice = input("Enter choice [1-2] (Press Enter for [1]): ").strip()
            if l_choice == "2":
                lifecycle = "funded"
                ch_steps = 1
                curr_phase = "funded"
                p1_target = 0.0
                p2_target = 0.0
                ch_risk = 1.25
                start_bal = round(float(account_info.balance), 2)
                f_risk_in = input("Enter Funded Risk Per Trade % [Default 1.0%]: ").strip().rstrip('%').strip()
                funded_risk = float(f_risk_in) if f_risk_in else 1.0
            else:
                lifecycle = "challenge"
                print("\nSelect Challenge Steps:")
                print("  [1] 1-Step Challenge (Single Target)")
                print("  [2] 2-Step Challenge (Phase 1 + Phase 2) (Default)")
                step_choice = input("Enter choice [1-2] (Press Enter for [2]): ").strip()
                if step_choice == "1":
                    ch_steps = 1
                    curr_phase = 1
                    p1_in = input("Enter Evaluation Target % [Default 10.0%]: ").strip().rstrip('%').strip()
                    p1_target = float(p1_in) if p1_in else 10.0
                    p2_target = 0.0
                    print(f"  -> 1-Step Target: +{p1_target:.1f}%")
                else:
                    ch_steps = 2
                    curr_phase = 1
                    tot_in = input("Enter Overall Evaluation Target % (Phase 1 + Phase 2 combined) [Default 13.0%]: ").strip().rstrip('%').strip()
                    overall_target = float(tot_in) if tot_in else 13.0
                    p1_in = input("Enter Phase 1 Target % [Default 8.0%]: ").strip().rstrip('%').strip()
                    p1_target = float(p1_in) if p1_in else 8.0
                    calc_p2 = max(0.0, round(overall_target - p1_target, 2))
                    p2_in = input(f"Enter Phase 2 Target % [Default {calc_p2:.1f}% based on overall {overall_target:.1f}%]: ").strip().rstrip('%').strip()
                    p2_target = float(p2_in) if p2_in else calc_p2
                    total_target = p1_target + p2_target
                    print(f"  -> 2-Step Configuration: Overall Target = +{total_target:.1f}% | Phase 1 = +{p1_target:.1f}%, Phase 2 = +{p2_target:.1f}%")

                start_bal = round(float(account_info.balance), 2)
                c_risk_in = input("Enter Challenge Risk Per Trade % [Default 1.25% - Optimal Speed]: ").strip().rstrip('%').strip()
                ch_risk = float(c_risk_in) if c_risk_in else 1.25
                f_risk_in = input("Enter Funded Risk Per Trade % [Default 1.0%]: ").strip().rstrip('%').strip()
                funded_risk = float(f_risk_in) if f_risk_in else 1.0

            daily_in = input("Enter Daily Drawdown Hard Limit % (Prop firm / Broker max) [Default 4.0%]: ").strip().rstrip('%').strip()
            daily_dd = float(daily_in) if daily_in else 4.0
            max_in = input("Enter Maximum Total Drawdown Hard Limit % (Prop firm / Broker max) [Default 8.0%]: ").strip().rstrip('%').strip()
            max_dd = float(max_in) if max_in else 8.0
            daily_cb, max_cb = prompt_circuit_breakers(daily_dd, max_dd)
            custom_risk_choice = input("Enable Custom Phase-Specific Risk Scaling? (y/N) [Default 'n' -> 1.00% fixed risk]: ").strip().lower()
            use_custom_phase_risk = (custom_risk_choice in ["y", "yes"])

        active_risk_pct = (ch_risk if lifecycle == "challenge" else funded_risk) if use_custom_phase_risk else 1.00

        cfg = {
            "login": int(account_info.login),
            "server": str(account_info.server),
            "currency": str(account_info.currency),
            "account_lifecycle": lifecycle,
            "challenge_steps": ch_steps,
            "current_phase": curr_phase,
            "phase_1_target_pct": round(p1_target, 2),
            "phase_2_target_pct": round(p2_target, 2),
            "phase_start_balance": round(start_bal, 2),
            "challenge_risk_pct": round(ch_risk, 2),
            "funded_risk_pct": round(funded_risk, 2),
            "use_custom_phase_risk": use_custom_phase_risk,
            "risk_per_trade": round(active_risk_pct / 100.0, 4),
            "daily_dd_limit_pct": round(daily_dd, 2),
            "daily_cb_pct": round(daily_cb, 2),
            "max_total_dd_pct": round(max_dd, 2),
            "max_cb_pct": round(max_cb, 2),
            "symbols": ["XAUUSD", "NAS100"],
            "high_water_mark": round(float(account_info.equity), 2),
            "use_liquidity_sweep": True,
            "use_news_shield": True,
            "entry_mode": "bar_close",
            "strategy_version": "v1.2",
            "notifications": {
                "active_platform": "none",
                "telegram_bot_token": "",
                "telegram_chat_id": "",
                "discord_webhook_url": ""
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        self.accounts[acc_id] = cfg
        self.save()
        print(f"\n[SAVED] Settings permanently remembered for Account {acc_id} in bot_accounts_config.json!\n")
        return cfg

    def advance_account_phase(self, acc_id: str, new_phase: Any, new_start_bal: Optional[float] = None, notifier: Optional[Any] = None):
        """Advances account from Phase 1 -> Phase 2 or to Funded, automatically syncing active risk."""
        if acc_id not in self.accounts:
            return
        cfg = self.accounts[acc_id]
        cfg["target_locked"] = False
        use_custom = cfg.get("use_custom_phase_risk", False)
        phase_name = ""
        target_pct = 0.0
        start_bal = cfg.get("phase_start_balance", 0.0)

        if str(new_phase).lower() in ["funded", "3"]:
            cfg["account_lifecycle"] = "funded"
            cfg["current_phase"] = "funded"
            active_r = cfg.get("funded_risk_pct", 1.0) if use_custom else 1.00
            cfg["risk_per_trade"] = round(active_r / 100.0, 4)
            phase_name = "Funded Mode"
            target_pct = 0.0
            print(f"\n[PHASE UPDATE] Account {acc_id} transitioned to FUNDED MODE!")
            print(f"  * Active Risk: {active_r:.2f}% (Custom Phase Risk: {'ENABLED' if use_custom else 'OFF - using default 1.00%'})\n")
        elif str(new_phase) == "2":
            cfg["account_lifecycle"] = "challenge"
            cfg["current_phase"] = 2
            if new_start_bal is not None:
                cfg["phase_start_balance"] = round(float(new_start_bal), 2)
            start_bal = cfg.get("phase_start_balance", 0.0)
            active_r = cfg.get("challenge_risk_pct", 1.30) if use_custom else 1.00
            cfg["risk_per_trade"] = round(active_r / 100.0, 4)
            phase_name = "Challenge Phase 2"
            target_pct = cfg.get("phase_2_target_pct", 5.0)
            print(f"\n[PHASE UPDATE] Account {acc_id} advanced to CHALLENGE PHASE 2!")
            print(f"  * Phase 2 Start Balance: ${cfg['phase_start_balance']:,.2f}")
            print(f"  * Phase 2 Target:        +{cfg.get('phase_2_target_pct', 5.0):.1f}%")
            print(f"  * Active Risk:           {active_r:.2f}% (Custom Phase Risk: {'ENABLED' if use_custom else 'OFF - using default 1.00%'})\n")
        elif str(new_phase) == "1":
            cfg["account_lifecycle"] = "challenge"
            cfg["current_phase"] = 1
            if new_start_bal is not None:
                cfg["phase_start_balance"] = round(float(new_start_bal), 2)
            start_bal = cfg.get("phase_start_balance", 0.0)
            active_r = cfg.get("challenge_risk_pct", 1.30) if use_custom else 1.00
            cfg["risk_per_trade"] = round(active_r / 100.0, 4)
            phase_name = "Challenge Phase 1"
            target_pct = cfg.get("phase_1_target_pct", 8.0)
            print(f"\n[PHASE UPDATE] Account {acc_id} set to CHALLENGE PHASE 1!")
            print(f"  * Phase 1 Start Balance: ${cfg['phase_start_balance']:,.2f}")
            print(f"  * Phase 1 Target:        +{cfg.get('phase_1_target_pct', 8.0):.1f}%")
            print(f"  * Active Risk:           {active_r:.2f}% (Custom Phase Risk: {'ENABLED' if use_custom else 'OFF - using default 1.00%'})\n")
        cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save()

        # Notify remote platforms if notifier is provided
        if notifier and hasattr(notifier, "notify_phase_transition"):
            try:
                notifier.notify_phase_transition(
                    account_id=acc_id,
                    new_phase_name=phase_name,
                    start_balance=start_bal,
                    target_pct=target_pct,
                    active_risk_pct=cfg.get("risk_per_trade", 0.01) * 100.0,
                    is_custom_risk_on=use_custom,
                    server=cfg.get("server", "MT5")
                )
            except Exception:
                pass

    def update_lifecycle_settings(
        self,
        acc_id: str,
        lifecycle: str,
        current_phase: Any,
        p1_target: float,
        p2_target: float,
        ch_risk: float,
        funded_risk: float,
        start_bal: Optional[float] = None
    ):
        if acc_id not in self.accounts:
            return
        cfg = self.accounts[acc_id]
        cfg["account_lifecycle"] = lifecycle.lower()
        cfg["current_phase"] = current_phase
        cfg["phase_1_target_pct"] = round(p1_target, 2)
        cfg["phase_2_target_pct"] = round(p2_target, 2)
        cfg["challenge_risk_pct"] = round(ch_risk, 2)
        cfg["funded_risk_pct"] = round(funded_risk, 2)
        if start_bal is not None:
            cfg["phase_start_balance"] = round(float(start_bal), 2)
        use_custom = cfg.get("use_custom_phase_risk", False)
        active_risk = (ch_risk if cfg["account_lifecycle"] == "challenge" else funded_risk) if use_custom else 1.00
        cfg["risk_per_trade"] = round(active_risk / 100.0, 4)
        cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def toggle_custom_phase_risk(self, acc_id: str) -> bool:
        """Toggles custom phase risk scaling on/off. When off, default is 1.00% overall."""
        if acc_id not in self.accounts:
            return False
        cfg = self.accounts[acc_id]
        curr = cfg.get("use_custom_phase_risk", False)
        cfg["use_custom_phase_risk"] = not curr
        if cfg["use_custom_phase_risk"]:
            active_risk = cfg.get("challenge_risk_pct", 1.30) if cfg.get("account_lifecycle") == "challenge" else cfg.get("funded_risk_pct", 1.00)
        else:
            active_risk = 1.00
        cfg["risk_per_trade"] = round(active_risk / 100.0, 4)
        cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save()
        return cfg["use_custom_phase_risk"]

    def format_account_dashboard(self, acc_id: str, equity: float, balance: float, daily_starting_equity: Optional[float] = None) -> str:
        """Returns a prominent, structured ASCII dashboard card for existing account data."""
        if acc_id not in self.accounts:
            return ""
        cfg = self.accounts[acc_id]
        lifecycle = cfg.get("account_lifecycle", "challenge").upper()
        curr_phase = cfg.get("current_phase", 1)
        ch_steps = cfg.get("challenge_steps", 2)
        start_bal = cfg.get("phase_start_balance", balance)
        p1_tgt = cfg.get("phase_1_target_pct", 8.0)
        p2_tgt = cfg.get("phase_2_target_pct", 5.0)
        ch_risk = cfg.get("challenge_risk_pct", 1.25)
        f_risk = cfg.get("funded_risk_pct", 1.0)
        use_custom = cfg.get("use_custom_phase_risk", False)
        active_risk_pct = cfg.get("risk_per_trade", 0.01) * 100.0
        risk_scaling_desc = f"[Scaling: ON ({active_risk_pct:.2f}%)]" if use_custom else f"[Scaling: OFF (Fixed {active_risk_pct:.2f}%)]"
        hwm = cfg.get("high_water_mark", equity)

        profit_dollar = balance - start_bal
        profit_pct = (profit_dollar / start_bal * 100.0) if start_bal > 0 else 0.0

        if lifecycle == "CHALLENGE":
            active_target_pct = p1_tgt if str(curr_phase) == "1" else p2_tgt
            target_dollar = start_bal * (1.0 + active_target_pct / 100.0)
            remaining_dollar = max(0.0, target_dollar - balance)
            phase_str = f"CHALLENGE MODE ({ch_steps}-Step | Phase {curr_phase} Active)"
            target_desc = f"Phase {curr_phase} Target: +{active_target_pct:.1f}% (${target_dollar:,.2f})"
            if ch_steps == 2:
                total_tgt = p1_tgt + p2_tgt
                target_desc += f"  [Combined: +{total_tgt:.1f}%]"
            if balance >= target_dollar:
                pass_label = "CHALLENGE PASSED! FUNDED READY" if (ch_steps == 1 or str(curr_phase) in ["2", "funded"]) else f"PHASE {curr_phase} TARGET REACHED (CLOSED BALANCE)"
                progress_str = f"+${profit_dollar:,.2f} (+{profit_pct:.2f}%)  >>> [{pass_label}] <<<"
            else:
                rem_pct = max(0.0, active_target_pct - profit_pct)
                progress_str = f"+${profit_dollar:,.2f} (+{profit_pct:.2f}%)  [${remaining_dollar:,.2f} / {rem_pct:.2f}% to target]"
        else:
            phase_str = "FUNDED ACCOUNT (Capital Preservation & Bi-Weekly Payouts)"
            target_desc = "Bi-Weekly Payout Growth Target (No Hard Ceiling)"
            progress_str = f"Net PnL: {profit_dollar:+,.2f} ({profit_pct:+.2f}%)"

        daily_limit = cfg.get("daily_dd_limit_pct", 4.0)
        daily_cb = cfg.get("daily_cb_pct", 3.0)
        max_limit = cfg.get("max_total_dd_pct", 8.0)
        max_cb = cfg.get("max_cb_pct", 7.0)
        day_start_eq = daily_starting_equity if daily_starting_equity is not None else get_daily_starting_equity(balance)
        daily_halt_eq = day_start_eq * (1.0 - daily_cb / 100.0)
        max_halt_eq = hwm * (1.0 - max_cb / 100.0)

        lines = [
            "╭" + "─" * 78 + "╮",
            "│           ACCOUNT PROFILE & PROP FIRM LIFECYCLE DASHBOARD                    │",
            "├" + "─" * 78 + "┤",
            f"│  Account ID:          {acc_id} ({cfg.get('server', 'MT5')})",
            f"│  Lifecycle Status:    {phase_str}",
            f"│  Phase Start Balance: ${start_bal:,.2f}  |  Current Balance: ${balance:,.2f}  |  Equity: ${equity:,.2f}",
            f"│  Target Milestone:    {target_desc}",
            f"│  Current Progress:    {progress_str}",
            f"│  Active Sizing Risk:  {active_risk_pct:.2f}% (~${balance * active_risk_pct / 100.0:,.2f})  {risk_scaling_desc}",
            "├" + "─" * 78 + "┤",
            "│  Drawdown & Circuit Breaker Limits:",
            f"│    * Daily Drawdown:   Hard Limit: -{daily_limit:.1f}% | Circuit Breaker: -{daily_cb:.1f}% (Halts <= ${daily_halt_eq:,.2f})",
            f"│    * Max Total DD:     Hard Limit: -{max_limit:.1f}% | Circuit Breaker: -{max_cb:.1f}% (Halts <= ${max_halt_eq:,.2f})",
            f"│    * Peak Equity(HWM): ${hwm:,.2f}",
            "╰" + "─" * 78 + "╯",
        ]
        return "\n".join(lines)

    def update_account_rules(
        self,
        acc_id: str,
        risk_pct: float,
        daily_dd: float,
        max_dd: float,
        daily_cb: Optional[float] = None,
        max_cb: Optional[float] = None
    ):
        if acc_id in self.accounts:
            if daily_cb is None:
                daily_cb = max(0.1, daily_dd - 1.0)
            if max_cb is None:
                max_cb = max(0.1, max_dd - 1.0)
            self.accounts[acc_id]["risk_per_trade"] = round(risk_pct / 100.0, 4)
            if self.accounts[acc_id].get("account_lifecycle") == "challenge":
                self.accounts[acc_id]["challenge_risk_pct"] = round(risk_pct, 2)
            else:
                self.accounts[acc_id]["funded_risk_pct"] = round(risk_pct, 2)
            self.accounts[acc_id]["daily_dd_limit_pct"] = round(daily_dd, 2)
            self.accounts[acc_id]["daily_cb_pct"] = round(daily_cb, 2)
            self.accounts[acc_id]["max_total_dd_pct"] = round(max_dd, 2)
            self.accounts[acc_id]["max_cb_pct"] = round(max_cb, 2)
            self.accounts[acc_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            print(f"\n[SUCCESS] Updated rules for Account {acc_id} successfully!")
            print(f"  * Active Risk Per Trade: {risk_pct:.2f}%")
            print(f"  * Hard DD Limits:        Daily: {daily_dd:.1f}% | Max Total: {max_dd:.1f}%")
            print(f"  * Circuit Breakers (CB): Daily: {daily_cb:.1f}% | Max Total: {max_cb:.1f}% (Halts trading before hitting hard limits)\n")


    def get_notification_config(self, acc_id: str) -> Dict:
        if acc_id in self.accounts:
            return self.accounts[acc_id].get("notifications", {
                "active_platform": "none",
                "telegram_bot_token": "",
                "telegram_chat_id": "",
                "discord_webhook_url": ""
            })
        return {
            "active_platform": "none",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "discord_webhook_url": ""
        }

    def update_notification_settings(self, acc_id: str, platform: str, tg_token: str = "", tg_chat_id: str = "", discord_url: str = ""):
        if acc_id in self.accounts:
            if "notifications" not in self.accounts[acc_id]:
                self.accounts[acc_id]["notifications"] = {
                    "active_platform": "none",
                    "telegram_bot_token": "",
                    "telegram_chat_id": "",
                    "discord_webhook_url": ""
                }
            n_cfg = self.accounts[acc_id]["notifications"]
            n_cfg["active_platform"] = platform.lower()
            if tg_token:
                n_cfg["telegram_bot_token"] = tg_token
            if tg_chat_id:
                n_cfg["telegram_chat_id"] = tg_chat_id
            if discord_url:
                n_cfg["discord_webhook_url"] = discord_url
            self.accounts[acc_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            print(f"\n[SUCCESS] Notification settings saved for Account {acc_id}!")

    def toggle_liquidity_sweep(self, acc_id: str) -> bool:
        if acc_id in self.accounts:
            curr = self.accounts[acc_id].get("use_liquidity_sweep", True)
            self.accounts[acc_id]["use_liquidity_sweep"] = not curr
            self.accounts[acc_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            return not curr
        return True

    def toggle_news_shield(self, acc_id: str) -> bool:
        if acc_id in self.accounts:
            curr = self.accounts[acc_id].get("use_news_shield", True)
            self.accounts[acc_id]["use_news_shield"] = not curr
            self.accounts[acc_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            return not curr
        return True

    def toggle_ema_gap_filter(self, acc_id: str) -> bool:
        if acc_id in self.accounts and "use_ema_gap_filter" in self.accounts[acc_id]:
            del self.accounts[acc_id]["use_ema_gap_filter"]
            self.save()
        return False

    def toggle_entry_mode(self, acc_id: str) -> str:
        if acc_id in self.accounts:
            curr = self.accounts[acc_id].get("entry_mode", "pre_arm")
            new_mode = "bar_close" if curr == "pre_arm" else "pre_arm"
            self.accounts[acc_id]["entry_mode"] = new_mode
            self.accounts[acc_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            return new_mode
        return "pre_arm"


class InstitutionalDCCBot:
    def __init__(
        self,
        symbols: List[str] = ["XAUUSD", "NAS100"],
        risk_per_trade: float = 0.01,
        daily_loss_limit_pct: Optional[float] = None,
        daily_dd_limit_pct: float = 4.0,
        daily_cb_pct: Optional[float] = None,
        max_total_dd_pct: float = 8.0,
        max_cb_pct: Optional[float] = None,
        high_water_mark: float = 0.0,
        config_mgr: Optional['AccountConfigManager'] = None,
        dry_run: bool = False,
        use_liquidity_sweep: bool = True,
        use_news_shield: bool = True,
        use_ema_gap_filter: bool = False,
        entry_mode: str = "bar_close",
        strategy_version: str = "v1.2",
    ):
        self.symbols = symbols
        self.risk_per_trade = risk_per_trade
        self.strategy_version = strategy_version

        # Configure Hard DD Limits & Circuit Breakers (-1% safety cushion)
        self.daily_dd_limit_pct = float(daily_dd_limit_pct)
        if daily_cb_pct is not None:
            self.daily_cb_pct = float(daily_cb_pct)
        elif daily_loss_limit_pct is not None:
            self.daily_cb_pct = float(daily_loss_limit_pct)
        else:
            self.daily_cb_pct = round(max(0.1, self.daily_dd_limit_pct - 1.0), 2)

        self.max_total_dd_pct = float(max_total_dd_pct)
        if max_cb_pct is not None:
            self.max_cb_pct = float(max_cb_pct)
        else:
            self.max_cb_pct = round(max(0.1, self.max_total_dd_pct - 1.0), 2)

        # Backwards compatibility alias: halt threshold
        self.daily_loss_limit_pct = self.daily_cb_pct
        self.high_water_mark = high_water_mark
        self.config_mgr = config_mgr
        self.dry_run = dry_run
        self.use_liquidity_sweep = use_liquidity_sweep
        self.use_news_shield = use_news_shield
        self.use_ema_gap_filter = False  # Permanently nuked in v1.2 TripleGuard
        self.entry_mode = entry_mode if entry_mode in ["pre_arm", "bar_close"] else "bar_close"
        self.news_engine = NewsEngine(cache_dir=self.config_mgr.config_path if False else None)
        self.active_news_shield: Optional[Dict] = None
        self.notified_news_activations: set = set()
        self.notified_news_lifted: set = set()
        self.notified_armed_setups: Dict[str, datetime] = {}
        self.notifier = NotificationManager()
        self.session_notified: Dict[str, bool] = {}
        self.current_session_state: Optional[str] = None
        self.audit_logger = MarketAuditLogger()
        self.console = Console(highlight=False, soft_wrap=True)
        self.spinner_frames = ["[|]", "[/]", "[-]", "[\\]"]
        self.spinner_colors = [
            "bold bright_cyan",
            "bold cyan",
            "bold bright_green",
            "bold green",
            "bold bright_yellow",
            "bold bright_magenta",
        ]
        self.spinner_idx = 0
        self.tz_ist = timezone(timedelta(hours=5, minutes=30))
        
        # Session Trading Window (06:00 to 19:00 UTC / 11:30 to 00:30 IST)
        # Asian session and late-night rollover (19:00 to 06:00 UTC) are disabled for new entries to prevent overnight chop.
        self.entry_start_hour_utc: int = 6
        self.entry_end_hour_utc: int = 19
        self.trap_hours_utc: List[int] = [9, 13]
        self.broker_offset: timedelta = timedelta(hours=0)

        self.data_provider = MT5DataProvider()
        self.engines: Dict[str, DCCEngine] = {}
        self.armed_states: Dict[str, PreArmedState] = {s: PreArmedState() for s in symbols}
        self.active_positions: Dict[str, ActiveTwinPosition] = {}
        self.last_checked_bars: Dict[str, Optional[int]] = {s: None for s in symbols}

        # Daily Circuit Breaker State
        self.current_trading_day: Optional[datetime.date] = None
        self.daily_starting_equity: float = 0.0
        self.circuit_breaker_active: bool = False
        self.challenge_target_reached: bool = False
        self.challenge_target_summary: str = ""
        self.recent_trade_outcomes: List[bool] = []
        self.nightly_audit_triggered_day: Optional[datetime.date] = None

        # Persistent Account Lifecycle State (Challenge vs Funded)
        self.account_lifecycle: str = "challenge"
        self.current_phase: Any = 1
        self.challenge_risk_pct: float = 1.30
        self.funded_risk_pct: float = 1.00
        self.target_locked: bool = False

        if self.config_mgr and hasattr(mt5, 'account_info'):
            try:
                acc_info = mt5.account_info()
                if acc_info:
                    acc_cfg = self.config_mgr.accounts.get(str(acc_info.login), {})
                    self.account_lifecycle = acc_cfg.get("account_lifecycle", "challenge")
                    self.current_phase = acc_cfg.get("current_phase", 1)
                    self.challenge_risk_pct = float(acc_cfg.get("challenge_risk_pct", 1.30))
                    self.funded_risk_pct = float(acc_cfg.get("funded_risk_pct", 1.00))
                    self.target_locked = bool(acc_cfg.get("target_locked", False))
                    if self.target_locked:
                        self.challenge_target_reached = True
                        self.challenge_target_summary = f"Phase {self.current_phase}: Target Locked (Persisted from previous session)"
            except Exception:
                pass

        # Alpha weights for recursive float EMA updates
        self.alpha9 = 2.0 / (9.0 + 1.0)
        self.alpha20 = 2.0 / (20.0 + 1.0)

        for s in symbols:
            cfg = CONFIGS[s]
            self.engines[s] = DCCEngine(
                adx_min_threshold=cfg.adx_min,
                atr_sl_multiplier=cfg.atr_sl_multiplier,
                risk_reward_ratio=cfg.tp1_rr,
                check_2h_room=False,
                use_daily_open_filter=False,
            )

        # Zero-latency lock-free live state exporter worker (eliminates thread thrashing & latency)
        self._state_queue: queue.Queue = queue.Queue(maxsize=1)
        self._state_worker_thread = threading.Thread(target=self._state_exporter_worker, daemon=True)
        self._state_worker_thread.start()

        # Elevate process priority to HIGH_PRIORITY_CLASS to protect core tick loops & order execution
        try:
            import psutil
            psutil.Process().nice(psutil.HIGH_PRIORITY_CLASS)
        except Exception:
            try:
                import ctypes
                ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00000080)
            except Exception:
                pass

    def initialize(self) -> bool:
        if not mt5.initialize():
            print(f"[ERROR] MT5 initialization failed: {mt5.last_error()}")
            return False

        account = mt5.account_info()
        if not account:
            print("[ERROR] Failed to fetch MT5 account info.")
            return False

        if self.config_mgr:
            notif_cfg = self.config_mgr.get_notification_config(str(account.login))
            self.notifier.update_config(notif_cfg)

        # Determine True Day Starting Equity in UTC (recovering any deals closed earlier on the active trading day)
        # On Saturday and Sunday, seamlessly persists Friday's starting equity, closed deals, and circuit breaker status
        now_utc = datetime.now(timezone.utc)
        active_day_start_utc = get_current_trading_day_start_utc(now_utc)
        start_ts = int(active_day_start_utc.timestamp())
        end_ts = int(pytime.time() + 86400)
        today_deals = mt5.history_deals_get(start_ts, end_ts)
        closed_pnl_today = 0.0
        if today_deals:
            for d in today_deals:
                if d.entry == 1:  # Exit deals (closed positions)
                    closed_pnl_today += float(d.profit + d.commission + d.swap)

        # Day Starting Equity = Current Balance - Closed Profit on Active Trading Day
        self.daily_starting_equity = max(0.01, float(account.balance) - closed_pnl_today)
        self.current_trading_day = active_day_start_utc.date()

        # Check if Circuit Breaker was already breached earlier on active trading day before startup
        cur_day_pnl = float(account.equity) - self.daily_starting_equity
        cur_daily_loss = max(0.0, -cur_day_pnl)
        init_daily_dd_pct = (cur_daily_loss / self.daily_starting_equity) * 100.0 if self.daily_starting_equity > 0 else 0.0
        day_label = "Friday (Persisted across weekend)" if now_utc.weekday() in [5, 6] else "today"
        if init_daily_dd_pct >= self.daily_cb_pct:
            self.circuit_breaker_active = True
            print("\n" + "!" * 80)
            print(f"[!! RECOVERED PRIOR LOSS: DAILY CIRCUIT BREAKER ACTIVE (-{init_daily_dd_pct:.2f}%) !!]")
            print(f"Closed deals PnL ({day_label}):  -${cur_daily_loss:,.2f} | Starting Equity: ${self.daily_starting_equity:,.2f}")
            print(f"Circuit Breaker Limit:   -{self.daily_cb_pct:.1f}% | Cushion: $0.00")
            print("ACTION: Trading remains HALTED for remainder of day to protect account.")
            print("!" * 80 + "\n")
        elif closed_pnl_today != 0.0:
            print(f"[AUDIT] Recovered prior closed trades ({day_label}): PnL {'+' if closed_pnl_today >= 0 else ''}${closed_pnl_today:,.2f} | Start Equity: ${self.daily_starting_equity:,.2f}")

        if self.high_water_mark <= 0.0:
            self.high_water_mark = account.equity
        elif account.equity > self.high_water_mark:
            self.high_water_mark = account.equity

        for s in self.symbols:
            if not mt5.symbol_select(s, True):
                print(f"[ERROR] Could not select symbol {s}")
                return False
            info = mt5.symbol_info(s)
            fill_mode = self.get_safe_filling_mode(info)
            print(f"[{s}] Contract={info.trade_contract_size} | Digits={info.digits} | MinLot={info.volume_min} | FillingMode={fill_mode}")

        # Auto-detect Broker Server Timezone Offset relative to UTC
        try:
            sample_sym = self.symbols[0]
            tick = mt5.symbol_info_tick(sample_sym)
            if tick and tick.time > 0:
                s_time = datetime.fromtimestamp(tick.time, timezone.utc)
                offset_hrs = round((s_time - datetime.now(timezone.utc)).total_seconds() / 3600.0)
                self.broker_offset = timedelta(hours=offset_hrs)
                offset_str = f"UTC+{offset_hrs}" if offset_hrs >= 0 else f"UTC{offset_hrs}"
                print(f"[TIME SYNC] Detected MT5 Broker Server Timezone: {offset_str} ({self.broker_offset})")
        except Exception as e:
            print(f"[WARN] Could not auto-detect broker offset: {e}")

        print("\n" + "=" * 75)
        print("INSTITUTIONAL DCC BOT INITIALIZED")
        print(f"Account Login:           {account.login} ({account.server})")
        print(f"Equity / Balance:        ${account.equity:,.2f} / ${account.balance:,.2f}")
        print(f"Peak Equity (HWM):       ${self.high_water_mark:,.2f}")
        print(f"Daily Starting Equity:   ${self.daily_starting_equity:,.2f}")
        daily_cb_equity = self.daily_starting_equity * (1.0 - self.daily_cb_pct / 100.0)
        daily_hard_equity = self.daily_starting_equity * (1.0 - self.daily_dd_limit_pct / 100.0)
        print(f"Daily DD (Hard Limit):   -{self.daily_dd_limit_pct:.1f}% (${daily_hard_equity:,.2f}) [Broker / Prop Firm Rule]")
        print(f"Daily Circuit Breaker:   -{self.daily_cb_pct:.1f}% (HALT trading if equity <= ${daily_cb_equity:,.2f} | {self.daily_dd_limit_pct - self.daily_cb_pct:.1f}% safety cushion)")
        max_cb_equity = self.high_water_mark * (1.0 - self.max_cb_pct / 100.0)
        max_hard_equity = self.high_water_mark * (1.0 - self.max_total_dd_pct / 100.0)
        print(f"Max DD (Hard Limit):     -{self.max_total_dd_pct:.1f}% (${max_hard_equity:,.2f}) [Broker / Prop Firm Rule]")
        print(f"Max Circuit Breaker:     -{self.max_cb_pct:.1f}% (Emergency halt if equity <= ${max_cb_equity:,.2f} | {self.max_total_dd_pct - self.max_cb_pct:.1f}% safety cushion)")
        print(f"Leverage:                1:{account.leverage}")
        print(f"Monitored Assets:        {', '.join(self.symbols)}")
        active_gear = "GEAR 1 (CHALLENGE: Dynamic 1.30%/1.00% Risk)" if getattr(self, "account_lifecycle", "challenge") == "challenge" else "GEAR 2 (FUNDED: Static Fixed 1.00% Risk)"
        print(f"ApexHunter Operating Gear: {active_gear}")
        strat_ver = getattr(self, "strategy_version", "v1.2")
        if strat_ver == "v1.2":
            strat_str = "v1.2 APEXHUNTER (Flagship: Smart Killzone Stretch>=1.10x | Dual-Gear | TripleGuard)"
        elif strat_ver == "v1.1":
            strat_str = "v1.1 EARLY APEXHUNTER (TripleGuard | Hard Daily KZ Pause 09:00 & 13:00)"
        else:
            strat_str = "v1.0 BASELINE DCC (Standard Continuous Reference)"
        print(f"Strategy Engine:         {strat_str}")
        print(f"Execution Mode:          {'DRY RUN (Paper Mode)' if self.dry_run else 'LIVE ORDER EXECUTION'}")
        sweep_str = "ENABLED (Turtle Soup Filter)" if self.use_liquidity_sweep else "DISABLED (Standard Baseline)"
        print(f"5M Liquidity Sweep:      {sweep_str}")
        news_str = "ENABLED (15m Blackout around USD High-Impact News)" if self.use_news_shield else "DISABLED (Off)"
        print(f"High-Impact News Shield: {news_str}")
        mode_str = "BAR-CLOSE (Instant Flip Entry / Matches Backtest)" if self.entry_mode == "bar_close" else "PRE-ARM (2-Min High-Frequency Tick Stream)"
        print(f"Entry Mode:              {mode_str}")
        if strat_ver == "v1.2":
            print(">> STATUS: [v1.2 APEXHUNTER PRODUCTION FLAGSHIP ACTIVE] <<")
        elif strat_ver == "v1.1":
            print(">> STATUS: [v1.1 EARLY APEXHUNTER ACTIVE] <<")
        else:
            print(">> STATUS: [v1.0 BASELINE DCC ACTIVE] <<")
        if self.entry_mode == "bar_close":
            print(">> STATUS: [EXACT 1-TO-1 BACKTEST EXECUTION MATCH ACTIVE] <<")
        
        plat = self.notifier.active_platform
        if plat == "telegram":
            notif_status = f"Telegram Bot (Chat ID: {self.notifier.config.get('telegram_chat_id', 'N/A')})"
        elif plat == "discord":
            notif_status = "Discord Webhook (Active)"
        else:
            notif_status = "Disabled (None)"
        print(f"Remote Notifications:    {notif_status}")
        print("=" * 75 + "\n")

        self.notifier.notify_startup(
            account_id=account.login,
            server=account.server,
            mode="dry_run" if self.dry_run else "live",
            equity=account.equity,
            balance=account.balance,
            risk_pct=self.risk_per_trade * 100.0,
            daily_dd=self.daily_dd_limit_pct,
            max_dd=self.max_total_dd_pct,
            symbols=self.symbols,
            daily_cb=self.daily_cb_pct,
            max_cb=self.max_cb_pct
        )

        # Broadcast initial session / trap hour pause status if starting inside a paused window
        self.check_session_and_trap_alerts(datetime.now(timezone.utc), is_startup=True)

        # Recover any active open positions from MT5 (Restores breakeven automation & terminal HUD persistence across restarts)
        self.recover_active_positions()

        # Export live state immediately on startup so visualizer reflects true equity and daily loss
        self.write_live_state()

        return True

    def recover_active_positions(self):
        """Recovers any live open positions from MetaTrader 5 on bot startup or restart.
        Reconstructs ActiveTwinPosition objects so breakeven automator, runner management,
        and HUD tracking persist seamlessly across process restarts."""
        try:
            if not mt5.initialize():
                return

            recovered_count = 0
            for symbol in self.symbols:
                open_pos = mt5.positions_get(symbol=symbol)
                if not open_pos or len(open_pos) == 0:
                    continue

                tickets = list(open_pos)
                ticket_a = None
                ticket_b = None

                for p in tickets:
                    comment = (p.comment or "").strip()
                    if "TP1" in comment:
                        ticket_a = p
                    elif "Runner" in comment or "TP2" in comment:
                        ticket_b = p

                # Fallback: if comments were stripped by broker, differentiate by TP distance
                if not ticket_a and not ticket_b:
                    if len(tickets) >= 2:
                        tickets_sorted = sorted(tickets, key=lambda p: abs(p.tp - p.price_open) if p.tp > 0 else 999999)
                        ticket_a = tickets_sorted[0]
                        ticket_b = tickets_sorted[1]
                    elif len(tickets) == 1:
                        ticket_b = tickets[0]

                ref_p = ticket_a or ticket_b
                if not ref_p:
                    continue

                dir_str = "BUY" if ref_p.type == 0 else "SELL"
                entry_price = float(ref_p.price_open)
                entry_time = datetime.fromtimestamp(ref_p.time, tz=timezone.utc)
                sl_price = float(ref_p.sl)
                tp1_price = float(ticket_a.tp) if ticket_a else 0.0
                tp2_price = float(ticket_b.tp) if ticket_b else 0.0
                lots_a = float(ticket_a.volume) if ticket_a else 0.0
                lots_b = float(ticket_b.volume) if ticket_b else 0.0
                t_a_num = int(ticket_a.ticket) if ticket_a else 0
                t_b_num = int(ticket_b.ticket) if ticket_b else 0

                runner_at_be = False
                be_sl = 0.0
                cfg = CONFIGS.get(symbol)
                spread = cfg.max_allowed_spread if cfg else 0.5
                if ticket_b:
                    if dir_str == "BUY" and sl_price >= entry_price - 0.1:
                        runner_at_be = True
                        be_sl = sl_price
                    elif dir_str == "SELL" and sl_price > 0 and sl_price <= entry_price + 0.1:
                        runner_at_be = True
                        be_sl = sl_price

                ticket_a_closed = (ticket_a is None and ticket_b is not None)

                self.active_positions[symbol] = ActiveTwinPosition(
                    symbol=symbol,
                    direction=dir_str,
                    entry_price=entry_price,
                    entry_spread=spread,
                    entry_time=entry_time,
                    ticket_a=t_a_num,
                    ticket_b=t_b_num,
                    sl_price=sl_price,
                    tp1_price=tp1_price,
                    tp2_price=tp2_price,
                    lots_a=lots_a,
                    lots_b=lots_b,
                    be_sl=be_sl,
                    ticket_a_closed=ticket_a_closed,
                    runner_moved_to_be=runner_at_be
                )
                recovered_count += 1
                print(f"[{symbol} PERSISTENCE RECOVERY] Restored active {dir_str} position: Ticket A: #{t_a_num} (Lots: {lots_a}, TP: {tp1_price}) | Ticket B: #{t_b_num} (Lots: {lots_b}, TP: {tp2_price}) | SL: {sl_price} | BE: {runner_at_be}")
                self.write_live_state(symbol)

            if recovered_count > 0:
                print(f"[PERSISTENCE] Successfully recovered {recovered_count} active position(s) from MetaTrader 5 into bot memory!\n")
        except Exception as e:
            print(f"[WARN] Error recovering active positions: {e}")

    @staticmethod
    def detect_liquidity_sweep(
        df_m5: pd.DataFrame,
        entry_idx: int,
        direction: int,  # 1 for BUY, -1 for SELL
        swing_lookback: int = 20,
        pullback_window: int = 8
    ) -> Tuple[bool, float, float]:
        """
        Checks if a 5M liquidity sweep occurred prior to entry_idx.
        Returns: (is_sweep, sweep_level, swept_by_pts)
        """
        if entry_idx < swing_lookback + 2:
            return False, 0.0, 0.0

        pullback_start_idx = max(0, entry_idx - pullback_window)
        swing_search_start = max(0, pullback_start_idx - swing_lookback)

        if pullback_start_idx <= swing_search_start:
            return False, 0.0, 0.0

        lows = df_m5['low'].values
        highs = df_m5['high'].values
        closes = df_m5['close'].values

        if direction == 1:  # BUY setup
            candidate_lows = []
            for j in range(swing_search_start + 1, pullback_start_idx):
                if lows[j] <= lows[j - 1] and lows[j] <= lows[j + 1]:
                    candidate_lows.append(lows[j])

            if not candidate_lows:
                candidate_lows = [min(lows[swing_search_start:pullback_start_idx])]

            curr_close = closes[entry_idx]
            for swing_low in candidate_lows:
                min_pullback_low = min(lows[pullback_start_idx:entry_idx + 1])
                if min_pullback_low < swing_low and curr_close > swing_low:
                    swept_pts = swing_low - min_pullback_low
                    return True, swing_low, swept_pts

            return False, 0.0, 0.0

        else:  # SELL setup
            candidate_highs = []
            for j in range(swing_search_start + 1, pullback_start_idx):
                if highs[j] >= highs[j - 1] and highs[j] >= highs[j + 1]:
                    candidate_highs.append(highs[j])

            if not candidate_highs:
                candidate_highs = [max(highs[swing_search_start:pullback_start_idx])]

            curr_close = closes[entry_idx]
            for swing_high in candidate_highs:
                max_pullback_high = max(highs[pullback_start_idx:entry_idx + 1])
                if max_pullback_high > swing_high and curr_close < swing_high:
                    swept_pts = max_pullback_high - swing_high
                    return True, swing_high, swept_pts

            return False, 0.0, 0.0

    @staticmethod
    def get_safe_filling_mode(info) -> int:
        """Determines valid MT5 filling mode for broker to avoid TRADE_RETCODE_INVALID_FILL."""
        if not info:
            return mt5.ORDER_FILLING_FOK
        modes = info.filling_mode
        # bit 0 = FOK (1), bit 1 = IOC (2)
        if modes & 1:
            return mt5.ORDER_FILLING_FOK
        elif modes & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def get_effective_risk_pct(self, symbol: str, adx: float = 25.0, close_p: float = 0.0, h1_e20: float = 0.0, atr: float = 1.0) -> float:
        """Determines active risk % based on ApexHunter Dual-Gear and real-time market regime."""
        if getattr(self, "account_lifecycle", "challenge") == "challenge":
            stretch_now = (abs(close_p - h1_e20) / (atr + 1e-9)) if (close_p > 0 and h1_e20 > 0) else 1.0
            is_trend = (adx >= 22.0) and (stretch_now >= 0.85)
            recent_wins = getattr(self, "recent_trade_outcomes", [])
            rec_wr = (sum(recent_wins) / len(recent_wins)) if len(recent_wins) >= 3 else 0.50
            if is_trend and rec_wr >= 0.50:
                return float(getattr(self, "challenge_risk_pct", 1.30))
            else:
                return 1.00  # Conservative baseline in chop
        else:
            return float(getattr(self, "funded_risk_pct", 1.00))

    def calculate_lots(self, symbol: str, sl_distance: float, target_risk_dollars: Optional[float] = None) -> Tuple[float, float, float]:
        account = mt5.account_info()
        equity = account.equity if account else 5000.0
        if target_risk_dollars is not None and target_risk_dollars > 0:
            risk_amount = target_risk_dollars
        else:
            risk_amount = equity * self.risk_per_trade

        sym_info = mt5.symbol_info(symbol)
        contract_size = sym_info.trade_contract_size if sym_info else (10.0 if "NAS" in symbol else 100.0)
        vol_step = sym_info.volume_step if sym_info else 0.01
        vol_min = sym_info.volume_min if sym_info else 0.01

        raw_lots = risk_amount / (sl_distance * contract_size)
        min_split_lot = max(vol_min * 2, 0.02)
        total_lots = max(min_split_lot, round(raw_lots / vol_step) * vol_step)
        total_lots = round(total_lots, 2)

        partial_lots = max(vol_min, round((total_lots * 0.5) / vol_step) * vol_step)
        partial_lots = round(partial_lots, 2)
        runner_lots = round(max(vol_min, total_lots - partial_lots), 2)

        return total_lots, partial_lots, runner_lots

    def get_clean_tick(self, symbol: str) -> Optional[Tuple[float, float, float]]:
        """Safely fetches and validates live bid, ask, spread. Returns None if invalid/spiking."""
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return None
            bid = float(tick.bid)
            ask = float(tick.ask)
            if bid <= 0.0 or ask <= 0.0 or ask < bid:
                return None
            spread = ask - bid
            cfg = CONFIGS[symbol]
            if spread > cfg.max_allowed_spread:
                # Spread spike detected (e.g. news or rollover)
                return None
            return bid, ask, spread
        except Exception:
            return None

    def check_candle_arm_status(self, symbol: str):
        """
        Runs on 5M candle close:
        1. Calculates all indicators (EMA9, EMA20, VWAP, 1H Bias, ADX, ATR, 5M & 2H Swing High/Low).
        2. Detects 5M liquidity sweep (Turtle soup) status and exact swept price levels.
        3. Logs the calculation to CSV & text log for 1-to-1 backtest comparison and debugging.
        4. Prints a formatted forensic audit card on the terminal.
        5. If valid, arms the setup for live tick breakout execution.
        """
        now_utc = datetime.now(timezone.utc)
        digits = 2 if "XAU" in symbol else 1

        # Fetch rates with sufficient warmup (500 M5 bars ensures all 288 bars of the current day are present for exact Session VWAP calculation)
        m5_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 500)
        h1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 200)
        h2_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H2, 0, 200)

        if m5_rates is None or h1_rates is None or len(m5_rates) < 30:
            return

        df_m5 = pd.DataFrame(m5_rates)
        df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s', utc=True) - self.broker_offset
        df_m5.set_index('time', inplace=True)
        df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

        df_1h = pd.DataFrame(h1_rates)
        df_1h['time'] = pd.to_datetime(df_1h['time'], unit='s', utc=True) - self.broker_offset
        df_1h.set_index('time', inplace=True)

        df_2h = pd.DataFrame(h2_rates)
        df_2h['time'] = pd.to_datetime(df_2h['time'], unit='s', utc=True) - self.broker_offset
        df_2h.set_index('time', inplace=True)

        engine = self.engines[symbol]
        df_prep = engine.prepare_data(df_m5, df_1h, df_2h)

        # Look at the newly closed 5M bar (index -2)
        closed_bar = df_prep.iloc[-2]
        c_time = closed_bar.name
        c_time_str = c_time.strftime("%Y-%m-%d %H:%M:%S")
        ist_time_str = c_time.astimezone(self.tz_ist).strftime("%Y-%m-%d %H:%M:%S")

        # 1H Timeframe Context bar
        h1_time = df_1h.index[-1]
        h1_time_str = h1_time.strftime("%Y-%m-%d %H:%M")
        h1_ist_str = h1_time.astimezone(self.tz_ist).strftime("%H:%M")

        open_p = float(closed_bar['open'])
        high_p = float(closed_bar['high'])
        low_p = float(closed_bar['low'])
        close_p = float(closed_bar['close'])
        vol = float(closed_bar['volume'])

        m5_e9 = float(closed_bar['ema9_5m'])
        m5_e20 = float(closed_bar['ema20_5m'])
        ema_gap = abs(m5_e9 - m5_e20)
        vwap = float(closed_bar['vwap_5m'])

        bias = int(closed_bar['bias_1h']) if not pd.isna(closed_bar['bias_1h']) else 0
        h1_e9 = float(closed_bar['ema9_1h']) if not pd.isna(closed_bar['ema9_1h']) else 0.0
        h1_e20 = float(closed_bar['ema20_1h']) if not pd.isna(closed_bar['ema20_1h']) else 0.0
        adx = float(closed_bar['adx_1h']) if not pd.isna(closed_bar['adx_1h']) else 0.0
        atr = float(closed_bar['atr_1h']) if not pd.isna(closed_bar['atr_1h']) else 0.0
        ema_gap_ratio = ema_gap / (atr + 1e-9)

        # Swing levels
        sw_high_5m = float(df_m5['high'].iloc[-22:-2].max()) if len(df_m5) >= 22 else high_p
        sw_low_5m = float(df_m5['low'].iloc[-22:-2].min()) if len(df_m5) >= 22 else low_p
        sw_high_2h = float(closed_bar['swing_high_2h']) if ('swing_high_2h' in closed_bar and not pd.isna(closed_bar['swing_high_2h'])) else 0.0
        sw_low_2h = float(closed_bar['swing_low_2h']) if ('swing_low_2h' in closed_bar and not pd.isna(closed_bar['swing_low_2h'])) else 0.0

        # Liquidity Sweep check
        sweep_dir = bias if bias != 0 else (1 if m5_e9 <= m5_e20 else -1)
        has_sweep, sweep_lvl, pts = self.detect_liquidity_sweep(
            df_m5, len(df_m5) - 2, sweep_dir,
            swing_lookback=20, pullback_window=8
        )

        cfg = CONFIGS[symbol]
        sl_dist = cfg.atr_sl_multiplier * atr
        tp1_dist = cfg.tp1_rr * sl_dist
        tp2_dist = cfg.tp2_rr * sl_dist

        open_pos = mt5.positions_get(symbol=symbol)

        decision = "NOT_ARMED"
        reason = "Normal price oscillation (no DCC setup)"
        planned_entry = 0.0
        planned_sl = 0.0
        planned_tp1 = 0.0
        planned_tp2 = 0.0
        planned_lots = 0.0
        direction = 0

        # Check if an armed setup on this symbol was not executed and is now expiring
        prev_state = self.armed_states[symbol]
        if prev_state.is_armed and prev_state.armed_bar_time != c_time:
            dir_str = "BUY" if prev_state.direction == 1 else "SELL"
            abort_msg = "Candle closed without breakout trigger execution"
            self.notifier.notify_setup_aborted(symbol, dir_str, abort_msg, close_p, 0.0)
            self.armed_states[symbol].is_armed = False

        # Pre-calculate dynamic cushion exposure
        is_exposure_capped = False
        exposure_cap_reason = ""
        if self.daily_starting_equity > 0:
            account = mt5.account_info()
            cur_equity = float(account.equity) if account else self.daily_starting_equity
            cur_daily_loss = max(0.0, self.daily_starting_equity - cur_equity)
            max_allowed_loss = self.daily_starting_equity * (self.daily_cb_pct / 100.0)
            remaining_cushion = max(0.0, max_allowed_loss - cur_daily_loss)
            min_viable_risk = max(10.0, self.daily_starting_equity * 0.002)
            sym_info = mt5.symbol_info(symbol)
            contract_size = sym_info.trade_contract_size if sym_info else (10.0 if "NAS" in symbol else 100.0)
            vol_min = sym_info.volume_min if sym_info else 0.01
            min_split_lot = max(vol_min * 2, 0.02)
            min_lot_risk = min_split_lot * sl_dist * contract_size
            effective_min_risk = max(min_viable_risk, min_lot_risk)
            if remaining_cushion < effective_min_risk:
                is_exposure_capped = True
                exposure_cap_reason = f"Remaining Daily CB Cushion (${remaining_cushion:.2f}) < Min Viable Risk (${effective_min_risk:.2f})"

        # 1. Circuit breaker check
        if self.circuit_breaker_active:
            decision = "BLOCKED_CIRCUIT_BREAKER"
            reason = f"Daily -{self.daily_cb_pct:.1f}% Circuit Breaker is ACTIVE (Hard Limit: -{self.daily_dd_limit_pct:.1f}%)"
            self.armed_states[symbol].is_armed = False
        # 1a. Challenge Target Lock-in Protection
        elif getattr(self, 'challenge_target_reached', False):
            decision = "BLOCKED_CHALLENGE_TARGET_REACHED"
            reason = f"Challenge Target Reached ({getattr(self, 'challenge_target_summary', 'Target Reached')}) - Trading Halted to Lock in Pass!"
            self.armed_states[symbol].is_armed = False
        # 1b. Dynamic Cushion Exposure Check (Option 1: Fill-the-Cushion Sizing)
        elif is_exposure_capped:
            decision = "BLOCKED_EXPOSURE_CAP"
            reason = exposure_cap_reason
            self.armed_states[symbol].is_armed = False
        # 2. Existing position check
        elif open_pos and len(open_pos) > 0:
            decision = "BLOCKED_POSITION_OPEN"
            reason = f"Trade already active on {symbol} (Ticket #{open_pos[0].ticket})"
            self.armed_states[symbol].is_armed = False
        # 3. Weekend Market Closure Filter (Saturday & Sunday / Friday post-close)
        elif now_utc.weekday() in [5, 6] or (now_utc.weekday() == 4 and now_utc.hour >= self.entry_end_hour_utc):
            day_name = now_utc.strftime('%A')
            decision = "SKIPPED_WEEKEND"
            reason = f"Weekend Market Closed ({day_name}). Trading resumes Monday 06:00 UTC (11:30 IST)."
            self.armed_states[symbol].is_armed = False
        # 4. Active Trading Session Filter (06:00 to 19:00 UTC / 11:30 to 00:30 IST)
        # Asian Session and rollover (19:00 to 06:00 UTC) is the Liquidity Range Formation phase, NOT an entry phase.
        elif now_utc.hour < self.entry_start_hour_utc or now_utc.hour >= self.entry_end_hour_utc:
            ist_str = now_utc.astimezone(self.tz_ist).strftime("%H:%M")
            decision = "SKIPPED_OUTSIDE_SESSION"
            reason = f"Outside Allowed Trading Window ({self.entry_start_hour_utc:02d}:00-{self.entry_end_hour_utc:02d}:00 UTC / 11:30-00:30 IST). Asian/Rollover Phase (Current: {now_utc.strftime('%H:%M')} UTC / {ist_str} IST)"
            self.armed_states[symbol].is_armed = False
        # 4. Dead trap hours (09:00 & 13:00 UTC)
        elif now_utc.hour in self.trap_hours_utc:
            strat_ver = getattr(self, "strategy_version", "v1.2")
            if strat_ver == "v1.1":
                # v1.1 Early ApexHunter: Hard pause on hours 09:00 and 13:00 UTC
                ist_str = now_utc.astimezone(self.tz_ist).strftime("%H:%M")
                decision = "SKIPPED_DEAD_HOUR_CHOP"
                reason = f"v1.1 Early ApexHunter: Hour {now_utc.hour:02d}:00 UTC ({ist_str} IST) in Hard Trap Zone (Paused)"
                self.armed_states[symbol].is_armed = False
            elif strat_ver == "v1.2":
                # v1.2 ApexHunter: Smart Hybrid Killzone (Stretch >= 1.10x ATR allows high-momentum breakouts)
                stretch_r = abs(close_p - h1_e20) / (atr + 1e-9)
                if stretch_r < 1.10:
                    ist_str = now_utc.astimezone(self.tz_ist).strftime("%H:%M")
                    decision = "SKIPPED_DEAD_HOUR_CHOP"
                    reason = f"v1.2 ApexHunter Smart Killzone: Hour {now_utc.hour:02d}:00 UTC ({ist_str} IST) in Trap Zone & Stretch ({stretch_r:.2f}x) < 1.10x ATR (Chop Trap)"
                    self.armed_states[symbol].is_armed = False
            # v1.0 Baseline DCC: No trap hour pause (hours 09:00 & 13:00 remain open)
        # 4. High-Impact News Shield Check (15m before & after USD news)
        elif self.use_news_shield and self.news_engine.get_active_news_shield(now_utc):
            active_shield = self.news_engine.get_active_news_shield(now_utc)
            decision = "PAUSED_HIGH_IMPACT_NEWS"
            reason = f"High-Impact {active_shield['country']} News ({active_shield['title']}) at {active_shield['event_time_str']} UTC. Shield active until {active_shield['resume_time_str']} UTC"
            self.armed_states[symbol].is_armed = False
        # 5. ADX threshold check
        elif adx < cfg.adx_min:
            decision = "SKIPPED_LOW_ADX"
            reason = f"1H ADX ({adx:.1f}) < min threshold ({cfg.adx_min:.1f})"
            self.armed_states[symbol].is_armed = False
        # 5b. TripleGuard Calendar Shield: Monday US Afternoon Trap Block (14:00 - 18:00 UTC)
        elif getattr(self, "strategy_version", "v1.2") in ["v1.1", "v1.2"] and now_utc.strftime('%A') == "Monday" and now_utc.hour in [14, 15, 16, 17]:
            decision = "SKIPPED_MONDAY_PM_TRAP"
            reason = f"{getattr(self, 'strategy_version', 'v1.2')} Calendar Shield: Monday US Afternoon Trap Hours (14:00-18:00 UTC) Active"
            self.armed_states[symbol].is_armed = False
        # 5c. TripleGuard Shield 2: 1H ADX Exhaustion Ceiling (ADX <= 45.0)
        elif getattr(self, "strategy_version", "v1.2") in ["v1.1", "v1.2"] and adx > 45.0:
            decision = "SKIPPED_ADX_EXHAUSTION"
            reason = f"{getattr(self, 'strategy_version', 'v1.2')} Shield 2: 1H ADX ({adx:.1f}) > 45.0 Exhaustion Ceiling"
            self.armed_states[symbol].is_armed = False
        # 5d. TripleGuard Shield 1: 1H Anti-Chop Floor (Stretch >= 0.40x ATR)
        elif getattr(self, "strategy_version", "v1.2") in ["v1.1", "v1.2"] and (abs(close_p - h1_e20) / (atr + 1e-9)) < 0.40:
            stretch_r = abs(close_p - h1_e20) / (atr + 1e-9)
            decision = "SKIPPED_ANTI_CHOP_FLOOR"
            reason = f"{getattr(self, 'strategy_version', 'v1.2')} Shield 1: 1H Stretch ({stretch_r:.2f}x) < 0.40x ATR Anti-Chop Floor"
            self.armed_states[symbol].is_armed = False
        # 5e. TripleGuard Shield 3: 5M No-Chase Guard (Chase <= 0.50x ATR)
        elif getattr(self, "strategy_version", "v1.2") in ["v1.1", "v1.2"] and (abs(close_p - m5_e9) / (atr + 1e-9)) > 0.50:
            chase_r = abs(close_p - m5_e9) / (atr + 1e-9)
            decision = "SKIPPED_NO_CHASE_GUARD"
            reason = f"{getattr(self, 'strategy_version', 'v1.2')} Shield 3: 5M Chase ({chase_r:.2f}x) > 0.50x ATR No-Chase Guard"
            self.armed_states[symbol].is_armed = False
        # 5. Bias check
        elif bias == 0 or pd.isna(bias):
            decision = "SKIPPED_NEUTRAL_BIAS"
            reason = "1H Trend Neutral (EMA9 == EMA20)"
            self.armed_states[symbol].is_armed = False
        elif atr <= 0:
            decision = "SKIPPED_INVALID_ATR"
            reason = f"ATR is invalid ({atr:.2f})"
            self.armed_states[symbol].is_armed = False
        else:
            if self.entry_mode == "bar_close":
                # =====================================================================
                # BAR-CLOSE MODE (Exact Backtest Match):
                # Detects EMA crossover flip on the newly closed 5M bar.
                # If flip + VWAP + 1H EMA20 (+ optional Sweep & EMA Gap) pass,
                # immediately places twin orders at the current market ask/bid.
                # =====================================================================
                prev_closed_bar = df_prep.iloc[-3] if len(df_prep) >= 3 else closed_bar
                prev_e9 = float(prev_closed_bar['ema9_5m'])
                prev_e20 = float(prev_closed_bar['ema20_5m'])
                prev_diff = prev_e9 - prev_e20
                curr_diff = m5_e9 - m5_e20

                if bias == 1:
                    direction = 1
                    is_flip = (prev_diff <= 0) and (curr_diff > 0)
                    if not is_flip:
                        decision = "SKIPPED_NO_FLIP"
                        reason = f"No Bullish EMA flip on bar close (Prev diff: {prev_diff:.2f}, Curr diff: {curr_diff:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif close_p <= vwap:
                        decision = "SKIPPED_BELOW_VWAP"
                        reason = f"5M Close below Session VWAP ({close_p:.2f} <= {vwap:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif close_p <= h1_e20:
                        decision = "SKIPPED_BELOW_H1_EMA20"
                        reason = f"5M Close below 1H EMA20 ({close_p:.2f} <= {h1_e20:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif self.use_liquidity_sweep and not has_sweep:
                        decision = "SKIPPED_SWEEP_UNCONFIRMED"
                        reason = f"5M Swing Low ({sw_low_5m:.2f}) liquidity sweep not satisfied"
                        self.armed_states[symbol].is_armed = False
                    else:
                        decision = "FIRED_BAR_CLOSE_BUY"
                        reason = "1H Bullish + 5M EMA Flip + VWAP + 1H EMA20 Confirmed"
                elif bias == -1:
                    direction = -1
                    is_flip = (prev_diff >= 0) and (curr_diff < 0)
                    if not is_flip:
                        decision = "SKIPPED_NO_FLIP"
                        reason = f"No Bearish EMA flip on bar close (Prev diff: {prev_diff:.2f}, Curr diff: {curr_diff:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif close_p >= vwap:
                        decision = "SKIPPED_ABOVE_VWAP"
                        reason = f"5M Close above Session VWAP ({close_p:.2f} >= {vwap:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif close_p >= h1_e20:
                        decision = "SKIPPED_ABOVE_H1_EMA20"
                        reason = f"5M Close above 1H EMA20 ({close_p:.2f} >= {h1_e20:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif self.use_liquidity_sweep and not has_sweep:
                        decision = "SKIPPED_SWEEP_UNCONFIRMED"
                        reason = f"5M Swing High ({sw_high_5m:.2f}) liquidity sweep not satisfied"
                        self.armed_states[symbol].is_armed = False
                    else:
                        decision = "FIRED_BAR_CLOSE_SELL"
                        reason = "1H Bearish + 5M EMA Flip + VWAP + 1H EMA20 Confirmed"

            else:
                # =====================================================================
                # PRE-ARM MODE (Original Strict Live Bot Behavior):
                # Detects compression pullback on the closed bar. If confirmed, pre-arms
                # the symbol and streams ticks during the final 2 minutes of the next bar
                # to trigger execution at the candle boundary (< 0.25s).
                # =====================================================================
                if bias == 1:
                    direction = 1
                    if m5_e9 > m5_e20:
                        decision = "SKIPPED_NO_PULLBACK"
                        reason = f"Trend expansion active (EMA9 {m5_e9:.2f} > EMA20 {m5_e20:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif close_p <= vwap:
                        decision = "SKIPPED_BELOW_VWAP"
                        reason = f"5M Close below Session VWAP ({close_p:.2f} <= {vwap:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif self.use_liquidity_sweep and not has_sweep:
                        decision = "SKIPPED_SWEEP_UNCONFIRMED"
                        reason = f"5M Swing Low ({sw_low_5m:.2f}) liquidity sweep not satisfied"
                        self.armed_states[symbol].is_armed = False
                    else:
                        decision = "ARMED_BUY"
                        reason = "1H Bullish + 5M Compression + VWAP + Sweep Confirmed"
                elif bias == -1:
                    direction = -1
                    if m5_e9 < m5_e20:
                        decision = "SKIPPED_NO_PULLBACK"
                        reason = f"Trend expansion active (EMA9 {m5_e9:.2f} < EMA20 {m5_e20:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif close_p >= vwap:
                        decision = "SKIPPED_ABOVE_VWAP"
                        reason = f"5M Close above Session VWAP ({close_p:.2f} >= {vwap:.2f})"
                        self.armed_states[symbol].is_armed = False
                    elif self.use_liquidity_sweep and not has_sweep:
                        decision = "SKIPPED_SWEEP_UNCONFIRMED"
                        reason = f"5M Swing High ({sw_high_5m:.2f}) liquidity sweep not satisfied"
                        self.armed_states[symbol].is_armed = False
                    else:
                        decision = "ARMED_SELL"
                        reason = "1H Bearish + 5M Compression + VWAP + Sweep Confirmed"

        if decision.startswith("FIRED_BAR_CLOSE"):
            target_risk = None
            account = mt5.account_info()
            bal_base = float(account.balance) if account else 5000.0
            active_risk_pct = self.get_effective_risk_pct(symbol, adx, close_p, h1_e20, atr)
            standard_risk = bal_base * (active_risk_pct / 100.0)
            if self.daily_starting_equity > 0:
                cur_equity = float(account.equity) if account else self.daily_starting_equity
                cur_daily_loss = max(0.0, self.daily_starting_equity - cur_equity)
                max_allowed_loss = self.daily_starting_equity * (self.daily_cb_pct / 100.0)
                remaining_cushion = max(0.0, max_allowed_loss - cur_daily_loss)
                if standard_risk > remaining_cushion and remaining_cushion > 0:
                    target_risk = remaining_cushion
                    print(f"\n>>> [{symbol} DYNAMIC CUSHION SIZING] Standard risk (${standard_risk:.2f} @ {active_risk_pct:.2f}%) scaled to fit remaining CB cushion (${target_risk:.2f})! <<<")
                else:
                    target_risk = standard_risk
            else:
                target_risk = standard_risk

            tot_lots, p_lots, r_lots = self.calculate_lots(symbol, sl_dist, target_risk_dollars=target_risk)
            planned_lots = tot_lots
            
            raw_tick = mt5.symbol_info_tick(symbol)
            if raw_tick is not None and raw_tick.ask > 0 and raw_tick.bid > 0 and raw_tick.ask >= raw_tick.bid:
                spread = raw_tick.ask - raw_tick.bid
                cfg = CONFIGS[symbol]
                if spread <= cfg.max_allowed_spread:
                    ref_price = raw_tick.ask if direction == 1 else raw_tick.bid
                    planned_entry = round(ref_price, digits)
                    if direction == 1:
                        planned_sl = round(ref_price - sl_dist, digits)
                        planned_tp1 = round(ref_price + tp1_dist, digits)
                        planned_tp2 = round(ref_price + tp2_dist, digits)
                    else:
                        planned_sl = round(ref_price + sl_dist, digits)
                        planned_tp1 = round(ref_price - tp1_dist, digits)
                        planned_tp2 = round(ref_price - tp2_dist, digits)

                    state = PreArmedState(
                        is_armed=False,
                        direction=direction,
                        armed_bar_time=c_time,
                        target_close_time=c_time,
                        prev_ema9=float(m5_e9),
                        prev_ema20=float(m5_e20),
                        h1_bias=int(bias),
                        h1_e20=float(h1_e20),
                        h1_adx=float(adx),
                        h1_atr=float(atr),
                        vwap_5m=float(vwap),
                        sl_distance=float(sl_dist),
                        tp1_distance=float(tp1_dist),
                        tp2_distance=float(tp2_dist),
                        projected_lots=tot_lots,
                        partial_lots=p_lots,
                        runner_lots=r_lots
                    )

                    print(f"\n>>> [BAR CLOSE FLIP CONFIRMED (BAR-CLOSE MODE)] FIRING IMMEDIATE TWIN ORDERS ON {symbol}! <<<")
                    self.execute_twin_orders(symbol, state, ref_price, spread)
                else:
                    decision = "ABORTED_SPREAD_SPIKE"
                    reason = f"Spread {spread:.2f} > max allowed {cfg.max_allowed_spread:.2f}"
                    self.notifier.notify_setup_aborted(symbol, "BUY" if direction == 1 else "SELL", reason, ref_price if 'ref_price' in locals() else close_p, spread)
            else:
                decision = "ABORTED_NULL_TICK"
                reason = "MT5 returned null tick at bar close"
                self.notifier.notify_setup_aborted(symbol, "BUY" if direction == 1 else "SELL", reason, close_p, 0.0)

        elif decision.startswith("ARMED"):
            target_risk = None
            account = mt5.account_info()
            bal_base = float(account.balance) if account else 5000.0
            active_risk_pct = self.get_effective_risk_pct(symbol, adx, close_p, h1_e20, atr)
            standard_risk = bal_base * (active_risk_pct / 100.0)
            if self.daily_starting_equity > 0:
                cur_equity = float(account.equity) if account else self.daily_starting_equity
                cur_daily_loss = max(0.0, self.daily_starting_equity - cur_equity)
                max_allowed_loss = self.daily_starting_equity * (self.daily_cb_pct / 100.0)
                remaining_cushion = max(0.0, max_allowed_loss - cur_daily_loss)
                if standard_risk > remaining_cushion and remaining_cushion > 0:
                    target_risk = remaining_cushion
                    print(f"\n>>> [{symbol} DYNAMIC CUSHION SIZING (PRE-ARM)] Standard risk (${standard_risk:.2f} @ {active_risk_pct:.2f}%) scaled to fit remaining CB cushion (${target_risk:.2f})! <<<")
                else:
                    target_risk = standard_risk
            else:
                target_risk = standard_risk

            tot_lots, p_lots, r_lots = self.calculate_lots(symbol, sl_dist, target_risk_dollars=target_risk)
            planned_lots = tot_lots
            target_close = c_time + pd.Timedelta(minutes=5)
            if direction == 1:
                planned_entry = round(high_p, digits)
                planned_sl = round(high_p - sl_dist, digits)
                planned_tp1 = round(high_p + tp1_dist, digits)
                planned_tp2 = round(high_p + tp2_dist, digits)
            else:
                planned_entry = round(low_p, digits)
                planned_sl = round(low_p + sl_dist, digits)
                planned_tp1 = round(low_p - tp1_dist, digits)
                planned_tp2 = round(low_p - tp2_dist, digits)

            self.armed_states[symbol] = PreArmedState(
                is_armed=True,
                direction=direction,
                armed_bar_time=c_time,
                target_close_time=target_close,
                prev_ema9=float(m5_e9),
                prev_ema20=float(m5_e20),
                h1_bias=int(bias),
                h1_e20=float(h1_e20),
                h1_adx=float(adx),
                h1_atr=float(atr),
                vwap_5m=float(vwap),
                sl_distance=float(sl_dist),
                tp1_distance=float(tp1_dist),
                tp2_distance=float(tp2_dist),
                projected_lots=tot_lots,
                partial_lots=p_lots,
                runner_lots=r_lots
            )

            # Dispatch remote Setup Armed Alert
            armed_key = f"{symbol}_{c_time_str}"
            if armed_key not in self.notified_armed_setups:
                self.notified_armed_setups[armed_key] = datetime.now(timezone.utc)
                dir_str = "BUY" if direction == 1 else "SELL"
                self.notifier.notify_setup_armed(
                    symbol=symbol,
                    direction=dir_str,
                    planned_entry=planned_entry,
                    planned_sl=planned_sl,
                    planned_tp1=planned_tp1,
                    planned_lots=tot_lots,
                    reason=reason,
                    bar_time=c_time
                )

        # Log complete calculation record to CSV and surveillance log
        log_record = {
            "timestamp_utc": c_time_str,
            "timestamp_ist": ist_time_str,
            "symbol": symbol,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": vol,
            "ema9_5m": round(m5_e9, digits),
            "ema20_5m": round(m5_e20, digits),
            "ema_gap": round(ema_gap, digits),
            "ema_gap_ratio_atr": round(ema_gap_ratio, 3),
            "vwap_5m": round(vwap, digits),
            "bias_1h": bias,
            "ema9_1h": round(h1_e9, digits),
            "ema20_1h": round(h1_e20, digits),
            "adx_1h": round(adx, 2),
            "atr_1h": round(atr, digits),
            "h1_ema20_level": round(h1_e20, digits),
            "swing_low_5m": round(sw_low_5m, digits),
            "swing_high_5m": round(sw_high_5m, digits),
            "swing_low_2h": round(sw_low_2h, digits),
            "swing_high_2h": round(sw_high_2h, digits),
            "sweep_detected": has_sweep,
            "sweep_level": round(sweep_lvl, digits),
            "swept_pts": round(pts, digits),
            "decision": decision,
            "reason": reason,
            "planned_entry": planned_entry,
            "planned_sl": planned_sl,
            "planned_tp1": planned_tp1,
            "planned_tp2": planned_tp2,
            "planned_lots": planned_lots
        }
        self.audit_logger.log_candle(log_record)
        self.write_live_state(symbol)

        # Terminal Visual Card
        bias_label = "BULLISH (+1)" if bias == 1 else ("BEARISH (-1)" if bias == -1 else "NEUTRAL (0)")
        adx_status = "PASS" if adx >= cfg.adx_min else "FAIL"
        sweep_str = f"CONFIRMED (Swept {sweep_lvl:.{digits}f} by {pts:.{digits}f} pts)" if has_sweep else "NO SWEEP"
        
        print("\n" + "=" * 80)
        print(f"  [CANDLE EVALUATION AUDIT] {symbol} | 5M: {c_time.strftime('%H:%M')} UTC ({c_time.astimezone(self.tz_ist).strftime('%H:%M')} IST) | 1H: {h1_time.strftime('%H:%M')} UTC ({h1_ist_str} IST)")
        print("=" * 80)
        print(f"  * Candle OHLC:    Open: {open_p:.{digits}f} | High: {high_p:.{digits}f} | Low: {low_p:.{digits}f} | Close: {close_p:.{digits}f} | Vol: {int(vol):,}")
        print(f"  * 5M Indicators:  EMA9: {m5_e9:.{digits}f} | EMA20: {m5_e20:.{digits}f} | Gap: {ema_gap:.{digits}f} ({ema_gap_ratio:.2f}x ATR) | VWAP: {vwap:.{digits}f}")
        print(f"  * 1H Map Context: Bias: {bias_label} | ADX: {adx:.1f} ({adx_status} >= {cfg.adx_min:.1f}) | ATR: {atr:.{digits}f} | 1H EMA20: {h1_e20:.{digits}f}")
        print(f"  * Swing Levels:   5M Low: {sw_low_5m:.{digits}f} | 5M High: {sw_high_5m:.{digits}f} | 2H Low: {sw_low_2h:.{digits}f} | 2H High: {sw_high_2h:.{digits}f}")
        print(f"  * 5M Sweep Check: {sweep_str}")
        if decision.startswith("ARMED") or decision.startswith("FIRED"):
            print(f"  * DECISION:       >>> {decision} <<<")
            print(f"    Planned Setup:  Entry: {planned_entry:.{digits}f} | SL: {planned_sl:.{digits}f} | TP1: {planned_tp1:.{digits}f} | TP2: {planned_tp2:.{digits}f} | Lots: {planned_lots}")
        else:
            print(f"  * DECISION:       {decision} -> {reason}")
        print("=" * 80 + "\n")

    def process_tick_stream_last_2min(self, symbol: str, seconds_left: float):
        """High-frequency tick handler active during the last 2 minutes of the armed candle."""
        if self.circuit_breaker_active or getattr(self, 'challenge_target_reached', False):
            self.armed_states[symbol].is_armed = False
            return

        state = self.armed_states[symbol]

        # 1. News shield check during countdown
        if self.use_news_shield:
            active_shield = self.news_engine.get_active_news_shield(datetime.now(timezone.utc))
            if active_shield:
                dir_str = "BUY" if state.direction == 1 else "SELL"
                abort_reason = f"High-Impact News Shield activated ({active_shield['title']} at {active_shield['event_time_str']} UTC)"
                print(f"\n[SETUP ABORTED] {symbol} {dir_str} cancelled: {abort_reason}")
                self.notifier.notify_setup_aborted(symbol, dir_str, abort_reason, 0.0, 0.0)
                state.is_armed = False
                return

        # 2. Fetch raw tick and check for valid tick & spread spike
        try:
            raw_tick = mt5.symbol_info_tick(symbol)
        except Exception:
            raw_tick = None

        if raw_tick is None:
            if seconds_left <= 0.25:
                dir_str = "BUY" if state.direction == 1 else "SELL"
                abort_msg = "MT5 returned null tick at 0.0s bar close"
                print(f"\n[SETUP ABORTED] {symbol} {dir_str} cancelled: {abort_msg}")
                self.notifier.notify_setup_aborted(symbol, dir_str, abort_msg, 0.0, 0.0)
                state.is_armed = False
            return

        bid = float(raw_tick.bid)
        ask = float(raw_tick.ask)
        if bid <= 0.0 or ask <= 0.0 or ask < bid:
            return

        spread = ask - bid
        cfg = CONFIGS[symbol]

        if spread > cfg.max_allowed_spread:
            # Spread spike detected (e.g. news or rollover)
            if seconds_left <= 0.25:
                dir_str = "BUY" if state.direction == 1 else "SELL"
                ref_p = ask if state.direction == 1 else bid
                abort_msg = f"Spread spiked to {spread:.2f} > max limit {cfg.max_allowed_spread:.2f}"
                print(f"\n[SETUP ABORTED] {symbol} {dir_str} cancelled: {abort_msg}")
                self.notifier.notify_setup_aborted(symbol, dir_str, abort_msg, ref_p, spread)
                state.is_armed = False
            return

        ref_price = ask if state.direction == 1 else bid

        # Zero-allocation recursive float EMA projection
        proj_e9 = ref_price * self.alpha9 + state.prev_ema9 * (1.0 - self.alpha9)
        proj_e20 = ref_price * self.alpha20 + state.prev_ema20 * (1.0 - self.alpha20)

        # Check if projected flip is valid
        is_flip_valid = False
        if state.direction == 1:
            if (proj_e9 > proj_e20) and (ref_price > state.vwap_5m) and (ref_price > state.h1_e20):
                is_flip_valid = True
        else:
            if (proj_e9 < proj_e20) and (ref_price < state.vwap_5m) and (ref_price < state.h1_e20):
                is_flip_valid = True

        # Print countdown heartbeat every ~15 seconds
        sec_int = int(seconds_left)
        if sec_int in [120, 90, 60, 30, 10, 5]:
            status = "FLIP HOLDING" if is_flip_valid else "FLIP FAILING"
            print(f"[{symbol} TICK MONITOR] T-{sec_int:02d}s | Price: {ref_price:.2f} | Spread: {spread:.2f} | E9 vs E20: ({proj_e9:.2f} / {proj_e20:.2f}) -> [{status}]")

        # Zero-Latency Fire or Abort at Candle Boundary (< 0.25s to close)
        if seconds_left <= 0.25:
            if is_flip_valid:
                print(f"\n>>> [0.0s BAR CLOSE CONFIRMED] FIRING ZERO-LATENCY TWIN ORDERS ON {symbol}! <<<")
                self.execute_twin_orders(symbol, state, ref_price, spread)
                state.is_armed = False  # Reset armed state
            else:
                dir_str = "BUY" if state.direction == 1 else "SELL"
                reasons = []
                if state.direction == 1:
                    if proj_e9 <= proj_e20:
                        reasons.append(f"EMA9 ({proj_e9:.2f}) <= EMA20 ({proj_e20:.2f})")
                    if ref_price <= state.vwap_5m:
                        reasons.append(f"Price below VWAP ({ref_price:.2f} <= {state.vwap_5m:.2f})")
                    if ref_price <= state.h1_e20:
                        reasons.append(f"Price below 1H EMA20 ({ref_price:.2f} <= {state.h1_e20:.2f})")
                else:
                    if proj_e9 >= proj_e20:
                        reasons.append(f"EMA9 ({proj_e9:.2f}) >= EMA20 ({proj_e20:.2f})")
                    if ref_price >= state.vwap_5m:
                        reasons.append(f"Price above VWAP ({ref_price:.2f} >= {state.vwap_5m:.2f})")
                    if ref_price >= state.h1_e20:
                        reasons.append(f"Price above 1H EMA20 ({ref_price:.2f} >= {state.h1_e20:.2f})")

                failure_detail = ", ".join(reasons) if reasons else "Flip conditions unmet"
                abort_msg = f"0.0s Bar Close Flip Failed: {failure_detail}"
                print(f"\n[SETUP ABORTED] {symbol} {dir_str} cancelled at bar close: {abort_msg}")
                self.notifier.notify_setup_aborted(symbol, dir_str, abort_msg, ref_price, spread)
                state.is_armed = False

    def execute_twin_orders(self, symbol: str, state: PreArmedState, entry_price: float, spread: float):
        sym_info = mt5.symbol_info(symbol)
        digits = sym_info.digits if sym_info else 2
        fill_mode = self.get_safe_filling_mode(sym_info)
        magic = CONFIGS[symbol].magic_number

        if state.direction == 1:
            order_type = mt5.ORDER_TYPE_BUY
            sl = round(entry_price - state.sl_distance, digits)
            tp1 = round(entry_price + state.tp1_distance, digits)
            tp2 = round(entry_price + state.tp2_distance, digits)
            dir_str = "BUY"
        else:
            order_type = mt5.ORDER_TYPE_SELL
            sl = round(entry_price + state.sl_distance, digits)
            tp1 = round(entry_price - state.tp1_distance, digits)
            tp2 = round(entry_price - state.tp2_distance, digits)
            dir_str = "SELL"

        print(f"[{symbol}] Sending Order A (TP1): {dir_str} {state.partial_lots} lots @ {entry_price:.{digits}f} | SL: {sl:.{digits}f} | TP1: {tp1:.{digits}f}")
        print(f"[{symbol}] Sending Order B (Runner): {dir_str} {state.runner_lots} lots @ {entry_price:.{digits}f} | SL: {sl:.{digits}f} | TP2: {tp2:.{digits}f}")

        if self.dry_run:
            print(f"[LiveBot] DRY-RUN SUCCESS: Simulated twin orders placed with 0 slippage.")
            self.active_positions[symbol] = ActiveTwinPosition(
                symbol=symbol,
                direction=dir_str,
                entry_price=entry_price,
                entry_spread=spread,
                entry_time=datetime.now(timezone.utc),
                ticket_a=999901,
                ticket_b=999902,
                sl_price=sl,
                tp1_price=tp1,
                tp2_price=tp2,
                lots_a=state.partial_lots,
                lots_b=state.runner_lots
            )
            self.notifier.notify_trade_opened(
                symbol=symbol,
                direction=dir_str,
                entry_price=entry_price,
                spread=spread,
                total_lots=round(state.partial_lots + state.runner_lots, 2),
                partial_lots=state.partial_lots,
                runner_lots=state.runner_lots,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                ticket_a=999901,
                ticket_b=999902,
                is_dry_run=True
            )
            return

        # 1. Order A (50% TP1)
        req_a = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": state.partial_lots,
            "type": order_type,
            "price": entry_price,
            "sl": sl,
            "tp": tp1,
            "deviation": 20,
            "magic": magic,
            "comment": "DCC_TP1_Part",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": fill_mode,
        }
        res_a = mt5.order_send(req_a)

        # 2. Order B (50% Runner)
        req_b = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": state.runner_lots,
            "type": order_type,
            "price": entry_price,
            "sl": sl,
            "tp": tp2,
            "deviation": 20,
            "magic": magic,
            "comment": "DCC_TP2_Runner",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": fill_mode,
        }
        res_b = mt5.order_send(req_b)

        ticket_a = res_a.order if (res_a and res_a.retcode == mt5.TRADE_RETCODE_DONE) else 0
        ticket_b = res_b.order if (res_b and res_b.retcode == mt5.TRADE_RETCODE_DONE) else 0

        if ticket_a and ticket_b:
            print(f"[{symbol} SUCCESS] Both tickets executed! Ticket A: #{ticket_a} | Ticket B: #{ticket_b}")
            self.active_positions[symbol] = ActiveTwinPosition(
                symbol=symbol,
                direction=dir_str,
                entry_price=entry_price,
                entry_spread=spread,
                entry_time=datetime.now(timezone.utc),
                ticket_a=ticket_a,
                ticket_b=ticket_b,
                sl_price=sl,
                tp1_price=tp1,
                tp2_price=tp2,
                lots_a=state.partial_lots,
                lots_b=state.runner_lots
            )
            self.notifier.notify_trade_opened(
                symbol=symbol,
                direction=dir_str,
                entry_price=entry_price,
                spread=spread,
                total_lots=round(state.partial_lots + state.runner_lots, 2),
                partial_lots=state.partial_lots,
                runner_lots=state.runner_lots,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                ticket_a=ticket_a,
                ticket_b=ticket_b,
                is_dry_run=False
            )
            self.write_live_state(symbol)
        else:
            ret_a = res_a.retcode if res_a else "None"
            ret_b = res_b.retcode if res_b else "None"
            print(f"[WARN] Order execution warning: RetCode A={ret_a}, RetCode B={ret_b}")

    def manage_active_positions(self):
        """Monitors open positions: Automatically moves Runner SL to Breakeven when Ticket A closes, and cleans up when closed."""
        for symbol in list(self.active_positions.keys()):
            pos_info = self.active_positions[symbol]

            # Check if tickets are still open in MT5
            open_positions = mt5.positions_get(symbol=symbol)
            open_tickets = [p.ticket for p in open_positions] if open_positions else []

            # 1. If both tickets are closed, remove from tracking immediately
            if pos_info.ticket_a not in open_tickets and pos_info.ticket_b not in open_tickets:
                # Fetch Deal History for Ticket A
                pnl_a = pos_info.pnl_a
                deals_a = None
                if pnl_a == 0.0:
                    try:
                        deals_a = mt5.history_deals_get(position=pos_info.ticket_a)
                        if deals_a:
                            exit_deals_a = [d for d in deals_a if d.entry == 1]
                            if exit_deals_a:
                                pnl_a = sum(float(d.profit + d.commission + d.swap) for d in exit_deals_a)
                    except Exception:
                        pass

                # Fetch Deal History for Ticket B
                pnl_b = pos_info.pnl_b
                exit_price_b = 0.0
                exit_comment_b = ""
                deals_b = None
                try:
                    deals_b = mt5.history_deals_get(position=pos_info.ticket_b)
                    if deals_b:
                        exit_deals_b = [d for d in deals_b if d.entry == 1]
                        if exit_deals_b:
                            pnl_b = sum(float(d.profit + d.commission + d.swap) for d in exit_deals_b)
                            exit_price_b = float(exit_deals_b[-1].price)
                            exit_comment_b = str(exit_deals_b[-1].comment)
                except Exception:
                    pass

                # Fallback calculation if dry run or broker history deals pending
                if pnl_a == 0.0 and pnl_b == 0.0 and (self.dry_run or not deals_b):
                    sym_info_calc = mt5.symbol_info(symbol)
                    contract_size = sym_info_calc.trade_contract_size if sym_info_calc else (10.0 if "NAS" in symbol else 100.0)
                    if pos_info.runner_moved_to_be:
                        pnl_a = abs(pos_info.tp1_price - pos_info.entry_price) * pos_info.lots_a * contract_size
                        pnl_b = 0.0
                    else:
                        pnl_a = -abs(pos_info.entry_price - pos_info.sl_price) * pos_info.lots_a * contract_size
                        pnl_b = -abs(pos_info.entry_price - pos_info.sl_price) * pos_info.lots_b * contract_size

                total_pnl = round(pnl_a + pnl_b, 2)

                # Classify exact outcome
                if pos_info.runner_moved_to_be:
                    if pnl_b > 0 and ("[tp" in exit_comment_b.lower() or (pos_info.tp2_price > 0 and abs(exit_price_b - pos_info.tp2_price) <= abs(exit_price_b - pos_info.be_sl))):
                        reason = "🎯 FULL TAKE PROFIT 2 (2.0R)"
                    else:
                        reason = "🛡️ RUNNER STOPPED AT BREAKEVEN (0.0R)"
                else:
                    if total_pnl < 0:
                        reason = "🛑 STOP LOSS HIT (-1.0R Initial Stop)"
                    elif total_pnl > 0:
                        reason = "🎯 TAKE PROFIT REACHED"
                    else:
                        reason = "⚠️ CLOSED AT ENTRY / MANUAL"

                # Calculate Today's Realized Performance & Daily Drawdown Metrics
                account = mt5.account_info()
                cur_equity = float(account.equity) if account else self.daily_starting_equity
                day_pnl = cur_equity - self.daily_starting_equity if self.daily_starting_equity > 0 else total_pnl
                day_pnl_pct = (day_pnl / self.daily_starting_equity) * 100.0 if self.daily_starting_equity > 0 else 0.0
                cur_daily_loss = max(0.0, -day_pnl)
                daily_dd_pct = (cur_daily_loss / self.daily_starting_equity) * 100.0 if self.daily_starting_equity > 0 else 0.0
                max_allowed_loss = self.daily_starting_equity * (self.daily_cb_pct / 100.0) if self.daily_starting_equity > 0 else 150.0
                remaining_cushion = max(0.0, max_allowed_loss - cur_daily_loss)

                print(f"\n[{symbol} TRADE COMPLETED] {reason} | Order A: ${pnl_a:+.2f} | Order B: ${pnl_b:+.2f} | Total: ${total_pnl:+.2f}")
                print(f"[{symbol} DAY PERFORMANCE] Net PnL: ${day_pnl:+.2f} ({day_pnl_pct:+.2f}%) | Daily DD: -{daily_dd_pct:.2f}% | CB Cushion: ${remaining_cushion:.2f}\n")

                self.notifier.notify_trade_closed(
                    symbol=symbol,
                    ticket=pos_info.ticket_b,
                    exit_reason=reason,
                    pnl=total_pnl,
                    ticket_a=pos_info.ticket_a,
                    pnl_a=pnl_a,
                    pnl_b=pnl_b,
                    day_pnl=day_pnl,
                    day_pnl_pct=day_pnl_pct,
                    daily_dd_pct=daily_dd_pct,
                    remaining_cushion=remaining_cushion,
                    daily_cb_pct=self.daily_cb_pct
                )
                del self.active_positions[symbol]
                if not hasattr(self, 'recent_trade_outcomes'):
                    self.recent_trade_outcomes = []
                self.recent_trade_outcomes.append(total_pnl > 0)
                if len(self.recent_trade_outcomes) > 5:
                    self.recent_trade_outcomes.pop(0)
                self.write_live_state(symbol)
                continue

            # 2. If runner already moved to BE, skip breakeven modification
            if pos_info.runner_moved_to_be:
                continue

            # 3. If Ticket A has closed (hit TP1) but Ticket B is still open -> Move SL to Breakeven
            if pos_info.ticket_a not in open_tickets and pos_info.ticket_b in open_tickets:
                sym_info = mt5.symbol_info(symbol)
                digits = sym_info.digits if sym_info else 2
                point = sym_info.point if sym_info else 0.01
                min_stop_dist = (sym_info.trade_stops_level if sym_info else 10) * point

                cur_tick = mt5.symbol_info_tick(symbol)
                if not cur_tick:
                    continue

                # Breakeven Stop: Entry + Spread (or Entry - Spread for SELL)
                if pos_info.direction == "BUY":
                    target_be = round(pos_info.entry_price + pos_info.entry_spread, digits)
                    # For a BUY, SL must be at least min_stop_dist below current Bid
                    max_allowed_sl = round(cur_tick.bid - min_stop_dist, digits)
                    if target_be > max_allowed_sl:
                        # Price temporarily pulling back close to BE, wait for tick breathing room
                        continue
                    be_sl = target_be
                else:
                    target_be = round(pos_info.entry_price - pos_info.entry_spread, digits)
                    # For a SELL, SL must be at least min_stop_dist above current Ask
                    min_allowed_sl = round(cur_tick.ask + min_stop_dist, digits)
                    if target_be < min_allowed_sl:
                        continue
                    be_sl = target_be

                # Fetch realized profit on Ticket A from MT5 deals
                pnl_a = 0.0
                try:
                    deals_a = mt5.history_deals_get(position=pos_info.ticket_a)
                    if deals_a:
                        exit_deals_a = [d for d in deals_a if d.entry == 1]
                        if exit_deals_a:
                            pnl_a = sum(float(d.profit + d.commission + d.swap) for d in exit_deals_a)
                except Exception as e:
                    print(f"[{symbol}] Error fetching history deals for #{pos_info.ticket_a}: {e}")

                if pnl_a == 0.0 and pos_info.tp1_price > 0:
                    contract_size = sym_info.trade_contract_size if sym_info else (10.0 if "NAS" in symbol else 100.0)
                    pnl_a = round(abs(pos_info.tp1_price - pos_info.entry_price) * pos_info.lots_a * contract_size, 2)

                pos_info.pnl_a = pnl_a
                pos_info.be_sl = be_sl

                # In dry-run mode, simulate breakeven instantly
                if self.dry_run:
                    print(f"\n>>> [{symbol} BREAKEVEN AUTOMATOR (DRY RUN)] Ticket A hit TP1! Runner #{pos_info.ticket_b} SL shifted to BREAKEVEN ({be_sl:.{digits}f})! Profit Banked: ${pnl_a:+.2f} <<<\n")
                    pos_info.runner_moved_to_be = True
                    pos_info.ticket_a_closed = True
                    self.notifier.notify_tp1_breakeven(symbol, pos_info.ticket_a, pos_info.ticket_b, be_sl, profit_a=pnl_a)
                    self.write_live_state(symbol)
                    continue

                # Get current position B details
                matching_b = [p for p in open_positions if p.ticket == pos_info.ticket_b]
                if not matching_b:
                    continue
                pos_b = matching_b[0]

                req_mod = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": pos_info.ticket_b,
                    "symbol": symbol,
                    "sl": be_sl,
                    "tp": pos_b.tp
                }
                res_mod = mt5.order_send(req_mod)
                if res_mod and res_mod.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"\n>>> [{symbol} BREAKEVEN AUTOMATOR] Ticket A hit TP1! Runner #{pos_info.ticket_b} SL shifted to BREAKEVEN ({be_sl:.{digits}f})! Profit Banked: ${pnl_a:+.2f} | Trade is 100% Risk-Free! <<<\n")
                    pos_info.runner_moved_to_be = True
                    pos_info.ticket_a_closed = True
                    self.notifier.notify_tp1_breakeven(symbol, pos_info.ticket_a, pos_info.ticket_b, be_sl, profit_a=pnl_a)
                    self.write_live_state(symbol)
                else:
                    err_msg = res_mod.comment if res_mod else "None"
                    print(f"[{symbol}] Breakeven modification pending ({err_msg}). Will retry next tick.")

    def write_live_state(self, symbol: Optional[str] = None):
        """Non-blocking exporter that writes live bot telemetry, active positions,
        and trade geometry to visualizer/live_state.json for the remote dashboard."""
        try:
            account = mt5.account_info()
            cur_equity = float(account.equity) if account else self.daily_starting_equity
            cur_balance = float(account.balance) if account else self.daily_starting_equity
            day_pnl = cur_equity - self.daily_starting_equity if self.daily_starting_equity > 0 else 0.0
            day_pnl_pct = (day_pnl / self.daily_starting_equity) * 100.0 if self.daily_starting_equity > 0 else 0.0
            cur_daily_loss = max(0.0, -day_pnl)
            daily_dd_pct = (cur_daily_loss / self.daily_starting_equity) * 100.0 if self.daily_starting_equity > 0 else 0.0
            max_allowed_loss = self.daily_starting_equity * (self.daily_cb_pct / 100.0) if self.daily_starting_equity > 0 else 150.0
            remaining_cushion = max(0.0, max_allowed_loss - cur_daily_loss)
            now_utc = datetime.now(timezone.utc)

            # Build positions map
            positions_data = {}
            for sym, pos in self.active_positions.items():
                cur_profit = 0.0
                try:
                    for t in [pos.ticket_a, pos.ticket_b]:
                        if t and t > 0:
                            p_info = mt5.positions_get(ticket=t)
                            if p_info and len(p_info) > 0:
                                cur_profit += float(p_info[0].profit)
                except Exception:
                    pass

                positions_data[sym] = {
                    "active": True,
                    "symbol": sym,
                    "direction": pos.direction,
                    "entry_price": pos.entry_price,
                    "entry_time": int(pos.entry_time.timestamp()) if pos.entry_time else 0,
                    "entry_spread": pos.entry_spread,
                    "sl_price": pos.sl_price,
                    "tp1_price": pos.tp1_price,
                    "tp2_price": pos.tp2_price,
                    "be_sl": pos.be_sl,
                    "runner_moved_to_be": pos.runner_moved_to_be,
                    "ticket_a": pos.ticket_a,
                    "ticket_b": pos.ticket_b,
                    "lots_a": pos.lots_a,
                    "lots_b": pos.lots_b,
                    "pnl_a": pos.pnl_a,
                    "current_profit": round(cur_profit, 2)
                }

            # Build armed setups map
            armed_data = {}
            for sym, armed in self.armed_states.items():
                if armed.is_armed:
                    est_entry = 0.0
                    try:
                        tick = mt5.symbol_info_tick(sym)
                        if tick:
                            est_entry = float(tick.ask if armed.direction == 1 else tick.bid)
                    except Exception:
                        pass

                    armed_data[sym] = {
                        "is_armed": True,
                        "direction": "BUY" if armed.direction == 1 else "SELL",
                        "projected_entry": round(est_entry, 2),
                        "sl_distance": armed.sl_distance,
                        "tp1_distance": armed.tp1_distance,
                        "tp2_distance": armed.tp2_distance,
                        "projected_lots": armed.projected_lots,
                        "vwap_5m": armed.vwap_5m,
                        "h1_e20": armed.h1_e20
                    }
                else:
                    armed_data[sym] = {"is_armed": False}

            state_payload = {
                "account": {
                    "id": account.login if account else 0,
                    "server": account.server if account else "Demo",
                    "equity": round(cur_equity, 2),
                    "balance": round(cur_balance, 2),
                    "daily_starting_equity": round(self.daily_starting_equity, 2),
                    "today_pnl": round(day_pnl, 2),
                    "today_pnl_pct": round(day_pnl_pct, 2),
                    "daily_dd_pct": round(daily_dd_pct, 2),
                    "daily_cb_pct": self.daily_cb_pct,
                    "remaining_cushion": round(remaining_cushion, 2),
                    "session": "PAUSED_CB" if self.circuit_breaker_active else self.get_session_state(now_utc),
                    "bot_heartbeat": now_utc.isoformat(),
                    "circuit_breaker_active": self.circuit_breaker_active
                },
                "positions": positions_data,
                "armed_setups": armed_data,
                "last_update_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
            }

            # Lock-free enqueue: worker thread serializes and writes atomically to disk in background
            if hasattr(self, '_state_queue'):
                if self._state_queue.full():
                    try:
                        self._state_queue.get_nowait()
                    except Exception:
                        pass
                try:
                    self._state_queue.put_nowait(state_payload)
                except Exception:
                    pass
        except Exception:
            pass

    def _state_exporter_worker(self):
        """Dedicated single background daemon worker for live_state.json.
        Runs independently in the background, eliminating thread thrashing and ensuring
        main bot execution loop returns in < 0.002ms with zero disk I/O contention."""
        state_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visualizer", "live_state.json")
        temp_file_path = state_file_path + ".tmp"
        while True:
            try:
                state_payload = self._state_queue.get()
                if state_payload is None:
                    break
                with open(temp_file_path, "w", encoding="utf-8") as f:
                    json.dump(state_payload, f, indent=2)
                os.replace(temp_file_path, state_file_path)
                self._state_queue.task_done()
            except Exception:
                pass

    def get_session_state(self, time_or_hour: Any, weekday: Optional[int] = None) -> str:
        """Returns the trading window / killzone state for a given UTC datetime or hour.
        Seamlessly detects weekend market closure (Saturday, Sunday, and Friday post-close).
        """
        if isinstance(time_or_hour, datetime):
            h = time_or_hour.hour
            w = time_or_hour.weekday()
        else:
            h = int(time_or_hour)
            w = weekday

        # Weekend Market Closed Check (Saturday = 5, Sunday = 6, Friday post-close = 4 and h >= 21)
        if w is not None:
            if w in [5, 6] or (w == 4 and h >= self.entry_end_hour_utc):
                return "PAUSED_WEEKEND"

        if h == 9:
            return "PAUSED_TRAP_09"
        elif h == 13:
            return "PAUSED_TRAP_13"
        elif h < self.entry_start_hour_utc or h >= self.entry_end_hour_utc:
            return "PAUSED_ASIAN"
        elif self.entry_start_hour_utc <= h < 12:
            return "ACTIVE_LONDON"
        else:  # 12 <= h < 21 and h != 13
            return "ACTIVE_NY"

    def check_session_and_trap_alerts(self, now_utc: datetime, is_startup: bool = False):
        """Monitors and broadcasts trading pause and resume milestones to Discord/Telegram.
        Transitions smoothly across Weekend closures, Killzone pauses, Dead Trap Hours, and Active Sessions.
        """
        new_state = self.get_session_state(now_utc)

        if is_startup:
            self.current_session_state = new_state
            if new_state == "PAUSED_WEEKEND":
                print("\n" + "=" * 80)
                print("[=== CURRENT STATUS: PAUSED - WEEKEND MARKET CLOSED ===]")
                print("Resumes: Monday 06:00 UTC (11:30 IST) at London Session Open")
                print("=" * 80 + "\n")
                self.notifier.notify_trading_paused(
                    zone_title="Weekend Market Closed (Saturday & Sunday)",
                    reason="Bot started during weekend market closure. Forex & CFD markets (XAUUSD & NAS100) are closed.",
                    resume_time_str="Monday 06:00 UTC (11:30 IST) - London Session Open",
                    is_startup=True
                )
            elif new_state == "PAUSED_TRAP_09":
                print("\n" + "=" * 80)
                print("[=== CURRENT STATUS: PAUSED IN MORNING DEAD TRAP HOUR ===]")
                print("Resumes: 10:00 UTC (15:30 IST)")
                print("=" * 80 + "\n")
                self.notifier.notify_trading_paused(
                    zone_title="Morning Dead Trap Hour / Killzone Pause (09:00-10:00 UTC / 14:30-15:30 IST)",
                    reason="Bot started during London midday lull. Scanning suspended to avoid false breakout traps.",
                    resume_time_str="10:00 UTC (15:30 IST)",
                    is_startup=True
                )
            elif new_state == "PAUSED_TRAP_13":
                print("\n" + "=" * 80)
                print("[=== CURRENT STATUS: PAUSED IN US DEAD TRAP HOUR ===]")
                print("Resumes: 14:00 UTC (19:30 IST)")
                print("=" * 80 + "\n")
                self.notifier.notify_trading_paused(
                    zone_title="US Dead Trap Hour / Killzone Pause (13:00-14:00 UTC / 18:30-19:30 IST)",
                    reason="Bot started during US pre-market transition chop. Trade arming and execution suspended.",
                    resume_time_str="14:00 UTC (19:30 IST)",
                    is_startup=True
                )
            elif new_state == "PAUSED_ASIAN":
                print("\n" + "=" * 80)
                print("[=== CURRENT STATUS: PAUSED IN ASIAN RANGE FORMATION ===]")
                print("Resumes: 06:00 UTC (11:30 IST) at London Session Open")
                print("=" * 80 + "\n")
                self.notifier.notify_trading_paused(
                    zone_title="Asian Session Range Formation (21:00-06:00 UTC / 02:30-11:30 IST)",
                    reason="Bot started outside active trading window. Asian session is designated for liquidity range building (entries disabled).",
                    resume_time_str="06:00 UTC (11:30 IST) - London Session Open",
                    is_startup=True
                )
            return

        if new_state == self.current_session_state:
            return

        prev_state = self.current_session_state
        self.current_session_state = new_state

        if new_state == "PAUSED_WEEKEND":
            for s in self.symbols:
                self.armed_states[s].is_armed = False
            print("\n" + "=" * 80)
            print("[=== TRADING PAUSED: WEEKEND MARKET CLOSED ===]")
            print("Reason:  Forex & CFD markets closed for the weekend. Capital is 100% protected.")
            print("Resumes: Monday 06:00 UTC (11:30 IST) - London Session Open")
            print("=" * 80 + "\n")
            self.notifier.notify_trading_paused(
                zone_title="Weekend Market Closed (Saturday & Sunday)",
                reason="Forex & CFD markets closed for the weekend. All scanning and execution suspended.",
                resume_time_str="Monday 06:00 UTC (11:30 IST) - London Session Open"
            )

        elif new_state == "PAUSED_TRAP_09":
            for s in self.symbols:
                self.armed_states[s].is_armed = False
            print("\n" + "=" * 80)
            print("[=== TRADING PAUSED: MORNING DEAD TRAP HOUR (09:00 UTC / 14:30 IST) ===]")
            print("Reason:  London midday liquidity lull. High risk of false breakout traps.")
            print("Resumes: 10:00 UTC (15:30 IST)")
            print("=" * 80 + "\n")
            self.notifier.notify_trading_paused(
                zone_title="Morning Dead Trap Hour / Killzone Pause (09:00-10:00 UTC / 14:30-15:30 IST)",
                reason="London midday liquidity lull. High risk of false breakout traps.",
                resume_time_str="10:00 UTC (15:30 IST)"
            )

        elif new_state == "PAUSED_TRAP_13":
            for s in self.symbols:
                self.armed_states[s].is_armed = False
            print("\n" + "=" * 80)
            print("[=== TRADING PAUSED: US DEAD TRAP HOUR (13:00 UTC / 18:30 IST) ===]")
            print("Reason:  US pre-market transition chop. Trade arming and execution suspended.")
            print("Resumes: 14:00 UTC (19:30 IST)")
            print("=" * 80 + "\n")
            self.notifier.notify_trading_paused(
                zone_title="US Dead Trap Hour / Killzone Pause (13:00-14:00 UTC / 18:30-19:30 IST)",
                reason="US pre-market transition chop. Trade arming and execution suspended.",
                resume_time_str="14:00 UTC (19:30 IST)"
            )

        elif new_state == "PAUSED_ASIAN":
            for s in self.symbols:
                self.armed_states[s].is_armed = False
            print("\n" + "=" * 80)
            print("[=== TRADING PAUSED: END-OF-DAY / ASIAN RANGE (21:00 UTC / 02:30 IST) ===]")
            print("Reason:  Active trading window closed. Asian session builds liquidity range (entries disabled).")
            print("Resumes: 06:00 UTC (11:30 IST) - London Session Open")
            print("=" * 80 + "\n")
            self.notifier.notify_trading_paused(
                zone_title="End-of-Day / Asian Session Range Formation (21:00-06:00 UTC / 02:30-11:30 IST)",
                reason="Active trading session closed. Asian session is designated for liquidity range building (entries disabled).",
                resume_time_str="06:00 UTC (11:30 IST) - London Session Open"
            )

        elif new_state == "ACTIVE_LONDON":
            if prev_state in ["PAUSED_ASIAN", "PAUSED_WEEKEND"]:
                print("\n" + "=" * 80)
                print("[=== TRADING RESUMED: LONDON SESSION OPEN (06:00 UTC / 11:30 IST) ===]")
                print("Active Window: European / London Killzone Window")
                print("Status:        Weekend closed / Asian range concluded. Active market surveillance restored.")
                print("=" * 80 + "\n")
                self.notifier.notify_trading_resumed(
                    zone_title="London Session Opened (06:00 UTC / 11:30 IST)",
                    session_name="European / London Killzone Window",
                    details="Weekend closed / Asian range concluded. Active market surveillance and trade execution restored."
                )
            elif prev_state == "PAUSED_TRAP_09":
                print("\n" + "=" * 80)
                print("[=== TRADING RESUMED: MORNING DEAD TRAP HOUR ENDED (10:00 UTC / 15:30 IST) ===]")
                print("Active Window: Pre-New York Window")
                print("Status:        Midday trap period concluded. Active market surveillance restored.")
                print("=" * 80 + "\n")
                self.notifier.notify_trading_resumed(
                    zone_title="Morning Dead Trap Hour Ended (10:00 UTC / 15:30 IST)",
                    session_name="Pre-New York Window",
                    details="Midday trap period concluded. Active market surveillance and trade execution restored."
                )

        elif new_state == "ACTIVE_NY":
            if prev_state == "ACTIVE_LONDON":
                print("\n" + "=" * 80)
                print("[=== SESSION UPDATE: NEW YORK SESSION OPEN (12:00 UTC / 17:30 IST) ===]")
                print("Active Window: US / New York Killzone Window Active")
                print("=" * 80 + "\n")
                self.notifier.notify_session(
                    session_name="New York Session (12:00 UTC / 17:30 IST)",
                    status="OPENED - US Killzone Window Active"
                )
            elif prev_state == "PAUSED_TRAP_13":
                print("\n" + "=" * 80)
                print("[=== TRADING RESUMED: US DEAD TRAP HOUR ENDED (14:00 UTC / 19:30 IST) ===]")
                print("Active Window: New York Active Trading Session")
                print("Status:        US pre-market transition ended. Active market surveillance restored.")
                print("=" * 80 + "\n")
                self.notifier.notify_trading_resumed(
                    zone_title="US Dead Trap Hour Ended (14:00 UTC / 19:30 IST)",
                    session_name="New York Active Trading Session",
                    details="US pre-market transition ended. Active market surveillance restored."
                )

    def _run_nightly_audit_async(self, audit_date: datetime.date):
        """Executes daily PnL performance summary and nightly forensic tick reconciliation in a non-blocking background thread."""
        try:
            now_utc = datetime.now(timezone.utc)
            print(f"\n[DailySummary] Session close reached ({self.entry_end_hour_utc}:05 UTC / {now_utc.astimezone(self.tz_ist).strftime('%H:%M')} IST). Compiling daily performance scorecard...")

            # 1. Calculate and dispatch End-of-Day PnL Summary
            account = mt5.account_info()
            cur_equity = float(account.equity) if account else self.daily_starting_equity
            cur_balance = float(account.balance) if account else self.daily_starting_equity
            start_eq = self.daily_starting_equity if self.daily_starting_equity > 0 else cur_balance
            day_pnl = cur_equity - start_eq
            day_pnl_pct = (day_pnl / start_eq * 100.0) if start_eq > 0 else 0.0
            cur_daily_loss = max(0.0, -day_pnl)
            daily_dd_pct = (cur_daily_loss / start_eq * 100.0) if start_eq > 0 else 0.0
            max_allowed_loss = start_eq * (self.daily_cb_pct / 100.0)
            cushion_remaining = max(0.0, max_allowed_loss - cur_daily_loss)

            day_start_dt = datetime(audit_date.year, audit_date.month, audit_date.day, 0, 0, tzinfo=timezone.utc)
            deals = mt5.history_deals_get(day_start_dt, now_utc)
            closed_deals = []
            win_count = 0
            loss_count = 0

            if deals:
                exit_deals = [d for d in deals if d.entry == 1]
                for d in exit_deals:
                    p = float(d.profit + d.commission + d.swap)
                    if p > 0:
                        win_count += 1
                    elif p < 0:
                        loss_count += 1
                    dir_str = "BUY" if d.type == 0 else "SELL"
                    closed_deals.append({
                        "ticket": d.ticket,
                        "symbol": d.symbol,
                        "type": dir_str,
                        "volume": d.volume,
                        "profit": round(p, 2),
                        "comment": d.comment
                    })

            self.notifier.notify_daily_summary(
                date_str=audit_date.strftime("%Y-%m-%d"),
                starting_equity=start_eq,
                closing_equity=cur_equity,
                balance=cur_balance,
                trades_count=len(closed_deals),
                winning_trades=win_count,
                losing_trades=loss_count,
                daily_pnl=round(day_pnl, 2),
                daily_pnl_pct=round(day_pnl_pct, 2),
                daily_dd_pct=round(daily_dd_pct, 2),
                cushion_remaining=round(cushion_remaining, 2),
                closed_trades_details=closed_deals
            )

            # 2. Trigger automated nightly reconciliation
            print(f"[NightlyReconciler] Triggering automated nightly tick replay & deal reconciliation...")
            from nightly_reconciler import NightlyReconciler
            reconciler = NightlyReconciler(symbols=self.symbols, strategy_version=self.strategy_version)
            reconciler.run(audit_date)
        except Exception as e:
            print(f"[DailySummary/NightlyReconciler] Background audit error: {e}")

    def run(self):
        print("\n[InstitutionalDCCBot] Starting Live Monitoring Loop...")
        if self.entry_mode == "bar_close":
            print("Surveillance: 5M Bar-Close Evaluations -> Instant Twin Order Flip (Exact 1-to-1 Backtest Match)")
        else:
            print("Surveillance: Background 5M checks -> Armed Candle -> 2-Min Ultra-Light Tick Stream")
        print(f"Logging System: Live calculations saved to {self.audit_logger.log_dir}/market_calculations_YYYYMMDD.csv\n")

        self.live = Live(console=self.console, refresh_per_second=4, transient=False)
        self.live.start()
        try:
            while True:
                now_utc = datetime.now(timezone.utc)
                now_ist = now_utc.astimezone(self.tz_ist)
                seconds_into_5m = (now_utc.minute % 5) * 60 + now_utc.second + now_utc.microsecond / 1_000_000.0
                seconds_left_in_5m = 300.0 - seconds_into_5m
                m_left = int(seconds_left_in_5m // 60)
                s_left = int(seconds_left_in_5m % 60)
                is_weekend = (now_utc.weekday() in [5, 6]) or (now_utc.weekday() == 4 and now_utc.hour >= self.entry_end_hour_utc)

                # 0. Check daily rollover (00:00 UTC - skips Saturday & Sunday to preserve Friday trading day state)
                active_trading_date = get_current_trading_day_start_utc(now_utc).date()
                if self.current_trading_day != active_trading_date:
                    self.current_trading_day = active_trading_date
                    account = mt5.account_info()
                    if account:
                        self.daily_starting_equity = account.equity
                    self.circuit_breaker_active = False
                    self.session_notified = {}
                    # Reset Phase 1 daily target halt on new trading day (Phase 2 remains locked if target_locked is True)
                    if not getattr(self, 'target_locked', False):
                        self.challenge_target_reached = False
                    print(f"\n[NEW TRADING DAY: {active_trading_date}] Circuit Breaker Reset. Starting Equity Anchor: ${self.daily_starting_equity:,.2f}")

                # 0.1 Check automated session-close audit trigger (19:05 UTC / 00:35 IST)
                if (now_utc.hour >= self.entry_end_hour_utc and now_utc.minute >= 5) and (self.nightly_audit_triggered_day != active_trading_date) and not is_weekend:
                    self.nightly_audit_triggered_day = active_trading_date
                    threading.Thread(target=self._run_nightly_audit_async, args=(active_trading_date,), daemon=True).start()

                # 1. Continuous Drawdown Surveillance (Two-Layer Circuit Breaker)
                account = mt5.account_info()
                if account:
                    current_equity = account.equity
                    current_balance = account.balance
                    acc_info = account

                    # A. High-Water Mark Peak Equity Tracking
                    if current_equity > self.high_water_mark:
                        self.high_water_mark = current_equity
                        if self.config_mgr:
                            acc_key = str(account.login)
                            if acc_key in self.config_mgr.accounts:
                                self.config_mgr.accounts[acc_key]["high_water_mark"] = round(float(current_equity), 2)
                                self.config_mgr.save()

                    # B. Layer 2: Maximum Total Drawdown Circuit Breaker
                    if self.high_water_mark > 0:
                        total_dd_pct = (self.high_water_mark - current_equity) / self.high_water_mark * 100.0
                        if total_dd_pct >= self.max_cb_pct:
                            print("\n" + "!" * 80)
                            print(f"[!! EMERGENCY: MAX ACCOUNT CIRCUIT BREAKER -{total_dd_pct:.2f}% TRIGGERED !!]")
                            print(f"High-Water Mark:       ${self.high_water_mark:,.2f}")
                            print(f"Current Equity:        ${current_equity:,.2f}")
                            print(f"Circuit Breaker Level: -{self.max_cb_pct:.2f}% (Hard Limit: -{self.max_total_dd_pct:.2f}%)")
                            print(f"Safety Cushion Saved:  {self.max_total_dd_pct - self.max_cb_pct:.1f}% capital buffer before hard prop firm breach")
                            print("ACTION: Emergency close on all positions. Bot permanently halted to protect capital.")
                            print("!" * 80 + "\n")
                            self.notifier.notify_circuit_breaker("max_total", current_equity, self.max_cb_pct, self.high_water_mark - current_equity, hard_limit_pct=self.max_total_dd_pct)
                            self.emergency_close_all()
                            break

                    # C. Layer 1: Daily Drawdown Circuit Breaker
                    if self.daily_starting_equity > 0:
                        daily_pnl = current_equity - self.daily_starting_equity
                        daily_dd_pct = (self.daily_starting_equity - current_equity) / self.daily_starting_equity * 100.0
                        if daily_dd_pct >= self.daily_cb_pct and not self.circuit_breaker_active:
                            self.circuit_breaker_active = True
                            print("\n" + "!" * 80)
                            print(f"[!! DAILY {self.daily_cb_pct:.1f}% CIRCUIT BREAKER TRIGGERED !!]")
                            print(f"Daily Starting Equity: ${self.daily_starting_equity:,.2f}")
                            print(f"Current Equity:        ${current_equity:,.2f}")
                            print(f"Daily Drawdown:        -{daily_dd_pct:.2f}% (Circuit Breaker: -{self.daily_cb_pct:.2f}% | Hard Limit: -{self.daily_dd_limit_pct:.2f}%)")
                            print(f"Safety Cushion Saved:  {self.daily_dd_limit_pct - self.daily_cb_pct:.1f}% capital buffer before hard prop firm breach")
                            print(f"Daily Loss:            -${abs(daily_pnl):,.2f}")
                            print("ACTION: Trading HALTED for remainder of day. All setup arming disabled until 00:00 UTC.")
                            print("!" * 80 + "\n")
                            self.notifier.notify_circuit_breaker("daily", current_equity, self.daily_cb_pct, abs(daily_pnl), hard_limit_pct=self.daily_dd_limit_pct)
                            for s in self.symbols:
                                self.armed_states[s].is_armed = False

                    # D. Challenge Target Surveillance & Lock-in Protection
                    if self.config_mgr and acc_info:
                        acc_id_str = str(acc_info.login)
                        cfg_acc = self.config_mgr.accounts.get(acc_id_str, {})
                        if cfg_acc.get("account_lifecycle") == "challenge":
                            c_phase = cfg_acc.get("current_phase", 1)
                            ch_steps = cfg_acc.get("challenge_steps", 2)
                            is_final_phase = (ch_steps == 1) or (str(c_phase) in ["2", "funded"])
                            t_pct = cfg_acc.get("phase_1_target_pct", 8.0) if str(c_phase) == "1" else cfg_acc.get("phase_2_target_pct", 5.0)
                            s_bal = cfg_acc.get("phase_start_balance", current_balance)
                            target_val = s_bal * (1.0 + t_pct / 100.0)

                            # Strictly evaluate on CLOSED BALANCE (never floating equity!)
                            if current_balance >= target_val and not self.challenge_target_reached:
                                self.challenge_target_reached = True
                                p_dollar = current_balance - s_bal
                                p_pct = (p_dollar / s_bal * 100.0) if s_bal > 0 else 0.0

                                if is_final_phase:
                                    # Phase 2 (or 1-Step): Final Challenge Passed! Halt permanently.
                                    cfg_acc["target_locked"] = True
                                    self.target_locked = True
                                    self.config_mgr.save()
                                    self.challenge_target_summary = f"Phase {c_phase}: +{t_pct:.1f}% Final Target Hit (Challenge Passed)"
                                    print("\n" + "*" * 80)
                                    print("🏆 [*** PROP FIRM EVALUATION CHALLENGE FULLY PASSED! ***]")
                                    print(f"Account:         {acc_id_str} ({cfg_acc.get('server', 'MT5')})")
                                    print(f"Final Milestone: Phase {c_phase} Target (+{t_pct:.1f}%) REACHED on Closed Balance!")
                                    print(f"Start Balance:   ${s_bal:,.2f}  ->  Final Balance: ${current_balance:,.2f}")
                                    print(f"Profit Gained:   +${p_dollar:,.2f} (+{p_pct:.2f}%)")
                                    print("PROTECTION:      Trading is PERMANENTLY HALTED. Submit account for funded phase!")
                                    print("*" * 80 + "\n")
                                else:
                                    # Phase 1: Only halt trading for today to lock in daily gains.
                                    # NOT permanently locked, allowing continuation or Phase 2 transition!
                                    cfg_acc["target_locked"] = False
                                    self.target_locked = False
                                    self.config_mgr.save()
                                    self.challenge_target_summary = f"Phase 1: +{t_pct:.1f}% Target Hit - Trading Halted Today"
                                    print("\n" + "*" * 80)
                                    print("🏆 [*** PHASE 1 TARGET REACHED ON CLOSED BALANCE! ***]")
                                    print(f"Account:         {acc_id_str} ({cfg_acc.get('server', 'MT5')})")
                                    print(f"Milestone:       Phase 1 Target (+{t_pct:.1f}%) REACHED on Closed Balance!")
                                    print(f"Start Balance:   ${s_bal:,.2f}  ->  Current Balance: ${current_balance:,.2f}")
                                    print(f"Profit Gained:   +${p_dollar:,.2f} (+{p_pct:.2f}%)")
                                    print("PROTECTION:      Trading HALTED FOR THE DAY to secure profits and prevent overtrading.")
                                    print("NEXT TRADING DAY: Trading will resume tomorrow, or advance to Phase 2 in the menu.")
                                    print("*" * 80 + "\n")

                                if hasattr(self.notifier, "notify_challenge_passed"):
                                    self.notifier.notify_challenge_passed(
                                        account_id=acc_id_str,
                                        phase=c_phase,
                                        target_pct=t_pct,
                                        current_balance=current_balance,
                                        profit_dollar=p_dollar,
                                        profit_pct=p_pct,
                                        is_final=is_final_phase,
                                        server=str(cfg_acc.get('server', 'MT5'))
                                    )
                                for s in self.symbols:
                                    self.armed_states[s].is_armed = False

                # 2. High-Impact News Shield Surveillance & State Machine
                if self.use_news_shield:
                    active_shield = self.news_engine.get_active_news_shield(now_utc)
                    if active_shield:
                        event_key = f"{active_shield['title']}_{active_shield['event_time_str']}"
                        if event_key not in self.notified_news_activations:
                            self.notified_news_activations.add(event_key)
                            self.active_news_shield = active_shield
                            print("\n" + "!" * 80)
                            print(f"[*** HIGH-IMPACT NEWS SHIELD ACTIVATED ***]")
                            print(f"Event:    {active_shield['country']} - {active_shield['title']}")
                            print(f"Release:  {active_shield.get('event_time_dual', active_shield['event_time_str'] + ' UTC')}")
                            print(f"Blackout: {active_shield.get('blackout_start_dual', active_shield['blackout_start_str'] + ' UTC')} -> {active_shield.get('resume_time_dual', active_shield['resume_time_str'] + ' UTC')}")
                            print(f"ACTION:   Trading paused. All armed setups cleared.")
                            print("!" * 80 + "\n")
                            self.notifier.notify_news_shield_activated(
                                active_shield['title'],
                                active_shield['country'],
                                active_shield['event_time_str'],
                                active_shield['resume_time_str']
                            )
                            for s in self.symbols:
                                self.armed_states[s].is_armed = False
                    elif self.active_news_shield is not None:
                        lift_key = f"{self.active_news_shield['title']}_{self.active_news_shield['event_time_str']}"
                        if lift_key not in self.notified_news_lifted:
                            self.notified_news_lifted.add(lift_key)
                            print("\n" + "=" * 80)
                            print(f"[=== HIGH-IMPACT NEWS SHIELD LIFTED ===]")
                            print(f"Event:    {self.active_news_shield['country']} - {self.active_news_shield['title']}")
                            print(f"Resumed:  {now_utc.strftime('%H:%M:%S')} UTC ({now_ist.strftime('%H:%M:%S')} IST). Normal trading resumed.")
                            print("=" * 80 + "\n")
                            self.notifier.notify_news_shield_lifted(
                                self.active_news_shield['title'],
                                self.active_news_shield['resume_time_str']
                            )
                        self.active_news_shield = None

                # 3. Session & Killzone Trap alerts (Pause & Resume transitions)
                self.check_session_and_trap_alerts(now_utc)

                # 4. Manage any active positions (Breakeven automator)
                self.manage_active_positions()

                # 5. Check for newly closed 5M bar on each symbol (Market open only)
                if not is_weekend:
                    for symbol in self.symbols:
                        m5_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 2)
                        if m5_rates is not None and len(m5_rates) >= 2:
                            last_bar_t = m5_rates[-2]['time']
                            if self.last_checked_bars[symbol] is None:
                                self.last_checked_bars[symbol] = last_bar_t
                                self.check_candle_arm_status(symbol)
                            elif self.last_checked_bars[symbol] != last_bar_t:
                                self.last_checked_bars[symbol] = last_bar_t
                                self.check_candle_arm_status(symbol)

                # 6. Process Armed Candlestick Tick Streaming in last 2 minutes (Pre-Arm Mode only)
                if not is_weekend and self.entry_mode == "pre_arm":
                    for symbol in self.symbols:
                        state = self.armed_states[symbol]
                        if state.is_armed and seconds_left_in_5m <= 120.0:
                            self.process_tick_stream_last_2min(symbol, seconds_left_in_5m)

                # 7. Real-time Heartbeat & Status Animation
                any_armed_in_window = any(
                    s.is_armed and seconds_left_in_5m <= 120.0 for s in self.armed_states.values()
                )
                if not any_armed_in_window:
                    frame = self.spinner_frames[self.spinner_idx % len(self.spinner_frames)]
                    frame_color = self.spinner_colors[self.spinner_idx % len(self.spinner_colors)]
                    self.spinner_idx += 1

                    tick_xau = mt5.symbol_info_tick("XAUUSD")
                    tick_nas = mt5.symbol_info_tick("NAS100")
                    eq_val = f"${account.equity:,.2f}" if account else "$0.00"

                    t = Text()

                    # 1. Rotating Radar Surveillance Badge
                    t.append("[", style="bold cyan")
                    t.append("SURVEILLANCE ", style="bold bright_white")
                    t.append(frame, style=frame_color)
                    t.append("] ", style="bold cyan")
                    mode_tag = "BAR-CLOSE" if self.entry_mode == "bar_close" else "PRE-ARM"
                    t.append(f"[{mode_tag}] ", style="bold magenta")

                    # 2. Dual Timezone Timestamps
                    t.append(f"{now_utc.strftime('%H:%M:%S')} UTC ", style="bold white")
                    t.append(f"({now_ist.strftime('%H:%M:%S')} IST)", style="bold bright_cyan")
                    t.append(" | ", style="bright_black")

                    # 3. Gold (XAUUSD) & Dynamic Spread Health
                    t.append("XAU: ", style="bold yellow")
                    if tick_xau:
                        xau_sp = tick_xau.ask - tick_xau.bid
                        t.append(f"{tick_xau.bid:.2f}/{tick_xau.ask:.2f} ", style="white")
                        if xau_sp <= 0.40:
                            xau_sp_style = "green"
                        elif xau_sp <= 0.65:
                            xau_sp_style = "yellow"
                        else:
                            xau_sp_style = "bold red"
                        t.append(f"(Sp: {xau_sp:.2f})", style=xau_sp_style)
                    else:
                        t.append("N/A", style="bright_black")
                    t.append(" | ", style="bright_black")

                    # 4. Nasdaq (NAS100) & Dynamic Spread Health
                    t.append("NAS: ", style="bold bright_blue")
                    if tick_nas:
                        nas_sp = tick_nas.ask - tick_nas.bid
                        t.append(f"{tick_nas.bid:.1f}/{tick_nas.ask:.1f} ", style="white")
                        if nas_sp <= 2.5:
                            nas_sp_style = "green"
                        elif nas_sp <= 7.0:
                            nas_sp_style = "yellow"
                        else:
                            nas_sp_style = "bold red"
                        t.append(f"(Sp: {nas_sp:.1f})", style=nas_sp_style)
                    else:
                        t.append("N/A", style="bright_black")
                    t.append(" | ", style="bright_black")

                    # 5. Next Bar Countdown with Urgency Color Coding
                    t.append("Next Bar in: ", style="white")
                    if seconds_left_in_5m > 120.0:
                        cd_style = "bold green"
                    elif seconds_left_in_5m > 30.0:
                        cd_style = "bold yellow"
                    else:
                        cd_style = "bold bright_red"
                    t.append(f"{m_left:02d}m {s_left:02d}s", style=cd_style)
                    t.append(" | ", style="bright_black")

                    # 6. Institutional Account Equity Anchor
                    t.append("Eq: ", style="bold white")
                    t.append(eq_val, style="bold bright_green")

                    # 7. Dynamic Armed Setup & News Shield Badges
                    armed_items = [
                        f"{sym} {st.direction_str}"
                        for sym, st in self.armed_states.items()
                        if st.is_armed
                    ]
                    if armed_items:
                        t.append(" | ", style="bright_black")
                        t.append(f"[⚡ ARMED: {', '.join(armed_items)}]", style="bold bright_yellow")

                    if self.use_news_shield and self.news_engine.get_active_news_shield(now_utc):
                        cur_shield = self.news_engine.get_active_news_shield(now_utc)
                        mins_rem = int(cur_shield['seconds_remaining'] // 60)
                        secs_rem = int(cur_shield['seconds_remaining'] % 60)
                        resume_ist = cur_shield.get('resume_time_ist_str', '')
                        ist_note = f" | Resumes {resume_ist} IST" if resume_ist else ""
                        t.append(" | ", style="bright_black")
                        t.append(f"[🛡️ NEWS SHIELD: {mins_rem:02d}m {secs_rem:02d}s{ist_note}]", style="bold bright_red")

                    if self.active_positions:
                        pos_items = [
                            f"{p.symbol} {p.direction}" for p in self.active_positions.values()
                        ]
                        t.append(" | ", style="bright_black")
                        t.append(f"[POS: {', '.join(pos_items)}]", style="bold bright_green")
                    elif is_weekend:
                        day_name = now_utc.strftime('%A')
                        t.append(" | ", style="bright_black")
                        t.append(f"[WEEKEND ({day_name.upper()}) - MARKET CLOSED - RESUMES MON 06:00 UTC]", style="bold bright_yellow")
                    elif now_utc.hour < self.entry_start_hour_utc or now_utc.hour >= self.entry_end_hour_utc:
                        t.append(" | ", style="bright_black")
                        t.append("[ASIAN RANGE - ENTRIES PAUSED]", style="bold bright_white")
                    elif getattr(self, 'challenge_target_reached', False):
                        t.append(" | ", style="bright_black")
                        t.append("[🏆 CHALLENGE TARGET REACHED - TRADING HALTED]", style="bold bright_green")
                    elif now_utc.hour in self.trap_hours_utc:
                        t.append(" | ", style="bright_black")
                        t.append("[TRAP HOUR - SMART KILLZONE ACTIVE (Stretch>=1.10x)]", style="bold bright_cyan")

                    self.live.update(t)
                    pytime.sleep(1.0 if is_weekend else 0.5)
                else:
                    pytime.sleep(0.05)

        except KeyboardInterrupt:
            print("\n[InstitutionalDCCBot] Shutdown requested by user.")
            self.notifier.notify_shutdown("User Stopped Bot (Ctrl+C)")
        except Exception as e:
            print(f"\n[InstitutionalDCCBot] Error: {e}")
            self.notifier.notify_shutdown(f"Unexpected Error: {e}")
        finally:
            if hasattr(self, "live") and self.live is not None:
                self.live.stop()
            mt5.shutdown()
            print("[InstitutionalDCCBot] MT5 connection closed cleanly.")

    def emergency_close_all(self):
        """Immediately closes all open positions on monitored symbols."""
        for symbol in self.symbols:
            open_pos = mt5.positions_get(symbol=symbol)
            for pos in (open_pos or []):
                sym_info = mt5.symbol_info(symbol)
                fill_mode = self.get_safe_filling_mode(sym_info)
                tick = mt5.symbol_info_tick(symbol)
                if not tick:
                    continue
                price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": pos.ticket,
                    "symbol": symbol,
                    "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                    "price": price,
                    "deviation": 30,
                    "magic": 999999,
                    "comment": "EMERGENCY_MAX_DD_HALT",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": fill_mode,
                }
                res = mt5.order_send(req)
                ret = res.retcode if res else "None"
                print(f"[EMERGENCY CLOSE] Position #{pos.ticket} on {symbol}: RetCode={ret}")


def render_institutional_control_menu(
    acc_id: str,
    server: str,
    cfg: Dict,
    equity: float,
    balance: float,
    free_margin: float,
    hwm: float,
    notif_cfg: Dict,
    console: Console,
    day_start_equity: Optional[float] = None
):
    w = 78
    border_col = "bright_blue"

    # 1. Top Header Banner
    banner_text = Text()
    banner_text.append("APEXHUNTER DCC TRADING BOT ", style="bold bright_white")
    banner_text.append("• ", style="bright_cyan")
    banner_text.append("CONTROL CENTER v1.2\n", style="bold bright_cyan")
    banner_text.append("Dual UTC + IST Engine  •  MetaTrader 5 Direct Bridge  •  v1.2 Flagship", style="bright_white")

    header = Panel(
        banner_text,
        box=box.ROUNDED,
        border_style=border_col,
        padding=(0, 2),
        width=w
    )
    console.print(header)

    # 2. Account Profile & Balances (2 Balanced Columns - Exact 36 chars each)
    lifecycle = cfg.get("account_lifecycle", "challenge").upper()
    curr_phase = cfg.get("current_phase", 1)
    ch_steps = cfg.get("challenge_steps", 2)
    start_bal = cfg.get("phase_start_balance", balance)
    p1_tgt = cfg.get("phase_1_target_pct", 8.0)
    p2_tgt = cfg.get("phase_2_target_pct", 5.0)
    use_custom = cfg.get("use_custom_phase_risk", False)
    active_risk_pct = cfg.get("risk_per_trade", 0.01) * 100.0
    first_trade_risk = balance * (active_risk_pct / 100.0)

    profit_dollar = balance - start_bal
    profit_pct = (profit_dollar / start_bal * 100.0) if start_bal > 0 else 0.0

    if lifecycle == "CHALLENGE":
        phase_str = f"[bold yellow]CHALLENGE (Phase {curr_phase})[/]"
    else:
        phase_str = "[bold bright_green]FUNDED (Profit Share)[/]"

    pnl_color = "bold bright_green" if profit_dollar >= 0 else "bold bright_red"
    pnl_sign = "+$" if profit_dollar >= 0 else "-$"
    pnl_str = f"[{pnl_color}]{pnl_sign}{abs(profit_dollar):,.2f} ({profit_pct:+.2f}%)[/]"

    acc_table = Table(box=box.ROUNDED, border_style=border_col, show_header=False, expand=True, width=w)
    acc_table.add_column("Col1", style="white", width=36)
    acc_table.add_column("Col2", style="white", width=36)

    acc_table.add_row(
        f"[bright_cyan]Account:[/] [bold white]{acc_id} ({server})[/]",
        f"[bright_cyan]Lifecycle:[/] {phase_str}"
    )
    acc_table.add_row(
        f"[bright_cyan]Balance:[/] [bold white]${balance:,.2f}[/] [white](Free: ${free_margin:,.0f})[/]",
        f"[bright_cyan]Equity:[/] [bold white]${equity:,.2f}[/] [white](HWM: ${hwm:,.0f})[/]"
    )
    acc_table.add_row(
        f"[bright_cyan]Phase Start:[/] [white]${start_bal:,.2f}[/]",
        f"[bright_cyan]Net Closed PnL:[/] {pnl_str}"
    )
    console.print(acc_table)

    # 3. Prop Firm Milestone & Risk Guardrails Card
    daily_limit_pct = cfg.get("daily_dd_limit_pct", 4.0)
    daily_cb_pct = cfg.get("daily_cb_pct", round(max(0.1, daily_limit_pct - 1.0), 2))
    max_limit_pct = cfg.get("max_total_dd_pct", 8.0)
    max_cb_pct = cfg.get("max_cb_pct", round(max(0.1, max_limit_pct - 1.0), 2))

    if day_start_equity is None:
        day_start_equity = get_daily_starting_equity(balance)

    daily_halt_equity = day_start_equity * (1.0 - daily_cb_pct / 100.0)
    daily_hard_equity = day_start_equity * (1.0 - daily_limit_pct / 100.0)
    max_halt_equity = hwm * (1.0 - max_cb_pct / 100.0)
    max_hard_equity = hwm * (1.0 - max_limit_pct / 100.0)

    daily_cushion = max(0.0, equity - daily_halt_equity)
    max_cushion = max(0.0, equity - max_halt_equity)

    daily_cushion_str = f"[bold bright_green]+${daily_cushion:,.2f}[/]" if daily_cushion > 0 else "[bold bright_red]$0.00 (HALTED)[/]"
    max_cushion_str = f"[bold bright_green]+${max_cushion:,.2f}[/]" if max_cushion > 0 else "[bold bright_red]$0.00 (HALTED)[/]"

    milestone_table = Table(
        box=box.ROUNDED,
        border_style=border_col,
        title="[bold bright_white]PROP FIRM TARGET MILESTONE & RISK GUARDRAILS[/]",
        title_style="bold bright_white",
        expand=True,
        width=w
    )
    milestone_table.add_column("Metric", style="bold white", width=14)
    milestone_table.add_column("Configuration & Live Progress", style="white", width=44)
    milestone_table.add_column("Safe Buffer", justify="right", style="bold", width=14)

    if lifecycle == "CHALLENGE":
        active_target_pct = p1_tgt if str(curr_phase) == "1" else p2_tgt
        target_dollar = start_bal * (1.0 + active_target_pct / 100.0)
        remaining_dollar = max(0.0, target_dollar - balance)
        rem_pct = max(0.0, active_target_pct - profit_pct)

        if ch_steps == 1:
            target_desc = f"Step 1: +{active_target_pct:.1f}% (${target_dollar:,.0f}) [1-Step Target]"
        else:
            target_desc = f"Phase {curr_phase}: +{active_target_pct:.1f}% (${target_dollar:,.0f}) [Total: +{p1_tgt + p2_tgt:.1f}%]"

        bar_len = 10
        ratio = min(1.0, max(0.0, profit_pct / active_target_pct)) if active_target_pct > 0 else 0.0
        filled = int(bar_len * ratio)
        if balance >= target_dollar:
            p_bar = f"[bold bright_green]{'=' * bar_len}[/]"
            pass_label = "PASSED!" if (ch_steps == 1 or str(curr_phase) in ["2", "funded"]) else f"PHASE {curr_phase} HIT"
            tgt_badge = f"[bold bright_green]{pass_label}[/]"
            buffer_desc = "[bold bright_green]Pass Locked[/]"
        else:
            p_bar = f"[bright_cyan]{'=' * filled}[/][white]{'-' * (bar_len - filled)}[/]"
            tgt_badge = f"[bold bright_cyan]{ratio*100.0:.0f}%[/] [white](${remaining_dollar:,.0f} left)[/]"
            buffer_desc = f"[bold cyan]+${remaining_dollar:,.0f} left[/]"

        milestone_table.add_row("Target Goal", target_desc, tgt_badge)
        milestone_table.add_row(
            "Closed Prog",
            f"[{p_bar}] [{pnl_color}]{pnl_sign}{abs(profit_dollar):,.2f} ({profit_pct:+.2f}%)[/]",
            buffer_desc
        )
    else:
        milestone_table.add_row("Target Goal", "Bi-Weekly Payout Growth Target (No Ceiling)", "[bold bright_green]FUNDED[/]")
        milestone_table.add_row("Closed Perf", f"Net Profit: [{pnl_color}]{pnl_sign}{abs(profit_dollar):,.2f} ({profit_pct:+.2f}%)[/]", "[bold green]Profit Share[/]")

    scaling_str = f"[bold cyan]ON ({active_risk_pct:.2f}%)[/]" if use_custom else f"[white]OFF ({active_risk_pct:.2f}%)[/]"
    milestone_table.add_row("Sizing Risk", f"{active_risk_pct:.2f}% (~${first_trade_risk:,.2f}) [Scaling: {scaling_str}]", "[white]Normal Mode[/]")
    milestone_table.add_row(
        "Daily DD",
        f"Hard: [bold bright_red]-{daily_limit_pct:.1f}%[/] (${daily_hard_equity:,.0f}) | CB: [bold yellow]-{daily_cb_pct:.1f}%[/] (${daily_halt_equity:,.0f})",
        daily_cushion_str
    )
    milestone_table.add_row(
        "Max Total DD",
        f"Hard: [bold bright_red]-{max_limit_pct:.1f}%[/] (${max_hard_equity:,.0f}) | CB: [bold yellow]-{max_cb_pct:.1f}%[/] (${max_halt_equity:,.0f})",
        max_cushion_str
    )
    console.print(milestone_table)

    # 4. Confluence & Engine Status Strip (5 Columns Fits 78 chars)
    conf_table = Table(box=box.ROUNDED, border_style=border_col, show_header=True, header_style="bold bright_cyan", expand=True, width=w)
    conf_table.add_column("Monitored Assets", justify="center", style="bold white", width=16)
    conf_table.add_column("5M Sweep", justify="center", width=12)
    conf_table.add_column("News Shield", justify="center", width=12)
    conf_table.add_column("Entry Mode", justify="center", width=12)
    conf_table.add_column("Remote Alerts", justify="center", width=12)

    use_sweep = cfg.get("use_liquidity_sweep", True)
    sweep_str = "[bold bright_green]ENABLED[/]" if use_sweep else "[bold red]DISABLED[/]"
    use_news = cfg.get("use_news_shield", True)
    news_str = "[bold bright_green]ENABLED[/]" if use_news else "[bold red]DISABLED[/]"
    entry_mode = cfg.get("entry_mode", "bar_close")
    mode_str = "[bold bright_cyan]BAR-CLOSE[/]" if entry_mode == "bar_close" else "[bold yellow]PRE-ARM[/]"

    plat = notif_cfg.get("active_platform", "none")
    if plat == "telegram":
        notif_str = "[bold bright_green]Telegram[/]"
    elif plat == "discord":
        notif_str = "[bold bright_green]Discord[/]"
    else:
        notif_str = "[white]Off[/]"

    symbols_str = ", ".join(cfg.get("symbols", ["XAUUSD", "NAS100"]))
    conf_table.add_row(symbols_str, sweep_str, news_str, mode_str, notif_str)
    console.print(conf_table)

    # 5. Action Command Palette (Clean 2-Column Non-Wrapping Layout)
    action_table = Table(
        box=box.ROUNDED,
        border_style=border_col,
        title="[bold bright_white]BOT CONTROL ACTION PALETTE[/]",
        title_style="bold bright_white",
        expand=True,
        width=w
    )
    action_table.add_column("Key", justify="center", width=6)
    action_table.add_column("Command Action & Controls", style="white")

    # Execution Modes
    action_table.add_row("[bold bright_green][1][/]", "[bold bright_green]Start LIVE Trading[/]  [white]— Real MT5 execution & automated risk sizing[/]")
    action_table.add_row("[bold bright_cyan][2][/]", "[bold bright_cyan]Start PAPER Trading[/] [white]— Dry-run simulation (Zero risk / Live ticks)[/]")
    action_table.add_section()

    # Risk & Lifecycle
    action_table.add_row("[bold yellow][3][/]", "[bold yellow]Manage Lifecycle & Risk[/] [white]— Phase, Targets, Risk % & Scaling[/]")
    action_table.add_row("[bold yellow][4][/]", "[bold yellow]Edit Drawdown & Circuit Breakers[/] [white]— Daily DD, Max DD, CB cushions[/]")
    action_table.add_row("[bold magenta][5][/]", "[bold magenta]Configure Remote Notifications[/] [white]— Telegram Bot & Discord Webhook[/]")
    action_table.add_section()

    # Confluence & Controls
    sweep_lbl = "[bold bright_green]ENABLED[/]" if use_sweep else "[bold red]DISABLED[/]"
    news_lbl = "[bold bright_green]ENABLED (15m Blackout)[/]" if use_news else "[bold red]DISABLED[/]"
    mode_lbl = "[bold bright_cyan]BAR-CLOSE (1:1)[/]" if entry_mode == "bar_close" else "[bold yellow]PRE-ARM[/]"

    action_table.add_row("[bold cyan][6][/]", f"[bold cyan]Toggle 5M Liquidity Sweep[/] [white]— Currently: {sweep_lbl}[/]")
    action_table.add_row("[bold cyan][7][/]", f"[bold cyan]Toggle News Shield[/] [white]— Currently: {news_lbl}[/]")
    action_table.add_row("[bold cyan][8][/]", f"[bold cyan]Switch Entry Execution Mode[/] [white]— Currently: {mode_lbl}[/]")
    action_table.add_row("[bold cyan][9][/]", "[bold cyan]View Economic Calendar[/] [white]— Upcoming USD News (Dual UTC + IST)[/]")
    action_table.add_section()

    # System
    action_table.add_row("[bold bright_red][10][/]", "[bold bright_red]Exit Bot[/] [white]— Shutdown MT5 connection & terminate session[/]")

    console.print(action_table)


def render_lifecycle_menu(acc_id: str, cfg: Dict, balance: float, console: Console):
    w = 78
    border_col = "bright_blue"
    l_curr = cfg.get("account_lifecycle", "challenge").upper()
    c_ph = cfg.get("current_phase", 1)
    ch_steps = cfg.get("challenge_steps", 2)

    status_str = f"CHALLENGE MODE ({ch_steps}-Step | Phase {c_ph})" if l_curr == "CHALLENGE" else "FUNDED ACCOUNT"
    use_scaling = cfg.get("use_custom_phase_risk", False)
    scaling_badge = "[bold bright_green]ENABLED[/]" if use_scaling else "[white]OFF (Fixed 1.00%)[/]"

    table = Table(box=box.ROUNDED, border_style=border_col, title=f"[bold bright_white]MANAGE LIFECYCLE & RISK — ACCOUNT {acc_id}[/]", width=w, expand=True)
    table.add_column("Parameter", style="bright_cyan", width=28)
    table.add_column("Current Setting", style="bold white")

    table.add_row("Lifecycle Status", f"[bold yellow]{status_str}[/]" if l_curr == "CHALLENGE" else "[bold bright_green]FUNDED MODE[/]")
    table.add_row("Phase Starting Balance", f"${cfg.get('phase_start_balance', balance):,.2f}")
    table.add_row("Phase 1 Evaluation Target", f"+{cfg.get('phase_1_target_pct', 8.0):.1f}%")
    if ch_steps > 1:
        table.add_row("Phase 2 Evaluation Target", f"+{cfg.get('phase_2_target_pct', 5.0):.1f}%")
    table.add_row("Challenge Risk Per Trade", f"{cfg.get('challenge_risk_pct', 1.25):.2f}%")
    table.add_row("Funded Risk Per Trade", f"{cfg.get('funded_risk_pct', 1.00):.2f}%")
    table.add_row("Custom Phase Risk Scaling", scaling_badge)
    console.print(table)

    menu = Table(box=box.ROUNDED, border_style=border_col, title="[bold bright_white]Select Action[/]", width=w, expand=True)
    menu.add_column("Key", justify="center", width=6)
    menu.add_column("Action & Details", style="white")

    menu.add_row("[bold yellow][1][/]", "[bold white]Advance / Switch Active Phase[/] [white]— Phase 1 → Phase 2 → Funded mode[/]")
    menu.add_row("[bold yellow][2][/]", f"[bold white]Edit Challenge Risk %[/] [white]— Current: [bold cyan]{cfg.get('challenge_risk_pct', 1.25):.2f}%[/][/]")
    menu.add_row("[bold yellow][3][/]", f"[bold white]Edit Funded Risk %[/] [white]— Current: [bold cyan]{cfg.get('funded_risk_pct', 1.00):.2f}%[/][/]")
    menu.add_row("[bold yellow][4][/]", "[bold white]Edit Phase Targets[/] [white]— Overall combined % & phase ratio[/]")
    menu.add_row("[bold yellow][5][/]", f"[bold white]Reset Starting Balance[/] [white]— Anchor: [bold cyan]${cfg.get('phase_start_balance', balance):,.2f}[/][/]")
    menu.add_row("[bold yellow][6][/]", f"[bold white]Toggle Phase Risk Scaling[/] [white]— Currently: {'[bold bright_green]ON[/]' if use_scaling else '[white]OFF (1.00%)[/]'}[/]")
    menu.add_row("[bold bright_cyan][7][/]", "[bold bright_cyan]Return to Main Menu[/] [white]— Go back to main command palette[/]")
    console.print(menu)


def render_rules_editor_header(acc_id: str, cfg: Dict, console: Console):
    w = 78
    border_col = "bright_blue"
    table = Table(box=box.ROUNDED, border_style=border_col, title=f"[bold bright_white]EDIT RISK & DRAWDOWN RULES — ACCOUNT {acc_id}[/]", width=w, expand=True)
    table.add_column("Parameter", style="bright_cyan", width=15)
    table.add_column("Hard Limit", style="bold bright_red", justify="center", width=10)
    table.add_column("Circuit Brk", style="bold yellow", justify="center", width=12)
    table.add_column("Safeguard Notes", style="white")

    curr_r = cfg.get("risk_per_trade", 0.01) * 100.0
    curr_d = cfg.get("daily_dd_limit_pct", 4.0)
    curr_d_cb = cfg.get("daily_cb_pct", round(max(0.1, curr_d - 1.0), 2))
    curr_m = cfg.get("max_total_dd_pct", 8.0)
    curr_m_cb = cfg.get("max_cb_pct", round(max(0.1, curr_m - 1.0), 2))

    table.add_row("Risk Per Trade", f"{curr_r:.2f}%", "-", "Position sizing via ATR SL")
    table.add_row("Daily Drawdown", f"-{curr_d:.1f}%", f"-{curr_d_cb:.1f}%", "Resets 00:00 UTC rollover")
    table.add_row("Max Total DD", f"-{curr_m:.1f}%", f"-{curr_m_cb:.1f}%", "Peak Equity (HWM) anchor")
    console.print(table)


def render_notifications_menu(acc_id: str, notif_cfg: Dict, console: Console):
    w = 78
    border_col = "bright_blue"
    plat = notif_cfg.get("active_platform", "none")

    table = Table(box=box.ROUNDED, border_style=border_col, title=f"[bold bright_white]REMOTE NOTIFICATIONS SETUP — ACCOUNT {acc_id}[/]", width=w, expand=True)
    table.add_column("Platform", style="bright_cyan", width=18)
    table.add_column("Current Status", width=18)
    table.add_column("Endpoint Preview", style="white")

    tg_tok = notif_cfg.get("telegram_bot_token", "")
    tg_tok_prev = f"{tg_tok[:10]}..." if tg_tok else "[white](not set)[/]"
    discord_url = notif_cfg.get("discord_webhook_url", "")
    discord_url_prev = f"{discord_url[:30]}..." if discord_url else "[white](not set)[/]"

    table.add_row("Telegram Bot", "[bold bright_green]Active[/]" if plat == "telegram" else "[white]Standby (Off)[/]", f"Token: {tg_tok_prev}")
    table.add_row("Discord Webhook", "[bold bright_green]Active[/]" if plat == "discord" else "[white]Standby (Off)[/]", f"URL: {discord_url_prev}")
    console.print(table)

    menu = Table(box=box.ROUNDED, border_style=border_col, title="[bold bright_white]Notification Options[/]", width=w, expand=True)
    menu.add_column("Key", justify="center", width=6)
    menu.add_column("Action & Details", style="white")

    menu.add_row("[bold bright_red][1][/]", "[bold white]Disable Notifications[/] [white]— Mute all Telegram & Discord alerts[/]")
    menu.add_row("[bold bright_cyan][2][/]", "[bold white]Configure Telegram Bot[/] [white]— Set bot token, chat ID & test[/]")
    menu.add_row("[bold magenta][3][/]", "[bold white]Configure Discord Webhook[/] [white]— Set webhook URL & test connection[/]")
    menu.add_row("[bold bright_green][4][/]", "[bold white]Send Test Verification Alert[/] [white]— Dispatch instant test notification[/]")
    menu.add_row("[bold bright_cyan][5][/]", "[bold bright_cyan]Return to Main Menu[/] [white]— Go back to main command palette[/]")
    console.print(menu)


def render_economic_calendar(console: Console, hours_ahead: int = 48):
    engine = NewsEngine()
    events = engine.get_upcoming_events(now_utc=datetime.now(timezone.utc), hours_ahead=hours_ahead)

    w = 78
    border_col = "bright_blue"
    cal_table = Table(
        title="UPCOMING HIGH-IMPACT ECONOMIC CALENDAR (USD | DUAL UTC + IST)",
        title_style="bold bright_cyan",
        box=box.ROUNDED,
        border_style=border_col,
        header_style="bold bright_cyan",
        expand=True,
        width=w
    )
    cal_table.add_column("Dual Time (UTC & IST)", style="bold white", width=28)
    cal_table.add_column("Country", justify="center", style="bold yellow", width=8)
    cal_table.add_column("Impact", justify="center", width=8)
    cal_table.add_column("Event Title", style="white")
    cal_table.add_column("Forecast", justify="right", style="cyan", width=10)
    cal_table.add_column("Previous", justify="right", style="white", width=10)

    if not events:
        console.print(Panel("[white]No high-impact USD events scheduled in the next 48 hours.[/]", title="Economic Calendar", border_style=border_col, width=w))
    else:
        for ev in events:
            f_val = ev.forecast if ev.forecast else "-"
            p_val = ev.previous if ev.previous else "-"
            impact_badge = "[bold bright_red]HIGH[/]" if ev.impact.lower() == "high" else f"[bold yellow]{ev.impact.upper()}[/]"
            cal_table.add_row(
                ev.time_dual_str,
                ev.country,
                impact_badge,
                ev.title,
                str(f_val),
                str(p_val)
            )
        console.print(cal_table)


def show_interactive_menu(account_info, acc_mgr: AccountConfigManager) -> Tuple[str, Dict]:
    acc_id = str(account_info.login)
    cfg = acc_mgr.get_or_setup_account(account_info)

    while True:
        fresh_acc = mt5.account_info()
        equity = fresh_acc.equity if fresh_acc else account_info.equity
        balance = fresh_acc.balance if fresh_acc else account_info.balance
        free_margin = fresh_acc.margin_free if fresh_acc else account_info.margin_free

        if equity > cfg.get("high_water_mark", 0.0):
            cfg["high_water_mark"] = round(float(equity), 2)
            acc_mgr.save()
        hwm = cfg.get("high_water_mark", equity)

        notif_cfg = acc_mgr.get_notification_config(acc_id)

        day_start_equity = get_daily_starting_equity(balance)

        # Render Unified Institutional Control Center UI
        render_institutional_control_menu(
            acc_id=acc_id,
            server=str(account_info.server),
            cfg=cfg,
            equity=equity,
            balance=balance,
            free_margin=free_margin,
            hwm=hwm,
            notif_cfg=notif_cfg,
            console=console,
            day_start_equity=day_start_equity
        )

        choice = input("\nEnter choice [1-10] (Press Enter for [1]): ").strip()
        if not choice or choice == "1":
            return "live", cfg
        elif choice == "2":
            return "dry_run", cfg
        elif choice == "3":
            render_lifecycle_menu(acc_id, cfg, balance, console)
            sub_c = input("\nEnter choice [1-7]: ").strip()
            if sub_c == "7" or not sub_c:
                continue
            if sub_c == "1":
                print("\nSelect New Active Phase:")
                print("  [1] Challenge Phase 1")
                print("  [2] Challenge Phase 2")
                print("  [3] Funded Mode")
                ph_c = input("Enter phase [1-3]: ").strip()
                notifier = NotificationManager(cfg.get("notifications", {}))
                if ph_c in ["1", "2"]:
                    sb_in = input(f"Enter Starting Balance for Phase {ph_c} [Current Balance: ${balance:,.2f}]: ").strip().lstrip('$').replace(',', '')
                    sb_val = float(sb_in) if sb_in else balance
                    acc_mgr.advance_account_phase(acc_id, ph_c, new_start_bal=sb_val, notifier=notifier)
                elif ph_c == "3":
                    acc_mgr.advance_account_phase(acc_id, "funded", notifier=notifier)
                cfg = acc_mgr.accounts[acc_id]
            elif sub_c == "2":
                cr_in = input(f"Enter new Challenge Risk % [Current: {cfg.get('challenge_risk_pct', 1.25):.2f}%]: ").strip().rstrip('%').strip()
                if cr_in:
                    try:
                        acc_mgr.update_lifecycle_settings(
                            acc_id,
                            lifecycle=cfg.get("account_lifecycle", "challenge"),
                            current_phase=cfg.get("current_phase", 1),
                            p1_target=cfg.get("phase_1_target_pct", 8.0),
                            p2_target=cfg.get("phase_2_target_pct", 5.0),
                            ch_risk=float(cr_in),
                            funded_risk=cfg.get("funded_risk_pct", 1.0)
                        )
                        cfg = acc_mgr.accounts[acc_id]
                    except ValueError:
                        console.print("[bold red]✖ Invalid risk value entered.[/]")
            elif sub_c == "3":
                fr_in = input(f"Enter new Funded Risk % [Current: {cfg.get('funded_risk_pct', 1.00):.2f}%]: ").strip().rstrip('%').strip()
                if fr_in:
                    try:
                        acc_mgr.update_lifecycle_settings(
                            acc_id,
                            lifecycle=cfg.get("account_lifecycle", "challenge"),
                            current_phase=cfg.get("current_phase", 1),
                            p1_target=cfg.get("phase_1_target_pct", 8.0),
                            p2_target=cfg.get("phase_2_target_pct", 5.0),
                            ch_risk=cfg.get("challenge_risk_pct", 1.25),
                            funded_risk=float(fr_in)
                        )
                        cfg = acc_mgr.accounts[acc_id]
                    except ValueError:
                        console.print("[bold red]✖ Invalid risk value entered.[/]")
            elif sub_c == "4":
                try:
                    curr_tot = cfg.get('phase_1_target_pct', 8.0) + cfg.get('phase_2_target_pct', 5.0)
                    tot_in = input(f"Enter Overall Target % (Phase 1 + Phase 2 combined) [Current: {curr_tot:.1f}%]: ").strip().rstrip('%').strip()
                    tot_val = float(tot_in) if tot_in else curr_tot
                    p1_in = input(f"Enter Phase 1 Target % [Current: {cfg.get('phase_1_target_pct', 8.0):.1f}%]: ").strip().rstrip('%').strip()
                    p1_val = float(p1_in) if p1_in else cfg.get("phase_1_target_pct", 8.0)
                    calc_p2 = max(0.0, round(tot_val - p1_val, 2))
                    p2_in = input(f"Enter Phase 2 Target % [Default: {calc_p2:.1f}% based on overall {tot_val:.1f}%]: ").strip().rstrip('%').strip()
                    p2_val = float(p2_in) if p2_in else calc_p2
                    acc_mgr.update_lifecycle_settings(
                        acc_id,
                        lifecycle=cfg.get("account_lifecycle", "challenge"),
                        current_phase=cfg.get("current_phase", 1),
                        p1_target=p1_val,
                        p2_target=p2_val,
                        ch_risk=cfg.get("challenge_risk_pct", 1.25),
                        funded_risk=cfg.get("funded_risk_pct", 1.0)
                    )
                    cfg = acc_mgr.accounts[acc_id]
                except ValueError:
                    console.print("[bold red]✖ Invalid target percentage entered.[/]")
            elif sub_c == "5":
                sb_in = input(f"Enter New Starting Balance [Current: ${cfg.get('phase_start_balance', balance):,.2f}]: ").strip().lstrip('$').replace(',', '')
                if sb_in:
                    try:
                        acc_mgr.update_lifecycle_settings(
                            acc_id,
                            lifecycle=cfg.get("account_lifecycle", "challenge"),
                            current_phase=cfg.get("current_phase", 1),
                            p1_target=cfg.get("phase_1_target_pct", 8.0),
                            p2_target=cfg.get("phase_2_target_pct", 5.0),
                            ch_risk=cfg.get("challenge_risk_pct", 1.25),
                            funded_risk=cfg.get("funded_risk_pct", 1.0),
                            start_bal=float(sb_in)
                        )
                        cfg = acc_mgr.accounts[acc_id]
                    except ValueError:
                        console.print("[bold red]✖ Invalid balance value entered.[/]")
            elif sub_c == "6":
                is_on = acc_mgr.toggle_custom_phase_risk(acc_id)
                cfg = acc_mgr.accounts[acc_id]
                status_str = "ENABLED" if is_on else "OFF (Fixed 1.00%)"
                console.print(f"\n[bold bright_green]✔ [PHASE RISK TOGGLED][/] Custom Phase-Specific Risk Scaling is now: [bold bright_yellow]{status_str}[/]")
                console.print(f"  * Active Sizing Risk: [bold cyan]{cfg.get('risk_per_trade', 0.01)*100.0:.2f}%[/]\n")
            if sub_c in ["1", "2", "3", "4", "5", "6"]:
                input("\nPress Enter to return to main control menu...")
        elif choice == "4":
            render_rules_editor_header(acc_id, cfg, console)
            curr_r = cfg.get("risk_per_trade", 0.01) * 100.0
            r_in = input(f"\nNew Risk Per Trade % [Current: {curr_r:.1f}%] (Press Enter to keep): ").strip().rstrip('%').strip()
            try:
                new_r = float(r_in) if r_in else curr_r
            except ValueError:
                console.print(f"[bold red]✖ Invalid risk value entered. Keeping current {curr_r:.1f}%.[/]")
                new_r = curr_r

            curr_d = cfg.get("daily_dd_limit_pct", 4.0)
            d_in = input(f"New Daily DD Hard Limit % [Current: {curr_d:.1f}%] (Press Enter to keep): ").strip().rstrip('%').strip()
            try:
                new_d = float(d_in) if d_in else curr_d
            except ValueError:
                console.print(f"[bold red]✖ Invalid daily DD value entered. Keeping current {curr_d:.1f}%.[/]")
                new_d = curr_d

            curr_m = cfg.get("max_total_dd_pct", 8.0)
            m_in = input(f"New Max Account DD Hard Limit % [Current: {curr_m:.1f}%] (Press Enter to keep): ").strip().rstrip('%').strip()
            try:
                new_m = float(m_in) if m_in else curr_m
            except ValueError:
                console.print(f"[bold red]✖ Invalid max DD value entered. Keeping current {curr_m:.1f}%.[/]")
                new_m = curr_m

            # Prompt Circuit Breaker auto-configuration (-1% safety cushion) or manual entry
            new_daily_cb, new_max_cb = prompt_circuit_breakers(new_d, new_m)

            acc_mgr.update_account_rules(acc_id, risk_pct=new_r, daily_dd=new_d, max_dd=new_m, daily_cb=new_daily_cb, max_cb=new_max_cb)
            cfg = acc_mgr.accounts[acc_id]
            input("\nPress Enter to return to main control menu...")
        elif choice == "5":
            render_notifications_menu(acc_id, notif_cfg, console)
            n_choice = input("\nEnter choice [1-5]: ").strip()
            if n_choice == "5" or not n_choice:
                continue

            if n_choice == "1":
                acc_mgr.update_notification_settings(acc_id, "none")
                console.print("\n[bold yellow]Remote notifications disabled.[/]")
            elif n_choice == "2":
                curr_tok = notif_cfg.get("telegram_bot_token", "")
                curr_chat = notif_cfg.get("telegram_chat_id", "")
                tok_prompt = f"Enter Telegram Bot Token [Current: {curr_tok[:10]}...]: " if curr_tok else "Enter Telegram Bot Token: "
                new_tok = input(tok_prompt).strip() or curr_tok
                chat_prompt = f"Enter Telegram Chat ID [Current: {curr_chat}]: " if curr_chat else "Enter Telegram Chat ID: "
                new_chat = input(chat_prompt).strip() or curr_chat

                if new_tok and new_chat:
                    test_ans = input("Send test verification message to Telegram right now? [Y/n]: ").strip().lower()
                    if test_ans != "n":
                        print("Sending test message to Telegram...")
                        ok, msg = NotificationManager.test_connection("telegram", new_tok, new_chat)
                        if ok:
                            console.print("\n[bold bright_green]✔ [SUCCESS][/] Test message confirmed on Telegram!")
                        else:
                            console.print(f"\n[bold red]✖ [WARN][/] Telegram Test Error: {msg}")
                    acc_mgr.update_notification_settings(acc_id, "telegram", tg_token=new_tok, tg_chat_id=new_chat)
                else:
                    console.print("[bold red]✖ [WARN][/] Bot Token or Chat ID cannot be empty. Setup cancelled.")

            elif n_choice == "3":
                curr_url = notif_cfg.get("discord_webhook_url", "")
                url_prompt = f"Enter Discord Webhook URL [Current: {curr_url[:30]}...]: " if curr_url else "Enter Discord Webhook URL: "
                new_url = input(url_prompt).strip() or curr_url

                if new_url:
                    test_ans = input("Send test verification message to Discord right now? [Y/n]: ").strip().lower()
                    if test_ans != "n":
                        print("Sending test message to Discord...")
                        ok, msg = NotificationManager.test_connection("discord", new_url)
                        if ok:
                            console.print("\n[bold bright_green]✔ [SUCCESS][/] Test notification confirmed on Discord!")
                        else:
                            console.print(f"\n[bold red]✖ [WARN][/] Discord Test Error: {msg}")
                    acc_mgr.update_notification_settings(acc_id, "discord", discord_url=new_url)
                else:
                    console.print("[bold red]✖ [WARN][/] Webhook URL cannot be empty. Setup cancelled.")

            elif n_choice == "4":
                plat = notif_cfg.get("active_platform", "none")
                if plat == "telegram":
                    print("Sending test message to Telegram...")
                    ok, msg = NotificationManager.test_connection("telegram", notif_cfg.get("telegram_bot_token", ""), notif_cfg.get("telegram_chat_id", ""))
                    status_style = "bold bright_green" if ok else "bold red"
                    console.print(f"\n[{status_style}][{'SUCCESS' if ok else 'FAILED'}][/] Telegram Test: {msg}")
                elif plat == "discord":
                    print("Sending test message to Discord...")
                    ok, msg = NotificationManager.test_connection("discord", notif_cfg.get("discord_webhook_url", ""))
                    status_style = "bold bright_green" if ok else "bold red"
                    console.print(f"\n[{status_style}][{'SUCCESS' if ok else 'FAILED'}][/] Discord Test: {msg}")
                else:
                    console.print("\n[bold yellow]Notifications are currently disabled. Please select [2] or [3] to configure a platform first.[/]")

            if n_choice in ["1", "2", "3", "4"]:
                cfg = acc_mgr.accounts[acc_id]
                input("\nPress Enter to return to main control menu...")
        elif choice == "6":
            new_sweep = acc_mgr.toggle_liquidity_sweep(acc_id)
            cfg = acc_mgr.accounts[acc_id]
            status_lbl = "[bold bright_green]ENABLED[/] (Filtering low-probability chop)" if new_sweep else "[bold red]DISABLED[/] (Standard DCC baseline)"
            console.print(f"\n[bold bright_green]✔ [UPDATED][/] 5M Liquidity Sweep Confluence is now {status_lbl}!")
            input("\nPress Enter to return to main control menu...")
        elif choice == "7":
            new_shield = acc_mgr.toggle_news_shield(acc_id)
            cfg = acc_mgr.accounts[acc_id]
            status_lbl = "[bold bright_green]ENABLED[/] (15m Blackout around USD High-Impact News)" if new_shield else "[bold red]DISABLED[/] (Off)"
            console.print(f"\n[bold bright_green]✔ [UPDATED][/] High-Impact News Shield is now {status_lbl}!")
            input("\nPress Enter to return to main control menu...")
        elif choice == "8":
            new_mode = acc_mgr.toggle_entry_mode(acc_id)
            cfg = acc_mgr.accounts[acc_id]
            mode_lbl = "[bold bright_cyan]BAR-CLOSE[/] [bright_green](Instant entry on EMA crossover / Matches Backtest 1:1)[/]" if new_mode == "bar_close" else "[bold yellow]PRE-ARM[/] [white](2-Min High-Frequency Tick Stream)[/]"
            console.print(f"\n[bold bright_green]✔ [UPDATED][/] Entry Execution Mode is now {mode_lbl}!")
            input("\nPress Enter to return to main control menu...")
        elif choice == "9":
            render_economic_calendar(console)
            input("\nPress Enter to return to main control menu...")
        elif choice == "10":
            console.print("\n[bold yellow]Shutting down Institutional DCC Bot connection to MT5...[/]")
            mt5.shutdown()
            sys.exit(0)
        else:
            console.print("[bold red]✖ [WARN][/] Invalid option. Please enter a number between 1 and 10.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Run DCC Institutional Live Bot on MT5")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--risk", type=float, default=None, help="Risk per trade as decimal (e.g. 0.01 for 1 percent)")
    parser.add_argument("--daily-loss-limit", type=float, default=None, help="Daily drawdown hard limit percentage (e.g. 4.0 percent)")
    parser.add_argument("--daily-cb", type=float, default=None, help="Daily circuit breaker percentage (e.g. 3.0 percent, must be < daily-loss-limit)")
    parser.add_argument("--max-total-dd", type=float, default=None, help="Maximum total drawdown hard limit percentage (e.g. 8.0 percent)")
    parser.add_argument("--max-cb", type=float, default=None, help="Maximum total circuit breaker percentage (e.g. 7.0 percent, must be < max-total-dd)")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run in paper trading mode")
    parser.add_argument("--auto", action="store_true", default=False, help="Bypass interactive menu and run immediately with saved account config")
    parser.add_argument("--no-sweep", action="store_true", default=False, help="Disable 5M liquidity sweep confluence filter")
    parser.add_argument("--no-news-shield", action="store_true", default=False, help="Disable Forex Factory high-impact news blackout shield")
    parser.add_argument("--no-ema-gap-filter", action="store_true", default=False, help="Deprecated: EMA gap filter is permanently nuked in v1.2")
    parser.add_argument("--entry-mode", choices=["pre_arm", "bar_close"], default=None, help="Entry mode: pre_arm (tick stream) or bar_close (instant flip)")
    parser.add_argument("--strategy-version", choices=["v1.0", "v1.1", "v1.2"], default=os.getenv("DCC_VERSION", "v1.2"), help="Strategy version: v1.0 (Baseline), v1.1 (Early ApexHunter), or v1.2 (ApexHunter Flagship)")
    args = parser.parse_args()

    if not mt5.initialize():
        print(f"[ERROR] MT5 initialization failed: {mt5.last_error()}")
        sys.exit(1)

    account_info = mt5.account_info()
    if not account_info:
        print("[ERROR] Failed to fetch MT5 account info. Please ensure MT5 is running and logged in.")
        sys.exit(1)

    acc_mgr = AccountConfigManager()

    if args.auto:
        cfg = acc_mgr.get_or_setup_account(account_info, auto_defaults=True)
        mode = "dry_run" if args.dry_run else "live"
        day_start = get_daily_starting_equity(account_info.balance)
        print(acc_mgr.format_account_dashboard(str(account_info.login), account_info.equity, account_info.balance, daily_starting_equity=day_start))
    else:
        mode, cfg = show_interactive_menu(account_info, acc_mgr)

    symbols = args.symbols if args.symbols is not None else cfg.get("symbols", ["XAUUSD", "NAS100"])
    risk = args.risk if args.risk is not None else cfg.get("risk_per_trade", 0.01)
    daily_dd = args.daily_loss_limit if args.daily_loss_limit is not None else cfg.get("daily_dd_limit_pct", 4.0)
    daily_cb = args.daily_cb if args.daily_cb is not None else cfg.get("daily_cb_pct", round(max(0.1, daily_dd - 1.0), 2))
    max_dd = args.max_total_dd if args.max_total_dd is not None else cfg.get("max_total_dd_pct", 8.0)
    max_cb = args.max_cb if args.max_cb is not None else cfg.get("max_cb_pct", round(max(0.1, max_dd - 1.0), 2))
    is_dry_run = True if (mode == "dry_run" or args.dry_run) else False
    use_sweep = False if args.no_sweep else cfg.get("use_liquidity_sweep", True)
    use_news = False if args.no_news_shield else cfg.get("use_news_shield", True)
    entry_m = args.entry_mode if args.entry_mode is not None else cfg.get("entry_mode", "bar_close")
    strat_ver = args.strategy_version if args.strategy_version is not None else cfg.get("strategy_version", "v1.2")

    # Automatically persist command-line overrides to bot_accounts_config.json so they are remembered forever
    cli_changed = False
    if "use_ema_gap_filter" in cfg:
        del cfg["use_ema_gap_filter"]
        cli_changed = True
    if args.entry_mode is not None and cfg.get("entry_mode") != args.entry_mode:
        cfg["entry_mode"] = args.entry_mode
        cli_changed = True
    if args.no_sweep and cfg.get("use_liquidity_sweep") is not False:
        cfg["use_liquidity_sweep"] = False
        cli_changed = True
    if args.no_news_shield and cfg.get("use_news_shield") is not False:
        cfg["use_news_shield"] = False
        cli_changed = True
    if args.strategy_version is not None and cfg.get("strategy_version") != args.strategy_version:
        cfg["strategy_version"] = args.strategy_version
        cli_changed = True
    if cli_changed:
        cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
        acc_mgr.save()
        print("[PERSISTENCE] Command-line settings permanently saved to bot_accounts_config.json!")

    bot = InstitutionalDCCBot(
        symbols=symbols,
        risk_per_trade=risk,
        daily_dd_limit_pct=daily_dd,
        daily_cb_pct=daily_cb,
        max_total_dd_pct=max_dd,
        max_cb_pct=max_cb,
        high_water_mark=cfg.get("high_water_mark", account_info.equity),
        config_mgr=acc_mgr,
        dry_run=is_dry_run,
        use_liquidity_sweep=use_sweep,
        use_news_shield=use_news,
        use_ema_gap_filter=False,
        entry_mode=entry_m,
        strategy_version=strat_ver,
    )
    if bot.initialize():
        bot.run()


if __name__ == "__main__":
    main()
