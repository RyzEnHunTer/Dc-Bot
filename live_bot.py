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

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
import json
import os
import sys
import time as pytime
from typing import Dict, List, Optional, Tuple

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from dcc_engine import DCCEngine, SignalType, TradeSignal
from mt5_data import MT5DataProvider
from notifier import NotificationManager


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
        max_allowed_spread=5.0,   # Max 5.0 pts spread on Nasdaq
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


@dataclass
class ActiveTwinPosition:
    symbol: str
    direction: str
    entry_price: float
    entry_spread: float
    entry_time: datetime
    ticket_a: int               # 50% TP1
    ticket_b: int               # 50% Runner
    ticket_a_closed: bool = False
    runner_moved_to_be: bool = False


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


class AccountConfigManager:
    """Persistent storage for per-account Risk, Daily DD, Max DD, and High-Water Mark."""
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
            if "notifications" not in cfg:
                cfg["notifications"] = {
                    "active_platform": "none",
                    "telegram_bot_token": "",
                    "telegram_chat_id": "",
                    "discord_webhook_url": ""
                }
                self.save()
            if "use_liquidity_sweep" not in cfg:
                cfg["use_liquidity_sweep"] = True
                self.save()
            # Track peak high-water mark
            if account_info.equity > cfg.get("high_water_mark", 0.0):
                cfg["high_water_mark"] = round(float(account_info.equity), 2)
                self.save()
            return cfg

        # NEW ACCOUNT DETECTED!
        print("\n" + "=" * 80)
        print(f"  >>> [NEW MT5 ACCOUNT DETECTED: {acc_id} ({account_info.server})] <<<")
        print("=" * 80)
        print(f"Equity: ${account_info.equity:,.2f} | Balance: ${account_info.balance:,.2f} | Leverage: 1:{account_info.leverage}")
        print("Let's configure the Risk & Drawdown Circuit Breakers for this account:\n")

        if auto_defaults:
            daily_dd = 3.0
            max_dd = 8.0
            risk_pct = 1.0
            print(f"  [Auto-Assigned Defaults] Daily DD: {daily_dd}%, Max DD: {max_dd}%, Risk: {risk_pct}%")
        else:
            daily_in = input("Enter Daily Drawdown Limit % [Press Enter for Default 3.0%]: ").strip()
            daily_dd = float(daily_in) if daily_in else 3.0

            max_in = input("Enter Maximum Total Drawdown Limit % [Press Enter for Default 8.0%]: ").strip()
            max_dd = float(max_in) if max_in else 8.0

            risk_in = input("Enter Risk Per Trade % [Press Enter for Default 1.0%]: ").strip()
            risk_pct = float(risk_in) if risk_in else 1.0

        cfg = {
            "login": int(account_info.login),
            "server": str(account_info.server),
            "currency": str(account_info.currency),
            "risk_per_trade": round(risk_pct / 100.0, 4),
            "daily_dd_limit_pct": round(daily_dd, 2),
            "max_total_dd_pct": round(max_dd, 2),
            "symbols": ["XAUUSD", "NAS100"],
            "high_water_mark": round(float(account_info.equity), 2),
            "use_liquidity_sweep": True,
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

    def update_account_rules(self, acc_id: str, risk_pct: float, daily_dd: float, max_dd: float):
        if acc_id in self.accounts:
            self.accounts[acc_id]["risk_per_trade"] = round(risk_pct / 100.0, 4)
            self.accounts[acc_id]["daily_dd_limit_pct"] = round(daily_dd, 2)
            self.accounts[acc_id]["max_total_dd_pct"] = round(max_dd, 2)
            self.accounts[acc_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            print(f"\n[SUCCESS] Updated rules for Account {acc_id} successfully!")

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


class InstitutionalDCCBot:
    def __init__(
        self,
        symbols: List[str] = ["XAUUSD", "NAS100"],
        risk_per_trade: float = 0.01,
        daily_loss_limit_pct: float = 3.0,
        max_total_dd_pct: float = 8.0,
        high_water_mark: float = 0.0,
        config_mgr: Optional['AccountConfigManager'] = None,
        dry_run: bool = False,
        use_liquidity_sweep: bool = True,
    ):
        self.symbols = symbols
        self.risk_per_trade = risk_per_trade
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_total_dd_pct = max_total_dd_pct
        self.high_water_mark = high_water_mark
        self.config_mgr = config_mgr
        self.dry_run = dry_run
        self.use_liquidity_sweep = use_liquidity_sweep
        self.notifier = NotificationManager()
        self.session_notified: Dict[str, bool] = {}
        self.audit_logger = MarketAuditLogger()
        self.spinner_frames = ["[|]", "[/]", "[-]", "[\\]"]
        self.spinner_idx = 0
        self.tz_ist = timezone(timedelta(hours=5, minutes=30))

        self.data_provider = MT5DataProvider()
        self.engines: Dict[str, DCCEngine] = {}
        self.armed_states: Dict[str, PreArmedState] = {s: PreArmedState() for s in symbols}
        self.active_positions: Dict[str, ActiveTwinPosition] = {}
        self.last_checked_bars: Dict[str, Optional[int]] = {s: None for s in symbols}

        # Daily Circuit Breaker State
        self.current_trading_day: Optional[datetime.date] = None
        self.daily_starting_equity: float = 0.0
        self.circuit_breaker_active: bool = False

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

        self.daily_starting_equity = account.equity
        self.current_trading_day = datetime.now(timezone.utc).date()

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

        print("\n" + "=" * 75)
        print("INSTITUTIONAL DCC BOT INITIALIZED")
        print(f"Account Login:           {account.login} ({account.server})")
        print(f"Equity / Balance:        ${account.equity:,.2f} / ${account.balance:,.2f}")
        print(f"Peak Equity (HWM):       ${self.high_water_mark:,.2f}")
        print(f"Daily Starting Equity:   ${self.daily_starting_equity:,.2f}")
        print(f"Daily DD Circuit Breaker: -{self.daily_loss_limit_pct:.1f}% (Halt if daily equity <= ${self.daily_starting_equity * (1.0 - self.daily_loss_limit_pct / 100.0):,.2f})")
        print(f"Max Account DD Breaker:   -{self.max_total_dd_pct:.1f}% (Emergency halt if equity <= ${self.high_water_mark * (1.0 - self.max_total_dd_pct / 100.0):,.2f})")
        print(f"Leverage:                1:{account.leverage}")
        print(f"Monitored Assets:        {', '.join(self.symbols)}")
        print(f"Risk Per Trade:          {self.risk_per_trade * 100:.1f}%")
        print(f"Execution Mode:          {'DRY RUN (Paper Mode)' if self.dry_run else 'LIVE ORDER EXECUTION'}")
        sweep_str = "ENABLED (Turtle Soup Filter)" if self.use_liquidity_sweep else "DISABLED (Standard Baseline)"
        print(f"5M Liquidity Sweep:      {sweep_str}")
        
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
            daily_dd=self.daily_loss_limit_pct,
            max_dd=self.max_total_dd_pct,
            symbols=self.symbols
        )

        return True

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

    def calculate_lots(self, symbol: str, sl_distance: float) -> Tuple[float, float, float]:
        account = mt5.account_info()
        equity = account.equity if account else 5000.0
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

        # Fetch rates with warmup
        m5_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 150)
        h1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 50)
        h2_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H2, 0, 50)

        if m5_rates is None or h1_rates is None or len(m5_rates) < 30:
            return

        df_m5 = pd.DataFrame(m5_rates)
        df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s', utc=True)
        df_m5.set_index('time', inplace=True)
        df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

        df_1h = pd.DataFrame(h1_rates)
        df_1h['time'] = pd.to_datetime(df_1h['time'], unit='s', utc=True)
        df_1h.set_index('time', inplace=True)

        df_2h = pd.DataFrame(h2_rates)
        df_2h['time'] = pd.to_datetime(df_2h['time'], unit='s', utc=True)
        df_2h.set_index('time', inplace=True)

        engine = self.engines[symbol]
        df_prep = engine.prepare_data(df_m5, df_1h, df_2h)

        # Look at the newly closed 5M bar (index -2)
        closed_bar = df_prep.iloc[-2]
        c_time = closed_bar.name
        c_time_str = c_time.strftime("%Y-%m-%d %H:%M:%S")
        ist_time_str = c_time.astimezone(self.tz_ist).strftime("%Y-%m-%d %H:%M:%S")

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

        # 1. Circuit breaker check
        if self.circuit_breaker_active:
            decision = "BLOCKED_CIRCUIT_BREAKER"
            reason = f"Daily -{self.daily_loss_limit_pct:.1f}% Circuit Breaker is ACTIVE"
            self.armed_states[symbol].is_armed = False
        # 2. Existing position check
        elif open_pos and len(open_pos) > 0:
            decision = "BLOCKED_POSITION_OPEN"
            reason = f"Trade already active on {symbol} (Ticket #{open_pos[0].ticket})"
            self.armed_states[symbol].is_armed = False
        # 3. Dead trap hours (09:00 & 13:00 UTC)
        elif now_utc.hour in [9, 13]:
            decision = "SKIPPED_DEAD_HOUR"
            reason = f"Dead Trap Hour Filter Active ({now_utc.hour:02d}:00 UTC)"
            self.armed_states[symbol].is_armed = False
        # 4. ADX threshold check
        elif adx < cfg.adx_min:
            decision = "SKIPPED_LOW_ADX"
            reason = f"1H ADX ({adx:.1f}) < min threshold ({cfg.adx_min:.1f})"
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
            # Check setup formation
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
                elif ema_gap > (0.35 * atr):
                    decision = "SKIPPED_GAP_TOO_WIDE"
                    reason = f"EMA Gap ({ema_gap:.2f}) > 0.35*ATR ({0.35*atr:.2f})"
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
                elif ema_gap > (0.35 * atr):
                    decision = "SKIPPED_GAP_TOO_WIDE"
                    reason = f"EMA Gap ({ema_gap:.2f}) > 0.35*ATR ({0.35*atr:.2f})"
                    self.armed_states[symbol].is_armed = False
                elif self.use_liquidity_sweep and not has_sweep:
                    decision = "SKIPPED_SWEEP_UNCONFIRMED"
                    reason = f"5M Swing High ({sw_high_5m:.2f}) liquidity sweep not satisfied"
                    self.armed_states[symbol].is_armed = False
                else:
                    decision = "ARMED_SELL"
                    reason = "1H Bearish + 5M Compression + VWAP + Sweep Confirmed"

        if decision.startswith("ARMED"):
            tot_lots, p_lots, r_lots = self.calculate_lots(symbol, sl_dist)
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

        # Terminal Visual Card
        bias_label = "BULLISH (+1)" if bias == 1 else ("BEARISH (-1)" if bias == -1 else "NEUTRAL (0)")
        adx_status = "PASS" if adx >= cfg.adx_min else "FAIL"
        sweep_str = f"CONFIRMED (Swept {sweep_lvl:.{digits}f} by {pts:.{digits}f} pts)" if has_sweep else "NO SWEEP"
        
        print("\n" + "=" * 80)
        print(f"  [5M CANDLE EVALUATION AUDIT] {symbol} | {c_time_str} UTC ({ist_time_str} IST)")
        print("=" * 80)
        print(f"  * Candle OHLC:    Open: {open_p:.{digits}f} | High: {high_p:.{digits}f} | Low: {low_p:.{digits}f} | Close: {close_p:.{digits}f} | Vol: {int(vol):,}")
        print(f"  * 5M Indicators:  EMA9: {m5_e9:.{digits}f} | EMA20: {m5_e20:.{digits}f} | Gap: {ema_gap:.{digits}f} ({ema_gap_ratio:.2f}x ATR) | VWAP: {vwap:.{digits}f}")
        print(f"  * 1H Map Context: Bias: {bias_label} | ADX: {adx:.1f} ({adx_status} >= {cfg.adx_min:.1f}) | ATR: {atr:.{digits}f} | 1H EMA20: {h1_e20:.{digits}f}")
        print(f"  * Swing Levels:   5M Low: {sw_low_5m:.{digits}f} | 5M High: {sw_high_5m:.{digits}f} | 2H Low: {sw_low_2h:.{digits}f} | 2H High: {sw_high_2h:.{digits}f}")
        print(f"  * 5M Sweep Check: {sweep_str}")
        if decision.startswith("ARMED"):
            print(f"  * DECISION:       >>> {decision} <<<")
            print(f"    Planned Setup:  Entry: {planned_entry:.{digits}f} | SL: {planned_sl:.{digits}f} | TP1: {planned_tp1:.{digits}f} | TP2: {planned_tp2:.{digits}f} | Lots: {planned_lots}")
        else:
            print(f"  * DECISION:       {decision} -> {reason}")
        print("=" * 80 + "\n")

    def process_tick_stream_last_2min(self, symbol: str, seconds_left: float):
        """High-frequency tick handler active during the last 2 minutes of the armed candle."""
        if self.circuit_breaker_active:
            self.armed_states[symbol].is_armed = False
            return

        state = self.armed_states[symbol]
        clean_tick = self.get_clean_tick(symbol)
        if clean_tick is None:
            return  # Skip bad/None tick without error

        bid, ask, spread = clean_tick
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

        # Zero-Latency Fire at Candle Boundary (< 0.25s to close)
        if seconds_left <= 0.25 and is_flip_valid:
            print(f"\n>>> [0.0s BAR CLOSE CONFIRMED] FIRING ZERO-LATENCY TWIN ORDERS ON {symbol}! <<<")
            self.execute_twin_orders(symbol, state, ref_price, spread)
            state.is_armed = False  # Reset armed state

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
                ticket_b=ticket_b
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
        else:
            ret_a = res_a.retcode if res_a else "None"
            ret_b = res_b.retcode if res_b else "None"
            print(f"[WARN] Order execution warning: RetCode A={ret_a}, RetCode B={ret_b}")

    def manage_active_positions(self):
        """Monitors open positions: Automatically moves Runner SL to Breakeven when Ticket A closes."""
        for symbol in list(self.active_positions.keys()):
            pos_info = self.active_positions[symbol]
            if pos_info.runner_moved_to_be:
                continue

            # Check if Ticket A is still open
            open_positions = mt5.positions_get(symbol=symbol)
            open_tickets = [p.ticket for p in open_positions] if open_positions else []

            # If Ticket A has closed (hit TP1) but Ticket B is still open
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
                    print(f"\n>>> [{symbol} BREAKEVEN AUTOMATOR] Ticket A hit TP1! Runner #{pos_info.ticket_b} SL shifted to BREAKEVEN ({be_sl:.{digits}f})! Trade is 100% Risk-Free! <<<\n")
                    pos_info.runner_moved_to_be = True
                    pos_info.ticket_a_closed = True
                    self.notifier.notify_tp1_breakeven(symbol, pos_info.ticket_a, pos_info.ticket_b, be_sl)
                else:
                    err_msg = res_mod.comment if res_mod else "None"
                    print(f"[{symbol}] Breakeven modification pending ({err_msg}). Will retry next tick.")

            # If both tickets are closed, remove from tracking
            if pos_info.ticket_a not in open_tickets and pos_info.ticket_b not in open_tickets:
                reason = "RUNNER CLOSED (TP2 / BE / SL)" if pos_info.runner_moved_to_be else "CLOSED (TP / SL / MANUAL)"
                print(f"[{symbol}] Position #{pos_info.ticket_b} closed ({reason}).")
                self.notifier.notify_trade_closed(symbol, pos_info.ticket_b, reason, 0.0)
                del self.active_positions[symbol]

    def run(self):
        print("\n[InstitutionalDCCBot] Starting Live Monitoring Loop...")
        print("Surveillance: Background 5M checks -> Armed Candle -> 2-Min Ultra-Light Tick Stream")
        print(f"Logging System: Live calculations saved to {self.audit_logger.log_dir}/market_calculations_YYYYMMDD.csv\n")

        try:
            while True:
                now_utc = datetime.now(timezone.utc)
                now_ist = now_utc.astimezone(self.tz_ist)
                seconds_into_5m = (now_utc.minute % 5) * 60 + now_utc.second + now_utc.microsecond / 1_000_000.0
                seconds_left_in_5m = 300.0 - seconds_into_5m
                m_left = int(seconds_left_in_5m // 60)
                s_left = int(seconds_left_in_5m % 60)

                # 0. Check daily rollover (00:00 UTC)
                today = now_utc.date()
                if self.current_trading_day != today:
                    self.current_trading_day = today
                    account = mt5.account_info()
                    if account:
                        self.daily_starting_equity = account.equity
                    self.circuit_breaker_active = False
                    self.session_notified = {}
                    print(f"\n[NEW TRADING DAY: {today}] Circuit Breaker Reset. Starting Equity Anchor: ${self.daily_starting_equity:,.2f}")

                # 1. Continuous Drawdown Surveillance (Two-Layer Circuit Breaker)
                account = mt5.account_info()
                if account:
                    current_equity = account.equity

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
                        if total_dd_pct >= self.max_total_dd_pct:
                            sys.stdout.write("\n")
                            print("\n" + "!" * 80)
                            print(f"[🚨 EMERGENCY: MAX ACCOUNT DRAWDOWN -{total_dd_pct:.2f}% REACHED 🚨]")
                            print(f"High-Water Mark:   ${self.high_water_mark:,.2f}")
                            print(f"Current Equity:    ${current_equity:,.2f}")
                            print(f"Max Limit Allowed: -{self.max_total_dd_pct:.2f}%")
                            print("ACTION: Emergency close on all positions. Bot permanently halted to protect capital.")
                            print("!" * 80 + "\n")
                            self.notifier.notify_circuit_breaker("max_total", current_equity, self.max_total_dd_pct, self.high_water_mark - current_equity)
                            self.emergency_close_all()
                            break

                    # C. Layer 1: Daily Drawdown Circuit Breaker
                    if self.daily_starting_equity > 0:
                        daily_pnl = current_equity - self.daily_starting_equity
                        daily_dd_pct = (self.daily_starting_equity - current_equity) / self.daily_starting_equity * 100.0
                        if daily_dd_pct >= self.daily_loss_limit_pct and not self.circuit_breaker_active:
                            self.circuit_breaker_active = True
                            sys.stdout.write("\n")
                            print("\n" + "!" * 80)
                            print(f"[🚨 DAILY {self.daily_loss_limit_pct:.1f}% CIRCUIT BREAKER TRIGGERED 🚨]")
                            print(f"Daily Starting Equity: ${self.daily_starting_equity:,.2f}")
                            print(f"Current Equity:        ${current_equity:,.2f}")
                            print(f"Daily Drawdown:        -{daily_dd_pct:.2f}% (Limit: -{self.daily_loss_limit_pct:.2f}%) | Loss: -${abs(daily_pnl):,.2f}")
                            print("ACTION: Trading HALTED for remainder of day. All setup arming disabled until 00:00 UTC.")
                            print("!" * 80 + "\n")
                            self.notifier.notify_circuit_breaker("daily", current_equity, self.daily_loss_limit_pct, abs(daily_pnl))
                            for s in self.symbols:
                                self.armed_states[s].is_armed = False

                # 2. Session alerts (London 06:00 UTC, NY 12:00 UTC, EOD 21:00 UTC)
                cur_h = now_utc.hour
                cur_m = now_utc.minute
                if cur_m == 0:
                    if cur_h == 6 and not self.session_notified.get("london_open", False):
                        self.session_notified["london_open"] = True
                        self.notifier.notify_session("London Session (06:00 UTC / 11:30 IST)", "OPENED")
                    elif cur_h == 12 and not self.session_notified.get("ny_open", False):
                        self.session_notified["ny_open"] = True
                        self.notifier.notify_session("New York Session (12:00 UTC / 17:30 IST)", "OPENED")
                    elif cur_h == 21 and not self.session_notified.get("eod_close", False):
                        self.session_notified["eod_close"] = True
                        self.notifier.notify_session("End-of-Day (21:00 UTC / 02:30 IST)", "REACHED")

                # 3. Manage any active positions (Breakeven automator)
                self.manage_active_positions()

                # 4. Check for newly closed 5M bar on each symbol
                for symbol in self.symbols:
                    m5_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 2)
                    if m5_rates is not None and len(m5_rates) >= 2:
                        last_bar_t = m5_rates[-2]['time']
                        if self.last_checked_bars[symbol] is None:
                            self.last_checked_bars[symbol] = last_bar_t
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            self.check_candle_arm_status(symbol)
                        elif self.last_checked_bars[symbol] != last_bar_t:
                            self.last_checked_bars[symbol] = last_bar_t
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            self.check_candle_arm_status(symbol)

                # 5. Process Armed Candlestick Tick Streaming in last 2 minutes
                for symbol in self.symbols:
                    state = self.armed_states[symbol]
                    if state.is_armed and seconds_left_in_5m <= 120.0:
                        self.process_tick_stream_last_2min(symbol, seconds_left_in_5m)

                # 6. Real-time Heartbeat & Status Animation
                any_armed_in_window = any(
                    s.is_armed and seconds_left_in_5m <= 120.0 for s in self.armed_states.values()
                )
                if not any_armed_in_window:
                    frame = self.spinner_frames[self.spinner_idx % len(self.spinner_frames)]
                    self.spinner_idx += 1

                    tick_xau = mt5.symbol_info_tick("XAUUSD")
                    tick_nas = mt5.symbol_info_tick("NAS100")
                    xau_str = f"{tick_xau.bid:.2f}/{tick_xau.ask:.2f} (Sp: {tick_xau.ask-tick_xau.bid:.2f})" if tick_xau else "N/A"
                    nas_str = f"{tick_nas.bid:.1f}/{tick_nas.ask:.1f} (Sp: {tick_nas.ask-tick_nas.bid:.1f})" if tick_nas else "N/A"
                    eq_val = f"${account.equity:,.2f}" if account else "$0.00"

                    hb = (
                        f"[SURVEILLANCE {frame}] {now_utc.strftime('%H:%M:%S')} UTC ({now_ist.strftime('%H:%M:%S')} IST) | "
                        f"XAU: {xau_str} | NAS: {nas_str} | Next Bar in: {m_left:02d}m {s_left:02d}s | "
                        f"Eq: {eq_val}"
                    )
                    sys.stdout.write(f"\r{hb.ljust(115)}")
                    sys.stdout.flush()
                    pytime.sleep(1.0)
                else:
                    pytime.sleep(0.05)

        except KeyboardInterrupt:
            print("\n[InstitutionalDCCBot] Shutdown requested by user.")
            self.notifier.notify_shutdown("User Stopped Bot (Ctrl+C)")
        except Exception as e:
            print(f"\n[InstitutionalDCCBot] Error: {e}")
            self.notifier.notify_shutdown(f"Unexpected Error: {e}")
        finally:
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

        risk_pct = cfg["risk_per_trade"] * 100.0
        daily_limit_pct = cfg["daily_dd_limit_pct"]
        max_limit_pct = cfg["max_total_dd_pct"]

        daily_halt_equity = equity * (1.0 - daily_limit_pct / 100.0)
        max_halt_equity = hwm * (1.0 - max_limit_pct / 100.0)
        first_trade_risk = equity * cfg["risk_per_trade"]

        notif_cfg = acc_mgr.get_notification_config(acc_id)
        plat = notif_cfg.get("active_platform", "none")
        if plat == "telegram":
            chat_display = notif_cfg.get("telegram_chat_id", "N/A")
            plat_str = f"Telegram Bot (Active | Chat ID: {chat_display})"
        elif plat == "discord":
            plat_str = "Discord Webhook (Active)"
        else:
            plat_str = "Disabled (None)"

        print("\n" + "=" * 80)
        print("               INSTITUTIONAL DCC TRADING BOT - CONTROL MENU")
        print("=" * 80)
        print(f"Active MT5 Account:  {acc_id} ({account_info.server})")
        print(f"Account Equity:      ${equity:,.2f}  |  Balance: ${balance:,.2f}  |  Free Margin: ${free_margin:,.2f}")
        print(f"Peak Equity (HWM):   ${hwm:,.2f}")
        print("-" * 80)
        print(f"Current Rules for Account {acc_id}:")
        print(f"  • Risk Per Trade:           {risk_pct:.1f}% (~${first_trade_risk:,.2f} risk on next trade)")
        print(f"  • Daily DD Circuit Breaker: {daily_limit_pct:.1f}% (Halts if today's equity <= ${daily_halt_equity:,.2f})")
        print(f"  • Max Total DD Breaker:     {max_limit_pct:.1f}% (Emergency stop if equity <= ${max_halt_equity:,.2f})")
        use_sweep = cfg.get("use_liquidity_sweep", True)
        sweep_str = "ENABLED (Recommended)" if use_sweep else "DISABLED (Off)"

        print(f"  • Monitored Assets:         {', '.join(cfg.get('symbols', ['XAUUSD', 'NAS100']))}")
        print(f"  • 5M Liquidity Sweep:       {sweep_str}")
        print(f"  • Remote Notifications:     {plat_str}")
        print("-" * 80)
        print("Select Action:")
        print("  [1] Start LIVE Trading (Real MT5 Orders)")
        print("  [2] Start PAPER Trading (Dry-Run / Zero Risk Simulation)")
        print("  [3] Edit Risk & Drawdown Rules (Change Daily DD, Max DD, Risk %)")
        print("  [4] Configure Remote Notifications (Telegram / Discord)")
        print(f"  [5] Toggle 5M Liquidity Sweep Confluence (Currently: {'ON' if use_sweep else 'OFF'})")
        print("  [6] Exit")
        print("=" * 80)

        choice = input("Enter choice [1-6] (Press Enter for [1]): ").strip()
        if not choice or choice == "1":
            return "live", cfg
        elif choice == "2":
            return "dry_run", cfg
        elif choice == "3":
            print(f"\n--- EDIT RULES FOR ACCOUNT {acc_id} ---")
            curr_r = cfg["risk_per_trade"] * 100.0
            r_in = input(f"New Risk Per Trade % [Current: {curr_r:.1f}%] (Press Enter to keep): ").strip()
            new_r = float(r_in) if r_in else curr_r

            curr_d = cfg["daily_dd_limit_pct"]
            d_in = input(f"New Daily DD Limit % [Current: {curr_d:.1f}%] (Press Enter to keep): ").strip()
            new_d = float(d_in) if d_in else curr_d

            curr_m = cfg["max_total_dd_pct"]
            m_in = input(f"New Max Account DD % [Current: {curr_m:.1f}%] (Press Enter to keep): ").strip()
            new_m = float(m_in) if m_in else curr_m

            acc_mgr.update_account_rules(acc_id, new_r, new_d, new_m)
            cfg = acc_mgr.accounts[acc_id]
            input("\nSettings updated! Press Enter to return to main menu...")
        elif choice == "4":
            print(f"\n--- CONFIGURE REMOTE NOTIFICATIONS FOR ACCOUNT {acc_id} ---")
            print(f"Current Active Platform: {plat.upper()}")
            print("  [1] Disable Notifications (None)")
            print("  [2] Set up Telegram Bot (Bot Token & Chat ID)")
            print("  [3] Set up Discord Webhook (Webhook URL)")
            print("  [4] Send Test Notification on Active Channel")
            print("  [5] Return to Main Menu")
            n_choice = input("Select option [1-5]: ").strip()

            if n_choice == "1":
                acc_mgr.update_notification_settings(acc_id, "none")
                print("\n[OK] Remote notifications disabled for this account.")
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
                            print("\n[SUCCESS] Test message confirmed on Telegram!")
                        else:
                            print(f"\n[WARN] Telegram Test Error: {msg}")
                    acc_mgr.update_notification_settings(acc_id, "telegram", tg_token=new_tok, tg_chat_id=new_chat)
                else:
                    print("[WARN] Bot Token or Chat ID cannot be empty. Setup cancelled.")

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
                            print("\n[SUCCESS] Test notification confirmed on Discord!")
                        else:
                            print(f"\n[WARN] Discord Test Error: {msg}")
                    acc_mgr.update_notification_settings(acc_id, "discord", discord_url=new_url)
                else:
                    print("[WARN] Webhook URL cannot be empty. Setup cancelled.")

            elif n_choice == "4":
                if plat == "telegram":
                    print("Sending test message to Telegram...")
                    ok, msg = NotificationManager.test_connection("telegram", notif_cfg.get("telegram_bot_token", ""), notif_cfg.get("telegram_chat_id", ""))
                    print(f"\n[{'SUCCESS' if ok else 'FAILED'}] Telegram Test: {msg}")
                elif plat == "discord":
                    print("Sending test message to Discord...")
                    ok, msg = NotificationManager.test_connection("discord", notif_cfg.get("discord_webhook_url", ""))
                    print(f"\n[{'SUCCESS' if ok else 'FAILED'}] Discord Test: {msg}")
                else:
                    print("\n[INFO] Notifications are currently disabled. Please select [2] or [3] to configure a platform first.")

            cfg = acc_mgr.accounts[acc_id]
            input("\nPress Enter to return to main menu...")
        elif choice == "5":
            new_sweep = acc_mgr.toggle_liquidity_sweep(acc_id)
            cfg = acc_mgr.accounts[acc_id]
            status_lbl = "ENABLED (Filtering low-probability chop)" if new_sweep else "DISABLED (Standard DCC baseline)"
            print(f"\n[UPDATED] 5M Liquidity Sweep Confluence is now {status_lbl.upper()}!")
            input("\nPress Enter to return to main menu...")
        elif choice == "6":
            print("Exiting Institutional DCC Bot.")
            mt5.shutdown()
            sys.exit(0)
        else:
            print("[WARN] Invalid option. Please enter 1, 2, 3, 4, 5, or 6.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Run DCC Institutional Live Bot on MT5")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--risk", type=float, default=None, help="Risk per trade as decimal (e.g. 0.01 for 1 percent)")
    parser.add_argument("--daily-loss-limit", type=float, default=None, help="Daily drawdown percentage circuit breaker (e.g. 3.0 percent)")
    parser.add_argument("--max-total-dd", type=float, default=None, help="Maximum total drawdown percentage circuit breaker (e.g. 8.0 percent)")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run in paper trading mode")
    parser.add_argument("--auto", action="store_true", default=False, help="Bypass interactive menu and run immediately with saved account config")
    parser.add_argument("--no-sweep", action="store_true", default=False, help="Disable 5M liquidity sweep confluence filter")
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
    else:
        mode, cfg = show_interactive_menu(account_info, acc_mgr)

    symbols = args.symbols if args.symbols is not None else cfg.get("symbols", ["XAUUSD", "NAS100"])
    risk = args.risk if args.risk is not None else cfg["risk_per_trade"]
    daily_dd = args.daily_loss_limit if args.daily_loss_limit is not None else cfg["daily_dd_limit_pct"]
    max_dd = args.max_total_dd if args.max_total_dd is not None else cfg["max_total_dd_pct"]
    is_dry_run = True if (mode == "dry_run" or args.dry_run) else False
    use_sweep = False if args.no_sweep else cfg.get("use_liquidity_sweep", True)

    bot = InstitutionalDCCBot(
        symbols=symbols,
        risk_per_trade=risk,
        daily_loss_limit_pct=daily_dd,
        max_total_dd_pct=max_dd,
        high_water_mark=cfg.get("high_water_mark", account_info.equity),
        config_mgr=acc_mgr,
        dry_run=is_dry_run,
        use_liquidity_sweep=use_sweep
    )
    if bot.initialize():
        bot.run()


if __name__ == "__main__":
    main()
