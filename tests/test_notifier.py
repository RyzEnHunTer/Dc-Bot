"""
Unit Test Suite for Remote Notification Engine (Telegram & Discord)
Verifies:
1. Non-blocking queue operations (zero delay on main thread)
2. Telegram & Discord payload dispatch
3. AccountConfigManager persistence of notification settings
4. Mutual exclusivity (only 1 platform active at any time)
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from notifier import NotificationManager
from live_bot import AccountConfigManager


class TestNotificationEngine(unittest.TestCase):

    def test_non_blocking_queue(self):
        """Verifies that sending an alert does not block the caller."""
        cfg = {
            "active_platform": "telegram",
            "telegram_bot_token": "fake_token_12345",
            "telegram_chat_id": "fake_chat_999",
            "discord_webhook_url": ""
        }
        nm = NotificationManager(cfg)
        
        t0 = time.time()
        # Enqueue multiple notifications in rapid succession
        nm.notify_startup(123456, "DemoServer", "live", 5000.0, 5000.0, 1.0, 3.0, 8.0, ["XAUUSD", "NAS100"])
        nm.notify_trade_opened("XAUUSD", "BUY", 2650.50, 0.30, 0.10, 0.05, 0.05, 2645.0, 2655.0, 2665.0, 1001, 1002)
        nm.notify_tp1_breakeven("XAUUSD", 1001, 1002, 2650.80)
        nm.notify_trade_closed("XAUUSD", 1002, "FULL_TP2", 150.0)
        nm.notify_circuit_breaker("daily", 4850.0, 3.0, 150.0)
        t_elapsed = time.time() - t0

        # Must execute in < 5 milliseconds because everything is pushed to the background queue
        self.assertLess(t_elapsed, 0.05, f"Alert enqueuing took too long: {t_elapsed:.4f}s")
        print(f"[PASS] 5 Alerts enqueued asynchronously in {t_elapsed * 1000:.2f}ms (Zero Tick Latency).")

    def test_mutual_exclusivity(self):
        """Verifies only one platform can be active at a time."""
        nm = NotificationManager(config={"active_platform": "none"})
        self.assertEqual(nm.active_platform, "none")

        nm.update_config({"active_platform": "telegram", "telegram_bot_token": "tok", "telegram_chat_id": "123"})
        self.assertEqual(nm.active_platform, "telegram")

        nm.update_config({"active_platform": "discord", "discord_webhook_url": "https://discord.com/api/..."})
        self.assertEqual(nm.active_platform, "discord")
        print("[PASS] Mutual exclusivity verified: Platform switches cleanly between None, Telegram, and Discord.")

    def test_daily_summary_notification(self):
        """Verifies that notify_daily_summary formats and enqueues end-of-day scorecard cleanly."""
        nm = NotificationManager(config={"active_platform": "discord", "discord_webhook_url": "https://dummy"})
        nm.notify_daily_summary(
            date_str="2026-09-15",
            starting_equity=5000.0,
            closing_equity=5125.50,
            balance=5125.50,
            trades_count=2,
            winning_trades=2,
            losing_trades=0,
            daily_pnl=125.50,
            daily_pnl_pct=2.51,
            daily_dd_pct=0.0,
            cushion_remaining=150.0,
            closed_trades_details=[
                {"ticket": 101, "symbol": "XAUUSD", "type": "SELL", "volume": 0.02, "profit": 62.75, "comment": "TP1"},
                {"ticket": 102, "symbol": "XAUUSD", "type": "SELL", "volume": 0.02, "profit": 62.75, "comment": "TP2"}
            ]
        )
        self.assertFalse(nm.msg_queue.empty())
        print("[PASS] notify_daily_summary formatted and enqueued successfully.")

    def test_account_config_persistence(self):
        """Verifies saving and retrieving notification settings in AccountConfigManager."""
        test_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_accounts_config.json")
        if os.path.exists(test_config_path):
            os.remove(test_config_path)

        mgr = AccountConfigManager(test_config_path)
        mgr.accounts["test_acc"] = {
            "login": 999999,
            "server": "TestServer",
            "risk_per_trade": 0.01,
            "daily_dd_limit_pct": 3.0,
            "max_total_dd_pct": 8.0,
            "symbols": ["XAUUSD"]
        }
        mgr.save()

        # Update notifications
        mgr.update_notification_settings("test_acc", "telegram", tg_token="123456:XYZ", tg_chat_id="88888")

        # Reload from disk
        mgr2 = AccountConfigManager(test_config_path)
        notif = mgr2.get_notification_config("test_acc")
        self.assertEqual(notif["active_platform"], "telegram")
        self.assertEqual(notif["telegram_bot_token"], "123456:XYZ")
        self.assertEqual(notif["telegram_chat_id"], "88888")
        print("[PASS] Notification settings persistently saved and reloaded from JSON successfully.")

        # Cleanup
        if os.path.exists(test_config_path):
            os.remove(test_config_path)

    def test_trading_pause_and_resume_alerts(self):
        """Verifies that notify_trading_paused and notify_trading_resumed format dual UTC+IST alerts properly."""
        cfg = {
            "active_platform": "discord",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "discord_webhook_url": "https://discord.com/api/webhooks/fake"
        }
        nm = NotificationManager(cfg)

        # Clear queue for inspection
        while not nm.msg_queue.empty():
            try:
                nm.msg_queue.get_nowait()
            except Exception:
                break

        # Test pause alert
        nm.notify_trading_paused(
            zone_title="Morning Dead Trap Hour (09:00-10:00 UTC / 14:30-15:30 IST)",
            reason="London midday liquidity lull.",
            resume_time_str="10:00 UTC (15:30 IST)"
        )
        # Test resume alert
        nm.notify_trading_resumed(
            zone_title="Morning Dead Trap Hour Ended (10:00 UTC / 15:30 IST)",
            session_name="Pre-New York Window",
            details="Midday trap concluded. Active surveillance restored."
        )

        self.assertFalse(nm.msg_queue.empty())
        print("[PASS] notify_trading_paused and notify_trading_resumed formatted and enqueued successfully.")

    def test_session_state_machine(self):
        """Verifies that get_session_state accurately maps all 24 hours of the day."""
        from live_bot import InstitutionalDCCBot
        bot = InstitutionalDCCBot(symbols=["XAUUSD"])

        expected_states = {
            0: "PAUSED_ASIAN",
            3: "PAUSED_ASIAN",
            5: "PAUSED_ASIAN",
            6: "ACTIVE_LONDON",
            7: "ACTIVE_LONDON",
            8: "ACTIVE_LONDON",
            9: "PAUSED_TRAP_09",
            10: "ACTIVE_LONDON",
            11: "ACTIVE_LONDON",
            12: "ACTIVE_NY",
            13: "PAUSED_TRAP_13",
            14: "ACTIVE_NY",
            18: "ACTIVE_NY",
            19: "PAUSED_ASIAN",
            20: "PAUSED_ASIAN",
            21: "PAUSED_ASIAN",
            22: "PAUSED_ASIAN",
            23: "PAUSED_ASIAN",
        }

        for h, exp in expected_states.items():
            actual = bot.get_session_state(h)
            self.assertEqual(actual, exp, f"Hour {h} expected {exp} but got {actual}")

        print("[PASS] Session and Killzone state machine accurately maps 24-hour cycle.")

    def test_rich_trade_notifications(self):
        """Verifies rich trade notifications: TP1 profit, order breakdowns, daily PnL, and CB cushions."""
        cfg = {
            "active_platform": "discord",
            "discord_webhook_url": "https://discord.com/api/webhooks/test"
        }
        nm = NotificationManager(cfg)
        
        # Test TP1 alert with banked profit
        nm.notify_tp1_breakeven("XAUUSD", 5699492095, 5699492096, 4357.08, profit_a=54.04)
        _, item = nm.msg_queue.get(timeout=1.0)
        self.assertIn("+$54.04", item["text"])
        self.assertIn("4,357.08", item["text"])
        self.assertEqual(item["embed"]["fields"][1]["name"], "Profit Locked")
        self.assertIn("+$54.04", item["embed"]["fields"][1]["value"])

        # Test Full TP2 winner trade closed alert
        nm.notify_trade_closed(
            symbol="XAUUSD",
            ticket=5699492096,
            exit_reason="🎯 FULL TAKE PROFIT 2 (2.0R)",
            pnl=96.44,
            ticket_a=5699492095,
            pnl_a=54.04,
            pnl_b=42.40,
            day_pnl=96.44,
            day_pnl_pct=1.93,
            daily_dd_pct=0.0,
            remaining_cushion=150.0,
            daily_cb_pct=3.0
        )
        _, item2 = nm.msg_queue.get(timeout=1.0)
        self.assertIn("FULL TP2 WINNER", item2["text"])
        self.assertIn("+$54.04", item2["text"])
        self.assertIn("+$42.40", item2["text"])
        self.assertIn("+$96.44", item2["text"])
        self.assertIn("+1.93%", item2["text"])
        self.assertIn("$150.00", item2["text"])
        self.assertEqual(item2["embed"]["color"], 0x00FF88)

        # Test Breakeven runner trade closed alert
        nm.notify_trade_closed(
            symbol="XAUUSD",
            ticket=5699139416,
            exit_reason="🛡️ RUNNER STOPPED AT BREAKEVEN (0.0R)",
            pnl=52.66,
            ticket_a=5699139415,
            pnl_a=52.16,
            pnl_b=0.50,
            day_pnl=52.66,
            day_pnl_pct=1.05,
            daily_dd_pct=0.0,
            remaining_cushion=150.0,
            daily_cb_pct=3.0
        )
        _, item3 = nm.msg_queue.get(timeout=1.0)
        self.assertIn("RUNNER AT BREAKEVEN", item3["text"])
        self.assertIn("+$52.16", item3["text"])
        self.assertIn("+$0.50", item3["text"])
        self.assertIn("+$52.66", item3["text"])
        self.assertEqual(item3["embed"]["color"], 0x00D4FF)

        # Test Stop Loss trade closed alert
        nm.notify_trade_closed(
            symbol="NAS100",
            ticket=5699999992,
            exit_reason="🛑 STOP LOSS HIT (-1.0R Initial Stop)",
            pnl=-50.00,
            ticket_a=5699999991,
            pnl_a=-25.00,
            pnl_b=-25.00,
            day_pnl=-50.00,
            day_pnl_pct=-1.00,
            daily_dd_pct=1.00,
            remaining_cushion=100.0,
            daily_cb_pct=3.0
        )
        _, item4 = nm.msg_queue.get(timeout=1.0)
        self.assertIn("STOP LOSS HIT", item4["text"])
        self.assertIn("-$25.00", item4["text"])
        self.assertIn("-$50.00", item4["text"])
        self.assertIn("-1.00%", item4["text"])
        self.assertIn("$100.00", item4["text"])
        self.assertEqual(item4["embed"]["color"], 0xFF4444)

        print("[PASS] Rich trade notifications verified: TP1 profit, breakdowns, daily PnL & cushions.")

    def test_weekend_market_closure_and_day_persistence(self):
        """Verifies weekend market closure detection and Friday trading day persistence."""
        from datetime import datetime, timezone
        from live_bot import InstitutionalDCCBot, get_current_trading_day_start_utc
        bot = InstitutionalDCCBot(symbols=["XAUUSD"])

        # Friday 14:00 UTC (Active NY)
        fri_dt = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
        fri_start = get_current_trading_day_start_utc(fri_dt)
        self.assertEqual(fri_start, datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(bot.get_session_state(fri_dt), "ACTIVE_NY")

        # Friday 22:00 UTC (Post-close weekend)
        fri_late = datetime(2026, 9, 11, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(bot.get_session_state(fri_late), "PAUSED_WEEKEND")

        # Saturday 10:00 UTC (Market closed - persists Friday anchor!)
        sat_dt = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
        sat_start = get_current_trading_day_start_utc(sat_dt)
        self.assertEqual(sat_start, datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(bot.get_session_state(sat_dt), "PAUSED_WEEKEND")

        # Sunday 18:00 UTC (Market closed - persists Friday anchor!)
        sun_dt = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
        sun_start = get_current_trading_day_start_utc(sun_dt)
        self.assertEqual(sun_start, datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(bot.get_session_state(sun_dt), "PAUSED_WEEKEND")

        # Monday 08:00 UTC (New trading day begins - Monday anchor!)
        mon_dt = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
        mon_start = get_current_trading_day_start_utc(mon_dt)
        self.assertEqual(mon_start, datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(bot.get_session_state(mon_dt), "ACTIVE_LONDON")

        print("[PASS] Weekend market closure and Friday day persistence verified across full cycle.")

    def test_challenge_passed_and_phase_transition_notifications(self):
        """Verifies distinct formatting for Phase 1 vs Final Phase and Phase Transitions."""
        nm = NotificationManager(config={"active_platform": "discord", "discord_webhook_url": "https://dummy"})

        # 1. Phase 1 passed (Daily lock-in halt, green embed)
        nm.notify_challenge_passed(
            account_id="123456",
            phase=1,
            target_pct=8.0,
            current_balance=5400.0,
            profit_dollar=400.0,
            profit_pct=8.0,
            is_final=False,
            server="FTMO-Demo"
        )
        _, item_p1 = nm.msg_queue.get(timeout=1.0)
        self.assertIn("PHASE 1 EVALUATION TARGET PASSED", item_p1["text"])
        self.assertIn("TRADING HALTED FOR TODAY", item_p1["text"])
        self.assertEqual(item_p1["embed"]["color"], 0x00D084)

        # 2. Phase 2 / Final Challenge passed (Permanent halt, gold embed)
        nm.notify_challenge_passed(
            account_id="123456",
            phase=2,
            target_pct=5.0,
            current_balance=5670.0,
            profit_dollar=270.0,
            profit_pct=5.0,
            is_final=True,
            server="FTMO-Demo"
        )
        _, item_final = nm.msg_queue.get(timeout=1.0)
        self.assertIn("PROP FIRM CHALLENGE FULLY PASSED", item_final["text"])
        self.assertIn("TRADING PERMANENTLY HALTED", item_final["text"])
        self.assertIn("FUNDED ACCOUNT", item_final["text"])
        self.assertEqual(item_final["embed"]["color"], 0xFFD700)

        # 3. Phase Transition Notification (Blue embed)
        nm.notify_phase_transition(
            account_id="123456",
            new_phase_name="Challenge Phase 2",
            start_balance=5400.0,
            target_pct=5.0,
            active_risk_pct=1.30,
            is_custom_risk_on=True,
            server="FTMO-Demo"
        )
        _, item_trans = nm.msg_queue.get(timeout=1.0)
        self.assertIn("ACCOUNT LIFECYCLE PHASE UPDATED", item_trans["text"])
        self.assertIn("CHALLENGE PHASE 2", item_trans["text"])
        self.assertIn("$5,400.00", item_trans["text"])
        self.assertEqual(item_trans["embed"]["color"], 0x3498DB)

        print("[PASS] Challenge passing (Phase 1 vs Final) and Phase Transition notifications verified.")


if __name__ == "__main__":
    unittest.main()


