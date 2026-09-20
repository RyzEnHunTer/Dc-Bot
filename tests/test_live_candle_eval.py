import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from live_bot import InstitutionalDCCBot

class TestLiveCandleEvaluation(unittest.TestCase):
    def test_candle_eval_not_blocked_by_healthy_cushion(self):
        """Verify that when daily starting equity is positive and remaining cushion is healthy,
        candle evaluation does NOT get trapped in NOT_ARMED and proceeds to strategy logic."""
        bot = InstitutionalDCCBot.__new__(InstitutionalDCCBot)
        bot.circuit_breaker_active = False
        bot.daily_starting_equity = 5000.0
        bot.daily_cb_pct = 3.0
        bot.daily_dd_limit_pct = 4.0
        bot.risk_per_trade = 0.01
        bot.symbols = ["XAUUSD"]
        bot.tz_ist = timezone.utc
        bot.entry_start_hour_utc = 6
        bot.entry_end_hour_utc = 19
        bot.trap_hours_utc = [9, 13]
        bot.use_news_shield = False
        bot.strategy_version = "v1.2"
        bot.use_liquidity_sweep = False
        bot.entry_mode = "bar_close"
        bot.armed_states = {"XAUUSD": MagicMock(is_armed=False)}
        bot.audit_logger = MagicMock()
        bot.notifier = MagicMock()
        bot.write_live_state = MagicMock()
        bot.broker_offset = timedelta(hours=0)
        bot.active_positions = {}
        bot.notified_armed_setups = {}
        bot.dry_run = True

        n = 100
        idx = pd.date_range("2026-09-14 10:00:00", periods=n, freq='5min', tz=timezone.utc)
        df_prep = pd.DataFrame({
            'open': np.full(n, 4300.0),
            'high': np.full(n, 4305.0),
            'low': np.full(n, 4295.0),
            'close': np.full(n, 4298.0),
            'volume': np.full(n, 500),
            'ema9_5m': np.full(n, 4298.0),
            'ema20_5m': np.full(n, 4300.0),
            'ema_gap': np.full(n, 2.0),
            'vwap_5m': np.full(n, 4305.0),
            'adx_1h': np.full(n, 30.0),
            'atr_1h': np.full(n, 15.0),
            'bias_1h': np.full(n, -1),
            'ema9_1h': np.full(n, 4310.0),
            'ema20_1h': np.full(n, 4315.0),
            'h1_ema20_level': np.full(n, 4315.0),
            'swing_high_2h': np.full(n, 4350.0),
            'swing_low_2h': np.full(n, 4250.0),
        }, index=idx)
        df_prep.loc[idx[n-3], 'ema9_5m'] = 4301.0
        df_prep.loc[idx[n-3], 'ema20_5m'] = 4300.0

        mock_engine = MagicMock()
        mock_engine.prepare_data.return_value = df_prep
        bot.engines = {"XAUUSD": mock_engine}

        rates_record = {
            'time': 1789380000,
            'open': 4300.0,
            'high': 4305.0,
            'low': 4295.0,
            'close': 4298.0,
            'tick_volume': 500
        }
        mock_rates = [rates_record] * 100

        eval_time = datetime(2026, 9, 15, 14, 5, 0, tzinfo=timezone.utc)

        with patch('live_bot.datetime') as mock_datetime, patch('live_bot.mt5') as mock_mt5:
            mock_datetime.now.return_value = eval_time
            mock_datetime.fromtimestamp = datetime.fromtimestamp
            mock_datetime.strptime = datetime.strptime
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            mock_account = MagicMock()
            mock_account.equity = 5000.0
            mock_account.balance = 5000.0
            mock_account.login = 12345
            mock_mt5.account_info.return_value = mock_account
            mock_mt5.positions_get.return_value = ()
            mock_mt5.symbol_info.return_value = MagicMock(trade_contract_size=100.0, volume_min=0.01, digits=2, volume_step=0.01)
            mock_mt5.symbol_info_tick.return_value = MagicMock(bid=4297.0, ask=4297.2)
            mock_mt5.order_send.return_value = MagicMock(retcode=10009, order=999)
            mock_mt5.copy_rates_from_pos.return_value = mock_rates

            bot.detect_liquidity_sweep = MagicMock(return_value=(True, 4305.0, 5.0))

            bot.check_candle_arm_status("XAUUSD")

            self.assertTrue(bot.audit_logger.log_candle.called)
            logged_record = bot.audit_logger.log_candle.call_args[0][0]
            
            self.assertNotEqual(logged_record['decision'], "NOT_ARMED")
            self.assertEqual(logged_record['decision'], "FIRED_BAR_CLOSE_SELL")

    def test_candle_eval_blocked_when_cushion_exhausted(self):
        """Verify that when remaining cushion is exhausted, it properly blocks with BLOCKED_EXPOSURE_CAP."""
        bot = InstitutionalDCCBot.__new__(InstitutionalDCCBot)
        bot.circuit_breaker_active = False
        bot.daily_starting_equity = 5000.0
        bot.daily_cb_pct = 3.0 # Max allowed loss = $150
        bot.daily_dd_limit_pct = 4.0
        bot.risk_per_trade = 0.01
        bot.symbols = ["XAUUSD"]
        bot.tz_ist = timezone.utc
        bot.entry_start_hour_utc = 6
        bot.entry_end_hour_utc = 19
        bot.trap_hours_utc = [9, 13]
        bot.use_news_shield = False
        bot.strategy_version = "v1.2"
        bot.use_liquidity_sweep = False
        bot.entry_mode = "bar_close"
        bot.armed_states = {"XAUUSD": MagicMock(is_armed=False)}
        bot.audit_logger = MagicMock()
        bot.notifier = MagicMock()
        bot.write_live_state = MagicMock()
        bot.broker_offset = timedelta(hours=0)
        bot.active_positions = {}
        bot.notified_armed_setups = {}
        bot.dry_run = True

        n = 100
        idx = pd.date_range("2026-09-14 10:00:00", periods=n, freq='5min', tz=timezone.utc)
        df_prep = pd.DataFrame({
            'open': np.full(n, 4300.0),
            'high': np.full(n, 4305.0),
            'low': np.full(n, 4295.0),
            'close': np.full(n, 4298.0),
            'volume': np.full(n, 500),
            'ema9_5m': np.full(n, 4298.0),
            'ema20_5m': np.full(n, 4300.0),
            'ema_gap': np.full(n, 2.0),
            'vwap_5m': np.full(n, 4305.0),
            'adx_1h': np.full(n, 30.0),
            'atr_1h': np.full(n, 15.0),
            'bias_1h': np.full(n, -1),
            'ema9_1h': np.full(n, 4310.0),
            'ema20_1h': np.full(n, 4315.0),
            'h1_ema20_level': np.full(n, 4315.0),
            'swing_high_2h': np.full(n, 4350.0),
            'swing_low_2h': np.full(n, 4250.0),
        }, index=idx)

        mock_engine = MagicMock()
        mock_engine.prepare_data.return_value = df_prep
        bot.engines = {"XAUUSD": mock_engine}

        rates_record = {
            'time': 1789380000,
            'open': 4300.0,
            'high': 4305.0,
            'low': 4295.0,
            'close': 4298.0,
            'tick_volume': 500
        }
        mock_rates = [rates_record] * 100

        eval_time = datetime(2026, 9, 14, 14, 5, 0, tzinfo=timezone.utc)

        with patch('live_bot.datetime') as mock_datetime, patch('live_bot.mt5') as mock_mt5:
            mock_datetime.now.return_value = eval_time
            mock_datetime.fromtimestamp = datetime.fromtimestamp
            mock_datetime.strptime = datetime.strptime
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            # Mock account with $148 daily loss -> remaining cushion = $2 (< $10 min risk)
            mock_account = MagicMock()
            mock_account.equity = 4852.0
            mock_account.balance = 4852.0
            mock_account.login = 12345
            mock_mt5.account_info.return_value = mock_account
            mock_mt5.positions_get.return_value = ()
            mock_mt5.symbol_info.return_value = MagicMock(trade_contract_size=100.0, volume_min=0.01, digits=2, volume_step=0.01)
            mock_mt5.symbol_info_tick.return_value = MagicMock(bid=4297.0, ask=4297.2)
            mock_mt5.copy_rates_from_pos.return_value = mock_rates

            bot.detect_liquidity_sweep = MagicMock(return_value=(True, 4305.0, 5.0))

            bot.check_candle_arm_status("XAUUSD")

            self.assertTrue(bot.audit_logger.log_candle.called)
            logged_record = bot.audit_logger.log_candle.call_args[0][0]
            
            self.assertEqual(logged_record['decision'], "BLOCKED_EXPOSURE_CAP")

if __name__ == '__main__':
    unittest.main()
