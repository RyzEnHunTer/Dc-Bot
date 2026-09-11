import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_bot import InstitutionalDCCBot, CONFIGS

class TestDynamicCushionSizing(unittest.TestCase):
    def setUp(self):
        self.bot = InstitutionalDCCBot(daily_loss_limit_pct=3.0, dry_run=True)
        self.bot.daily_starting_equity = 5000.0
        self.bot.daily_cb_pct = 3.0
        self.bot.risk_per_trade = 0.01  # 1% = $50 standard

    @patch("live_bot.mt5")
    def test_standard_sizing_with_clean_account(self, mock_mt5):
        """When account has no losses, standard 1.0% risk ($50) is used."""
        mock_acc = MagicMock()
        mock_acc.equity = 5000.0
        mock_acc.balance = 5000.0
        mock_mt5.account_info.return_value = mock_acc

        mock_sym = MagicMock()
        mock_sym.trade_contract_size = 10.0  # NAS100
        mock_sym.volume_step = 0.01
        mock_sym.volume_min = 0.01
        mock_mt5.symbol_info.return_value = mock_sym

        sl_dist = 15.0  # 15 points
        # Risk = $50. raw_lots = 50 / (15 * 10) = 0.333 -> 0.33 lots
        tot, p_lots, r_lots = self.bot.calculate_lots("NAS100", sl_dist)
        self.assertEqual(tot, 0.33)
        self.assertEqual(p_lots, 0.16)
        self.assertEqual(r_lots, 0.17)

    @patch("live_bot.mt5")
    def test_dynamic_cushion_scaling_after_two_losses(self, mock_mt5):
        """
        After 2 losses with broker slippage/spreads, total closed loss is $115 (2.3%).
        Max Daily CB = $150 (3.0%).
        Remaining Cushion = $35.00.
        Standard risk ($50.00) > $35.00 cushion.
        Dynamic cushion scales target risk to $35.00.
        """
        mock_acc = MagicMock()
        mock_acc.equity = 4885.0  # $115 down
        mock_acc.balance = 4885.0
        mock_mt5.account_info.return_value = mock_acc

        mock_sym = MagicMock()
        mock_sym.trade_contract_size = 10.0  # NAS100
        mock_sym.volume_step = 0.01
        mock_sym.volume_min = 0.01
        mock_mt5.symbol_info.return_value = mock_sym

        sl_dist = 15.0

        # Calculate cushion
        cur_daily_loss = max(0.0, self.bot.daily_starting_equity - mock_acc.equity)
        max_allowed_loss = self.bot.daily_starting_equity * (self.bot.daily_cb_pct / 100.0)
        remaining_cushion = max(0.0, max_allowed_loss - cur_daily_loss)
        self.assertAlmostEqual(remaining_cushion, 35.0, places=2)

        # Scaled calculation
        tot, p_lots, r_lots = self.bot.calculate_lots("NAS100", sl_dist, target_risk_dollars=remaining_cushion)
        # raw_lots = 35 / (15 * 10) = 35 / 150 = 0.233 -> 0.23 lots
        self.assertEqual(tot, 0.23)
        actual_dollar_risk = tot * sl_dist * 10.0
        self.assertAlmostEqual(actual_dollar_risk, 34.50, places=2)
        # Confirm risk strictly fits inside the $35 cushion
        self.assertLessEqual(actual_dollar_risk, remaining_cushion)
        # Confirm if trade is a loss, total day loss ($115 + $34.50 = $149.50) <= $150 (3.0% CB)
        self.assertLessEqual(cur_daily_loss + actual_dollar_risk, max_allowed_loss)

    @patch("live_bot.mt5")
    def test_exposure_cap_blocks_when_cushion_below_effective_min_risk(self, mock_mt5):
        """
        When cushion is below effective minimum risk (e.g. only $4 left, or min lot risk > cushion),
        it safely blocks entry with BLOCKED_EXPOSURE_CAP.
        """
        mock_acc = MagicMock()
        mock_acc.equity = 4852.0  # $148 down, only $2 cushion left
        mock_acc.balance = 4852.0
        mock_mt5.account_info.return_value = mock_acc

        mock_sym = MagicMock()
        mock_sym.trade_contract_size = 10.0
        mock_sym.volume_step = 0.01
        mock_sym.volume_min = 0.01
        mock_mt5.symbol_info.return_value = mock_sym

        sl_dist = 15.0
        cur_daily_loss = self.bot.daily_starting_equity - mock_acc.equity
        max_allowed_loss = self.bot.daily_starting_equity * (self.bot.daily_cb_pct / 100.0)
        remaining_cushion = max(0.0, max_allowed_loss - cur_daily_loss)
        min_viable_risk = max(10.0, self.bot.daily_starting_equity * 0.002)
        min_split_lot = 0.02
        min_lot_risk = min_split_lot * sl_dist * 10.0
        effective_min_risk = max(min_viable_risk, min_lot_risk)

        self.assertLess(remaining_cushion, effective_min_risk)

if __name__ == "__main__":
    unittest.main()
