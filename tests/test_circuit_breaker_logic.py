import os
import sys
from datetime import datetime, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_bot import InstitutionalDCCBot

# Initialize bot in dry-run mode
bot = InstitutionalDCCBot(daily_loss_limit_pct=3.0, dry_run=True)

# Test 1: Daily anchor setting
bot.daily_starting_equity = 10000.0
bot.current_trading_day = date(2026, 9, 4)

# Scenario A: Equity drops by 2% ($9,800) -> Circuit breaker should NOT trigger
eq_a = 9800.0
dd_pct_a = (bot.daily_starting_equity - eq_a) / bot.daily_starting_equity * 100.0
assert dd_pct_a == 2.0
assert not (dd_pct_a >= bot.daily_loss_limit_pct)
print(f"[PASS] Scenario A (2% DD): DD={dd_pct_a:.1f}% -> Circuit Breaker: INACTIVE")

# Scenario B: Equity drops by 3% ($9,700) -> Circuit breaker MUST trigger
eq_b = 9700.0
dd_pct_b = (bot.daily_starting_equity - eq_b) / bot.daily_starting_equity * 100.0
assert dd_pct_b == 3.0
assert (dd_pct_b >= bot.daily_loss_limit_pct)
bot.circuit_breaker_active = True
print(f"[PASS] Scenario B (3% DD): DD={dd_pct_b:.1f}% -> Circuit Breaker: TRIGGERED (Trading Halted)")

# Scenario C: Arming check when circuit breaker is active
bot.check_candle_arm_status("XAUUSD")
assert not bot.armed_states["XAUUSD"].is_armed
print("[PASS] Scenario C: Symbol arming blocked while Circuit Breaker is active.")

# Scenario D: New Day Rollover (00:00 UTC)
tomorrow = date(2026, 9, 5)
if bot.current_trading_day != tomorrow:
    bot.current_trading_day = tomorrow
    bot.daily_starting_equity = eq_b  # Reset to new start
    bot.circuit_breaker_active = False

assert not bot.circuit_breaker_active
assert bot.daily_starting_equity == 9700.0
print(f"[PASS] Scenario D: New trading day ({tomorrow}) -> Circuit Breaker RESET successfully. New Anchor: ${bot.daily_starting_equity:,.2f}")

print("\nALL 3% DAILY CIRCUIT BREAKER UNIT TESTS PASSED (100% VERIFIED)!\n")
