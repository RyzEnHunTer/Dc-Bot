"""
Trade Verdict Classifier for DCC Trading Strategy.
Analyzes mathematical trade profiles and outputs institutional diagnostic autopsies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from .forensic_extractor import TradeForensicProfile


@dataclass
class TradeForensicVerdict:
    ticket: Optional[int]
    symbol: str
    direction: str
    outcome: str
    primary_verdict: str
    verdict_title: str
    severity: str  # "CRITICAL", "MODERATE", "EXECUTION_NOISE", "IDEAL_WIN"
    root_cause: str
    contributing_factors: List[str]
    optimization_recommendation: str
    metrics_summary: Dict[str, Any] = field(default_factory=dict)


class TradeVerdictClassifier:
    """Classifies any DCC trade into institutional strategy failure/success modes."""

    @classmethod
    def classify(cls, p: TradeForensicProfile) -> TradeForensicVerdict:
        contributing: List[str] = []
        is_win = (p.realized_pnl > 0.05 or "WIN" in p.outcome)

        # ---------------------------------------------------------------------
        # Case A: Winning Trade Classifications (Winning Edge Autopsy)
        # ---------------------------------------------------------------------
        if is_win:
            if p.mfe_r >= 2.0 and p.mae_r <= 0.35:
                verdict = "WIN_CLEAN_MOMENTUM"
                title = "🚀 Clean Institutional Momentum Expansion"
                severity = "IDEAL_WIN"
                root_cause = (
                    f"Textbook institutional execution: Sweep rejection was instantaneous, higher-timeframe "
                    f"order flow provided massive tailwind, and the position absorbed negligible adverse heat ({p.mae_r:.2f}R)."
                )
                recommendation = "Institutional A+ signature. Prioritize setups displaying this zero-heat excursion velocity."
            elif p.rvol_5m >= 1.4 and (p.sweep_quality_score >= 65.0 or p.is_major_swing_sweep):
                verdict = "WIN_LIQUIDITY_RUNAWAY"
                title = "⚡ Liquidity Vacuum Runaway Expansion"
                severity = "IDEAL_WIN"
                root_cause = (
                    f"Aggressive institutional stop clearing: Trigger bar expanded with {p.rvol_5m:.2f}x 20-SMA volume "
                    f"following a high-quality liquidity grab ({p.sweep_quality_score:.0f}/100 LSQI), triggering an immediate momentum cascade."
                )
                recommendation = "High relative volume (>1.4x) post-sweep is a confirmed statistical edge multiplier. Scale runners aggressively on these setups."
            elif p.h1_adx >= 22.0 and p.h1_adx_slope > 0.0 and p.h1_ema20_stretch_ratio <= 1.0:
                verdict = "WIN_TREND_CONTINUATION"
                title = "🌊 Strong Higher-Timeframe Trend Continuation"
                severity = "IDEAL_WIN"
                root_cause = (
                    f"Optimal trend alignment: 1H ADX was strong and rising ({p.h1_adx:.1f}, slope: {p.h1_adx_slope:+.2f}) "
                    f"while price entered at healthy equilibrium ({p.h1_ema20_stretch_ratio:.2f}x ATR from 1H EMA20), carrying trade to targets."
                )
                recommendation = "Entries with rising 1H ADX and EMA stretch under 1.0x ATR possess the highest win conversion rate."
            elif p.h1_ema20_stretch_ratio <= 0.85 and p.sweep_quality_score >= 50.0:
                verdict = "WIN_MEAN_REVERSION_BOUNCE"
                title = "🎯 High-Quality Mean Reversion Capture"
                severity = "IDEAL_WIN"
                root_cause = (
                    f"Precision value entry: Setup triggered with ample room back to the 1H EMA20 "
                    f"({p.h1_ema20_stretch_ratio:.2f}x ATR) and clean liquidity grab confirmation ({p.sweep_quality_score:.0f}/100 LSQI)."
                )
                recommendation = "Mean-reversion bounce verified. Maintain 1H EMA stretch requirement under 0.9x ATR."
            else:
                verdict = "WIN_SCRATCH_SECURED"
                title = "🛡️ Partial Banked & Breakeven Protection"
                severity = "IDEAL_WIN"
                root_cause = (
                    f"First target (TP1 at {p.tp1_price:.2f}) was secured cleanly; runner was protected by automated "
                    f"Breakeven shift, locking in profits while eliminating downside risk."
                )
                recommendation = "Twin-ticket architecture performed as intended, locking in profit while insulating the account from adverse reversals."

            # Winning Edge Confluences & Catalysts
            if p.rvol_5m >= 1.2:
                contributing.append(f"Volume Surge: Trigger bar expanded with {p.rvol_5m:.2f}x 20-SMA volume.")
            if p.sweep_detected and p.is_major_swing_sweep:
                contributing.append(f"Macro Liquidity Grab: Swept major institutional 2H swing level ({p.sweep_level:.2f}).")
            elif p.sweep_detected:
                contributing.append(f"Clean Liquidity Sweep: Cleared {p.swept_pts:.2f} pts of resting liquidity (LSQI: {p.sweep_quality_score:.0f}/100).")
            if p.mae_r <= 0.25:
                contributing.append(f"Minimal Heat: Position experienced virtually zero drawdown ({p.mae_r:.2f}R MAE).")
            if p.h1_ema20_stretch_ratio <= 0.75:
                contributing.append(f"Compression Equilibrium: Entry was tight to 1H EMA20 ({p.h1_ema20_stretch_ratio:.2f}x ATR).")
            if p.mfe_r >= 2.0:
                contributing.append(f"Favorable Extension: Trade expanded to a peak excursion of +{p.mfe_r:.2f}R.")

            return TradeForensicVerdict(
                ticket=p.ticket,
                symbol=p.symbol,
                direction=p.direction,
                outcome=p.outcome,
                primary_verdict=verdict,
                verdict_title=title,
                severity=severity,
                root_cause=root_cause,
                contributing_factors=contributing,
                optimization_recommendation=recommendation,
                metrics_summary={
                    "PnL ($)": round(p.realized_pnl, 2),
                    "PnL (R)": p.pnl_r,
                    "MFE": f"{p.mfe_r:.2f}R",
                    "MAE": f"{p.mae_r:.2f}R",
                    "1H EMA Stretch": f"{p.h1_ema20_stretch_ratio}x ATR",
                    "LSQI Score": f"{p.sweep_quality_score:.0f}/100",
                    "RVol": f"{p.rvol_5m:.2f}x",
                    "Bars Held": p.bars_held
                }
            )

        # ---------------------------------------------------------------------
        # Case B: Losing Trade Classifications (Forensic Autopsy)
        # ---------------------------------------------------------------------

        # Check 1: HTF Exhaustion Trap (Overextended from 1H EMA20 or declining ADX)
        if p.is_htf_overextended or p.is_adx_exhausted:
            verdict = "LOSS_HTF_EXHAUSTION_TRAP"
            title = "⚠️ 1H Rubber-Band Trend Exhaustion"
            severity = "CRITICAL"
            root_cause = (
                f"Entry was severely overextended ({p.h1_ema20_stretch_ratio:.2f}x 1H ATR away from 1H EMA20) "
                f"while 1H ADX was actively rolling over ({p.h1_adx:.1f}, slope: {p.h1_adx_slope:+.2f}). "
                f"The trade bought/sold into exhausted momentum and was snapped back by mean-reversion counter-flow."
            )
            recommendation = "Implement a strict 1H EMA20 stretch ceiling: Block new entries when distance from 1H EMA20 exceeds 1.35x ATR."

        # Check 2: Near-Miss Reversal (Almost reached TP1 before catastrophic dump)
        elif p.near_miss_tp1:
            verdict = "LOSS_NEAR_MISS_REVERSAL"
            title = "💔 Near-Miss Reversal (TP1 Target Miss)"
            severity = "MODERATE"
            root_cause = (
                f"Trade showed strong initial momentum, reaching a peak of +{p.mfe_r:.2f}R "
                f"(within a few ticks of the {p.tp1_price:.2f} TP1 target), but failed to fill before a violent reversal stopped it out."
            )
            recommendation = "Evaluate dynamic trailing SL or moving SL to partial breakeven once favorable excursion crosses +1.0R."

        # Check 3: Runaway Counter-Trend Momentum Breach
        elif p.sweep_penetration_ratio > 0.85:
            verdict = "LOSS_RUNAWAY_MOMENTUM_BREACH"
            title = "🌊 Runaway Counter-Trend Momentum Blowout"
            severity = "CRITICAL"
            root_cause = (
                f"The liquidity sweep penetrated {p.swept_pts:.2f} pts ({p.sweep_penetration_ratio:.2f}x ATR) "
                f"without sharp rejection. Market order flow was actively trending against the bias rather than hunting stops."
            )
            recommendation = "Reject sweep triggers if sweep penetration exceeds 0.75x 5M ATR (distinguishes stop hunts from momentum breakouts)."

        # Check 4: Weak / Low-Liquidity Sweep
        elif not p.sweep_detected or p.sweep_quality_score < 40.0:
            verdict = "LOSS_WEAK_LIQUIDITY_SWEEP"
            title = "🍂 Low-Quality Liquidity Pool (Fakeout Sweep)"
            severity = "MODERATE"
            root_cause = (
                f"Setup triggered on a weak internal swing with negligible stop liquidity "
                f"(LSQI Score: {p.sweep_quality_score:.0f}/100). Institutional participants were not active."
            )
            recommendation = "Require liquidity sweeps to clear at least a 12-bar swing high/low or prioritize 2H macro liquidity pools."

        # Check 5: Stale Chop / Compression Death
        elif p.bars_held >= 10 and p.mfe_r < 0.6:
            verdict = "LOSS_CHOP_COMPRESSION_DEATH"
            title = "⏳ Stale Chop & Compression Decay"
            severity = "EXECUTION_NOISE"
            root_cause = (
                f"Trade lingered for {p.bars_held} bars in sideways compression with low volume ({p.rvol_5m:.2f}x SMA) "
                f"and zero follow-through (MFE: {p.mfe_r:.2f}R) before drifting into the Stop Loss."
            )
            recommendation = "Add a time-based decay rule: If trade fails to achieve +0.5R within 6 bars, tighten stop loss to reduced risk."

        # Check 6: Instant Adverse Rejection
        elif p.instant_reversal:
            verdict = "LOSS_INSTANT_REJECTION"
            title = "⚡ Instant Adverse Rejection"
            severity = "CRITICAL"
            root_cause = (
                f"Immediate sharp reversal upon entry (MFE: {p.mfe_r:.2f}R, MAE: {p.mae_r:.2f}R). "
                f"Entry bar closed with a hostile adverse rejection wick ({p.adverse_wick_ratio * 100:.0f}% of candle range)."
            )
            recommendation = "Filter out entry bars that close with adverse wicks exceeding 40% of the total bar range."

        # Default Fallback Loss
        else:
            verdict = "LOSS_MARKET_VOLATILITY"
            title = "📉 Market Volatility Stop-Out"
            severity = "EXECUTION_NOISE"
            root_cause = f"Trade followed DCC entry criteria but encountered regular market order flow rejection (PnL: {p.pnl_r:+.2f}R)."
            recommendation = "Natural market friction. Maintain risk discipline."

        # Compile contributing forensic bullet points
        if p.h1_ema20_stretch_ratio >= 1.25:
            contributing.append(f"1H EMA20 Stretch: Price was {p.h1_ema20_stretch_ratio:.2f}x ATR away from its 1H mean.")
        if p.h1_adx_slope < -0.3:
            contributing.append(f"1H ADX Momentum Loss: ADX fell {abs(p.h1_adx_slope):.2f} pts leading into entry.")
        if p.rvol_5m < 0.8:
            contributing.append(f"Low Volume Trigger: 5M bar had only {p.rvol_5m:.2f}x 20-SMA volume.")
        if p.mfe_r >= 0.8:
            contributing.append(f"High Unrealized Excursion: Trade reached +{p.mfe_r:.2f}R before failing.")
        if p.adverse_wick_ratio >= 0.35:
            contributing.append(f"Adverse Wick: Trigger bar showed {p.adverse_wick_ratio * 100:.0f}% rejection wick fighting position direction.")

        return TradeForensicVerdict(
            ticket=p.ticket,
            symbol=p.symbol,
            direction=p.direction,
            outcome=p.outcome,
            primary_verdict=verdict,
            verdict_title=title,
            severity=severity,
            root_cause=root_cause,
            contributing_factors=contributing,
            optimization_recommendation=recommendation,
            metrics_summary={
                "PnL ($)": round(p.realized_pnl, 2),
                "PnL (R)": p.pnl_r,
                "MFE": f"{p.mfe_r:.2f}R",
                "MAE": f"{p.mae_r:.2f}R",
                "1H EMA Stretch": f"{p.h1_ema20_stretch_ratio}x ATR",
                "LSQI Score": f"{p.sweep_quality_score:.0f}/100"
            }
        )
