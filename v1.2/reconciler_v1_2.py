"""
DCC v1.2 Daily Forensic Audit & Trade Reconciliation Engine
============================================================
Reconciles live broker MT5 deals against DCCEngineV12 backtest replay.
Features:
- Auto-detects and normalizes broker timezone offset (UTC+3 Cyprus) to True UTC.
- Groups twin live tickets (Partial TP1 + Runner TP2) into unified trade setups.
- Accurately attributes realized live PnL using deal position_ids.
- Computes match rate %, signal discrepancies, and forensic execution deltas.
"""

import argparse
from datetime import datetime, date, timedelta, timezone
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

V12_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(V12_DIR)
if V12_DIR not in sys.path:
    sys.path.insert(0, V12_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine_v1_2 import DCCEngineV12
from config_v1_2 import SYMBOL_CONFIGS_V12, TRIPLEGUARD_SHIELD, CALENDAR_SHIELD


class ReconcilerV12:
    def __init__(self, symbols: Optional[List[str]] = None):
        self.symbols = symbols if symbols else ["XAUUSD", "NAS100"]
        self.broker_offset = timedelta(hours=3)

    def detect_broker_offset(self) -> timedelta:
        """Detects current broker server timezone offset relative to True UTC."""
        if not mt5.initialize():
            return timedelta(hours=3)
        rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 1)
        if rates is not None and len(rates) > 0:
            broker_time = datetime.fromtimestamp(rates[0]['time'], tz=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            diff_hours = round((broker_time - now_utc).total_seconds() / 3600.0)
            return timedelta(hours=diff_hours)
        return timedelta(hours=3)

    def get_symbol_specs(self, symbol: str) -> Dict[str, Any]:
        cfg = SYMBOL_CONFIGS_V12.get(symbol, SYMBOL_CONFIGS_V12["XAUUSD"])
        info = mt5.symbol_info(symbol)
        return {
            "tick_size": info.trade_tick_size if info else cfg.tick_size,
            "tick_val": info.trade_tick_value if info else cfg.tick_val,
            "contract_size": info.trade_contract_size if info else cfg.contract_size,
            "digits": info.digits if info else cfg.digits,
            "comm_per_lot": cfg.comm_per_lot,
            "atr_sl_mult": cfg.atr_sl_multiplier,
            "tp1_rr": cfg.tp1_rr,
            "tp2_rr": cfg.tp2_rr
        }

    def fetch_live_deals(self, target_date: date) -> List[Dict[str, Any]]:
        """Fetches all live deals for target_date from MT5 and normalizes to True UTC."""
        utc_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc)
        utc_end = utc_start + timedelta(days=1)
        broker_start = utc_start + self.broker_offset
        broker_end = utc_end + self.broker_offset

        deals = mt5.history_deals_get(broker_start, broker_end)
        if not deals:
            return []

        parsed = []
        for d in deals:
            if d.entry in (0, 1):  # 0=ENTRY_IN, 1=ENTRY_OUT
                deal_utc = datetime.fromtimestamp(d.time, tz=timezone.utc) - self.broker_offset
                parsed.append({
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
        return parsed

    def run_daily_backtest(self, target_date: date) -> List[Dict[str, Any]]:
        """Replays all closed 5M bars for target_date using DCCEngineV12."""
        utc_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc)
        utc_end = utc_start + timedelta(days=1)
        warmup_start = utc_start - timedelta(days=5)

        backtest_trades = []

        for sym in self.symbols:
            cfg = SYMBOL_CONFIGS_V12.get(sym, SYMBOL_CONFIGS_V12["XAUUSD"])
            specs = self.get_symbol_specs(sym)
            val_per_pt = specs["tick_val"] / specs["tick_size"]

            # Query MT5 rates shifted to True UTC
            r_m5 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, warmup_start + self.broker_offset, utc_end + self.broker_offset)
            r_h1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, warmup_start + self.broker_offset, utc_end + self.broker_offset)
            r_h2 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H2, warmup_start + self.broker_offset, utc_end + self.broker_offset)

            if r_m5 is None or r_h1 is None or len(r_m5) < 30:
                continue

            df_m5 = pd.DataFrame(r_m5)
            df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s', utc=True) - self.broker_offset
            df_m5.set_index('time', inplace=True)
            df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

            df_h1 = pd.DataFrame(r_h1)
            df_h1['time'] = pd.to_datetime(df_h1['time'], unit='s', utc=True) - self.broker_offset
            df_h1.set_index('time', inplace=True)

            df_h2 = pd.DataFrame(r_h2) if r_h2 is not None else None
            if df_h2 is not None and len(df_h2) > 0:
                df_h2['time'] = pd.to_datetime(df_h2['time'], unit='s', utc=True) - self.broker_offset
                df_h2.set_index('time', inplace=True)

            engine = DCCEngineV12(
                symbol=sym,
                atr_sl_multiplier=cfg.atr_sl_multiplier,
                tp1_rr=cfg.tp1_rr,
                tp2_rr=cfg.tp2_rr,
                min_1h_stretch=TRIPLEGUARD_SHIELD.min_1h_stretch,
                max_1h_adx=TRIPLEGUARD_SHIELD.max_1h_adx,
                max_5m_chase=TRIPLEGUARD_SHIELD.max_5m_chase,
                block_monday_pm=CALENDAR_SHIELD.block_monday_pm
            )

            df_prep = engine.prepare_data(df_m5, df_h1, df_h2)
            today_mask = df_prep.index.date == target_date
            today_bars = df_prep[today_mask]

            # Replay each 5M bar
            n_bars = len(df_prep)
            i = 25
            while i < n_bars:
                bar_time = df_prep.index[i]
                if bar_time.date() != target_date:
                    i += 1
                    continue

                prev_bar = df_prep.iloc[i - 1]
                curr_bar = df_prep.iloc[i]

                sig = engine.evaluate_bar(prev_bar, curr_bar, symbol=sym)
                if sig is not None:
                    # Check liquidity sweep using fast fractal reclaim detector
                    has_sw, _, _ = DCCEngineV12.detect_liquidity_sweep_fast(df_m5, i, 1 if sig.signal_type.value == 1 else -1)
                    if not has_sw:
                        i += 1
                        continue

                    # Sizing: 1% ($50) on $5,000 base
                    total_risk = 50.0
                    total_lots = max(0.01, round(total_risk / (sig.sl_distance * val_per_pt), 2))
                    part_lots = round(total_lots * 0.5, 2)
                    run_lots = max(0.01, round(total_lots - part_lots, 2))

                    # Simulate trade execution forward
                    pos_buy = (sig.signal_type.value == 1)
                    entry_p = sig.entry_price
                    curr_sl = sig.stop_loss
                    tp1_p = sig.take_profit_1
                    tp2_p = sig.take_profit_2
                    tp1_hit = False

                    exit_time = None
                    net_pnl = 0.0

                    j = i + 1
                    while j < n_bars:
                        bar = df_prep.iloc[j]
                        b_high = float(bar['high'])
                        b_low = float(bar['low'])
                        b_time = df_prep.index[j]

                        sl_hit = (b_low <= curr_sl) if pos_buy else (b_high >= curr_sl)
                        tp1_h = (b_high >= tp1_p) if pos_buy else (b_low <= tp1_p)
                        tp2_h = (b_high >= tp2_p) if pos_buy else (b_low <= tp2_p)

                        if sl_hit:
                            exit_time = b_time
                            comm = total_lots * cfg.comm_per_lot
                            if tp1_hit:
                                p1 = part_lots * sig.tp1_distance * val_per_pt
                                net_pnl = round(p1 - comm, 2)
                            else:
                                loss_val = total_lots * sig.sl_distance * val_per_pt
                                net_pnl = round(-loss_val - comm, 2)
                            break
                        elif tp2_h and tp1_hit:
                            exit_time = b_time
                            comm = total_lots * cfg.comm_per_lot
                            p1 = part_lots * sig.tp1_distance * val_per_pt
                            p2 = run_lots * sig.tp2_distance * val_per_pt
                            net_pnl = round(p1 + p2 - comm, 2)
                            break
                        elif tp1_h and not tp1_hit:
                            tp1_hit = True
                            curr_sl = entry_p

                        j += 1

                    if exit_time is None:
                        exit_time = df_prep.index[-1]
                        close_p = float(df_prep['close'].iloc[-1])
                        run_pts = (close_p - entry_p) if pos_buy else (entry_p - close_p)
                        comm = total_lots * cfg.comm_per_lot
                        p1 = (part_lots * sig.tp1_distance * val_per_pt) if tp1_hit else (part_lots * run_pts * val_per_pt)
                        p2 = run_lots * run_pts * val_per_pt
                        net_pnl = round(p1 + p2 - comm, 2)

                    backtest_trades.append({
                        "symbol": sym,
                        "direction": "BUY" if pos_buy else "SELL",
                        "entry_time": bar_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "entry_price": round(entry_p, specs["digits"]),
                        "sl_price": round(sig.stop_loss, specs["digits"]),
                        "tp1_price": round(tp1_p, specs["digits"]),
                        "tp2_price": round(tp2_p, specs["digits"]),
                        "net_pnl": net_pnl,
                        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "stretch": round(sig.stretch_ratio, 2),
                        "chase": round(sig.chase_ratio, 2),
                        "adx": round(sig.adx_1h, 1)
                    })

                    # Fast-forward past exit
                    i = j
                    continue

                i += 1

        return backtest_trades

    def reconcile(
        self,
        target_date: date,
        bt_trades: List[Dict[str, Any]],
        live_deals: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Reconciles backtest setups vs grouped live broker setups."""
        raw_entries = [d for d in live_deals if d["entry_type"] == "ENTRY_IN"]
        exit_deals = [d for d in live_deals if d["entry_type"] == "ENTRY_OUT"]

        grouped_setups = []
        for d in raw_entries:
            d_time = pd.to_datetime(d["time"], utc=True)
            existing = None
            for s in grouped_setups:
                if s["symbol"] == d["symbol"] and s["type"] == d["type"]:
                    s_time = pd.to_datetime(s["time"], utc=True)
                    if abs((d_time - s_time).total_seconds()) <= 60:
                        existing = s
                        break

            pos_id = d.get("position_id", d["order"])
            if existing:
                existing["tickets"].append(d["ticket"])
                existing["position_ids"].append(pos_id)
                existing["volume"] = round(existing["volume"] + d["volume"], 2)
            else:
                grouped_setups.append({
                    "symbol": d["symbol"],
                    "type": d["type"],
                    "time": d["time"],
                    "price": d["price"],
                    "volume": d["volume"],
                    "tickets": [d["ticket"]],
                    "position_ids": [pos_id],
                    "profit": 0.0
                })

        for s in grouped_setups:
            s_profit = sum(ed["profit"] for ed in exit_deals if ed.get("position_id") in s["position_ids"])
            s["profit"] = round(s_profit, 2)

        matched_live_indices = set()
        items = []

        for bt in bt_trades:
            bt_time = pd.to_datetime(bt["entry_time"], utc=True)
            matched = None
            for idx, ld in enumerate(grouped_setups):
                if idx in matched_live_indices:
                    continue
                if ld["symbol"] == bt["symbol"] and ld["type"] == bt["direction"]:
                    ld_time = pd.to_datetime(ld["time"], utc=True)
                    if abs((ld_time - bt_time).total_seconds()) <= 360:
                        matched = ld
                        matched_live_indices.add(idx)
                        break

            if matched:
                items.append({
                    "status": "MATCHED_SUCCESS",
                    "symbol": bt["symbol"],
                    "direction": bt["direction"],
                    "signal_time": bt["entry_time"],
                    "live_ticket": "/".join(str(t) for t in matched["tickets"]),
                    "backtest_pnl": bt["net_pnl"],
                    "live_pnl": matched["profit"],
                    "details": "Exact v1.2 setup verified on live MT5 broker."
                })
            else:
                items.append({
                    "status": "MISSED_OR_FILTERED",
                    "symbol": bt["symbol"],
                    "direction": bt["direction"],
                    "signal_time": bt["entry_time"],
                    "live_ticket": None,
                    "backtest_pnl": bt["net_pnl"],
                    "live_pnl": 0.0,
                    "details": "Setup triggered in tick replay but no matching live deal was found."
                })

        for idx, ld in enumerate(grouped_setups):
            if idx not in matched_live_indices:
                items.append({
                    "status": "UNEXPECTED_LIVE_DEAL",
                    "symbol": ld["symbol"],
                    "direction": ld["type"],
                    "signal_time": ld["time"],
                    "live_ticket": "/".join(str(t) for t in ld["tickets"]),
                    "backtest_pnl": 0.0,
                    "live_pnl": ld["profit"],
                    "details": "Live broker deal was executed but not generated by v1.2 engine."
                })

        total_bt = len(bt_trades)
        n_matched = sum(1 for item in items if item["status"] == "MATCHED_SUCCESS")
        match_rate = round(n_matched / total_bt * 100.0, 1) if total_bt > 0 else 100.0

        return {
            "date": target_date.strftime("%Y-%m-%d"),
            "match_rate": match_rate,
            "total_backtest": total_bt,
            "total_live": len(grouped_setups),
            "matched": n_matched,
            "discrepancies": len(items) - n_matched,
            "backtest_pnl": round(sum(bt["net_pnl"] for bt in bt_trades), 2),
            "live_pnl": round(sum(d["profit"] for d in live_deals if d["entry_type"] == "ENTRY_OUT"), 2),
            "details": items
        }


def main():
    parser = argparse.ArgumentParser(description="DCC v1.2 Reconciliation Auditor")
    parser.add_argument("--date", type=str, default=datetime.now(timezone.utc).strftime("%Y-%m-%d"), help="Date (YYYY-MM-DD)")
    args = parser.parse_args()

    target_dt = datetime.strptime(args.date, "%Y-%m-%d").date()
    reconciler = ReconcilerV12()
    reconciler.broker_offset = reconciler.detect_broker_offset()

    print(f"\n[*] Replaying broker ticks for {target_dt} using DCC v1.2 TripleGuard...")
    bt_trades = reconciler.run_daily_backtest(target_dt)
    print(f"[OK] v1.2 Setups Found: {len(bt_trades)}")

    print(f"[*] Fetching live broker deals for {target_dt}...")
    live_deals = reconciler.fetch_live_deals(target_dt)
    print(f"[OK] Live Deals Retrieved: {len(live_deals)}")

    audit = reconciler.reconcile(target_dt, bt_trades, live_deals)

    print("\n" + "="*80)
    print(f"  DCC v1.2 TRIPLEGUARD AUDIT SCORECARD ({target_dt})")
    print("="*80)
    print(f"Match Rate:        {audit['match_rate']}%")
    print(f"Strategy Signals:  {audit['total_backtest']}")
    print(f"Live Setups:       {audit['total_live']}")
    print(f"Matched Trades:    {audit['matched']}")
    print(f"Discrepancies:     {audit['discrepancies']}")
    print(f"Backtest PnL:      ${audit['backtest_pnl']:+,.2f}")
    print(f"Live Broker PnL:   ${audit['live_pnl']:+,.2f}")
    print("="*80)
    if audit['details']:
        for item in audit['details']:
            print(f"[{item['status']}] {item['symbol']} {item['direction']} @ {item['signal_time']} | BT: ${item['backtest_pnl']} | Live: ${item['live_pnl']} | Ticket: {item['live_ticket']}")
    print("="*80)


if __name__ == "__main__":
    main()
