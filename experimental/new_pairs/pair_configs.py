"""
Configuration specifications for candidate pairs being evaluated for chop hedging.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class CandidateConfig:
    symbol: str
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    adx_min: float
    atr_sl_multiplier: float
    tp1_rr: float
    tp2_rr: float
    max_allowed_spread: float


CANDIDATE_CONFIGS: Dict[str, CandidateConfig] = {
    "EURUSD": CandidateConfig(
        symbol="EURUSD",
        point=1e-5,
        tick_size=1e-5,
        tick_value=1.0,
        contract_size=100000.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.2,
        max_allowed_spread=0.00025,
    ),
    "GBPUSD": CandidateConfig(
        symbol="GBPUSD",
        point=1e-5,
        tick_size=1e-5,
        tick_value=1.0,
        contract_size=100000.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.2,
        max_allowed_spread=0.00030,
    ),
    "USDJPY": CandidateConfig(
        symbol="USDJPY",
        point=0.001,
        tick_size=0.001,
        tick_value=0.6515,
        contract_size=100000.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.2,
        max_allowed_spread=0.035,
    ),
    "GBPJPY": CandidateConfig(
        symbol="GBPJPY",
        point=0.001,
        tick_size=0.001,
        tick_value=0.6515,
        contract_size=100000.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.2,
        max_allowed_spread=0.045,
    ),
    "GER40": CandidateConfig(
        symbol="GER40",
        point=0.1,
        tick_size=0.1,
        tick_value=1.1637,
        contract_size=10.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=2.5,
    ),
    "US30": CandidateConfig(
        symbol="US30",
        point=0.1,
        tick_size=0.1,
        tick_value=1.0,
        contract_size=10.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=4.0,
    ),
    "XTIUSD": CandidateConfig(
        symbol="XTIUSD",
        point=0.01,
        tick_size=0.01,
        tick_value=0.1,
        contract_size=1000.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=0.05,
    ),
    "BTCUSD": CandidateConfig(
        symbol="BTCUSD",
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        contract_size=1.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=25.0,
    ),
    "UK100": CandidateConfig(
        symbol="UK100",
        point=0.1,
        tick_size=0.1,
        tick_value=1.3553,
        contract_size=10.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=2.0,
    ),
    "JPN225": CandidateConfig(
        symbol="JPN225",
        point=1.0,
        tick_size=1.0,
        tick_value=0.6509,
        contract_size=100.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=6.0,
    ),
    "XAGUSD": CandidateConfig(
        symbol="XAGUSD",
        point=0.001,
        tick_size=0.001,
        tick_value=5.0,
        contract_size=5000.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=0.04,
    ),
    "EURJPY": CandidateConfig(
        symbol="EURJPY",
        point=0.001,
        tick_size=0.001,
        tick_value=0.6509,
        contract_size=100000.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=0.02,
    ),
    "AUDJPY": CandidateConfig(
        symbol="AUDJPY",
        point=0.001,
        tick_size=0.001,
        tick_value=0.6509,
        contract_size=100000.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=0.02,
    ),
    "SPX500": CandidateConfig(
        symbol="SPX500",
        point=0.1,
        tick_size=0.1,
        tick_value=0.1,
        contract_size=10.0,
        adx_min=15.0,
        atr_sl_multiplier=1.0,
        tp1_rr=1.4,
        tp2_rr=2.0,
        max_allowed_spread=0.5,
    ),
}
