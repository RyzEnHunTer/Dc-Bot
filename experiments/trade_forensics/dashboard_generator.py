"""
Visual Forensic Dashboard & Report Generator.
Generates standalone interactive HTML visualizer and markdown dossiers for DCC trade autopsies.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from .verdict_classifier import TradeForensicVerdict


class ForensicDashboardGenerator:
    """Produces HTML visual dashboards and formatted markdown dossiers."""

    @classmethod
    def generate_html_dashboard(
        cls,
        date_str: str,
        summary_stats: Dict[str, Any],
        verdicts: List[TradeForensicVerdict],
        executive_memo: str,
        output_path: str
    ) -> str:
        """Generates a state-of-the-art standalone HTML dashboard."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        trade_cards_html = []
        for idx, v in enumerate(verdicts, 1):
            is_win = "WIN" in v.outcome or "WIN" in v.primary_verdict or v.metrics_summary.get("PnL ($)", 0.0) > 0.05
            badge_color = "#10b981" if is_win else "#ef4444"
            badge_bg = "rgba(16, 185, 129, 0.15)" if is_win else "rgba(239, 68, 68, 0.15)"
            card_border = "rgba(16, 185, 129, 0.3)" if is_win else "rgba(255, 255, 255, 0.08)"
            pnl_val = v.metrics_summary.get("PnL ($)", 0.0)
            pnl_class = "pnl-positive" if pnl_val >= 0 else "pnl-negative"

            factors_li = "".join(f"<li>{f}</li>" for f in v.contributing_factors) if v.contributing_factors else "<li>Normal execution parameters</li>"

            mfe = v.metrics_summary.get("MFE", "0.0R")
            mae = v.metrics_summary.get("MAE", "0.0R")
            stretch = v.metrics_summary.get("1H EMA Stretch", "0.0x")
            lsqi = v.metrics_summary.get("LSQI Score", "0/100")
            rvol = v.metrics_summary.get("RVol", "-")
            bars = v.metrics_summary.get("Bars Held", "-")

            root_cause_title = "🎯 Execution Analysis & Edge Mechanics:" if is_win else "🔬 Forensic Root Cause:"
            factors_title = "🏆 Winning Edge Catalysts & Confluences:" if is_win else "📊 Contributing Flaws / Risk Factors:"
            rule_icon = "💎" if is_win else "💡"
            rule_label = "Edge Preservation & Scaling:" if is_win else "Strategy Optimization:"

            card = f"""
            <div class="trade-card" style="border-color: {card_border};">
                <div class="trade-card-header">
                    <div class="trade-meta">
                        <span class="symbol-pill">{v.symbol}</span>
                        <span class="dir-pill {v.direction.lower()}">{v.direction}</span>
                        <span class="ticket-pill">Ticket #{v.ticket if v.ticket else idx}</span>
                        {"<span class='winner-pill' style='background: rgba(16, 185, 129, 0.2); color: #10b981; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600;'>WINNING EDGE</span>" if is_win else ""}
                    </div>
                    <div class="verdict-badge" style="background: {badge_bg}; color: {badge_color}; border: 1px solid {badge_color};">
                        {v.verdict_title}
                    </div>
                </div>

                <div class="excursion-bar-container">
                    <div class="metric-mini">
                        <span class="label">MFE (Peak):</span>
                        <span class="val green">+{mfe}</span>
                    </div>
                    <div class="metric-mini">
                        <span class="label">MAE (Heat):</span>
                        <span class="val red">-{mae}</span>
                    </div>
                    <div class="metric-mini">
                        <span class="label">1H Stretch:</span>
                        <span class="val amber">{stretch}</span>
                    </div>
                    <div class="metric-mini">
                        <span class="label">LSQI:</span>
                        <span class="val blue">{lsqi}</span>
                    </div>
                    <div class="metric-mini">
                        <span class="label">RVol:</span>
                        <span class="val" style="color: #a78bfa;">{rvol}</span>
                    </div>
                    <div class="metric-mini">
                        <span class="label">Net PnL:</span>
                        <span class="val {pnl_class}">${pnl_val:+,.2f}</span>
                    </div>
                </div>

                <div class="root-cause-box" style="{"border-left-color: #10b981;" if is_win else ""}">
                    <div class="box-title">{root_cause_title}</div>
                    <div class="box-text">{v.root_cause}</div>
                </div>

                <div class="factors-box">
                    <div class="box-title">{factors_title}</div>
                    <ul class="factors-list">
                        {factors_li}
                    </ul>
                </div>

                <div class="rule-box" style="{"background: rgba(16, 185, 129, 0.08); border-color: rgba(16, 185, 129, 0.2);" if is_win else ""}">
                    <span class="rule-icon">{rule_icon}</span>
                    <span class="rule-text"><strong>{rule_label}</strong> {v.optimization_recommendation}</span>
                </div>
            </div>
            """
            trade_cards_html.append(card)

        all_cards_str = "\n".join(trade_cards_html)

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DCC Trade Forensics Dossier — {date_str}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: #0a0d14;
            --bg-secondary: #111726;
            --bg-card: rgba(17, 24, 39, 0.7);
            --border: rgba(255, 255, 255, 0.08);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --accent-indigo: #6366f1;
            --accent-amber: #f59e0b;
            --accent-blue: #38bdf8;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg-primary);
            color: var(--text-main);
            font-family: 'Outfit', sans-serif;
            line-height: 1.5;
            padding: 24px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }}
        h1 {{
            font-size: 26px;
            font-weight: 700;
            background: linear-gradient(135deg, #fff, #9ca3af);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .badge-date {{
            background: rgba(99, 102, 241, 0.15);
            color: var(--accent-indigo);
            padding: 6px 14px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 13px;
            border: 1px solid rgba(99, 102, 241, 0.3);
        }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}
        .kpi-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            padding: 18px;
            border-radius: 12px;
            backdrop-filter: blur(10px);
        }}
        .kpi-label {{
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 6px;
        }}
        .kpi-val {{
            font-size: 24px;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
        }}
        .pnl-positive {{ color: var(--accent-green); }}
        .pnl-negative {{ color: var(--accent-red); }}
        .green {{ color: var(--accent-green); }}
        .red {{ color: var(--accent-red); }}
        .amber {{ color: var(--accent-amber); }}
        .blue {{ color: var(--accent-blue); }}

        .memo-container {{
            background: rgba(17, 24, 39, 0.6);
            border: 1px solid rgba(99, 102, 241, 0.25);
            border-left: 4px solid var(--accent-indigo);
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 32px;
        }}
        .memo-title {{
            font-size: 16px;
            font-weight: 600;
            color: #c7d2fe;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .memo-text {{
            font-size: 14px;
            color: #e5e7eb;
            white-space: pre-wrap;
            line-height: 1.6;
        }}

        .section-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            color: #e5e7eb;
        }}
        .trade-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 20px;
        }}
        .trade-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 22px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s, border-color 0.2s;
        }}
        .trade-card:hover {{
            border-color: rgba(255, 255, 255, 0.18);
        }}
        .trade-card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 16px;
        }}
        .trade-meta {{
            display: flex;
            gap: 8px;
            align-items: center;
        }}
        .symbol-pill {{
            background: rgba(255, 255, 255, 0.1);
            padding: 4px 10px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 13px;
        }}
        .dir-pill {{
            padding: 4px 10px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 13px;
        }}
        .dir-pill.buy {{ background: rgba(16, 185, 129, 0.2); color: var(--accent-green); }}
        .dir-pill.sell {{ background: rgba(239, 68, 68, 0.2); color: var(--accent-red); }}
        .ticket-pill {{
            color: var(--text-muted);
            font-size: 13px;
            font-family: 'JetBrains Mono', monospace;
        }}
        .verdict-badge {{
            padding: 5px 12px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
        }}

        .excursion-bar-container {{
            display: flex;
            gap: 16px;
            background: rgba(0, 0, 0, 0.25);
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            flex-wrap: wrap;
        }}
        .metric-mini {{
            display: flex;
            flex-direction: column;
        }}
        .metric-mini .label {{
            font-size: 11px;
            color: var(--text-muted);
        }}
        .metric-mini .val {{
            font-size: 15px;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
        }}

        .root-cause-box {{
            background: rgba(239, 68, 68, 0.06);
            border-left: 3px solid var(--accent-red);
            padding: 12px 14px;
            border-radius: 4px;
            margin-bottom: 12px;
        }}
        .box-title {{
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #d1d5db;
            margin-bottom: 4px;
        }}
        .box-text {{
            font-size: 14px;
            color: #f3f4f6;
        }}
        .factors-box {{
            padding: 8px 14px;
            margin-bottom: 12px;
        }}
        .factors-list {{
            list-style-type: none;
            padding-left: 0;
            font-size: 13px;
            color: var(--text-muted);
        }}
        .factors-list li {{
            margin-bottom: 4px;
            position: relative;
            padding-left: 14px;
        }}
        .factors-list li::before {{
            content: "•";
            position: absolute;
            left: 0;
            color: var(--accent-indigo);
        }}
        .rule-box {{
            background: rgba(16, 185, 129, 0.08);
            border: 1px solid rgba(16, 185, 129, 0.2);
            padding: 10px 14px;
            border-radius: 6px;
            font-size: 13px;
            color: #d1fae5;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .rule-icon {{ font-size: 16px; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>DCC Algorithmic Trade Forensics Dossier</h1>
                <p style="color: var(--text-muted); font-size: 14px; margin-top: 4px;">Institutional Mathematical Autopsy & Strategy Failure/Success Analysis</p>
            </div>
            <div class="badge-date">{date_str}</div>
        </header>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">Session Net PnL</div>
                <div class="kpi-val { 'pnl-positive' if summary_stats.get('net_pnl', 0) >= 0 else 'pnl-negative' }">
                    ${summary_stats.get('net_pnl', 0.0):+,.2f}
                </div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Trades Analyzed</div>
                <div class="kpi-val">{len(verdicts)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Win Rate</div>
                <div class="kpi-val green">{summary_stats.get('win_rate_pct', 0.0):.1f}%</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Daily Drawdown</div>
                <div class="kpi-val red">-{summary_stats.get('daily_dd_pct', 0.0):.2f}%</div>
            </div>
        </div>

        <div class="memo-container">
            <div class="memo-title">
                <span>🏛️</span> Executive Trade Desk Briefing
            </div>
            <div class="memo-text">{executive_memo}</div>
        </div>

        <div class="section-title">🔍 Individual Strategy Autopsies</div>
        <div class="trade-grid">
            {all_cards_str}
        </div>
    </div>
</body>
</html>
"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return output_path
