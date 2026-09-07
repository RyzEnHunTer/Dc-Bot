"""
Data Pipeline for TradingView-Style Visualizer
Generates candlestick data (OHLCV + EMA9 + EMA20 + VWAP) and parses
backtest trade logs with exact TradingView Long/Short Position geometry.
"""

import os
import sys
import json
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
            print(f"[{symbol}] Loaded {len(df)} 5M bars from local cache.")
            return df
        except Exception as e:
            print(f"[{symbol}] Cache load failed: {e}. Fetching fresh.")

    # Fetch from MT5
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            start_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
            end_dt = datetime(2026, 7, 1, tzinfo=timezone.utc)
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

    import bisect
    df = pd.read_csv(csv_path)
    trades = []

    for idx, row in df.iterrows():
        trade_id = int(row.get('trade_id', idx + 1))
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
        
        actual_entry = float(row['actual_entry'])
        atr = float(row['atr_1h'])
        
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

        spread = float(row.get('entry_spread', 0.0))
        be_price = round(actual_entry + spread if direction == "BUY" else actual_entry - spread, digits)

        tp1_hit = bool(row.get('tp1_hit', False))
        tp2_hit = bool(row.get('tp2_hit', False))
        be_hit = bool(row.get('be_hit', False))
        exit_reason = str(row.get('exit_reason', 'UNKNOWN'))
        
        net_pnl = round(float(row.get('net_pnl', 0.0)), 2)
        return_pct = round(float(row.get('return_pct', 0.0)), 2)
        total_lots = round(float(row.get('total_lots', 0.01)), 2)
        partial_lots = round(float(row.get('partial_lots', 0.01)), 2)
        runner_lots = round(float(row.get('runner_lots', 0.01)), 2)
        duration_m = float(row.get('duration_m', 0.0))
        duration_str = format_duration(duration_m)
        balance = round(float(row.get('balance', row.get('account_balance', 5000.0))), 2)

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
                "desc": f"First target reached! Secured +${float(row.get('partial_pnl', 0.0)):,.2f}. Stop loss shifted to Breakeven @ {be_price:,.2f}.",
                "type": "tp1"
            })

        if exit_reason == "FULL_TP2":
            exit_badge = "WINNER (TP2)"
            badge_color = "green"
            narrative.append({
                "time": exit_time_utc,
                "title": f"FULL TP2 HIT (+{tp2_rr}R) -> Runner Closed ({runner_lots} Lots)",
                "desc": f"Runner reached final target TP2! +${float(row.get('runner_pnl', 0.0)):,.2f}. Total Trade PnL: +${net_pnl:,.2f} (+{return_pct}%).",
                "type": "win"
            })
        elif exit_reason == "TP1_THEN_BE":
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
                "type": "info"
            })

        # TradingView Long / Short Position Geometry
        geometry = {
            "type": "long" if direction == "BUY" else "short",
            "entryPrice": actual_entry,
            "slPrice": sl_price,
            "tp1Price": tp1_price,
            "tp2Price": tp2_price,
            "bePrice": be_price,
            "entryTime": entry_candle_time,
            "exitTime": exit_candle_time,
            "entryBarIndex": entry_bar_index,
            "exitBarIndex": exit_bar_index,
            "rrRatio": tp1_rr,
            "targetDistance": round(tp1_distance, digits),
            "stopDistance": round(sl_distance, digits),
            "tp1Hit": tp1_hit,
            "tp2Hit": tp2_hit,
            "beHit": be_hit,
            "exitReason": exit_reason
        }

        trades.append({
            "id": idx + 1,
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "entry_unix": entry_unix,
            "exit_unix": exit_unix,
            "entry_candle_time": entry_candle_time,
            "exit_candle_time": exit_candle_time,
            "entry_bar_index": entry_bar_index,
            "exit_bar_index": exit_bar_index,
            "entry_time_utc": entry_time_utc,
            "entry_time_ist": entry_time_ist,
            "exit_time_utc": exit_time_utc,
            "exit_time_ist": exit_time_ist,
            "duration_str": duration_str,
            "duration_m": round(duration_m, 1),
            "actual_entry": actual_entry,
            "sl_price": sl_price,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "be_price": be_price,
            "spread": spread,
            "total_lots": total_lots,
            "partial_lots": partial_lots,
            "runner_lots": runner_lots,
            "net_pnl": net_pnl,
            "return_pct": return_pct,
            "balance": balance,
            "exit_reason": exit_reason,
            "exit_badge": exit_badge,
            "badge_color": badge_color,
            "adx_1h": round(float(row.get('adx_1h', 0.0)), 1),
            "atr_1h": round(atr, 2),
            "month": str(row.get('month', '')),
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
    tp2_wins = [t for t in trades if t['exit_reason'] == 'FULL_TP2']
    be_scratches = [t for t in trades if t['exit_reason'] == 'TP1_THEN_BE']
    sl_losses = [t for t in trades if t['exit_reason'] == 'SL']

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
    print("TRADINGVIEW VISUALIZER DATA PIPELINE")
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

    # Strategy 1: CB + 5M Liquidity Sweep Confluence (Flagship Institutional Model)
    cb_sweep_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_cb_liquidity_sweep.csv")
    if os.path.exists(cb_sweep_csv):
        print(f"\nParsing CB + 5M Liquidity Sweep Backtest ({cb_sweep_csv})...")
        cb_sweep_trades = parse_trades(cb_sweep_csv, candles)
        strategies["cb_5m_liquidity_sweep"] = {
            "name": "CB + 5M Liquidity Sweep (60.3% WR | 2.13 PF)",
            "description": "High-conviction institutional strategy combining 3% Daily Circuit Breaker with 5M Liquidity Sweep confirmation.",
            "metrics": compute_metrics(cb_sweep_trades),
            "trades": cb_sweep_trades
        }
        print(f"-> Parsed {len(cb_sweep_trades)} trades.")

    # Strategy 2: Daily 3% Circuit Breaker Portfolio
    cb_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_with_3pct_circuit_breaker.csv")
    if os.path.exists(cb_csv):
        print(f"\nParsing 3% Circuit Breaker Backtest ({cb_csv})...")
        cb_trades = parse_trades(cb_csv, candles)
        strategies["circuit_breaker_3pct"] = {
            "name": "Daily 3% Circuit Breaker Portfolio (58.7% WR)",
            "description": "Institutional capital preservation with automatic session suspension on 3% daily DD.",
            "metrics": compute_metrics(cb_trades),
            "trades": cb_trades
        }
        print(f"-> Parsed {len(cb_trades)} trades.")

    # Strategy 3: Low-DD Optimized Portfolio
    opt_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_optimized_low_dd.csv")
    if os.path.exists(opt_csv):
        print(f"\nParsing Low-DD Optimized Backtest ({opt_csv})...")
        opt_trades = parse_trades(opt_csv, candles)
        strategies["optimized_low_dd"] = {
            "name": "Low-DD Optimized Portfolio (4.16% Max DD)",
            "description": "Enhanced with peak liquidity session filtering to minimize drawdown.",
            "metrics": compute_metrics(opt_trades),
            "trades": opt_trades
        }
        print(f"-> Parsed {len(opt_trades)} trades.")

    # Strategy 4: Strict 6-Month Portfolio
    strict_csv = os.path.join(PROJECT_ROOT, "reports", "trades_log_strict_6month.csv")
    if os.path.exists(strict_csv):
        print(f"\nParsing Strict 6-Month Backtest ({strict_csv})...")
        strict_trades = parse_trades(strict_csv, candles)
        strategies["strict_6month"] = {
            "name": "6-Month Strict Portfolio (Max 2 Trades)",
            "description": "Standard institutional execution with Max 2 concurrent positions.",
            "metrics": compute_metrics(strict_trades),
            "trades": strict_trades
        }
        print(f"-> Parsed {len(strict_trades)} trades.")

    # 3. Export complete payload
    data_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": ["XAUUSD", "NAS100"],
        "candles": candles,
        "strategies": strategies,
        "default_strategy": "cb_5m_liquidity_sweep" if "cb_5m_liquidity_sweep" in strategies else list(strategies.keys())[0]
    }

    print(f"\nWriting visualizer payload to {OUTPUT_JSON}...")
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data_payload, f)

    size_mb = os.path.getsize(OUTPUT_JSON) / (1024 * 1024)
    print(f"[SUCCESS] Exported {OUTPUT_JSON} ({size_mb:.2f} MB).")
    print("=======================================================\n")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
