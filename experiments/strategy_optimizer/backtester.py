"""
High-Precision Experimental Backtester & Grid Simulator.
Strictly isolated in experiments/strategy_optimizer/ - DOES NOT TOUCH PRODUCTION FILES.

Simulates DCC strategy with:
- Twin Orders (50% partial at TP1, Breakeven stop move, runner to TP2)
- Daily Circuit Breaker (max 2 losses / day)
- Real commission ($5/lot) and contract specs ($100/pt Gold, $10/pt NAS100)
- Overall Max Drawdown & Worst Daily Drawdown
- Month-by-Month Breakdown (Jan - Sep 2026)
- Killzone Hour (09:00 & 13:00 UTC) Efficacy Audit by Month
- September 15 Forensic Outcome
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine import DCCEngine
from experiments.strategy_optimizer.optimizer_engine import (
    StrategyConfig,
    detect_liquidity_sweep_fast,
    count_recent_flips,
    compute_dynamic_tp
)

CACHE_DIR = os.path.join(PROJECT_ROOT, "data_cache")
broker_offset = timedelta(hours=3)

SPECS = {
    "XAUUSD": {
        "tick_size": 0.01,
        "tick_val": 1.0,
        "contract_size": 100.0,
        "atr_sl_mult": 1.3,
        "comm_per_lot": 5.0
    },
    "NAS100": {
        "tick_size": 0.1,
        "tick_val": 0.1,
        "contract_size": 10.0,
        "atr_sl_mult": 1.4,
        "comm_per_lot": 5.0
    }
}


class BacktestDataset:
    """Caches pre-calculated multi-timeframe indicator data in memory."""
    _cache: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]] = {}

    @classmethod
    def get_data(cls, sym: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        if sym in cls._cache:
            return cls._cache[sym]

        print(f"Loading and preparing indicator data for {sym}...")
        df_m5 = pd.read_parquet(os.path.join(CACHE_DIR, f"m5_bars_{sym}.parquet"))
        df_m5['time'] = (pd.to_datetime(df_m5['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_m5.set_index('time', inplace=True)
        df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

        df_1h = pd.read_parquet(os.path.join(CACHE_DIR, f"h1_bars_{sym}.parquet"))
        df_1h['time'] = (pd.to_datetime(df_1h['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_1h.set_index('time', inplace=True)

        df_2h = pd.read_parquet(os.path.join(CACHE_DIR, f"h2_bars_{sym}.parquet"))
        df_2h['time'] = (pd.to_datetime(df_2h['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_2h.set_index('time', inplace=True)

        cfg = SPECS[sym]
        engine = DCCEngine(atr_sl_multiplier=cfg["atr_sl_mult"], risk_reward_ratio=1.4)
        df_prep = engine.prepare_data(df_m5, df_1h, df_2h)

        # Pre-compute distance to 1H EMA20 and 5M EMA crossovers for extreme speed
        df_prep['dist_to_h1_ema20'] = (df_prep['close'] - df_prep['ema20_1h']).abs()
        df_prep['stretch_ratio'] = df_prep['dist_to_h1_ema20'] / (df_prep['atr_1h'] + 1e-9)

        # Truncate to evaluation period (2026-01-01 to 2026-09-16)
        eval_bars = df_prep[(df_prep.index >= '2026-01-01') & (df_prep.index <= '2026-09-16 23:59:59')].copy()
        cls._cache[sym] = (eval_bars, df_m5)
        print(f"Loaded {len(eval_bars)} bars for {sym}.")
        return eval_bars, df_m5


def simulate_strategy(
    sym: str,
    cfg: StrategyConfig,
    allow_overnight: bool = True
) -> pd.DataFrame:
    """
    Simulates a single symbol under the given StrategyConfig.
    """
    eval_bars, df_m5 = BacktestDataset.get_data(sym)
    sym_spec = SPECS[sym]

    tick_size = sym_spec["tick_size"]
    tick_val = sym_spec["tick_val"]
    val_per_pt = tick_val / tick_size
    sl_multiplier = sym_spec["atr_sl_mult"]
    comm_per_lot = sym_spec["comm_per_lot"]

    active_trade = None
    completed_trades = []
    current_balance = 5000.0

    daily_losses = 0
    current_day = ""

    n_bars = len(eval_bars)
    for i in range(25, n_bars):
        c_bar = eval_bars.iloc[i]
        p_bar = eval_bars.iloc[i - 1]
        t = eval_bars.index[i]
        t_hour = t.hour
        t_day = t.strftime('%Y-%m-%d')

        if t_day != current_day:
            current_day = t_day
            daily_losses = 0

        high_p = float(c_bar['high'])
        low_p = float(c_bar['low'])
        close_p = float(c_bar['close'])

        # -------------------------------------------------------------
        # 1. Manage Active Trade
        # -------------------------------------------------------------
        if active_trade is not None:
            pos = active_trade
            is_buy = (pos['direction'] == "BUY")
            sl_hit = (low_p <= pos['current_sl']) if is_buy else (high_p >= pos['current_sl'])
            tp1_hit = (high_p >= pos['tp1_price']) if is_buy else (low_p <= pos['tp1_price'])
            tp2_hit = (high_p >= pos['tp2_price']) if is_buy else (low_p <= pos['tp2_price'])
            eod_hit = (t_hour >= 21) if not allow_overnight else False

            if tp2_hit:
                pos['exit_time'] = t
                pos['exit_price'] = pos['tp2_price']
                pos['exit_reason'] = "FULL_TP2"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                pts1 = pos['tp1_dist']
                pts2 = pos['tp2_dist']
                p1_dollars = pos['part_lots'] * pts1 * val_per_pt
                p2_dollars = pos['run_lots'] * pts2 * val_per_pt
                comm = pos['total_lots'] * comm_per_lot
                net = p1_dollars + p2_dollars - comm
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue

            elif sl_hit:
                pos['exit_time'] = t
                pos['exit_price'] = pos['current_sl']
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                comm = pos['total_lots'] * comm_per_lot
                if pos['tp1_hit']:
                    pos['exit_reason'] = "TP1_THEN_BE"
                    p1_dollars = pos['part_lots'] * pos['tp1_dist'] * val_per_pt
                    p2_dollars = 0.0
                    net = p1_dollars - comm
                else:
                    pos['exit_reason'] = "SL"
                    net = -50.0 - comm
                    daily_losses += 1

                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue

            elif tp1_hit and not pos['tp1_hit']:
                pos['tp1_hit'] = True
                pos['current_sl'] = pos['entry_price']  # Move SL to BE

            elif eod_hit or i == n_bars - 1:
                pos['exit_time'] = t
                pos['exit_price'] = close_p
                pos['exit_reason'] = "EOD_EXIT" if eod_hit else "END_OF_DATA"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                comm = pos['total_lots'] * comm_per_lot
                run_pts = (close_p - pos['entry_price']) if is_buy else (pos['entry_price'] - close_p)
                p2_dollars = pos['run_lots'] * run_pts * val_per_pt
                p1_dollars = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                net = p1_dollars + p2_dollars - comm
                if net < 0:
                    daily_losses += 1
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue

        # -------------------------------------------------------------
        # 2. Entry Signal Evaluation
        # -------------------------------------------------------------
        if active_trade is None:
            # Daily Circuit Breaker: Max 2 losses
            if daily_losses >= 2:
                continue

            # Trading Hours: Active session (06:00 to 21:00 UTC)
            if not (6 <= t_hour < 21):
                continue

            # Blocked hours / Killzones check
            if cfg.blocked_hours and t_hour in cfg.blocked_hours:
                continue

            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0 or c_bar['adx_1h'] < cfg.adx_min:
                continue

            atr = float(c_bar['atr_1h'])
            if atr <= 0:
                continue

            # 1H EMA Stretch Filter (Rubber-band protection)
            if cfg.max_h1_stretch is not None:
                stretch = float(c_bar['stretch_ratio'])
                if stretch > cfg.max_h1_stretch:
                    continue  # Overextended away from 1H 20 EMA!

            # 5M Chop Filter (Recent crossovers in last 12 bars)
            if cfg.max_recent_flips is not None:
                flips = count_recent_flips(eval_bars, i, lookback_bars=12)
                if flips > cfg.max_recent_flips:
                    continue  # Choppy sideways market!

            vwap = float(c_bar['vwap_5m'])
            h1_e20 = float(c_bar['ema20_1h'])
            curr_diff = float(c_bar['ema9_5m']) - float(c_bar['ema20_5m'])
            prev_diff = float(p_bar['ema9_5m']) - float(p_bar['ema20_5m'])

            sig_type = 0
            if bias == 1 and prev_diff <= 0 and curr_diff > 0 and close_p > vwap and close_p > h1_e20:
                sig_type = 1
            elif bias == -1 and prev_diff >= 0 and curr_diff < 0 and close_p < vwap and close_p < h1_e20:
                sig_type = -1

            if sig_type != 0:
                # 5M Liquidity Sweep Check (Optional toggle)
                if cfg.use_sweep:
                    # Note: fast check on df_m5 at bar timestamp
                    m5_idx = df_m5.index.get_loc(t)
                    has_sw, _, _ = detect_liquidity_sweep_fast(df_m5, m5_idx, bias, 20, 8)
                    if not has_sw:
                        continue

                sl_dist = sl_multiplier * atr
                entry_p = close_p
                sw_h_2h = float(c_bar.get('swing_high_2h', 0.0))
                sw_l_2h = float(c_bar.get('swing_low_2h', 0.0))

                if cfg.tp_model == "dynamic_2h":
                    tp1_p, tp2_p, eff_tp1_rr, eff_tp2_rr = compute_dynamic_tp(
                        entry_p, sl_dist, sig_type, sw_h_2h, sw_l_2h, cfg.tp1_rr, cfg.tp2_rr
                    )
                    tp1_dist = abs(tp1_p - entry_p)
                    tp2_dist = abs(tp2_p - entry_p)
                else:
                    eff_tp1_rr = cfg.tp1_rr
                    eff_tp2_rr = cfg.tp2_rr
                    tp1_dist = eff_tp1_rr * sl_dist
                    tp2_dist = eff_tp2_rr * sl_dist
                    if sig_type == 1:
                        tp1_p = entry_p + tp1_dist
                        tp2_p = entry_p + tp2_dist
                    else:
                        tp1_p = entry_p - tp1_dist
                        tp2_p = entry_p - tp2_dist

                sl_p = entry_p - sl_dist if sig_type == 1 else entry_p + sl_dist
                dir_str = "BUY" if sig_type == 1 else "SELL"

                # Position sizing for $50 fixed risk
                raw_lots = 50.0 / (sl_dist * val_per_pt)
                tot_lots = max(0.01, round(raw_lots, 2))
                part_lots = round(tot_lots * 0.5, 2)
                run_lots = round(tot_lots - part_lots, 2)

                is_killzone = (t_hour in [9, 13])

                active_trade = {
                    "trade_id": len(completed_trades) + 1,
                    "symbol": sym,
                    "direction": dir_str,
                    "entry_time": t,
                    "entry_price": entry_p,
                    "initial_sl": sl_p,
                    "current_sl": sl_p,
                    "sl_dist": sl_dist,
                    "tp1_dist": tp1_dist,
                    "tp2_dist": tp2_dist,
                    "tp1_price": tp1_p,
                    "tp2_price": tp2_p,
                    "eff_tp1_rr": eff_tp1_rr,
                    "eff_tp2_rr": eff_tp2_rr,
                    "total_lots": tot_lots,
                    "part_lots": part_lots,
                    "run_lots": run_lots,
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "is_killzone": is_killzone,
                    "entry_hour": t_hour,
                    "starting_balance": round(current_balance, 2)
                }

    if not completed_trades:
        return pd.DataFrame()

    df_trades = pd.DataFrame(completed_trades)
    df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'])
    df_trades['exit_dt'] = pd.to_datetime(df_trades['exit_time'])
    df_trades['month'] = df_trades['entry_dt'].dt.strftime('%Y-%m')
    df_trades['date'] = df_trades['exit_dt'].dt.strftime('%Y-%m-%d')
    return df_trades


def calculate_comprehensive_metrics(df_trades: pd.DataFrame, initial_balance: float = 5000.0) -> Dict:
    """
    Computes all standard & prop-firm metrics including:
    - Net PnL ($ and %)
    - Win Rate (%)
    - Profit Factor
    - Overall Max Drawdown ($ and %)
    - Worst Daily Drawdown ($ and %)
    - Monthly breakdown
    - Killzone (09:00 & 13:00 UTC) breakdown by month
    - Sep 15, 2026 performance
    """
    if df_trades.empty:
        return {
            "total_trades": 0, "net_pnl": 0.0, "pnl_pct": 0.0, "win_rate": 0.0,
            "profit_factor": 0.0, "overall_max_dd_dollars": 0.0, "overall_max_dd_pct": 0.0,
            "worst_daily_dd_dollars": 0.0, "worst_daily_dd_pct": 0.0,
            "monthly_stats": {}, "killzone_stats": {}, "sep15_trades": 0, "sep15_pnl": 0.0
        }

    total_trades = len(df_trades)
    wins = df_trades[df_trades['net_pnl'] > 0]
    losses = df_trades[df_trades['net_pnl'] < 0]
    win_rate = (len(wins) / total_trades) * 100.0
    net_pnl = df_trades['net_pnl'].sum()
    pnl_pct = (net_pnl / initial_balance) * 100.0

    gross_win = wins['net_pnl'].sum() if len(wins) > 0 else 0.0
    gross_loss = abs(losses['net_pnl'].sum()) if len(losses) > 0 else 0.0
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 999.0

    # 1. Overall Max Drawdown (Peak to Trough)
    df_trades['cum_pnl'] = df_trades['net_pnl'].cumsum()
    df_trades['equity'] = initial_balance + df_trades['cum_pnl']
    df_trades['peak'] = df_trades['equity'].cummax()
    df_trades['dd_dollars'] = df_trades['peak'] - df_trades['equity']
    df_trades['dd_pct'] = (df_trades['dd_dollars'] / df_trades['peak']) * 100.0
    overall_max_dd_dollars = round(float(df_trades['dd_dollars'].max()), 2)
    overall_max_dd_pct = round(float(df_trades['dd_pct'].max()), 2)

    # 2. Worst Daily Drawdown
    daily_pnl = df_trades.groupby('date')['net_pnl'].sum()
    daily_losses_only = daily_pnl[daily_pnl < 0]
    worst_daily_dd_dollars = round(float(abs(daily_losses_only.min())), 2) if len(daily_losses_only) > 0 else 0.0
    worst_daily_dd_pct = round((worst_daily_dd_dollars / initial_balance) * 100.0, 2)

    # 3. Monthly Breakdown
    monthly_stats = {}
    for m, m_group in df_trades.groupby('month'):
        m_wins = m_group[m_group['net_pnl'] > 0]
        m_losses = m_group[m_group['net_pnl'] < 0]
        m_wr = (len(m_wins) / len(m_group)) * 100.0 if len(m_group) > 0 else 0.0
        m_pnl = m_group['net_pnl'].sum()
        monthly_stats[m] = {
            "trades": len(m_group),
            "pnl": round(m_pnl, 2),
            "win_rate": round(m_wr, 1)
        }

    # 4. Killzone (09:00 & 13:00 UTC) Breakdown by Month
    killzone_stats = {}
    for m, m_group in df_trades.groupby('month'):
        kz_trades = m_group[m_group['is_killzone']]
        non_kz_trades = m_group[~m_group['is_killzone']]
        kz_pnl = kz_trades['net_pnl'].sum() if len(kz_trades) > 0 else 0.0
        non_kz_pnl = non_kz_trades['net_pnl'].sum() if len(non_kz_trades) > 0 else 0.0
        killzone_stats[m] = {
            "kz_trades": len(kz_trades),
            "kz_pnl": round(kz_pnl, 2),
            "non_kz_pnl": round(non_kz_pnl, 2),
            "kz_helps": (kz_pnl > 0)  # If kz_pnl > 0, trading it helped; if < 0, blocking it helped!
        }

    # 5. September 15, 2026 Targeted Test
    sep15_trades = df_trades[df_trades['entry_dt'].dt.strftime('%Y-%m-%d') == '2026-09-15']
    sep15_count = len(sep15_trades)
    sep15_pnl = round(float(sep15_trades['net_pnl'].sum()), 2) if sep15_count > 0 else 0.0

    return {
        "total_trades": total_trades,
        "net_pnl": round(net_pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": profit_factor,
        "overall_max_dd_dollars": overall_max_dd_dollars,
        "overall_max_dd_pct": overall_max_dd_pct,
        "worst_daily_dd_dollars": worst_daily_dd_dollars,
        "worst_daily_dd_pct": worst_daily_dd_pct,
        "monthly_stats": monthly_stats,
        "killzone_stats": killzone_stats,
        "sep15_trades": sep15_count,
        "sep15_pnl": sep15_pnl
    }
