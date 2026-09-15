"""
Unit & Regression Tests for NightlyReconciler and StorageManager
"""

import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, date, timezone
from unittest.mock import patch, MagicMock

from storage_manager import StorageManager
from nightly_reconciler import NightlyReconciler


class TestStorageManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.storage = StorageManager(audit_dir=self.test_dir, retention_days=7)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_daily_report(self):
        sample_dict = {"date": "2026-09-14", "summary": {"matched_trades": 2}}
        json_p, md_p = self.storage.save_daily_report("20260914", sample_dict, "# Sample MD")

        self.assertTrue(os.path.exists(json_p))
        self.assertTrue(os.path.exists(md_p))
        latest_p = os.path.join(self.storage.audit_dir, "audit_reconciliation_latest.json")
        self.assertTrue(os.path.exists(latest_p))

    def test_7day_rolling_retention_pruning(self):
        # Create a fresh file (1 day old)
        fresh_p, _ = self.storage.save_daily_report("20260914", {"test": "fresh"})
        # Create an aged file (10 days old)
        aged_p, _ = self.storage.save_daily_report("20260901", {"test": "aged"})

        # Manually backdate the aged file's mtime to 10 days ago
        old_time = time.time() - (10 * 86400)
        os.utime(aged_p, (old_time, old_time))

        # Run pruning without cloud archival
        deleted = self.storage.prune_aged_reports(archive_before_delete=False)

        self.assertIn(aged_p, deleted)
        self.assertFalse(os.path.exists(aged_p))
        self.assertTrue(os.path.exists(fresh_p))


class TestNightlyReconciler(unittest.TestCase):
    def setUp(self):
        self.reconciler = NightlyReconciler(symbols=["XAUUSD", "NAS100"])

    def test_reconciliation_matching_logic(self):
        target_date = date(2026, 9, 14)

        bt_trades = [
            {
                "symbol": "XAUUSD",
                "direction": "SELL",
                "entry_time": "2026-09-14 14:15:00",
                "actual_entry": 4295.50,
                "net_pnl": 35.20,
            },
            {
                "symbol": "NAS100",
                "direction": "BUY",
                "entry_time": "2026-09-14 16:30:00",
                "actual_entry": 24100.0,
                "net_pnl": 52.00,
            }
        ]

        # Live deals: Only XAUUSD was executed; NAS100 was missed
        live_deals = [
            {
                "ticket": 12345678,
                "symbol": "XAUUSD",
                "type": "SELL",
                "entry_type": "ENTRY_IN",
                "time": "2026-09-14 14:15:02",
                "price": 4295.40,
                "profit": 34.80,
                "comment": "dcc_twin_a"
            }
        ]

        result = self.reconciler.reconcile(target_date, bt_trades, live_deals)

        self.assertEqual(result["summary"]["total_backtest_signals"], 2)
        self.assertEqual(result["summary"]["total_live_entries"], 1)
        self.assertEqual(result["summary"]["matched_trades"], 1)
        self.assertEqual(result["summary"]["discrepancies"], 1)
        self.assertEqual(result["summary"]["match_rate_pct"], 50.0)

        details = result["reconciliation_details"]
        self.assertEqual(details[0]["status"], "MATCHED_SUCCESS")
        self.assertEqual(details[0]["live_ticket"], 12345678)
        self.assertEqual(details[1]["status"], "MISSED_OR_FILTERED")

    def test_unexpected_live_deal_detection(self):
        target_date = date(2026, 9, 14)
        bt_trades = []  # No strategy signals

        # Live manual trade executed
        live_deals = [
            {
                "ticket": 99999999,
                "symbol": "XAUUSD",
                "type": "BUY",
                "entry_type": "ENTRY_IN",
                "time": "2026-09-14 11:00:00",
                "price": 4300.0,
                "profit": 15.0,
                "comment": "manual_trade"
            }
        ]

        result = self.reconciler.reconcile(target_date, bt_trades, live_deals)
        self.assertEqual(result["summary"]["matched_trades"], 0)
        self.assertEqual(result["summary"]["discrepancies"], 1)
        self.assertEqual(result["reconciliation_details"][0]["status"], "UNEXPECTED_LIVE_DEAL")

    def test_markdown_report_formatting(self):
        audit_result = {
            "date": "2026-09-14",
            "audit_generated_at": "2026-09-15 00:00:00 UTC",
            "summary": {
                "match_rate_pct": 100.0,
                "total_backtest_signals": 1,
                "total_live_entries": 1,
                "matched_trades": 1,
                "discrepancies": 0,
                "backtest_net_pnl": 50.0,
                "live_broker_net_pnl": 49.5,
            },
            "reconciliation_details": [
                {
                    "status": "MATCHED_SUCCESS",
                    "symbol": "XAUUSD",
                    "direction": "BUY",
                    "signal_time": "2026-09-14 10:00:00",
                    "live_ticket": 12345,
                    "backtest_pnl": 50.0,
                    "live_pnl": 49.5,
                    "discrepancy_reason": "Exact match"
                }
            ]
        }
        md = self.reconciler.generate_markdown_report(audit_result)
        self.assertIn("100.0%", md)
        self.assertIn("XAUUSD", md)
        self.assertIn("12345", md)


if __name__ == "__main__":
    unittest.main()
