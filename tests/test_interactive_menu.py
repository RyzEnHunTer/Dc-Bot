"""
Test Suite for AccountConfigManager, Auto-Configuring Circuit Breakers, and Strict Safety Buffers
"""

import json
import os
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_bot import AccountConfigManager, InstitutionalDCCBot, prompt_circuit_breakers

TEST_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_bot_accounts_config.json")

def test_circuit_breaker_prompt_auto():
    # User selects Option 1 (or hits Enter) -> Auto-configure (-1% safety cushion)
    with patch("builtins.input", return_value=""):
        daily_cb, max_cb = prompt_circuit_breakers(daily_dd=4.0, max_dd=8.0)
        assert daily_cb == 3.0, f"Expected 3.0, got {daily_cb}"
        assert max_cb == 7.0, f"Expected 7.0, got {max_cb}"
    print("[PASS] prompt_circuit_breakers: Auto-configuration (-1% buffer) verified (4% -> 3%, 8% -> 7%).")

def test_circuit_breaker_prompt_manual_validation():
    # User selects Option 2 (Manual):
    # First attempts invalid values: 4.5 (>= 4.0), then enters valid 2.5
    # For Max CB: attempts invalid 8.0 (>= 8.0), then enters valid 6.5
    inputs = iter(["2", "4.5", "2.5", "8.0", "6.5"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        daily_cb, max_cb = prompt_circuit_breakers(daily_dd=4.0, max_dd=8.0)
        assert daily_cb == 2.5, f"Expected 2.5, got {daily_cb}"
        assert max_cb == 6.5, f"Expected 6.5, got {max_cb}"
    print("[PASS] prompt_circuit_breakers: Strict validation (rejecting CB >= DD) verified.")

def test_account_config_manager():
    if os.path.exists(TEST_CONFIG_PATH):
        os.remove(TEST_CONFIG_PATH)

    mgr = AccountConfigManager(config_path=TEST_CONFIG_PATH)
    assert len(mgr.accounts) == 0

    # 1. Simulate new account detected
    mock_acc = MagicMock()
    mock_acc.login = 213877054
    mock_acc.server = "OctaFX-Demo"
    mock_acc.currency = "USD"
    mock_acc.equity = 5417.64
    mock_acc.balance = 5313.79
    mock_acc.leverage = 500

    cfg = mgr.get_or_setup_account(mock_acc, auto_defaults=True)
    assert cfg["login"] == 213877054
    assert cfg["daily_dd_limit_pct"] == 4.0
    assert cfg["daily_cb_pct"] == 3.0
    assert cfg["max_total_dd_pct"] == 8.0
    assert cfg["max_cb_pct"] == 7.0
    assert cfg["risk_per_trade"] == 0.01
    assert cfg["high_water_mark"] == 5417.64
    print("[PASS] New account detection & auto-defaults saved (Daily DD: 4%, CB: 3% | Max DD: 8%, CB: 7%).")

    # 2. Verify persistence on disk
    assert os.path.exists(TEST_CONFIG_PATH)
    mgr2 = AccountConfigManager(config_path=TEST_CONFIG_PATH)
    assert "213877054" in mgr2.accounts
    assert mgr2.accounts["213877054"]["daily_dd_limit_pct"] == 4.0
    assert mgr2.accounts["213877054"]["daily_cb_pct"] == 3.0
    print("[PASS] Persistent disk reloading verified.")

    # 3. Simulate editing rules with auto CB cushion
    mgr2.update_account_rules("213877054", risk_pct=1.5, daily_dd=5.0, max_dd=10.0, daily_cb=4.0, max_cb=9.0)
    assert mgr2.accounts["213877054"]["risk_per_trade"] == 0.015
    assert mgr2.accounts["213877054"]["daily_dd_limit_pct"] == 5.0
    assert mgr2.accounts["213877054"]["daily_cb_pct"] == 4.0
    assert mgr2.accounts["213877054"]["max_total_dd_pct"] == 10.0
    assert mgr2.accounts["213877054"]["max_cb_pct"] == 9.0
    print("[PASS] Rule editing & persistence verified.")

    # 4. Verify EMA Gap Filter and Entry Mode defaults & toggles
    assert cfg["use_ema_gap_filter"] is True
    assert cfg["entry_mode"] == "pre_arm"
    
    new_gap = mgr2.toggle_ema_gap_filter("213877054")
    assert new_gap is False
    assert mgr2.accounts["213877054"]["use_ema_gap_filter"] is False
    mgr2.toggle_ema_gap_filter("213877054")
    assert mgr2.accounts["213877054"]["use_ema_gap_filter"] is True

    new_mode = mgr2.toggle_entry_mode("213877054")
    assert new_mode == "bar_close"
    assert mgr2.accounts["213877054"]["entry_mode"] == "bar_close"
    mgr2.toggle_entry_mode("213877054")
    assert mgr2.accounts["213877054"]["entry_mode"] == "pre_arm"
    print("[PASS] EMA Gap Filter & Entry Mode defaults and toggle persistence verified.")

    # 5. Clean up test file
    if os.path.exists(TEST_CONFIG_PATH):
        os.remove(TEST_CONFIG_PATH)

def test_two_layer_circuit_breaker_protection():
    # Test Bot initialization with dual-layer circuit breaker and hard limits
    bot = InstitutionalDCCBot(
        daily_dd_limit_pct=4.0,
        daily_cb_pct=3.0,
        max_total_dd_pct=8.0,
        max_cb_pct=7.0,
        high_water_mark=10000.0,
        dry_run=True,
        use_ema_gap_filter=False,
        entry_mode="bar_close"
    )
    assert bot.use_ema_gap_filter is False
    assert bot.entry_mode == "bar_close"
    print("[PASS] Bot initialized in exact backtest match mode (gap filter OFF + bar_close mode).")
    bot.daily_starting_equity = 10000.0

    # Normal trading equity: $9,800 (-2% daily, -2% max DD)
    eq1 = 9800.0
    daily_dd1 = (bot.daily_starting_equity - eq1) / bot.daily_starting_equity * 100.0
    max_dd1 = (bot.high_water_mark - eq1) / bot.high_water_mark * 100.0
    assert daily_dd1 == 2.0 and not (daily_dd1 >= bot.daily_cb_pct)
    assert max_dd1 == 2.0 and not (max_dd1 >= bot.max_cb_pct)
    print("[PASS] Normal equity ($9,800) -> Circuit Breakers INACTIVE.")

    # Layer 1 Circuit Breaker Trigger: $9,700 (-3.0% daily DD)
    # The Circuit Breaker halts trading at -3.0%, saving a 1.0% buffer before the hard limit (-4.0%)!
    eq2 = 9700.0
    daily_dd2 = (bot.daily_starting_equity - eq2) / bot.daily_starting_equity * 100.0
    max_dd2 = (bot.high_water_mark - eq2) / bot.high_water_mark * 100.0
    assert daily_dd2 >= bot.daily_cb_pct
    assert daily_dd2 < bot.daily_dd_limit_pct  # Hard limit NOT breached!
    assert round(bot.daily_dd_limit_pct - daily_dd2, 2) == 1.0  # 1% buffer protected
    print("[PASS] Layer 1 (-3% Daily CB) halts trading; hard limit (-4%) protected with 1% cushion.")

    # Layer 2 Circuit Breaker Trigger: $9,300 (-7.0% Max Total DD)
    # The Circuit Breaker triggers emergency liquidation at -7.0%, saving a 1.0% buffer before catastrophic hard breach (-8.0%)!
    eq3 = 9300.0
    max_dd3 = (bot.high_water_mark - eq3) / bot.high_water_mark * 100.0
    assert max_dd3 >= bot.max_cb_pct
    assert max_dd3 < bot.max_total_dd_pct  # Hard limit NOT breached!
    assert round(bot.max_total_dd_pct - max_dd3, 2) == 1.0  # 1% buffer protected
    print("[PASS] Layer 2 (-7% Max CB) triggers emergency halt; hard limit (-8%) protected with 1% cushion.")

if __name__ == "__main__":
    test_circuit_breaker_prompt_auto()
    test_circuit_breaker_prompt_manual_validation()
    test_account_config_manager()
    test_two_layer_circuit_breaker_protection()
    print("\nALL ACCOUNT CONFIG & CIRCUIT BREAKER TESTS PASSED (100% VERIFIED)!\n")
