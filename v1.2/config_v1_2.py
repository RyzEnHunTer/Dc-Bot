"""
DCC v1.2 TripleGuard Configuration
Locked & Validated Configuration for Gold (XAUUSD) and Nasdaq (NAS100)
"""

from dataclasses import dataclass
from datetime import time
from typing import Dict, List


@dataclass
class SymbolConfigV12:
    symbol: str
    tick_size: float
    tick_val: float
    contract_size: float
    atr_sl_multiplier: float
    tp1_rr: float
    tp2_rr: float
    adx_min: float = 15.0
    comm_per_lot: float = 5.0
    digits: int = 2


@dataclass
class TripleGuardShieldConfig:
    min_1h_stretch: float = 0.40   # 1H Anti-Chop Floor (>= 0.40x ATR)
    max_1h_adx: float = 45.0       # 1H ADX Exhaustion Ceiling (<= 45.0)
    max_5m_chase: float = 0.50     # 5M No-Chase Guard (<= 0.50x ATR)
    check_2h_room: bool = False    # 2H Swing Room Rule permanently bypassed


@dataclass
class CalendarShieldConfig:
    block_monday_pm: bool = True               # Blocks Monday US session
    monday_pm_blocked_hours: List[int] = None  # [14, 15, 16, 17] UTC
    friday_enabled: bool = True                # Friday trading enabled
    friday_min_hour: int = 7                   # 07:00 UTC (avoids 06:00 pre-London trap)
    friday_max_hour: int = 18                  # 18:00 UTC (avoids pre-weekend rollover)
    session_start_hour: int = 6                # 06:00 UTC
    session_end_hour: int = 19                 # 19:00 UTC
    dead_trap_hours: List[int] = None          # [9, 13] UTC institutional dead trap hours

    def __post_init__(self):
        if self.monday_pm_blocked_hours is None:
            self.monday_pm_blocked_hours = [14, 15, 16, 17]
        if self.dead_trap_hours is None:
            self.dead_trap_hours = [9, 13]


@dataclass
class RiskGovernanceConfig:
    account_capital: float = 5000.0
    risk_per_trade_pct: float = 0.01          # 1.0% ($50 on $5,000)
    daily_circuit_breaker_pct: float = 0.03   # 3.0% ($150 on $5,000)
    max_consecutive_daily_losses: int = 2     # Halt after 2 stop-outs
    partial_lot_ratio: float = 0.50           # 50% TP1
    runner_lot_ratio: float = 0.50            # 50% TP2
    move_runner_to_be: bool = True            # Move SL to breakeven when TP1 hits


SYMBOL_CONFIGS_V12: Dict[str, SymbolConfigV12] = {
    "XAUUSD": SymbolConfigV12(
        symbol="XAUUSD",
        tick_size=0.01,
        tick_val=1.0,
        contract_size=100.0,
        atr_sl_multiplier=0.90,
        tp1_rr=1.40,
        tp2_rr=2.20,
        adx_min=15.0,
        comm_per_lot=5.0,
        digits=2
    ),
    "NAS100": SymbolConfigV12(
        symbol="NAS100",
        tick_size=0.1,
        tick_val=0.1,
        contract_size=10.0,
        atr_sl_multiplier=1.00,
        tp1_rr=1.50,
        tp2_rr=2.00,
        adx_min=15.0,
        comm_per_lot=5.0,
        digits=1
    )
}

TRIPLEGUARD_SHIELD = TripleGuardShieldConfig()
CALENDAR_SHIELD = CalendarShieldConfig()
RISK_GOVERNANCE = RiskGovernanceConfig()
