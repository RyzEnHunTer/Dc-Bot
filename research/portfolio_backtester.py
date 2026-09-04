"""
DCC Portfolio Multi-Pair Backtester (XAUUSD + EURUSD + GBPUSD)
Enforces:
- STRICTLY NO OVERLAPPING on the same pair (max 1 position per symbol)
- NO DUPLICATION of signals
- Realistic Entry Hours (06:00 to 16:00 UTC) preventing late-day EOD cutoff traps
- 1.5R Partial Profit Booking (50% close) + SL to Breakeven
- 2.0R Runner Target
- Realistic tick execution on MT5 bid/ask data with spread and slippage
- Global portfolio risk management
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from dcc_engine import DCCEngine, SignalType, TradeSignal
from mt5_data import MT5DataProvider


@dataclass
class PortfolioTradeRecord:
    trade_id: int
    symbol: str
    direction: str
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    duration_minutes: float
    planned_entry: float
    actual_entry: float
    entry_spread: float
    entry_slippage: float
    stop_loss: float
    tp1_price: float
    tp2_price: float
    tp1_hit: bool
    tp2_hit: bool
    be_hit: bool
    exit_reason: str
    lots: float
    partial_pnl: float
    runner_pnl: float
    gross_pnl: float
    commission: float
    net_pnl: float
    return_pct: float
    account_balance: float
    r_multiple: float
    adx_1h: float
    atr_1h: float


class DCCPortfolioBacktester:
    # Symbol specifications
    SYMBOL_SPECS = {
        "XAUUSD": {"contract_size": 100.0, "digits": 2, "commission": 5.0, "min_lot": 0.02},
        "EURUSD": {"contract_size": 100_000.0, "digits": 5, "commission": 5.0, "min_lot": 0.02},
        "GBPUSD": {"contract_size": 100_000.0, "digits": 5, "commission": 5.0, "min_lot": 0.02},
    }

    def __init__(
        self,
        symbols: List[str] = ["XAUUSD", "EURUSD", "GBPUSD"],
        data_provider: Optional[MT5DataProvider] = None,
        initial_balance: float = 100_000.0,
        risk_per_trade: float = 0.01,  # 1.0% risk per trade
        max_portfolio_positions: int = 2,  # At most 2 pairs open simultaneously across portfolio
        partial_ratio: float = 0.5,  # 50% partial profit booking
        tp1_rr: float = 1.5,  # 1.5 RR target for partial
        tp2_rr: float = 2.0,  # 2.0 RR target for runner
        adx_min: float = 20.0,
        entry_start_hour: int = 6,
        entry_end_hour: int = 16,
    ):
        self.symbols = symbols
        self.data_provider = data_provider if data_provider is not None else MT5DataProvider()
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.max_portfolio_positions = max_portfolio_positions
        self.partial_ratio = partial_ratio
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr
        self.adx_min = adx_min
        self.entry_start_hour = entry_start_hour
        self.entry_end_hour = entry_end_hour

        self.trades: List[PortfolioTradeRecord] = []
        self.equity_curve: List[Dict] = []

    def run(self, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        print(f"\n=======================================================")
        print(f"Starting DCC Multi-Pair Portfolio Backtest")
        print(f"Pairs: {', '.join(self.symbols)}")
        print(f"Period: {start_date.date()} to {end_date.date()}")
        print(f"Initial Balance: ${self.initial_balance:,.2f} | Risk per Trade: {self.risk_per_trade*100:.1f}%")
        print(f"Rules: STRICTLY NO OVERLAPPING (Max 1 per pair, Max {self.max_portfolio_positions} concurrent)")
        print(f"Entry Window: {self.entry_start_hour:02d}:00 to {self.entry_end_hour:02d}:00 UTC (No late entries)")
        print(f"Safety: 1.5R Partial Booking (50%) + Move SL to Breakeven")
        print(f"=======================================================\n")

        # 1. Prepare data for each symbol
        prepared_data = {}
        engines = {}

        for sym in self.symbols:
            eng = DCCEngine(
                adx_min_threshold=self.adx_min,
                check_2h_room=False,
                use_daily_open_filter=False,
                london_session_start=time(self.entry_start_hour, 0),
                london_session_end=time(self.entry_end_hour, 0),
                ny_session_start=time(self.entry_start_hour, 0),
                ny_session_end=time(self.entry_end_hour, 0),
                avoid_monday_london=False,
                avoid_friday_ny=False,
                avoid_nfp_friday=False,
                eod_exit_time=time(21, 0)
            )
            engines[sym] = eng

            df_m5, df_1h, df_2h = self.data_provider.fetch_multi_timeframe_rates(sym, start_date, end_date, warmup_days=20)
            df_prep = eng.prepare_data(df_m5, df_1h, df_2h)

            eval_mask = (df_prep.index >= pd.Timestamp(start_date, tz=timezone.utc)) & \
                        (df_prep.index <= pd.Timestamp(end_date, tz=timezone.utc))
            prepared_data[sym] = df_prep.loc[eval_mask]
            print(f"[Portfolio] Prepared {len(prepared_data[sym])} 5M bars for {sym}")

        # 2. Extract and merge all candidate signals chronologically
        all_signals = []
        for sym in self.symbols:
            df = prepared_data[sym]
            eng = engines[sym]
            for i in range(1, len(df)):
                prev_bar = df.iloc[i - 1]
                curr_bar = df.iloc[i]
                sig = eng.evaluate_bar(prev_bar, curr_bar, symbol=sym)
                if sig:
                    all_signals.append((curr_bar.name, sym, sig))

        all_signals.sort(key=lambda x: x[0])
        print(f"[Portfolio] Total candidate signals across portfolio: {len(all_signals)}")

        # 3. Simulate with strict position tracking (NO overlapping on the same symbol!)
        active_positions: Dict[str, datetime] = {}
        current_tick_cache: Dict[str, Dict[datetime.date, pd.DataFrame]] = {s: {} for s in self.symbols}
        trade_id = 0

        for bar_time, sym, sig in all_signals:
            # Clean up exited positions
            active_positions = {s: exit_t for s, exit_t in active_positions.items() if exit_t > bar_time}

            # STRICT CHECK 1: No overlapping on the same pair!
            if sym in active_positions:
                continue

            # STRICT CHECK 2: Portfolio concurrent position limit
            if len(active_positions) >= self.max_portfolio_positions:
                continue

            # Fetch ticks
            t_date = bar_time.date()
            if t_date not in current_tick_cache[sym]:
                current_tick_cache[sym][t_date] = self.data_provider.fetch_ticks_day(sym, t_date)

            day_ticks = current_tick_cache[sym][t_date]
            if day_ticks is None or len(day_ticks) == 0:
                continue

            # Simulate tick execution
            trade_rec, exit_time = self._simulate_portfolio_trade(
                trade_id=trade_id + 1,
                signal=sig,
                bar_close_time=bar_time,
                ticks_df=day_ticks
            )

            if trade_rec is not None:
                trade_id += 1
                self.trades.append(trade_rec)
                self.balance = trade_rec.account_balance

                # Register active position until exit_time
                active_positions[sym] = exit_time

                self.equity_curve.append({
                    "time": trade_rec.exit_time,
                    "symbol": sym,
                    "balance": self.balance,
                    "pnl": trade_rec.net_pnl,
                    "reason": trade_rec.exit_reason
                })

                pnl_str = f"+${trade_rec.net_pnl:,.2f}" if trade_rec.net_pnl >= 0 else f"-${abs(trade_rec.net_pnl):,.2f}"
                fill_fmt = f"{trade_rec.actual_entry:.5f}" if ('USD' in sym and sym != 'XAUUSD') else f"{trade_rec.actual_entry:.2f}"
                print(
                    f"Trade #{trade_rec.trade_id:02d} [{sym:6s} {trade_rec.direction:4s}] "
                    f"{trade_rec.entry_time.strftime('%m-%d %H:%M')} -> {trade_rec.exit_time.strftime('%H:%M')} "
                    f"({trade_rec.duration_minutes:.0f}m) | "
                    f"Fill: {fill_fmt} | "
                    f"{trade_rec.exit_reason:28s} | "
                    f"PnL: {pnl_str:10s} | Balance: ${self.balance:,.2f}"
                )

        return self.get_portfolio_analysis()

    def _simulate_portfolio_trade(
        self,
        trade_id: int,
        signal: TradeSignal,
        bar_close_time: datetime,
        ticks_df: pd.DataFrame
    ) -> Tuple[Optional[PortfolioTradeRecord], datetime]:
        spec = self.SYMBOL_SPECS.get(signal.symbol, {"contract_size": 100.0, "digits": 2, "commission": 5.0, "min_lot": 0.02})
        contract_size = spec["contract_size"]
        commission_per_lot = spec["commission"]

        mask = ticks_df['time_dt'] >= bar_close_time
        sub_ticks = ticks_df.loc[mask]
        if len(sub_ticks) == 0:
            return None, bar_close_time

        first_tick = sub_ticks.iloc[0]
        entry_time = first_tick['time_dt']

        if signal.signal_type == SignalType.BUY:
            actual_entry = float(first_tick['ask'])
            entry_spread = float(first_tick['ask'] - first_tick['bid'])
            entry_slippage = actual_entry - signal.entry_price

            initial_sl = actual_entry - signal.sl_distance
            tp1 = actual_entry + (self.tp1_rr * signal.sl_distance)
            tp2 = actual_entry + (self.tp2_rr * signal.sl_distance)
            direction = "BUY"
        else:
            actual_entry = float(first_tick['bid'])
            entry_spread = float(first_tick['ask'] - first_tick['bid'])
            entry_slippage = signal.entry_price - actual_entry

            initial_sl = actual_entry + signal.sl_distance
            tp1 = actual_entry - (self.tp1_rr * signal.sl_distance)
            tp2 = actual_entry - (self.tp2_rr * signal.sl_distance)
            direction = "SELL"

        # Position Sizing
        risk_amount = self.balance * self.risk_per_trade
        sl_points = signal.sl_distance
        total_lots = risk_amount / (sl_points * contract_size)
        total_lots = max(spec["min_lot"], round(total_lots, 2))

        partial_lots = round(total_lots * self.partial_ratio, 2)
        runner_lots = round(total_lots - partial_lots, 2)

        current_sl = initial_sl
        tp1_hit = False
        tp2_hit = False
        be_hit = False
        partial_exit_price = actual_entry
        runner_exit_price = actual_entry
        exit_time = entry_time
        exit_reason = "EOD Close"

        eod_time = time(21, 0)
        ticks_values = sub_ticks[['time_dt', 'bid', 'ask']].values

        for row in ticks_values:
            t_dt = row[0]
            bid = row[1]
            ask = row[2]
            exit_time = t_dt  # Update exit_time to last visited tick

            if signal.signal_type == SignalType.BUY:
                if not tp1_hit:
                    if bid >= tp1:
                        tp1_hit = True
                        partial_exit_price = bid
                        current_sl = actual_entry + entry_spread  # Breakeven safe!
                    elif bid <= current_sl:
                        partial_exit_price = bid
                        runner_exit_price = bid
                        exit_reason = "SL Hit"
                        break
                else:
                    if bid >= tp2:
                        tp2_hit = True
                        runner_exit_price = bid
                        exit_reason = "TP1 (1.5R) + TP2 (2.0R) Full Win"
                        break
                    elif bid <= current_sl:
                        be_hit = True
                        runner_exit_price = bid
                        exit_reason = "TP1 (1.5R) + BE Runner"
                        break

                if t_dt.time() >= eod_time:
                    if not tp1_hit:
                        partial_exit_price = bid
                    runner_exit_price = bid
                    exit_reason = "EOD Close" if not tp1_hit else "TP1 (1.5R) + EOD Runner"
                    break

            else:  # SELL
                if not tp1_hit:
                    if ask <= tp1:
                        tp1_hit = True
                        partial_exit_price = ask
                        current_sl = actual_entry - entry_spread  # Breakeven safe!
                    elif ask >= current_sl:
                        partial_exit_price = ask
                        runner_exit_price = ask
                        exit_reason = "SL Hit"
                        break
                else:
                    if ask <= tp2:
                        tp2_hit = True
                        runner_exit_price = ask
                        exit_reason = "TP1 (1.5R) + TP2 (2.0R) Full Win"
                        break
                    elif ask >= current_sl:
                        be_hit = True
                        runner_exit_price = ask
                        exit_reason = "TP1 (1.5R) + BE Runner"
                        break

                if t_dt.time() >= eod_time:
                    if not tp1_hit:
                        partial_exit_price = ask
                    runner_exit_price = ask
                    exit_reason = "EOD Close" if not tp1_hit else "TP1 (1.5R) + EOD Runner"
                    break

        if signal.signal_type == SignalType.BUY:
            partial_gross = (partial_exit_price - actual_entry) * partial_lots * contract_size
            runner_gross = (runner_exit_price - actual_entry) * runner_lots * contract_size
        else:
            partial_gross = (actual_entry - partial_exit_price) * partial_lots * contract_size
            runner_gross = (actual_entry - runner_exit_price) * runner_lots * contract_size

        total_gross = partial_gross + runner_gross
        commission = total_lots * commission_per_lot
        net_pnl = total_gross - commission
        new_balance = self.balance + net_pnl
        return_pct = (net_pnl / self.balance) * 100.0
        duration_minutes = max(1.0, (exit_time - entry_time).total_seconds() / 60.0)
        r_multiple = net_pnl / (risk_amount + 1e-9)

        record = PortfolioTradeRecord(
            trade_id=trade_id,
            symbol=signal.symbol,
            direction=direction,
            signal_time=bar_close_time,
            entry_time=entry_time,
            exit_time=exit_time,
            duration_minutes=duration_minutes,
            planned_entry=signal.entry_price,
            actual_entry=actual_entry,
            entry_spread=entry_spread,
            entry_slippage=entry_slippage,
            stop_loss=initial_sl,
            tp1_price=tp1,
            tp2_price=tp2,
            tp1_hit=tp1_hit,
            tp2_hit=tp2_hit,
            be_hit=be_hit,
            exit_reason=exit_reason,
            lots=total_lots,
            partial_pnl=partial_gross,
            runner_pnl=runner_gross,
            gross_pnl=total_gross,
            commission=commission,
            net_pnl=net_pnl,
            return_pct=return_pct,
            account_balance=new_balance,
            r_multiple=r_multiple,
            adx_1h=signal.adx_1h,
            atr_1h=signal.atr_1h
        )
        return record, exit_time

    def get_portfolio_analysis(self) -> pd.DataFrame:
        if not self.trades:
            print("[Portfolio] No trades executed.")
            return pd.DataFrame()

        df = pd.DataFrame([vars(t) for t in self.trades])
        total_trades = len(df)
        winning_trades = df[df['net_pnl'] > 0]
        losing_trades = df[df['net_pnl'] <= 0]
        win_rate = (len(winning_trades) / total_trades) * 100.0

        gross_profit = winning_trades['net_pnl'].sum() if len(winning_trades) > 0 else 0.0
        gross_loss = abs(losing_trades['net_pnl'].sum()) if len(losing_trades) > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

        total_net_pnl = df['net_pnl'].sum()
        return_on_capital = (total_net_pnl / self.initial_balance) * 100.0

        equity_series = df['account_balance']
        cum_max = equity_series.cummax()
        drawdowns = (cum_max - equity_series) / cum_max * 100.0
        max_drawdown = drawdowns.max()

        df['trade_date'] = df['exit_time'].dt.date
        daily_pnl = df.groupby('trade_date')['net_pnl'].sum()
        daily_loss_pct = (daily_pnl[daily_pnl < 0].abs() / self.initial_balance) * 100.0
        max_daily_loss = daily_loss_pct.max() if len(daily_loss_pct) > 0 else 0.0

        print("\n=======================================================")
        print("     DCC MULTI-PAIR PORTFOLIO PERFORMANCE (NO STACKING)")
        print("=======================================================")
        print(f"Pairs Included:         {', '.join(self.symbols)}")
        print(f"Total Trades:           {total_trades}")
        print(f"Trades per Day:         {total_trades / 44.0:.1f} (~{total_trades / 8.8:.1f} trades/week)")
        print(f"Winning / Losing Trades:{len(winning_trades)} / {len(losing_trades)}")
        print(f"Win Rate:               {win_rate:.2f}%")
        print(f"Profit Factor:          {profit_factor:.2f}")
        print(f"Initial Balance:        ${self.initial_balance:,.2f}")
        print(f"Final Balance:          ${self.balance:,.2f}")
        print(f"Total Net Profit:       ${total_net_pnl:,.2f} ({return_on_capital:+.2f}%)")
        print(f"Max Overall Drawdown:   {max_drawdown:.2f}%")
        print(f"Max Daily Drawdown:     {max_daily_loss:.2f}%")
        print(f"Avg Win / Avg Loss:     ${winning_trades['net_pnl'].mean():,.2f} / ${abs(losing_trades['net_pnl'].mean()):,.2f}")
        print(f"Avg Trade Duration:     {df['duration_minutes'].mean():.1f} mins ({df['duration_minutes'].mean()/60.0:.1f} hours)")
        print("-------------------------------------------------------")
        print("Breakdown by Pair:")
        for sym in self.symbols:
            sym_df = df[df['symbol'] == sym]
            if not sym_df.empty:
                s_wins = len(sym_df[sym_df['net_pnl'] > 0])
                s_total = len(sym_df)
                s_pnl = sym_df['net_pnl'].sum()
                print(f"  {sym:6s}: {s_total:2d} trades | Win Rate: {s_wins/s_total*100:5.1f}% | Net PnL: ${s_pnl:10,.2f}")
        print("=======================================================\n")

        # Save portfolio trade log and equity curve
        csv_path = "d:\\FOREX\\DC\\portfolio_trades_log.csv"
        df.to_csv(csv_path, index=False)
        print(f"[Portfolio] Saved trade log to: {csv_path}")

        # Plot portfolio equity curve
        chart_path = "d:\\FOREX\\DC\\portfolio_equity_curve.png"
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={'height_ratios': [3, 1]})

        dates = df['exit_time']
        balances = df['account_balance']
        ax1.plot(dates, balances, color='#2962FF', linewidth=2.5, marker='o', markersize=3, label='Portfolio Equity ($)')
        ax1.axhline(y=self.initial_balance, color='#787B86', linestyle='--', alpha=0.7, label=f'Initial Balance (${self.initial_balance:,.0f})')
        ax1.set_title(
            f"DCC Multi-Pair Portfolio Tick Backtest: {', '.join(self.symbols)} (July & August 2026)\n"
            f"Strictly NO Overlapping | Total Trades: {total_trades} | Win Rate: {win_rate:.1f}% | "
            f"Profit Factor: {profit_factor:.2f} | Net Return: +${total_net_pnl:,.2f} (+{return_on_capital:.2f}%)",
            fontsize=11, fontweight='bold', pad=12
        )
        ax1.set_ylabel("Account Balance ($)", fontsize=10)
        ax1.grid(True, alpha=0.3)
        ax1.legend(loc='upper left')

        cum_max = balances.cummax()
        dd = (cum_max - balances) / cum_max * 100.0
        ax2.fill_between(dates, 0, -dd, color='#EF5350', alpha=0.45, label=f'Drawdown % (Max: {max_drawdown:.2f}%)')
        ax2.set_ylabel("Drawdown %", fontsize=10)
        ax2.set_xlabel("Date", fontsize=10)
        ax2.grid(True, alpha=0.3)
        ax2.legend(loc='lower left')

        plt.tight_layout()
        plt.savefig(chart_path, dpi=150)
        plt.close()
        print(f"[Portfolio] Saved equity curve plot to: {chart_path}")

        return df


def main():
    start_dt = datetime(2026, 7, 1)
    end_dt = datetime(2026, 8, 31, 23, 59, 59)

    backtester = DCCPortfolioBacktester(
        symbols=["XAUUSD", "EURUSD", "GBPUSD"],
        initial_balance=100_000.0,
        risk_per_trade=0.01,
        max_portfolio_positions=2,  # Max 2 concurrent across portfolio
        partial_ratio=0.5,
        tp1_rr=1.5,
        tp2_rr=2.0,
        adx_min=20.0,
        entry_start_hour=6,
        entry_end_hour=16  # Active day entry window (06:00 to 16:00 UTC)
    )
    backtester.run(start_dt, end_dt)


if __name__ == "__main__":
    main()
