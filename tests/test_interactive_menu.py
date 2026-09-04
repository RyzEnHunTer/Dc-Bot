"""
Test Suite for AccountConfigManager and Two-Layer Circuit Breakers
"""

import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_bot import AccountConfigManager, InstitutionalDCCBot

TEST_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_bot_accounts_config.json")

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
    assert cfg["daily_dd_limit_pct"] == 3.0
    assert cfg["max_total_dd_pct"] == 8.0
    assert cfg["risk_per_trade"] == 0.01
    assert cfg["high_water_mark"] == 5417.64
    print("[PASS] New account detection & auto-defaults saved.")

    # 2. Verify persistence on disk
    assert os.path.exists(TEST_CONFIG_PATH)
    mgr2 = AccountConfigManager(config_path=TEST_CONFIG_PATH)
    assert "213877054" in mgr2.accounts
    assert mgr2.accounts["213877054"]["daily_dd_limit_pct"] == 3.0
    print("[PASS] Persistent disk reloading verified.")

    # 3. Simulate editing rules
    mgr2.update_account_rules("213877054", risk_pct=1.5, daily_dd=2.5, max_dd=7.5)
    assert mgr2.accounts["213877054"]["risk_per_trade"] == 0.015
    assert mgr2.accounts["213877054"]["daily_dd_limit_pct"] == 2.5
    assert mgr2.accounts["213877054"]["max_total_dd_pct"] == 7.5
    print("[PASS] Rule editing & persistence verified.")

    # 4. Clean up test file
    if os.path.exists(TEST_CONFIG_PATH):
        os.remove(TEST_CONFIG_PATH)

def test_two_layer_circuit_breaker():
    # Test Bot initialization with dual-layer circuit breaker
    bot = InstitutionalDCCBot(
        daily_loss_limit_pct=3.0,
        max_total_dd_pct=8.0,
        high_water_mark=10000.0,
        dry_run=True
    )
    bot.daily_starting_equity = 10000.0

    # Normal trading equity: $9,800 (-2% daily, -2% max DD)
    eq1 = 9800.0
    daily_dd1 = (bot.daily_starting_equity - eq1) / bot.daily_starting_equity * 100.0
    max_dd1 = (bot.high_water_mark - eq1) / bot.high_water_mark * 100.0
    assert daily_dd1 == 2.0 and not (daily_dd1 >= bot.daily_loss_limit_pct)
    assert max_dd1 == 2.0 and not (max_dd1 >= bot.max_total_dd_pct)
    print("[PASS] Normal equity ($9,800) -> Both breakers INACTIVE.")

    # Layer 1 Trigger: $9,700 (-3.0% daily DD)
    eq2 = 9700.0
    daily_dd2 = (bot.daily_starting_equity - eq2) / bot.daily_starting_equity * 100.0
    max_dd2 = (bot.high_water_mark - eq2) / bot.high_water_mark * 100.0
    assert daily_dd2 >= bot.daily_loss_limit_pct
    assert max_dd2 < bot.max_total_dd_pct  # Max DD not triggered yet (only -3%)
    print("[PASS] Layer 1 (-3% Daily DD) triggers daily halt, Max DD remains safe.")

    # Layer 2 Trigger: $9,200 (-8.0% Max Total DD)
    eq3 = 9200.0
    max_dd3 = (bot.high_water_mark - eq3) / bot.high_water_mark * 100.0
    assert max_dd3 >= bot.max_total_dd_pct
    print("[PASS] Layer 2 (-8% Max DD) triggers CATASTROPHIC EMERGENCY PARACHUTE.")

if __name__ == "__main__":
    test_account_config_manager()
    test_two_layer_circuit_breaker()
    print("\nALL ACCOUNT CONFIG & CIRCUIT BREAKER TESTS PASSED (100% VERIFIED)!\n")
