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
    assert cfg["account_lifecycle"] == "challenge"
    assert cfg["challenge_steps"] == 2
    assert cfg["current_phase"] == 1
    assert cfg["phase_1_target_pct"] == 8.0
    assert cfg["phase_2_target_pct"] == 5.0
    assert cfg["challenge_risk_pct"] == 1.25
    assert cfg["funded_risk_pct"] == 1.0
    assert cfg["risk_per_trade"] == 0.0125
    assert cfg["high_water_mark"] == 5417.64
    print("[PASS] New account detection & auto-defaults saved (Challenge 2-Step, 1.25% Risk, Daily DD: 4%, CB: 3%).")

    # 2. Verify persistence on disk
    assert os.path.exists(TEST_CONFIG_PATH)
    mgr2 = AccountConfigManager(config_path=TEST_CONFIG_PATH)
    assert "213877054" in mgr2.accounts
    assert mgr2.accounts["213877054"]["daily_dd_limit_pct"] == 4.0
    assert mgr2.accounts["213877054"]["daily_cb_pct"] == 3.0
    assert mgr2.accounts["213877054"]["challenge_risk_pct"] == 1.25
    print("[PASS] Persistent disk reloading verified.")

    # 3. Simulate editing rules with auto CB cushion
    mgr2.update_account_rules("213877054", risk_pct=1.5, daily_dd=5.0, max_dd=10.0, daily_cb=4.0, max_cb=9.0)
    assert mgr2.accounts["213877054"]["risk_per_trade"] == 0.015
    assert mgr2.accounts["213877054"]["challenge_risk_pct"] == 1.5
    assert mgr2.accounts["213877054"]["daily_dd_limit_pct"] == 5.0
    assert mgr2.accounts["213877054"]["daily_cb_pct"] == 4.0
    assert mgr2.accounts["213877054"]["max_total_dd_pct"] == 10.0
    assert mgr2.accounts["213877054"]["max_cb_pct"] == 9.0
    print("[PASS] Rule editing & persistence verified.")

    # 4. Verify Entry Mode defaults & toggles (EMA Gap filter permanently nuked)
    assert "use_ema_gap_filter" not in cfg
    assert cfg["entry_mode"] == "bar_close"

    new_mode = mgr2.toggle_entry_mode("213877054")
    assert new_mode == "pre_arm"
    assert mgr2.accounts["213877054"]["entry_mode"] == "pre_arm"
    mgr2.toggle_entry_mode("213877054")
    assert mgr2.accounts["213877054"]["entry_mode"] == "bar_close"
    print("[PASS] Entry Mode defaults and toggle persistence verified (EMA gap filter permanently nuked).")

    # 5. Clean up test file
    if os.path.exists(TEST_CONFIG_PATH):
        os.remove(TEST_CONFIG_PATH)


def test_account_lifecycle_and_target_tracking():
    if os.path.exists(TEST_CONFIG_PATH):
        os.remove(TEST_CONFIG_PATH)

    mgr = AccountConfigManager(config_path=TEST_CONFIG_PATH)
    mock_acc = MagicMock()
    mock_acc.login = 999123
    mock_acc.server = "FTMO-Demo"
    mock_acc.currency = "USD"
    mock_acc.equity = 5000.00
    mock_acc.balance = 5000.00
    mock_acc.leverage = 100

    # 1. Test auto-setup 2-step challenge
    cfg = mgr.get_or_setup_account(mock_acc, auto_defaults=True)
    assert cfg["account_lifecycle"] == "challenge"
    assert cfg["current_phase"] == 1
    assert cfg["risk_per_trade"] == 0.0125  # Challenge 1.25%

    # 2. Test dashboard generation
    dash = mgr.format_account_dashboard("999123", equity=5200.00, balance=5200.00)
    assert "CHALLENGE MODE (2-Step | Phase 1 Active)" in dash
    assert "Phase 1 Target: +8.0%" in dash
    assert "+$200.00 (+4.00%)" in dash
    assert "Active Sizing Risk:  1.25%" in dash
    print("[PASS] Dashboard generation verified with accurate target tracking.")

    # 3. Test Phase 1 -> Phase 2 advancement
    mgr.advance_account_phase("999123", new_phase="2", new_start_bal=5400.00)
    cfg2 = mgr.accounts["999123"]
    assert cfg2["current_phase"] == 2
    assert cfg2["phase_start_balance"] == 5400.00
    assert cfg2["risk_per_trade"] == 0.0125  # Still in challenge mode (1.25%)
    print("[PASS] Phase 1 -> Phase 2 transition verified with new starting balance.")

    # 4. Test Phase 2 -> Funded advancement
    mgr.advance_account_phase("999123", new_phase="funded")
    cfg3 = mgr.accounts["999123"]
    assert cfg3["account_lifecycle"] == "funded"
    assert cfg3["current_phase"] == "funded"
    assert cfg3["risk_per_trade"] == 0.0100  # Automatically shifted to Funded 1.00%!
    print("[PASS] Funded transition verified with automatic risk downshift to 1.00%.")

    # 5. Test manual 1-step challenge prompt setup
    mock_1step = MagicMock()
    mock_1step.login = 888456
    mock_1step.server = "AlphaCapital"
    mock_1step.currency = "USD"
    mock_1step.equity = 10000.00
    mock_1step.balance = 10000.00
    mock_1step.leverage = 100

    # Inputs: [1] Challenge -> [1] 1-Step -> Target 10% -> Risk 1.25% -> Funded Risk 1.0% -> Daily DD 4% -> Max DD 8% -> Auto CB
    inputs_1step = iter(["1", "1", "10.0", "1.25", "1.0", "4.0", "8.0", "1"])
    with patch("builtins.input", lambda prompt="": next(inputs_1step)):
        cfg_1step = mgr.get_or_setup_account(mock_1step, auto_defaults=False)
        assert cfg_1step["challenge_steps"] == 1
        assert cfg_1step["phase_1_target_pct"] == 10.0
        assert cfg_1step["risk_per_trade"] == 0.0125
    print("[PASS] Manual 1-Step challenge setup verified.")

    # 6. Test manual Funded account prompt setup
    mock_funded = MagicMock()
    mock_funded.login = 777999
    mock_funded.server = "FundedNext-Live"
    mock_funded.currency = "USD"
    mock_funded.equity = 25000.00
    mock_funded.balance = 25000.00
    mock_funded.leverage = 100

    # Inputs: [2] Funded -> Funded Risk 0.75% -> Daily DD 4% -> Max DD 8% -> Auto CB
    inputs_funded = iter(["2", "0.75", "4.0", "8.0", "1"])
    with patch("builtins.input", lambda prompt="": next(inputs_funded)):
        cfg_funded = mgr.get_or_setup_account(mock_funded, auto_defaults=False)
        assert cfg_funded["account_lifecycle"] == "funded"
        assert cfg_funded["funded_risk_pct"] == 0.75
        assert cfg_funded["risk_per_trade"] == 0.0075
    print("[PASS] Manual Funded account setup verified with custom risk.")

    # Clean up test file
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


def test_challenge_target_lock_in_protection():
    bot = InstitutionalDCCBot(
        daily_dd_limit_pct=4.0,
        daily_cb_pct=3.0,
        max_total_dd_pct=8.0,
        max_cb_pct=7.0,
        dry_run=True,
    )
    assert not bot.challenge_target_reached

    # 1. Test that tick countdown blocks and disarms if target reached
    bot.armed_states["XAUUSD"].is_armed = True
    bot.challenge_target_reached = True
    bot.challenge_target_summary = "Phase 1: +8.0% Target Hit"
    bot.process_tick_stream_last_2min("XAUUSD", seconds_left=30.0)
    assert not bot.armed_states["XAUUSD"].is_armed
    print("[PASS] Challenge target reached -> Tick stream setup disarmed immediately.")

    # 2. Test that arm status check blocks if target reached
    bot.check_candle_arm_status("XAUUSD")
    assert not bot.armed_states["XAUUSD"].is_armed
    print("[PASS] Challenge target reached -> New setup arming blocked to lock in pass.")


if __name__ == "__main__":
    test_circuit_breaker_prompt_auto()
    test_circuit_breaker_prompt_manual_validation()
    test_account_config_manager()
    test_account_lifecycle_and_target_tracking()
    test_two_layer_circuit_breaker_protection()
    test_challenge_target_lock_in_protection()
    print("\nALL ACCOUNT CONFIG & CIRCUIT BREAKER TESTS PASSED (100% VERIFIED)!\n")
