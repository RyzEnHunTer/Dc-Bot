"""
Data Pipeline for TradingView-Style Visualizer
Generates candlestick data (OHLCV + EMA9 + EMA20 + VWAP) and parses
backtest trade logs with exact TradingView Long/Short Position geometry.
Includes Full 8.25-Month Chained Portfolio, Phase 1, and Phase 2!
"""

import os
import sys
import json
import bisect
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "visualizer")
OUTPUT_JSON = os.path.join(OUTPUT_DIR, "data.json")

IST_OFFSET = timedelta(hours=5, minutes=30)
TZ_IST = timezone(IST_OFFSET)


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Computes daily resetting VWAP."""
    typical_price = (df['high'] + df['low'] + df['close']) / 3.0
    df['tp_vol'] = typical_price * df['tick_volume']
    
    # Identify day changes in UTC
    dt_series = pd.to_datetime(df['time'], unit='s', utc=True)
    df['date'] = dt_series.dt.date
    
    cum_vol = df.groupby('date')['tick_volume'].cumsum()
    cum_tp_vol = df.groupby('date')['tp_vol'].cumsum()
    
    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return vwap.bfill().ffill()


def fetch_or_load_candles(symbol: str) -> pd.DataFrame:
    """Fetches 5M candles from MT5 or local parquet cache."""
    cache_parquet = os.path.join(PROJECT_ROOT, "data_cache", f"m5_bars_{symbol}.parquet")
    
    # Check if local M5 parquet cache already exists
    if os.path.exists(cache_parquet):
        try:
            df = pd.read_parquet(cache_parquet)
            df['dt'] = pd.to_datetime(df['time'], unit='s', utc=True)
            # Check if cache covers through September 2026
            if df['dt'].max().year == 2026 and df['dt'].max().month >= 9:
                print(f"[{symbol}] Loaded {len(df)} 5M bars from local cache (up to {df['dt'].max()}).")
                return df.drop(columns=['dt'], errors='ignore')
        except Exception as e:
            print(f"[{symbol}] Cache load failed: {e}. Fetching fresh.")

    # Fetch from MT5
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            start_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
            end_dt = datetime(2026, 9, 10, tzinfo=timezone.utc)
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_dt, end_dt)
            mt5.shutdown()
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df.to_parquet(cache_parquet, index=False)
                print(f"[{symbol}] Fetched {len(df)} 5M bars from MT5 and saved to {cache_parquet}.")
                return df
    except Exception as e:
        print(f"[{symbol}] MT5 fetch error: {e}")

    raise RuntimeError(f"Could not load or fetch M5 bars for {symbol}")


def process_symbol_candles(symbol: str) -> List[Dict]:
    """Processes 5M bars, adding EMA9, EMA20, and VWAP."""
    df = fetch_or_load_candles(symbol)
    
    # Compute EMAs
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    
    # Compute VWAP
    df['vwap'] = calculate_vwap(df)

    digits = 2 if symbol == "XAUUSD" else 1

    bars = []
    for _, row in df.iterrows():
        t = int(row['time'])
        bars.append({
            "time": t,
            "open": round(float(row['open']), digits),
            "high": round(float(row['high']), digits),
            "low": round(float(row['low']), digits),
            "close": round(float(row['close']), digits),
            "volume": int(row['tick_volume']),
            "ema9": round(float(row['ema9']), digits),
            "ema20": round(float(row['ema20']), digits),
            "vwap": round(float(row['vwap']), digits),
        })

    return bars


def format_duration(minutes: float) -> str:
    if minutes < 60:
        return f"{int(round(minutes))}m"
    hours = int(minutes // 60)
    mins = int(round(minutes % 60))
    return f"{hours}h {mins:02d}m"


def parse_trades(csv_path: str, candles_dict: Optional[Dict] = None) -> List[Dict]:
    """Parses trades and builds TradingView Long/Short Position geometry with candle snapping."""
    if not os.path.exists(csv_path):
        print(f"[Warn] Trade log {csv_path} not found.")
        return []

    def safe_float(val, default=0.0):
        """Safely convert a value to float, returning default if NaN/None."""
        try:
            if pd.isna(val):
                return default
            return float(val)
        except (ValueError, TypeError):
            return default

    df = pd.read_csv(csv_path)
    trades = []

    for idx, row in df.iterrows():
        raw_tid = row.get('trade_id', idx + 1)
        trade_id = int(raw_tid) if pd.notna(raw_tid) else idx + 1
        symbol = str(row['symbol']).strip()
        direction = str(row['direction']).strip().upper()
        
        # Parse entry and exit datetimes
        entry_dt = pd.to_datetime(row['entry_time'], utc=True)
        exit_dt = pd.to_datetime(row['exit_time'], utc=True)
        
        entry_unix = int(entry_dt.timestamp())
        exit_unix = int(exit_dt.timestamp())
        
        # Snap entry and exit to exact candle boundaries and candle indices
        entry_candle_time = entry_unix
        exit_candle_time = exit_unix
        entry_bar_index = None
        exit_bar_index = None

        if candles_dict and symbol in candles_dict:
            symbol_bars = candles_dict[symbol]
            if symbol_bars:
                c_times = [b['time'] for b in symbol_bars]
                e_idx = bisect.bisect_right(c_times, entry_unix) - 1
                if e_idx < 0:
                    e_idx = 0
                x_idx = bisect.bisect_right(c_times, exit_unix) - 1
                if x_idx < e_idx:
                    x_idx = e_idx
                entry_candle_time = c_times[e_idx]
                exit_candle_time = c_times[x_idx]
                entry_bar_index = e_idx
                exit_bar_index = x_idx

        # Dual timestamps
        entry_ist = entry_dt.astimezone(TZ_IST)
        exit_ist = exit_dt.astimezone(TZ_IST)
        
        entry_time_utc = entry_dt.strftime("%Y-%m-%d %H:%M UTC")
        entry_time_ist = entry_ist.strftime("%H:%M IST")
        exit_time_utc = exit_dt.strftime("%Y-%m-%d %H:%M UTC")
        exit_time_ist = exit_ist.strftime("%H:%M IST")
        
        actual_entry = safe_float(row.get('actual_entry', row.get('entry_price', 0.0)))
        
        # Exact strategy parameters
        if symbol == "XAUUSD":
            sl_multiplier = 0.9
            tp1_rr = 1.4
            tp2_rr = 2.2
            digits = 2
        else:
            sl_multiplier = 1.0
            tp1_rr = 1.5
            tp2_rr = 2.0
            digits = 1

        atr = safe_float(row.get('atr_1h'), safe_float(row.get('sl_dist', 1.0)) / sl_multiplier)

        if 'sl_price' in row and not pd.isna(row['sl_price']):
            sl_price = round(float(row['sl_price']), digits)
            tp1_price = round(float(row['tp1_price']), digits)
            tp2_price = round(float(row['tp2_price']), digits)
            sl_distance = abs(actual_entry - sl_price)
            tp1_distance = abs(tp1_price - actual_entry)
            tp2_distance = abs(tp2_price - actual_entry)
        else:
            sl_distance = sl_multiplier * atr
            tp1_distance = tp1_rr * sl_distance
            tp2_distance = tp2_rr * sl_distance
            if direction == "BUY":
                sl_price = round(actual_entry - sl_distance, digits)
                tp1_price = round(actual_entry + tp1_distance, digits)
                tp2_price = round(actual_entry + tp2_distance, digits)
            else:
                sl_price = round(actual_entry + sl_distance, digits)
                tp1_price = round(actual_entry - tp1_distance, digits)
                tp2_price = round(actual_entry - tp2_distance, digits)

        spread = safe_float(row.get('entry_spread', 0.0))
        be_price = round(actual_entry + spread if direction == "BUY" else actual_entry - spread, digits)

        tp1_hit = bool(row.get('tp1_hit', False))
        tp2_hit = bool(row.get('tp2_hit', False))
        be_hit = bool(row.get('be_hit', False))
        exit_reason = str(row.get('exit_reason', 'UNKNOWN'))
        
        net_pnl = round(safe_float(row.get('net_pnl', 0.0)), 2)
        total_lots = round(safe_float(row.get('total_lots', 0.01), 0.01), 2)
        partial_lots = round(safe_float(row.get('partial_lots', row.get('part_lots', 0.01)), 0.01), 2)
        runner_lots = round(safe_float(row.get('runner_lots', row.get('run_lots', 0.01)), 0.01), 2)
        duration_m = safe_float(row.get('duration_m', 0.0))
        duration_str = format_duration(duration_m)
        balance = round(safe_float(row.get('balance', row.get('account_balance', row.get('ending_balance', 5000.0))), 5000.0), 2)

        if 'return_pct' in row and not pd.isna(row['return_pct']):
            return_pct = round(float(row['return_pct']), 2)
        else:
            starting_bal = safe_float(row.get('starting_balance', 5000.0), 5000.0)
            return_pct = round((net_pnl / starting_bal) * 100.0, 2) if starting_bal > 0 else 0.0

        # Build narrative log for inspector panel
        narrative = []
        narrative.append({
            "time": entry_time_utc,
            "title": f"ENTRY: {direction} {total_lots} Lots @ {actual_entry:,.2f}",
            "desc": f"DCC Breakout filled at market quote (Spread: {spread:.2f}). Initial SL: {sl_price:,.2f} (-{sl_multiplier:.1f}x ATR), TP1: {tp1_price:,.2f} (+{tp1_rr}R), TP2: {tp2_price:,.2f} (+{tp2_rr}R).",
            "type": "entry"
        })
        
        if tp1_hit:
            narrative.append({
                "time": f"During Trade",
                "title": f"TP1 HIT (+{tp1_rr}R) -> 50% Closed ({partial_lots} Lots)",
                "desc": f"First target reached! Secured partial profit. Stop loss shifted to Breakeven @ {be_price:,.2f}.",
                "type": "tp1"
            })

        if exit_reason in ["FULL_TP2", "TP2"]:
            exit_badge = "WINNER (TP2)"
            badge_color = "green"
            narrative.append({
                "time": exit_time_utc,
                "title": f"FULL TP2 HIT (+{tp2_rr}R) -> Runner Closed ({runner_lots} Lots)",
                "desc": f"Runner reached final target TP2! Total Trade PnL: +${net_pnl:,.2f} (+{return_pct}%).",
                "type": "win"
            })
        elif exit_reason in ["TP1_THEN_BE", "BE"]:
            exit_badge = "RESCUED (BE)"
            badge_color = "cyan"
            narrative.append({
                "time": exit_time_utc,
                "title": f"BREAKEVEN SCRATCH -> Runner Closed ({runner_lots} Lots)",
                "desc": f"Market retraced and stopped runner at Breakeven buffer. Account protected! Net Profit: +${net_pnl:,.2f}.",
                "type": "be"
            })
        elif exit_reason == "SL":
            exit_badge = "STOPPED (SL)"
            badge_color = "red"
            narrative.append({
                "time": exit_time_utc,
                "title": f"STOP LOSS HIT (-1.0R)",
                "desc": f"Position hit initial SL at {sl_price:,.2f}. Capital risk managed strictly. Net PnL: -${abs(net_pnl):,.2f} ({return_pct}%).",
                "type": "loss"
            })
        else:
            exit_badge = exit_reason
            badge_color = "yellow"
            narrative.append({
                "time": exit_time_utc,
                "title": f"EXIT: {exit_reason}",
                "desc": f"Closed at session end / market close. Net PnL: ${net_pnl:,.2f}.",
                "type": "exit"
            })

        # Exact TradingView Geometry
        geometry = {
            "entryPrice": actual_entry,
            "slPrice": sl_price,
            "tpPrice": tp2_price,
            "tp1Price": tp1_price,
            "bePrice": be_price,
            "entryTime": entry_candle_time,
            "exitTime": exit_candle_time,
            "entryBarIndex": entry_bar_index,
            "exitBarIndex": exit_bar_index,
            "direction": direction,
            "stopDistance": round(sl_distance, digits),
            "targetDistance": round(tp2_distance, digits),
            "rrRatio": tp2_rr,
            "outcome": exit_badge,
            "outcomeColor": badge_color
        }

        trades.append({
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "entry_time_utc": entry_time_utc,
            "entry_time_ist": entry_time_ist,
            "exit_time_utc": exit_time_utc,
            "exit_time_ist": exit_time_ist,
            "actual_entry": actual_entry,
            "sl_price": sl_price,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "be_price": be_price,
            "total_lots": total_lots,
            "partial_lots": partial_lots,
            "runner_lots": runner_lots,
            "net_pnl": net_pnl,
            "return_pct": return_pct,
            "balance": balance,
            "duration_str": duration_str,
            "exit_reason": exit_reason,
            "exit_badge": exit_badge,
            "badge_color": badge_color,
            "tp1_hit": tp1_hit,
            "tp2_hit": tp2_hit,
            "be_hit": be_hit,
            "geometry": geometry,
            "narrative": narrative
        })

    return trades


def compute_metrics(trades: List[Dict]) -> Dict:
    """Computes overall performance KPIs."""
    if not trades:
        return {}
    
    total = len(trades)
    wins = [t for t in trades if t['net_pnl'] > 0]
    losses = [t for t in trades if t['net_pnl'] <= 0]
    tp2_wins = [t for t in trades if t['exit_badge'] == 'WINNER (TP2)']
    be_scratches = [t for t in trades if t['exit_badge'] == 'RESCUED (BE)']
    sl_losses = [t for t in trades if t['exit_badge'] == 'STOPPED (SL)']

    win_rate = (len(wins) / total * 100.0) if total > 0 else 0.0
    gross_profit = sum(t['net_pnl'] for t in wins)
    gross_loss = abs(sum(t['net_pnl'] for t in losses))
    net_profit = sum(t['net_pnl'] for t in trades)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0
    
    # Equity curve & Max Drawdown
    initial = 5000.0
    balances = [initial]
    for t in trades:
        balances.append(balances[-1] + t['net_pnl'])
    s = pd.Series(balances)
    cummax = s.cummax()
    dd_pct = ((cummax - s) / cummax * 100.0).max()
    roi_pct = (net_profit / initial) * 100.0

    return {
        "initialBalance": initial,
        "finalBalance": round(balances[-1], 2),
        "totalNetProfit": round(net_profit, 2),
        "roiPct": round(roi_pct, 2),
        "winRate": round(win_rate, 1),
        "profitFactor": profit_factor,
        "maxDrawdownPct": round(dd_pct, 2),
        "totalTrades": total,
        "winningTrades": len(wins),
        "losingTrades": len(losses),
        "tp2FullWins": len(tp2_wins),
        "beScratches": len(be_scratches),
        "slLosses": len(sl_losses),
        "avgTradePnl": round(net_profit / total, 2) if total > 0 else 0.0
    }


def main():
    print("\n=======================================================")
    print("TRADINGVIEW VISUALIZER DATA PIPELINE (FULL MULTI-PHASE)")
    print("=======================================================\n")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Process 5M bars for monitored assets
    candles = {}
    for sym in ["XAUUSD", "NAS100"]:
        print(f"Processing 5M candles for {sym}...")
        candles[sym] = process_symbol_candles(sym)
        print(f"-> {len(candles[sym])} bars processed with EMA9, EMA20 & VWAP.")

    # 2. Process trade logs for all backtest configurations
    strategies = {}

    # File paths
    phase1_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_cb_liquidity_sweep.csv")
    phase2_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_phase2_jul_sep2026.csv")
    phase2_no_sweep_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_phase2_no_sweep.csv")
    cb_3pct_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_with_3pct_circuit_breaker.csv")
    opt_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_optimized_low_dd.csv")
    apexhunter_csv = os.path.join(PROJECT_ROOT, "reports", "trades_apexhunter_official.csv")

    p1_trades = parse_trades(phase1_csv, candles) if os.path.exists(phase1_csv) else []
    p2_trades = parse_trades(phase2_csv, candles) if os.path.exists(phase2_csv) else []

    # Flagship: DCC v1.2 ApexHunter Production Model
    if os.path.exists(apexhunter_csv):
        apexhunter_trades = parse_trades(apexhunter_csv, candles)
        for i, tr in enumerate(apexhunter_trades, 1):
            tr['trade_id'] = i
        strategies["apexhunter_official"] = {
            "name": "DCC v1.2 ApexHunter (Official Flagship Dual-Gear)",
            "description": "Production institutional engine: Dual-Gear Regime (1.30%/1.00%), Smart Killzone (Stretch>=1.10x), TripleGuard & 3% Daily CB (342 trades, 59.4% WR, +$10,363 PnL).",
            "metrics": compute_metrics(apexhunter_trades),
            "trades": apexhunter_trades
        }
        print(f"-> DCC v1.2 ApexHunter Flagship: {len(apexhunter_trades)} trades.")

    # Combined Chained Trades (All 8.25 Months: Jan 1 - Sep 8, 2026)
    if p1_trades and p2_trades:
        chained_trades = p1_trades + p2_trades
        # Re-index trade_id sequentially
        for i, tr in enumerate(chained_trades, 1):
            tr['trade_id'] = i
        strategies["chained_official"] = {
            "name": "Official Chained Strategy (Jan - Sep 2026 | 471 Trades)",
            "description": "Complete continuous institutional portfolio across all 8.25 months with 3% Daily CB & 5M Liquidity Sweep.",
            "metrics": compute_metrics(chained_trades),
            "trades": chained_trades
        }
        print(f"-> Chained Portfolio: {len(chained_trades)} total trades.")

    # Strategy: Official Chained WITHOUT EOD Exit (Continuous Holding)
    no_eod_csv = os.path.join(PROJECT_ROOT, "reports", "trades_official_no_eod_continuous.csv")
    if os.path.exists(no_eod_csv):
        no_eod_trades = parse_trades(no_eod_csv, candles)
        for i, tr in enumerate(no_eod_trades, 1):
            tr['trade_id'] = i
        strategies["chained_official_no_eod"] = {
            "name": "Official Strategy - NO EOD Close (Holding Overnight)",
            "description": "Continuous multi-session holding without artificial 21:00 UTC cutoff (471 trades, +195.8% ROI, $9,790 Net Profit).",
            "metrics": compute_metrics(no_eod_trades),
            "trades": no_eod_trades
        }
        print(f"-> Official NO-EOD Strategy: {len(no_eod_trades)} continuous trades.")


    # Strategy: Phase 2 Dedicated (July 1 - September 8, 2026)
    if p2_trades:
        strategies["phase2_official"] = {
            "name": "Official Flagship Model (Phase 2: Jul - Sep 2026)",
            "description": "Q3 Summer Consolidation & Trend Continuity backtest across 149 trades with 3% CB & 5M Sweep.",
            "metrics": compute_metrics(p2_trades),
            "trades": p2_trades
        }
        print(f"-> Phase 2 Flagship: {len(p2_trades)} trades.")

    # Strategy: Phase 1 Dedicated (January 1 - June 30, 2026)
    if p1_trades:
        strategies["phase1_official"] = {
            "name": "Official Flagship Model (Phase 1: Jan - Jun 2026)",
            "description": "H1 High-Conviction Institutional Trend Model (322 trades, 60.3% WR, +45.2% ROI).",
            "metrics": compute_metrics(p1_trades),
            "trades": p1_trades
        }
        print(f"-> Phase 1 Flagship: {len(p1_trades)} trades.")

    # Strategy: Phase 2 No-Sweep Experimental
    if os.path.exists(phase2_no_sweep_csv):
        p2_ns_trades = parse_trades(phase2_no_sweep_csv, candles)
        strategies["phase2_no_sweep"] = {
            "name": "Phase 2 Experimental (No 5M Sweep Filter)",
            "description": "Pure DCC Dynamic Crossover test without liquidity sweep filter (153 trades, +15.36% ROI).",
            "metrics": compute_metrics(p2_ns_trades),
            "trades": p2_ns_trades
        }
        print(f"-> Phase 2 No-Sweep: {len(p2_ns_trades)} trades.")

    # Strategy: 3% Circuit Breaker H1
    if os.path.exists(cb_3pct_csv):
        cb_trades = parse_trades(cb_3pct_csv, candles)
        strategies["circuit_breaker_3pct"] = {
            "name": "Daily 3% Circuit Breaker Portfolio (H1 2026)",
            "description": "Institutional capital preservation with automatic session suspension on 3% daily DD.",
            "metrics": compute_metrics(cb_trades),
            "trades": cb_trades
        }

    # Strategy: Optimized Low-DD Model
    if os.path.exists(opt_csv):
        opt_trades = parse_trades(opt_csv, candles)
        strategies["optimized_low_dd"] = {
            "name": "Low-DD Optimized Portfolio (4.16% Max DD)",
            "description": "Enhanced with peak liquidity session filtering to minimize drawdown.",
            "metrics": compute_metrics(opt_trades),
            "trades": opt_trades
        }

    # 3. Export complete payload
    data_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": ["XAUUSD", "NAS100"],
        "candles": candles,
        "strategies": strategies,
        "default_strategy": "apexhunter_official" if "apexhunter_official" in strategies else ("chained_official" if "chained_official" in strategies else list(strategies.keys())[0])
    }

    print(f"\nWriting visualizer payload to {OUTPUT_JSON}...")
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data_payload, f)

    size_mb = os.path.getsize(OUTPUT_JSON) / (1024 * 1024)
    print(f"[SUCCESS] Exported {OUTPUT_JSON} ({size_mb:.2f} MB).")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
