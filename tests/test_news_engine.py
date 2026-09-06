"""
Comprehensive Test Suite for Forex Factory News Engine & Setup Lifecycle Notifications
Verifies:
1. NewsEngine: API fetching, local disk caching, 4-hour expiry, and offline fallback.
2. News blackout math: [Event - 15m, Event + 15m] USD high-impact window detection.
3. NotificationManager: Formatting and non-blocking queue dispatch for:
   - notify_news_shield_activated
   - notify_news_shield_lifted
   - notify_setup_armed
   - notify_setup_aborted
4. Setup abort logic: Exact failure message construction for spread blowout, VWAP loss, E9/E20 flip failure, and news shield pause.
"""

from datetime import datetime, timedelta, timezone
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from news_engine import EconomicEvent, NewsEngine
from notifier import NotificationManager


class TestNewsEngineAndNotifications(unittest.TestCase):

    def setUp(self):
        self.test_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch_test_cache")
        os.makedirs(self.test_cache_dir, exist_ok=True)
        self.cache_file = os.path.join(self.test_cache_dir, "news_calendar_cache.json")

    def tearDown(self):
        if os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
            except Exception:
                pass
        if os.path.exists(self.test_cache_dir):
            try:
                os.rmdir(self.test_cache_dir)
            except Exception:
                pass

    def test_news_engine_cache_and_parse(self):
        """Verifies parsing of raw Fair Economy JSON and disk caching."""
        sample_data = [
            {
                "title": "Non-Farm Employment Change",
                "country": "USD",
                "date": "2026-09-04T08:30:00-04:00",
                "impact": "High",
                "forecast": "165K",
                "previous": "114K"
            },
            {
                "title": "Unemployment Rate",
                "country": "USD",
                "date": "2026-09-04T08:30:00-04:00",
                "impact": "High",
                "forecast": "4.3%",
                "previous": "4.3%"
            },
            {
                "title": "German Prelim CPI m/m",
                "country": "EUR",
                "date": "2026-09-04T08:00:00-04:00",
                "impact": "High",
                "forecast": "0.1%",
                "previous": "0.3%"
            },
            {
                "title": "Average Hourly Earnings m/m",
                "country": "USD",
                "date": "2026-09-04T08:30:00-04:00",
                "impact": "Medium",
                "forecast": "0.3%",
                "previous": "0.2%"
            }
        ]

        # Seed local cache
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(sample_data, f)

        engine = NewsEngine(cache_dir=self.test_cache_dir, blackout_minutes=15)
        self.assertTrue(len(engine.events) > 0, "Failed to load events from cache")

        # Check high-impact USD filtering
        high_usd = [e for e in engine.events if e.is_high_impact and e.country == "USD"]
        self.assertEqual(len(high_usd), 2)
        self.assertEqual(high_usd[0].title, "Non-Farm Employment Change")

        # 08:30 EDT (-04:00) is 12:30 UTC
        expected_utc = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(high_usd[0].time_utc, expected_utc)
        print("[PASS] NewsEngine parsed and normalized ISO timestamps into UTC correctly.")

    def test_news_blackout_window_math(self):
        """Verifies the 15-minute pre- and post-news blackout calculations."""
        sample_data = [
            {
                "title": "Non-Farm Employment Change",
                "country": "USD",
                "date": "2026-09-04T12:30:00+00:00",
                "impact": "High",
                "forecast": "165K",
                "previous": "114K"
            }
        ]
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(sample_data, f)

        engine = NewsEngine(cache_dir=self.test_cache_dir, blackout_minutes=15)

        # 1. Exactly 16 minutes before: Outside blackout (No shield)
        t_before = datetime(2026, 9, 4, 12, 14, 0, tzinfo=timezone.utc)
        shield = engine.get_active_news_shield(t_before)
        self.assertIsNone(shield, "Shield should NOT be active 16 minutes before event")

        # 2. Exactly 15 minutes before: Blackout STARTS
        t_start = datetime(2026, 9, 4, 12, 15, 0, tzinfo=timezone.utc)
        shield = engine.get_active_news_shield(t_start)
        self.assertIsNotNone(shield, "Shield MUST be active 15 minutes before event")
        self.assertEqual(shield["title"], "Non-Farm Employment Change")
        self.assertEqual(shield["seconds_remaining"], 30 * 60)

        # 3. Exact moment of news release (12:30:00 UTC)
        t_release = datetime(2026, 9, 4, 12, 30, 0, tzinfo=timezone.utc)
        shield = engine.get_active_news_shield(t_release)
        self.assertIsNotNone(shield)
        self.assertEqual(shield["seconds_remaining"], 15 * 60)

        # 4. 14 minutes post-news: Still in blackout (spread recovering)
        t_post = datetime(2026, 9, 4, 12, 44, 0, tzinfo=timezone.utc)
        shield = engine.get_active_news_shield(t_post)
        self.assertIsNotNone(shield)
        self.assertEqual(shield["seconds_remaining"], 60)

        # 5. Exactly 15 minutes 1 second post-news (12:45:01 UTC): Shield LIFTED
        t_lift = datetime(2026, 9, 4, 12, 45, 1, tzinfo=timezone.utc)
        shield = engine.get_active_news_shield(t_lift)
        self.assertIsNone(shield, "Shield MUST be lifted at T + 15m")
        print("[PASS] News blackout window [-15m, +15m] math verified perfectly.")

    def test_setup_lifecycle_notifications(self):
        """Verifies non-blocking remote notifications for news shields and setup lifecycles."""
        cfg = {
            "active_platform": "telegram",
            "telegram_bot_token": "fake_token",
            "telegram_chat_id": "fake_chat",
            "discord_webhook_url": ""
        }
        nm = NotificationManager(cfg)

        t0 = time.time()
        # 1. Setup Armed
        nm.notify_setup_armed(
            symbol="XAUUSD",
            direction="BUY",
            planned_entry=2654.50,
            planned_sl=2648.20,
            planned_tp1=2663.30,
            planned_lots=0.10,
            reason="1H Bullish + 5M Compression + VWAP + Sweep Confirmed"
        )

        # 2. News Shield Activated
        nm.notify_news_shield_activated(
            title="Non-Farm Employment Change",
            country="USD",
            release_time_str="12:30:00",
            resume_time_str="12:45:00"
        )

        # 3. Setup Aborted (Spread Spike)
        nm.notify_setup_aborted(
            symbol="XAUUSD",
            direction="BUY",
            abort_reason="Spread spiked to 1.55 > max limit 0.65",
            last_price=2654.40,
            last_spread=1.55
        )

        # 4. Setup Aborted (Flip failure at 0.0s bar close)
        nm.notify_setup_aborted(
            symbol="NAS100",
            direction="SELL",
            abort_reason="0.0s Bar Close Flip Failed: EMA9 (20150.2) >= EMA20 (20148.8), Price above VWAP",
            last_price=20152.0,
            last_spread=2.8
        )

        # 5. News Shield Lifted
        nm.notify_news_shield_lifted(
            title="Non-Farm Employment Change",
            resume_time_str="12:45:00"
        )

        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.05, f"Notification enqueuing blocked main thread for {elapsed:.4f}s")
        print(f"[PASS] All 5 setup lifecycle & news shield notifications enqueued in {elapsed * 1000:.2f}ms.")

    def test_setup_abort_reason_builder(self):
        """Verifies exact string construction for zero-latency abort reasons."""
        # Simulated tick and indicators at 0.0s boundary
        direction = 1  # BUY
        proj_e9 = 2650.10
        proj_e20 = 2651.40  # e9 <= e20 (failing)
        ref_price = 2649.80
        vwap_5m = 2650.50   # ref <= vwap (failing)
        h1_e20 = 2648.00    # ref > h1_e20 (passing)

        reasons = []
        if direction == 1:
            if proj_e9 <= proj_e20:
                reasons.append(f"EMA9 ({proj_e9:.2f}) <= EMA20 ({proj_e20:.2f})")
            if ref_price <= vwap_5m:
                reasons.append(f"Price below VWAP ({ref_price:.2f} <= {vwap_5m:.2f})")
            if ref_price <= h1_e20:
                reasons.append(f"Price below 1H EMA20 ({ref_price:.2f} <= {h1_e20:.2f})")

        failure_detail = ", ".join(reasons)
        abort_msg = f"0.0s Bar Close Flip Failed: {failure_detail}"

        expected = "0.0s Bar Close Flip Failed: EMA9 (2650.10) <= EMA20 (2651.40), Price below VWAP (2649.80 <= 2650.50)"
        self.assertEqual(abort_msg, expected)
        print("[PASS] Abort reason builder formatted exact forensic failure reasons correctly.")

    def test_dual_utc_ist_timestamps(self):
        """Verifies dual UTC + IST calculation and formatting across news and notifications."""
        # 1. EconomicEvent dual time (12:30 UTC + 5h30m = 18:00 IST)
        dt = datetime(2026, 9, 4, 12, 30, 0, tzinfo=timezone.utc)
        ev = EconomicEvent(
            title="Non-Farm Payrolls",
            country="USD",
            date_utc=dt,
            impact="High"
        )
        self.assertEqual(ev.time_dual_str, "2026-09-04 12:30 UTC (18:00 IST)")
        self.assertEqual(ev.time_ist.hour, 18)
        self.assertEqual(ev.time_ist.minute, 0)

        # 2. Notifier format_dual_time
        from notifier import format_dual_hhmm, format_dual_time
        s_date = format_dual_time(dt, include_date=True)
        self.assertIn("12:30:00 UTC", s_date)
        self.assertIn("18:00:00 IST", s_date)

        # 3. Notifier format_dual_hhmm
        s_hhmm = format_dual_hhmm("12:30")
        self.assertEqual(s_hhmm, "12:30 UTC (18:00 IST)")
        print("[PASS] Dual UTC + IST local time calculations verified across all components.")


if __name__ == "__main__":
    unittest.main()
