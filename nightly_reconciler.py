"""
Nightly Forensic Tick-by-Tick Backtest & Live Bot Reconciliation Engine
Runs automatically at market close (19:05 UTC) or on-demand via CLI.
Replays exact broker tick streams through institutional DCC strategy rules,
cross-checks against live MT5 broker deals (history_deals_get),
identifies trade discrepancies with forensic explanations,
generates a daily scorecard, alerts Telegram/Discord, and manages rolling 7-day disk retention.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from dcc_engine import DCCEngine
from news_engine import NewsEngine
from notifier import NotificationManager
from storage_manager import StorageManager


# -------------------------------------------------------------------------
# Symbol Configuration Specification
# -------------------------------------------------------------------------
@dataclass
class SymbolConfig:
    symbol: str
    atr_sl_multiplier: float
    tp1_rr: float
    tp2_rr: float
    adx_min: float
    max_allowed_spread: float


CONFIGS = {
    "XAUUSD": SymbolConfig("XAUUSD", atr_sl_multiplier=0.9, tp1_rr=1.4, tp2_rr=2.2, adx_min=15.0, max_allowed_spread=0.60),
    "NAS100": SymbolConfig("NAS100", atr_sl_multiplier=1.0, tp1_rr=1.5, tp2_rr=2.0, adx_min=15.0, max_allowed_spread=4.50),
}


def compute_session_vwap(df: pd.DataFrame) -> pd.Series:
    """Computes daily resetting session VWAP from 00:00:00 UTC."""
    typical_price = (df['high'] + df['low'] + df['close']) / 3.0
    tp_vol = typical_price * df['volume']
    date_series = df.index.date
    df_temp = pd.DataFrame({'tp_vol': tp_vol, 'volume': df['volume'], 'date': date_series}, index=df.index)
    cum_vol = df_temp.groupby('date')['volume'].cumsum()
    cum_tp_vol = df_temp.groupby('date')['tp_vol'].cumsum()
    vwap = cum_tp_vol / (cum_vol.replace(0, np.nan) + 1e-9)
    return vwap.bfill().ffill()


def detect_sweep_at_bar(df_m5: pd.DataFrame, bar_idx: int, direction: int, lookback: int = 24) -> Tuple[bool, float, float]:
    """Detects 5M liquidity sweep (Turtle Soup) prior to entry bar."""
    if bar_idx < lookback + 2:
        return False, 0.0, 0.0

    swing_search_start = max(0, bar_idx - lookback)
    pullback_start_idx = max(swing_search_start + 1, bar_idx - 3)
    if pullback_start_idx <= swing_search_start:
        return False, 0.0, 0.0

    lows = df_m5['low'].values
    highs = df_m5['high'].values
    closes = df_m5['close'].values

    if direction == 1:  # BUY
        cand_lows = [lows[j] for j in range(swing_search_start + 1, pullback_start_idx)
                     if lows[j] <= lows[j - 1] and lows[j] <= lows[j + 1]]
        if not cand_lows:
            cand_lows = [min(lows[swing_search_start:pullback_start_idx])]
        curr_close = closes[bar_idx]
        for sw_low in cand_lows:
            min_pb = min(lows[pullback_start_idx:bar_idx + 1])
            if min_pb < sw_low and curr_close > sw_low:
                return True, sw_low, sw_low - min_pb
        return False, 0.0, 0.0
    else:  # SELL
        cand_highs = [highs[j] for j in range(swing_search_start + 1, pullback_start_idx)
                      if highs[j] >= highs[j - 1] and highs[j] >= highs[j + 1]]
        if not cand_highs:
            cand_highs = [max(highs[swing_search_start:pullback_start_idx])]
        curr_close = closes[bar_idx]
        for sw_high in cand_highs:
            max_pb = max(highs[pullback_start_idx:bar_idx + 1])
            if max_pb > sw_high and curr_close < sw_high:
                return True, sw_high, max_pb - sw_high
        return False, 0.0, 0.0


def simulate_tick_by_tick_trade(
    symbol: str,
    direction: int,
    sub_ticks_df: pd.DataFrame,
    sl_distance: float,
    tp1_rr: float,
    tp2_rr: float,
    balance: float,
    risk_pct: float,
    specs: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Replays real broker tick stream to simulate market execution and twin-ticket exit."""
    if len(sub_ticks_df) == 0:
        return None

    first_tick = sub_ticks_df.iloc[0]
    entry_time = first_tick['time_dt']
    contract_size = specs["contract_size"]
    vol_step = specs["volume_step"]
    vol_min = specs["volume_min"]

    if direction == 1:
        actual_entry = float(first_tick['ask'])
        entry_spread = float(first_tick['ask'] - first_tick['bid'])
        dir_str = "BUY"
        sl_p = actual_entry - sl_distance
        tp1_p = actual_entry + (tp1_rr * sl_distance)
        tp2_p = actual_entry + (tp2_rr * sl_distance)
    else:
        actual_entry = float(first_tick['bid'])
        entry_spread = float(first_tick['ask'] - first_tick['bid'])
        dir_str = "SELL"
        sl_p = actual_entry + sl_distance
        tp1_p = actual_entry - (tp1_rr * sl_distance)
        tp2_p = actual_entry - (tp2_rr * sl_distance)

    risk_amount = balance * risk_pct
    total_lots = risk_amount / (sl_distance * contract_size)
    min_split_lot = max(vol_min * 2, 0.02)
    total_lots = max(min_split_lot, round(total_lots / vol_step) * vol_step)
    total_lots = round(total_lots, 2)

    partial_lots = max(vol_min, round((total_lots * 0.5) / vol_step) * vol_step)
    partial_lots = round(partial_lots, 2)
    runner_lots = round(max(vol_min, total_lots - partial_lots), 2)

    curr_sl = sl_p
    tp1_hit = False
    tp2_hit = False
    be_hit = False
    sl_hit = False
    exit_time = entry_time
    exit_price_a = actual_entry
    exit_price_b = actual_entry
    exit_reason = "END_OF_SESSION"

    tick_arr = sub_ticks_df[['time_dt', 'bid', 'ask']].values

    for row in tick_arr:
        t_dt = row[0]
        bid = float(row[1])
        ask = float(row[2])
        exit_time = t_dt

        if direction == 1:  # BUY
            if not tp1_hit:
                if bid <= curr_sl:
                    sl_hit = True
                    exit_price_a = curr_sl
                    exit_price_b = curr_sl
                    exit_reason = "STOP_LOSS_HIT (-1.0R)"
                    break
                elif bid >= tp1_p:
                    tp1_hit = True
                    exit_price_a = tp1_p
                    curr_sl = actual_entry + entry_spread
            else:
                if bid <= curr_sl:
                    be_hit = True
                    exit_price_b = curr_sl
                    exit_reason = f"RUNNER_BREAKEVEN (0.0R) + BANKED_TP1 (+{tp1_rr}R)"
                    break
                elif bid >= tp2_p:
                    tp2_hit = True
                    exit_price_b = tp2_p
                    exit_reason = f"FULL_TP2_WINNER (+{tp2_rr}R)"
                    break
        else:  # SELL
            if not tp1_hit:
                if ask >= curr_sl:
                    sl_hit = True
                    exit_price_a = curr_sl
                    exit_price_b = curr_sl
                    exit_reason = "STOP_LOSS_HIT (-1.0R)"
                    break
                elif ask <= tp1_p:
                    tp1_hit = True
                    exit_price_a = tp1_p
                    curr_sl = actual_entry - entry_spread
            else:
                if ask >= curr_sl:
                    be_hit = True
                    exit_price_b = curr_sl
                    exit_reason = f"RUNNER_BREAKEVEN (0.0R) + BANKED_TP1 (+{tp1_rr}R)"
                    break
                elif ask <= tp2_p:
                    tp2_hit = True
                    exit_price_b = tp2_p
                    exit_reason = f"FULL_TP2_WINNER (+{tp2_rr}R)"
                    break

    # PnL Math in Dollars
    if direction == 1:
        pts_a = exit_price_a - actual_entry
        pts_b = exit_price_b - actual_entry
    else:
        pts_a = actual_entry - exit_price_a
        pts_b = actual_entry - exit_price_b

    dollars_a = pts_a * partial_lots * contract_size
    dollars_b = pts_b * runner_lots * contract_size
    comm_a = partial_lots * 5.0
    comm_b = runner_lots * 5.0
    net_pnl = dollars_a + dollars_b - (comm_a + comm_b)

    return {
        "symbol": symbol,
        "direction": dir_str,
        "entry_time": entry_time.strftime("%Y-%m-%d %H:%M:%S"),
        "actual_entry": round(actual_entry, specs["digits"]),
        "entry_spread": round(entry_spread, specs["digits"]),
        "sl_price": round(sl_p, specs["digits"]),
        "tp1_price": round(tp1_p, specs["digits"]),
        "tp2_price": round(tp2_p, specs["digits"]),
        "total_lots": total_lots,
        "partial_lots": partial_lots,
        "runner_lots": runner_lots,
        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S") if hasattr(exit_time, 'strftime') else str(exit_time),
        "exit_reason": exit_reason,
        "dollars_a": round(dollars_a, 2),
        "dollars_b": round(dollars_b, 2),
        "net_pnl": round(net_pnl, 2),
    }


class NightlyReconciler:
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        retention_days: int = 7,
        strategy_version: str = "v1.2",
    ):
        self.symbols = symbols or ["XAUUSD", "NAS100"]
        self.strategy_version = strategy_version
        self.news_engine = NewsEngine()
        self.storage = StorageManager(retention_days=retention_days)
        self.notifier = NotificationManager()
        self.broker_offset = self.detect_broker_offset()

    def detect_broker_offset(self) -> timedelta:
        """Detects MT5 broker server timezone offset relative to UTC."""
        try:
            sample_sym = self.symbols[0]
            tick = mt5.symbol_info_tick(sample_sym)
            if tick and tick.time > 0:
                s_time = datetime.fromtimestamp(tick.time, timezone.utc)
                offset_hrs = round((s_time - datetime.now(timezone.utc)).total_seconds() / 3600.0)
                return timedelta(hours=offset_hrs)
        except Exception:
            pass
        return timedelta(hours=3)

    def get_symbol_specs(self, symbol: str) -> Dict[str, Any]:
        info = mt5.symbol_info(symbol)
        if info is None:
            if "NAS" in symbol or "100" in symbol:
                return {"contract_size": 10.0, "volume_min": 0.01, "volume_step": 0.01, "digits": 1}
            return {"contract_size": 100.0, "volume_min": 0.01, "volume_step": 0.01, "digits": 2}
        return {
            "contract_size": info.trade_contract_size,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "digits": info.digits,
        }

    def fetch_live_deals(self, target_date: datetime.date) -> List[Dict[str, Any]]:
        """Queries MT5 history deals for target date with True UTC conversion."""
        utc_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc)
        utc_end = utc_start + timedelta(days=1)
        broker_start = utc_start + self.broker_offset
        broker_end = utc_end + self.broker_offset

        deals = mt5.history_deals_get(broker_start, broker_end)
        if not deals:
            return []

        parsed_deals = []
        for d in deals:
            # Filter out non-trade entries (deal types: 0=BUY, 1=SELL)
            if d.entry in (0, 1):  # 0=ENTRY_IN, 1=ENTRY_OUT
                deal_utc = datetime.fromtimestamp(d.time, tz=timezone.utc) - self.broker_offset
                parsed_deals.append({
                    "ticket": d.ticket,
                    "order": d.order,
                    "position_id": getattr(d, "position_id", d.order),
                    "symbol": d.symbol,
                    "time": deal_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "time_posix": d.time - int(self.broker_offset.total_seconds()),
                    "type": "BUY" if d.type == 0 else "SELL",
                    "entry_type": "ENTRY_IN" if d.entry == 0 else "ENTRY_OUT",
                    "volume": round(d.volume, 2),
                    "price": round(d.price, 2),
                    "profit": round(d.profit, 2),
                    "comment": d.comment
                })
        return parsed_deals

    def run_daily_backtest(self, target_date: datetime.date) -> List[Dict[str, Any]]:
        """Replays all broker ticks for target_date across configured symbols in True UTC."""
        utc_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc)
        utc_end = utc_start + timedelta(days=1)
        session_start = utc_start.replace(hour=6)
        session_end = utc_start.replace(hour=19)

        backtest_trades = []

        for sym in self.symbols:
            cfg = CONFIGS.get(sym, CONFIGS["XAUUSD"])
            specs = self.get_symbol_specs(sym)

            # 1. Multi-timeframe rates with warmup queried in broker time, shifted to True UTC
            warmup_start = utc_start - timedelta(days=5)
            r_m5 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, warmup_start + self.broker_offset, utc_end + self.broker_offset)
            r_1h = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, warmup_start + self.broker_offset, utc_end + self.broker_offset)
            r_2h = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H2, warmup_start + self.broker_offset, utc_end + self.broker_offset)

            if r_m5 is None or r_1h is None or len(r_m5) < 30:
                print(f"[{sym}] Insufficient rates for {target_date}, skipping.")
                continue

            df_m5 = pd.DataFrame(r_m5)
            df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s', utc=True) - self.broker_offset
            df_m5.set_index('time', inplace=True)
            df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

            df_1h = pd.DataFrame(r_1h)
            df_1h['time'] = pd.to_datetime(df_1h['time'], unit='s', utc=True) - self.broker_offset
            df_1h.set_index('time', inplace=True)

            df_2h = pd.DataFrame(r_2h)
            df_2h['time'] = pd.to_datetime(df_2h['time'], unit='s', utc=True) - self.broker_offset
            df_2h.set_index('time', inplace=True)

            engine = DCCEngine(atr_sl_multiplier=cfg.atr_sl_multiplier, risk_reward_ratio=cfg.tp1_rr)
            df_prep = engine.prepare_data(df_m5, df_1h, df_2h)
            df_prep['vwap_5m'] = compute_session_vwap(df_m5)

            # 2. Fetch raw broker ticks for the session queried in broker time, shifted to True UTC
            raw_ticks = mt5.copy_ticks_range(sym, session_start + self.broker_offset, session_end + self.broker_offset, mt5.COPY_TICKS_ALL)
            if raw_ticks is None or len(raw_ticks) == 0:
                print(f"[{sym}] No broker ticks found for {target_date}.")
                continue

            ticks_df = pd.DataFrame(raw_ticks)
            ticks_df['time_dt'] = pd.to_datetime(ticks_df['time_msc'], unit='ms', utc=True) - self.broker_offset
            ticks_df = ticks_df[(ticks_df['bid'] > 0) & (ticks_df['ask'] > 0) & (ticks_df['ask'] >= ticks_df['bid'])]

            # Evaluate 5M bars inside session
            session_bars = df_prep[(df_prep.index >= session_start) & (df_prep.index < session_end)]
            n_bars = len(session_bars)
            i = 2

            while i < n_bars:
                bar = session_bars.iloc[i]
                prev_bar = session_bars.iloc[i - 1]
                bar_time = session_bars.index[i]
                # In bar-close mode, the 5M candle closes and executes at bar_time + 5 minutes
                exec_time = bar_time + timedelta(minutes=5)
                t_hour = exec_time.hour

                # Strategy-specific session & killzone filters
                strat_ver = getattr(self, "strategy_version", "v1.2")
                if strat_ver == "v1.2":
                    # v1.2 ApexHunter Flagship: Monday PM Block (16:00 UTC and later)
                    if exec_time.weekday() == 0 and t_hour >= 16:
                        i += 1
                        continue

                    # ApexHunter Smart Killzone Check (Hours 09:00 & 13:00 UTC require stretch >= 1.10x ATR)
                    if t_hour in (9, 13):
                        h1_e20_temp = float(bar['ema20_1h']) if not pd.isna(bar['ema20_1h']) else 0.0
                        close_p_temp = float(bar['close'])
                        atr_temp = float(bar['atr_1h']) if not pd.isna(bar['atr_1h']) else 1.0
                        stretch_r = abs(close_p_temp - h1_e20_temp) / (atr_temp + 1e-9)
                        if stretch_r < 1.10:
                            i += 1
                            continue
                elif strat_ver == "v1.1":
                    # v1.1 Early ApexHunter: Monday PM Block + Hard Killzone Pause
                    if exec_time.weekday() == 0 and t_hour >= 16:
                        i += 1
                        continue
                    if t_hour in (9, 13):
                        i += 1
                        continue
                # v1.0 Baseline DCC: No Monday PM block, No killzone pause

                # News shield check at candle close / execution time
                if self.news_engine.get_active_news_shield(exec_time):
                    i += 1
                    continue

                bias = int(bar['bias_1h']) if not pd.isna(bar['bias_1h']) else 0
                adx = float(bar['adx_1h']) if not pd.isna(bar['adx_1h']) else 0.0
                atr = float(bar['atr_1h']) if not pd.isna(bar['atr_1h']) else 0.0

                if bias == 0 or adx < cfg.adx_min or atr <= 0:
                    i += 1
                    continue

                close_p = float(bar['close'])
                m5_e9 = float(bar['ema9_5m'])
                m5_e20 = float(bar['ema20_5m'])
                vwap = float(bar['vwap_5m'])
                h1_e20 = float(bar['ema20_1h'])

                prev_diff = float(prev_bar['ema9_5m']) - float(prev_bar['ema20_5m'])
                curr_diff = m5_e9 - m5_e20

                if strat_ver in ("v1.1", "v1.2"):
                    # Canonical TripleGuard Policies (v1.1 Early ApexHunter and v1.2 Flagship)
                    # Shield 1: 1H Anti-Chop Floor (Stretch >= 0.40x ATR)
                    stretch_ratio = abs(close_p - h1_e20) / (atr + 1e-9)
                    if stretch_ratio < 0.40:
                        i += 1
                        continue
                    # Shield 2: 1H ADX Exhaustion Ceiling (ADX <= 45.0)
                    if adx > 45.0:
                        i += 1
                        continue
                    # Shield 3: 5M No-Chase Guard (Chase <= 0.50x ATR)
                    chase_ratio = abs(close_p - m5_e9) / (atr + 1e-9)
                    if chase_ratio > 0.50:
                        i += 1
                        continue
                else:
                    # v1.0 Baseline DCC: Legacy EMA Gap Filter
                    ema_gap = abs(m5_e9 - m5_e20)
                    if ema_gap > (0.35 * atr):
                        i += 1
                        continue

                sig_direction = 0
                if bias == 1 and prev_diff <= 0 and curr_diff > 0 and close_p > vwap and close_p > h1_e20:
                    sig_direction = 1
                elif bias == -1 and prev_diff >= 0 and curr_diff < 0 and close_p < vwap and close_p < h1_e20:
                    sig_direction = -1

                if sig_direction != 0:
                    bar_idx_full = df_m5.index.get_loc(bar_time)
                    has_sw, sw_lvl, _ = detect_sweep_at_bar(df_m5, bar_idx_full, sig_direction, lookback=24)
                    if not has_sw:
                        i += 1
                        continue

                    # Slice ticks occurring strictly after candle close (exact order execution point)
                    sub_ticks = ticks_df[ticks_df['time_dt'] >= exec_time]
                    if len(sub_ticks) == 0:
                        i += 1
                        continue

                    first_tick = sub_ticks.iloc[0]
                    entry_spread = float(first_tick['ask'] - first_tick['bid'])
                    if entry_spread > cfg.max_allowed_spread:
                        # Blocked by spread spike
                        i += 1
                        continue

                    sl_dist = cfg.atr_sl_multiplier * atr
                    trade_res = simulate_tick_by_tick_trade(
                        symbol=sym,
                        direction=sig_direction,
                        sub_ticks_df=sub_ticks,
                        sl_distance=sl_dist,
                        tp1_rr=cfg.tp1_rr,
                        tp2_rr=cfg.tp2_rr,
                        balance=5000.0,
                        risk_pct=0.01,
                        specs=specs,
                    )

                    if trade_res:
                        trade_res["trigger_bar_time"] = bar_time.strftime("%Y-%m-%d %H:%M:%S")
                        backtest_trades.append(trade_res)

                        # Advance index past trade exit time
                        exit_dt = pd.to_datetime(trade_res["exit_time"], utc=True)
                        while i < n_bars and session_bars.index[i] <= exit_dt:
                            i += 1
                        continue

                i += 1

        return backtest_trades

    def reconcile(
        self,
        target_date: datetime.date,
        backtest_trades: List[Dict[str, Any]],
        live_deals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Reconciles backtest setups against live MT5 broker deals.
        Categorizes: MATCHED_SUCCESS, BLOCKED_BY_CUSHION, MISSED_DOWNTIME_BUG, UNEXPECTED_LIVE_DEAL.
        """
        # Group raw ENTRY_IN live deals into cohesive trade setups (twin tickets: partial + runner)
        raw_live_entries = [d for d in live_deals if d["entry_type"] == "ENTRY_IN"]
        exit_deals = [d for d in live_deals if d["entry_type"] == "ENTRY_OUT"]
        
        grouped_setups = []
        for d in raw_live_entries:
            d_time = pd.to_datetime(d["time"], utc=True)
            existing = None
            for s in grouped_setups:
                if s["symbol"] == d["symbol"] and s["type"] == d["type"]:
                    s_time = pd.to_datetime(s["time"], utc=True)
                    if abs((d_time - s_time).total_seconds()) <= 60:
                        existing = s
                        break
            pos_id = d.get("position_id", d.get("order", d.get("ticket", 0)))
            order_id = d.get("order", d.get("ticket", 0))
            if existing:
                existing["tickets"].append(d["ticket"])
                existing["orders"].append(order_id)
                existing["position_ids"].append(pos_id)
                existing["volume"] = round(existing.get("volume", 0.0) + d.get("volume", 0.0), 2)
            else:
                grouped_setups.append({
                    "tickets": [d["ticket"]],
                    "orders": [order_id],
                    "position_ids": [pos_id],
                    "ticket": d["ticket"],
                    "order": order_id,
                    "symbol": d["symbol"],
                    "time": d["time"],
                    "time_posix": d.get("time_posix", 0),
                    "type": d["type"],
                    "entry_type": "ENTRY_IN",
                    "volume": d.get("volume", 0.0),
                    "price": d["price"],
                    "profit": 0.0,
                    "comment": d["comment"]
                })

        # Calculate realized PnL for each live setup from corresponding exit deals
        for s in grouped_setups:
            s_exit_pnl = sum(ed["profit"] for ed in exit_deals if ed.get("position_id") in s["position_ids"] or ed.get("order") in s["orders"])
            s["profit"] = round(s_exit_pnl, 2)

        matched_live_indices = set()
        reconciliation_items = []

        # Check each backtest trade against grouped live setups
        for bt in backtest_trades:
            bt_time = pd.to_datetime(bt["entry_time"], utc=True)
            matched_setup = None

            for idx, ld in enumerate(grouped_setups):
                if idx in matched_live_indices:
                    continue
                if ld["symbol"] == bt["symbol"] and ld["type"] == bt["direction"]:
                    ld_time = pd.to_datetime(ld["time"], utc=True)
                    time_diff = abs((ld_time - bt_time).total_seconds())
                    if time_diff <= 360:  # within 6 minutes (allowing for bar close + fill)
                        matched_setup = ld
                        matched_live_indices.add(idx)
                        break

            if matched_setup:
                ticket_str = "/".join(str(t) for t in matched_setup["tickets"])
                reconciliation_items.append({
                    "status": "MATCHED_SUCCESS",
                    "symbol": bt["symbol"],
                    "direction": bt["direction"],
                    "signal_time": bt["entry_time"],
                    "live_ticket": ticket_str,
                    "backtest_entry": bt["actual_entry"],
                    "live_entry": matched_setup["price"],
                    "backtest_pnl": bt["net_pnl"],
                    "live_pnl": matched_setup.get("profit", 0.0),
                    "discrepancy_reason": "Exact strategy entry confirmed and executed by live bot."
                })
            else:
                reconciliation_items.append({
                    "status": "MISSED_OR_FILTERED",
                    "symbol": bt["symbol"],
                    "direction": bt["direction"],
                    "signal_time": bt["entry_time"],
                    "live_ticket": None,
                    "backtest_entry": bt["actual_entry"],
                    "live_entry": None,
                    "backtest_pnl": bt["net_pnl"],
                    "live_pnl": 0.0,
                    "discrepancy_reason": "Setup found in tick replay but no live broker deal was recorded (Bot was not running, CB cushion was exhausted, or spread spiked at fill)."
                })

        # Check for unexpected live setups
        for idx, ld in enumerate(grouped_setups):
            if idx not in matched_live_indices:
                ticket_str = "/".join(str(t) for t in ld["tickets"])
                reconciliation_items.append({
                    "status": "UNEXPECTED_LIVE_DEAL",
                    "symbol": ld["symbol"],
                    "direction": ld["type"],
                    "signal_time": ld["time"],
                    "live_ticket": ticket_str,
                    "backtest_entry": None,
                    "live_entry": ld["price"],
                    "backtest_pnl": 0.0,
                    "live_pnl": ld.get("profit", 0.0),
                    "discrepancy_reason": "Live broker deal was executed but was not identified by the standard strategy backtest engine (Check manual trades or overrides)."
                })

        # Scorecard Metrics
        total_bt = len(backtest_trades)
        total_live = len(grouped_setups)
        matched = sum(1 for item in reconciliation_items if item["status"] == "MATCHED_SUCCESS")
        discrepancies = len(reconciliation_items) - matched
        bt_net_pnl = sum(bt["net_pnl"] for bt in backtest_trades)
        live_net_pnl = sum(d["profit"] for d in live_deals)

        return {
            "date": target_date.strftime("%Y-%m-%d"),
            "audit_generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "summary": {
                "total_backtest_signals": total_bt,
                "total_live_entries": total_live,
                "matched_trades": matched,
                "discrepancies": discrepancies,
                "match_rate_pct": round((matched / total_bt * 100.0) if total_bt > 0 else 100.0, 1),
                "backtest_net_pnl": round(bt_net_pnl, 2),
                "live_broker_net_pnl": round(live_net_pnl, 2),
            },
            "reconciliation_details": reconciliation_items,
            "backtest_trades": backtest_trades,
            "live_deals": live_deals,
        }

    def generate_markdown_report(self, audit_result: Dict[str, Any]) -> str:
        s = audit_result["summary"]
        dt = audit_result["date"]
        lines = [
            f"# DCC Daily Forensic Audit & Trade Reconciliation Scorecard",
            f"**Trading Date:** `{dt}` | **Generated:** `{audit_result['audit_generated_at']}`",
            "",
            "## Summary Metrics",
            f"- **Match Rate:** `{s['match_rate_pct']}%`",
            f"- **Strategy Signals Detected:** `{s['total_backtest_signals']}`",
            f"- **Live Broker Entries:** `{s['total_live_entries']}`",
            f"- **Fully Reconciled Matches:** `{s['matched_trades']}`",
            f"- **Discrepancies Flagged:** `{s['discrepancies']}`",
            f"- **Backtest Net PnL:** `${s['backtest_net_pnl']:,.2f}`",
            f"- **Live Broker PnL:** `${s['live_broker_net_pnl']:,.2f}`",
            "",
            "## Reconciliation Breakdown",
            "| Status | Symbol | Dir | Signal Time | Ticket | Backtest PnL | Live PnL | Forensic Diagnosis |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        if not audit_result["reconciliation_details"]:
            lines.append("| `NO_TRADES` | - | - | - | - | $0.00 | $0.00 | No strategy setups or live trades occurred on this date. |")
        else:
            for d in audit_result["reconciliation_details"]:
                status_icon = "[MATCHED]" if d["status"] == "MATCHED_SUCCESS" else "[FLAGGED]"
                ticket_str = str(d["live_ticket"]) if d["live_ticket"] else "N/A"
                lines.append(
                    f"| {status_icon} | **{d['symbol']}** | {d['direction']} | {d['signal_time']} | {ticket_str} | "
                    f"${d['backtest_pnl']:,.2f} | ${d['live_pnl']:,.2f} | {d['discrepancy_reason']} |"
                )

        return "\n".join(lines)

    def run(self, target_date: datetime.date) -> Dict[str, Any]:
        """Main execution flow for a target date."""
        print(f"\n================================================================================")
        print(f"  >>> RUNNING NIGHTLY FORENSIC RECONCILIATION: {target_date} <<<")
        print(f"================================================================================")

        if not mt5.initialize():
            print("[NightlyReconciler] Error: Failed to connect to MetaTrader 5.")
            return {}

        # 1. Run Strategy Replay on raw ticks
        print(f"[*] [1/4] Replaying broker ticks for {self.symbols}...")
        bt_trades = self.run_daily_backtest(target_date)
        print(f"[OK] Backtest completed. Setups found: {len(bt_trades)}")

        # 2. Fetch Live MT5 Deals
        print(f"[*] [2/4] Fetching live broker deals from MT5...")
        live_deals = self.fetch_live_deals(target_date)
        print(f"[OK] Live deals retrieved: {len(live_deals)}")

        # 3. Reconcile
        print(f"[*] [3/4] Reconciling backtest setups vs. live broker execution...")
        audit_result = self.reconcile(target_date, bt_trades, live_deals)
        md_report = self.generate_markdown_report(audit_result)

        # 4. Save and manage storage retention
        date_str = target_date.strftime("%Y%m%d")
        json_path, md_path = self.storage.save_daily_report(date_str, audit_result, md_report)
        print(f"[OK] Saved daily audit report to: {json_path}")

        # Cloud Archival to Google Drive under 'bot backtest/YYYY-MM-DD/'
        target_subfolder = target_date.strftime("%Y-%m-%d")
        ok_drive, msg_drive = self.storage.archive_to_google_drive(
            json_path, folder_name="bot backtest", subfolder_date=target_subfolder
        )
        if ok_drive:
            print(f"[Storage] Daily JSON scorecard archived to Google Drive: {msg_drive}")
        if md_path:
            self.storage.archive_to_google_drive(
                md_path, folder_name="bot backtest", subfolder_date=target_subfolder
            )

        # Prune aged reports (> 7 days)
        print(f"[*] [4/4] Checking rolling 7-day disk retention...")
        pruned = self.storage.prune_aged_reports(archive_before_delete=True)
        if pruned:
            print(f"[Storage] Pruned {len(pruned)} aged report(s) from VPS.")
        else:
            print(f"[Storage] VPS disk clean. All reports within 7-day rolling retention.")

        # 5. Deep Winning & Losing Trade Forensics Engine
        print(f"[*] Executing Deep Trade Forensics & Executive Briefing...")
        discord_fields = []
        try:
            from experiments.trade_forensics.analyze import analyze_session_date
            forensics_data = analyze_session_date(target_date, upload_to_drive=True)
            if forensics_data:
                verdicts = forensics_data.get("verdicts", [])
                win_v = [v for v in verdicts if "WIN" in v.outcome or "WIN" in v.primary_verdict or v.metrics_summary.get("PnL ($)", 0.0) > 0.05]
                loss_v = [v for v in verdicts if v not in win_v]

                if win_v:
                    top_win = win_v[0]
                    discord_fields.append({
                        "name": f"🏆 Top Winning Edge: {top_win.symbol} {top_win.direction}",
                        "value": f"**{top_win.verdict_title}**\n• PnL: ${top_win.metrics_summary.get('PnL ($)', 0):+,.2f} ({top_win.metrics_summary.get('PnL (R)', 0)}R)\n• MFE: {top_win.metrics_summary.get('MFE', '0R')} | MAE: {top_win.metrics_summary.get('MAE', '0R')}\n• Edge: {top_win.root_cause[:180]}...",
                        "inline": False
                    })
                if loss_v:
                    top_loss = loss_v[0]
                    discord_fields.append({
                        "name": f"⚠️ Primary Loss Autopsy: {top_loss.symbol} {top_loss.direction}",
                        "value": f"**{top_loss.verdict_title}**\n• PnL: ${top_loss.metrics_summary.get('PnL ($)', 0):+,.2f}\n• Root Cause: {top_loss.root_cause[:180]}...",
                        "inline": False
                    })

                discord_fields.append({
                    "name": "☁️ Google Drive Archive",
                    "value": f"Full forensic HTML dashboard, JSON autopsy & AI executive briefing saved under `bot backtest/{target_subfolder}/`.",
                    "inline": False
                })
        except Exception as e:
            print(f"[NightlyReconciler] Warning: Forensics analysis failed: {e}")

        # Dispatch Notification
        s = audit_result["summary"]
        alert_text = (
            f"<b>🌙 DCC Nightly Audit Scorecard ({target_date})</b>\n\n"
            f"• <b>Match Rate:</b> {s['match_rate_pct']}%\n"
            f"• <b>Strategy Setups:</b> {s['total_backtest_signals']}\n"
            f"• <b>Live Broker Deals:</b> {s['total_live_entries']}\n"
            f"• <b>Reconciled Matches:</b> {s['matched_trades']}\n"
            f"• <b>Discrepancies:</b> {s['discrepancies']}\n"
            f"• <b>Backtest PnL:</b> ${s['backtest_net_pnl']:,.2f}\n"
            f"• <b>Live Broker PnL:</b> ${s['live_broker_net_pnl']:,.2f}\n\n"
            f"<i>Detailed forensic JSON & HTML Dashboard saved to Google Drive under 'bot backtest/{target_subfolder}/'.</i>"
        )
        self.notifier.notify_nightly_audit(
            title=f"🌙 DCC Nightly Audit Scorecard ({target_date})",
            summary_text=alert_text,
            discord_fields=discord_fields
        )

        # Print Scorecard to Console
        print("\n" + md_report + "\n")
        print("================================================================================\n")
        return audit_result


def main():
    parser = argparse.ArgumentParser(description="DCC Nightly Forensic Tick Reconciliation Engine")
    parser.add_argument("--date", type=str, help="Target audit date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--today", action="store_true", help="Audit today's trading session")
    parser.add_argument("--yesterday", action="store_true", help="Audit yesterday's trading session")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "NAS100"], help="Symbols to audit")
    parser.add_argument("--retention-days", type=int, default=7, help="Rolling local storage retention in days")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    if args.yesterday:
        target_date = (now_utc - timedelta(days=1)).date()
    elif args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = now_utc.date()

    reconciler = NightlyReconciler(symbols=args.symbols, retention_days=args.retention_days)
    reconciler.run(target_date)


if __name__ == "__main__":
    main()
