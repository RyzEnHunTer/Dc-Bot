"""
DCC Tick-by-Tick High-Precision Backtester
Supports:
- Partial Profit Booking at 1.5 RR (50% close)
- Moving SL to Breakeven after TP1 to make trades safe
- Runner target at 2.0 RR (or trail)
- High-frequency execution across full London & New York sessions
- Realistic spread cost, entry slippage, and exit slippage on actual ticks
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from dcc_engine import DCCEngine, SignalType, TradeSignal
from mt5_data import MT5DataProvider


@dataclass
class TradeRecord:
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


class DCCBacktester:
    def __init__(
        self,
        engine: Optional[DCCEngine] = None,
        data_provider: Optional[MT5DataProvider] = None,
        initial_balance: float = 100_000.0,
        risk_per_trade: float = 0.01,  # 1.0% risk per trade
        commission_per_lot: float = 5.0,  # $5 round turn per lot
        contract_size: float = 100.0,  # 100 oz per lot for XAUUSD
        use_partial_tp: bool = True,
        partial_ratio: float = 0.5,  # Close 50% at TP1
        tp1_rr: float = 1.5,  # 1.5 RR partial booking
        tp2_rr: float = 2.0,  # 2.0 RR runner target
        move_sl_to_be: bool = True,  # Move SL to Breakeven once TP1 is hit
    ):
        self.engine = engine if engine is not None else DCCEngine()
        self.data_provider = data_provider if data_provider is not None else MT5DataProvider()
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.risk_per_trade = risk_per_trade
        self.commission_per_lot = commission_per_lot
        self.contract_size = contract_size

        # Partial booking settings
        self.use_partial_tp = use_partial_tp
        self.partial_ratio = partial_ratio
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr
        self.move_sl_to_be = move_sl_to_be

        self.trades: List[TradeRecord] = []
        self.equity_curve: List[Dict] = []

    def run(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        print(f"\n=======================================================")
        print(f"Starting DCC Tick Backtest: {symbol}")
        print(f"Period: {start_date.date()} to {end_date.date()}")
        print(f"Initial Balance: ${self.initial_balance:,.2f} | Risk: {self.risk_per_trade*100:.1f}%")
        print(f"Partial Booking: TP1={self.tp1_rr}R (50%) + Move SL to BE | Runner TP2={self.tp2_rr}R (50%)")
        print(f"=======================================================\n")

        # 1. Fetch multi-timeframe rates
        df_m5, df_1h, df_2h = self.data_provider.fetch_multi_timeframe_rates(
            symbol=symbol,
            start_dt=start_date,
            end_dt=end_date,
            warmup_days=20
        )

        # 2. Prepare indicators without lookahead bias
        print("[Backtester] Preparing multi-timeframe indicators...")
        df_prepared = self.engine.prepare_data(df_m5, df_1h, df_2h)

        # Filter M5 bars to backtest evaluation window
        eval_mask = (df_prepared.index >= pd.Timestamp(start_date, tz=timezone.utc)) & \
                    (df_prepared.index <= pd.Timestamp(end_date, tz=timezone.utc))
        df_eval = df_prepared.loc[eval_mask]
        print(f"[Backtester] Evaluating across {len(df_eval)} 5M bars...")

        current_tick_date = None
        current_day_ticks: Optional[pd.DataFrame] = None

        trade_id = 0
        i = 1
        n_bars = len(df_eval)

        while i < n_bars:
            prev_bar = df_eval.iloc[i - 1]
            curr_bar = df_eval.iloc[i]
            bar_time = curr_bar.name

            # Check signal on 5M close
            signal = self.engine.evaluate_bar(prev_bar, curr_bar, symbol=symbol)

            if signal is not None:
                # Load ticks for this day
                trade_date = bar_time.date()
                if current_tick_date != trade_date:
                    current_tick_date = trade_date
                    current_day_ticks = self.data_provider.fetch_ticks_day(symbol, trade_date)

                if current_day_ticks is not None and len(current_day_ticks) > 0:
                    trade_record, exit_time = self._simulate_tick_trade(
                        trade_id=trade_id + 1,
                        signal=signal,
                        bar_close_time=bar_time,
                        ticks_df=current_day_ticks
                    )

                    if trade_record is not None:
                        trade_id += 1
                        self.trades.append(trade_record)
                        self.balance = trade_record.account_balance
                        self.equity_curve.append({
                            "time": trade_record.exit_time,
                            "balance": self.balance,
                            "pnl": trade_record.net_pnl,
                            "reason": trade_record.exit_reason
                        })

                        # Print trade result
                        pnl_str = f"+${trade_record.net_pnl:,.2f}" if trade_record.net_pnl >= 0 else f"-${abs(trade_record.net_pnl):,.2f}"
                        print(
                            f"Trade #{trade_record.trade_id:02d} [{trade_record.direction}] "
                            f"{trade_record.entry_time.strftime('%m-%d %H:%M')} -> {trade_record.exit_time.strftime('%H:%M')} "
                            f"({trade_record.duration_minutes:.0f}m) | "
                            f"Fill: {trade_record.actual_entry:.2f} (slip: {trade_record.entry_slippage:+.2f}) | "
                            f"{trade_record.exit_reason} | "
                            f"PnL: {pnl_str} | Balance: ${self.balance:,.2f}"
                        )

                        # Advance 5M index to avoid double entering on the exact same flip
                        # Allow next entry after 2 bars (10 mins) or after exit
                        adv_time = min(exit_time, bar_time + timedelta(minutes=15))
                        while i < n_bars and df_eval.index[i] <= adv_time:
                            i += 1
                        continue

            i += 1

        print("\n[Backtester] Backtest execution complete.")
        return self.get_trade_analysis()

    def _simulate_tick_trade(
        self,
        trade_id: int,
        signal: TradeSignal,
        bar_close_time: datetime,
        ticks_df: pd.DataFrame
    ) -> Tuple[Optional[TradeRecord], datetime]:
        mask = ticks_df['time_dt'] >= bar_close_time
        sub_ticks = ticks_df.loc[mask]

        if len(sub_ticks) == 0:
            return None, bar_close_time

        first_tick = sub_ticks.iloc[0]
        entry_time = first_tick['time_dt']

        # Determine Entry Fill Price & Slippage
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
        total_lots = risk_amount / (sl_points * self.contract_size)
        total_lots = max(0.02, round(total_lots, 2))  # At least 0.02 so it can be split 50/50

        partial_lots = round(total_lots * self.partial_ratio, 2)
        runner_lots = round(total_lots - partial_lots, 2)

        # State tracking
        current_sl = initial_sl
        tp1_hit = False
        tp2_hit = False
        be_hit = False
        partial_exit_price = actual_entry
        runner_exit_price = actual_entry
        exit_time = entry_time
        exit_reason = "EOD"

        eod_time = self.engine.eod_exit_time
        ticks_values = sub_ticks[['time_dt', 'bid', 'ask']].values

        for row in ticks_values:
            t_dt = row[0]
            bid = row[1]
            ask = row[2]

            # BUY ORDER EXECUTION
            if signal.signal_type == SignalType.BUY:
                # Step 1: Check if TP1 (1.5 RR) has not been hit yet
                if not tp1_hit:
                    # Target 1 reached!
                    if bid >= tp1:
                        tp1_hit = True
                        partial_exit_price = bid
                        # Move SL to Breakeven (Entry + Spread to cover cost = safe trade!)
                        if self.move_sl_to_be:
                            current_sl = actual_entry + entry_spread
                    # Initial Stop Loss hit
                    elif bid <= current_sl:
                        partial_exit_price = bid
                        runner_exit_price = bid
                        exit_time = t_dt
                        exit_reason = "SL Hit"
                        break

                # Step 2: If TP1 was already hit, manage the runner
                else:
                    # Runner Target 2 reached!
                    if bid >= tp2:
                        tp2_hit = True
                        runner_exit_price = bid
                        exit_time = t_dt
                        exit_reason = "TP1 (1.5R) + TP2 (2.0R) Full Win"
                        break
                    # Breakeven Stop hit
                    elif bid <= current_sl:
                        be_hit = True
                        runner_exit_price = bid
                        exit_time = t_dt
                        exit_reason = "TP1 (1.5R) + BE Runner"
                        break

                # EOD Force Close
                if t_dt.time() >= eod_time:
                    if not tp1_hit:
                        partial_exit_price = bid
                    runner_exit_price = bid
                    exit_time = t_dt
                    exit_reason = "EOD Close" if not tp1_hit else "TP1 (1.5R) + EOD Runner"
                    break

            # SELL ORDER EXECUTION
            else:
                if not tp1_hit:
                    if ask <= tp1:
                        tp1_hit = True
                        partial_exit_price = ask
                        if self.move_sl_to_be:
                            current_sl = actual_entry - entry_spread
                    elif ask >= current_sl:
                        partial_exit_price = ask
                        runner_exit_price = ask
                        exit_time = t_dt
                        exit_reason = "SL Hit"
                        break
                else:
                    if ask <= tp2:
                        tp2_hit = True
                        runner_exit_price = ask
                        exit_time = t_dt
                        exit_reason = "TP1 (1.5R) + TP2 (2.0R) Full Win"
                        break
                    elif ask >= current_sl:
                        be_hit = True
                        runner_exit_price = ask
                        exit_time = t_dt
                        exit_reason = "TP1 (1.5R) + BE Runner"
                        break

                if t_dt.time() >= eod_time:
                    if not tp1_hit:
                        partial_exit_price = ask
                    runner_exit_price = ask
                    exit_time = t_dt
                    exit_reason = "EOD Close" if not tp1_hit else "TP1 (1.5R) + EOD Runner"
                    break

        # Calculate PnLs for both halves
        if signal.signal_type == SignalType.BUY:
            partial_gross = (partial_exit_price - actual_entry) * partial_lots * self.contract_size
            runner_gross = (runner_exit_price - actual_entry) * runner_lots * self.contract_size
        else:
            partial_gross = (actual_entry - partial_exit_price) * partial_lots * self.contract_size
            runner_gross = (actual_entry - runner_exit_price) * runner_lots * self.contract_size

        total_gross = partial_gross + runner_gross
        commission = total_lots * self.commission_per_lot
        net_pnl = total_gross - commission
        new_balance = self.balance + net_pnl
        return_pct = (net_pnl / self.balance) * 100.0
        duration_minutes = (exit_time - entry_time).total_seconds() / 60.0
        r_multiple = net_pnl / (risk_amount + 1e-9)

        record = TradeRecord(
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

    def get_trade_analysis(self) -> pd.DataFrame:
        if not self.trades:
            print("[Backtester] No trades executed in this period.")
            return pd.DataFrame()

        df = pd.DataFrame([vars(t) for t in self.trades])

        total_trades = len(df)
        winning_trades = df[df['net_pnl'] > 0]
        losing_trades = df[df['net_pnl'] <= 0]
        tp1_wins = df[df['tp1_hit'] == True]
        full_tp2_wins = df[df['tp2_hit'] == True]
        be_runners = df[df['be_hit'] == True]

        win_rate = (len(winning_trades) / total_trades) * 100.0
        gross_profit = winning_trades['net_pnl'].sum() if len(winning_trades) > 0 else 0.0
        gross_loss = abs(losing_trades['net_pnl'].sum()) if len(losing_trades) > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

        total_net_pnl = df['net_pnl'].sum()
        return_on_capital = (total_net_pnl / self.initial_balance) * 100.0

        # Drawdown calculation
        equity_series = df['account_balance']
        cum_max = equity_series.cummax()
        drawdowns = (cum_max - equity_series) / cum_max * 100.0
        max_drawdown = drawdowns.max()

        # Daily drawdown
        df['trade_date'] = df['exit_time'].dt.date
        daily_pnl = df.groupby('trade_date')['net_pnl'].sum()
        daily_loss_pct = (daily_pnl[daily_pnl < 0].abs() / self.initial_balance) * 100.0
        max_daily_loss = daily_loss_pct.max() if len(daily_loss_pct) > 0 else 0.0

        avg_duration = df['duration_minutes'].mean()
        avg_win = winning_trades['net_pnl'].mean() if len(winning_trades) > 0 else 0.0
        avg_loss = losing_trades['net_pnl'].mean() if len(losing_trades) > 0 else 0.0

        print("\n=======================================================")
        print("          DCC PARTIAL-BOOKING BACKTEST PERFORMANCE     ")
        print("=======================================================")
        print(f"Total Trades:           {total_trades}")
        print(f"Trades per Day:         {total_trades / 44.0:.1f} (~{total_trades / 8.8:.1f} trades/week)")
        print(f"Winning / Losing Trades:{len(winning_trades)} / {len(losing_trades)}")
        print(f"TP1 (1.5R) Hit Count:   {len(tp1_wins)} ({len(tp1_wins)/total_trades*100:.1f}%) [Trades made safe!]")
        print(f"TP2 (2.0R) Hit Count:   {len(full_tp2_wins)} ({len(full_tp2_wins)/total_trades*100:.1f}%) [Full Target]")
        print(f"BE Runner Hit Count:    {len(be_runners)} ({len(be_runners)/total_trades*100:.1f}%) [Risk-Free Exit]")
        print(f"Win Rate:               {win_rate:.2f}%")
        print(f"Profit Factor:          {profit_factor:.2f}")
        print(f"Initial Balance:        ${self.initial_balance:,.2f}")
        print(f"Final Balance:          ${self.balance:,.2f}")
        print(f"Total Net Profit:       ${total_net_pnl:,.2f} ({return_on_capital:+.2f}%)")
        print(f"Max Overall Drawdown:   {max_drawdown:.2f}%")
        print(f"Max Daily Drawdown:     {max_daily_loss:.2f}%")
        print(f"Avg Win / Avg Loss:     ${avg_win:,.2f} / ${abs(avg_loss):,.2f}")
        print(f"Avg Trade Duration:     {avg_duration:.1f} mins ({avg_duration/60.0:.1f} hours)")
        print(f"Avg Entry Spread:       ${df['entry_spread'].mean():.2f}")
        print(f"Avg Entry Slippage:     ${df['entry_slippage'].abs().mean():.2f}")
        print("=======================================================\n")

        return df
