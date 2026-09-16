"""
OpenRouter LLM Executive Trade Briefing Polisher.
Grounded post-processing module that transforms mathematical trade autopsies
into institutional hedge-fund caliber executive briefings.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from .verdict_classifier import TradeForensicVerdict


class OpenRouterPolisher:
    """Dispatches trade autopsies to OpenRouter with strict numerical grounding and offline fallback."""

    DEFAULT_MODEL = "z-ai/glm-5.2:free"
    FALLBACK_MODELS = [
        "inclusionai/ling-3.0-flash-fin:free",
        "nvidia/nemotron-3.5-lightning:free",
        "liquid/lfm-2.5-2.6b:free"
    ]

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        cfg_key, cfg_model = self._load_config()
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip() or cfg_key
        self.model = model or os.environ.get("OPENROUTER_MODEL", "").strip() or cfg_model or self.DEFAULT_MODEL

    @staticmethod
    def _load_config() -> tuple[str, str]:
        """Attempts to load OpenRouter configuration from bot_accounts_config.json."""
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            cfg_path = os.path.join(base_dir, "bot_accounts_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    or_cfg = data.get("openrouter", {})
                    return or_cfg.get("api_key", ""), or_cfg.get("model", "")
        except Exception:
            pass
        return "", ""

    def polish_session_briefing(
        self,
        date_str: str,
        summary_stats: Dict[str, Any],
        verdicts: List[TradeForensicVerdict]
    ) -> str:
        """Generates a polished executive memo using OpenRouter LLM or instant offline fallback."""
        if not verdicts:
            return f"### 🌙 Executive Trade Desk Briefing ({date_str})\n\nNo strategy setups triggered during this trading session. All capital was protected."

        # If no API key is set, use the institutional offline template immediately
        if not self.api_key:
            return self._generate_offline_briefing(date_str, summary_stats, verdicts)

        # Build strictly grounded JSON payload
        trades_payload = []
        for v in verdicts:
            trades_payload.append({
                "ticket": v.ticket,
                "symbol": v.symbol,
                "direction": v.direction,
                "outcome": v.outcome,
                "diagnostic_verdict": v.primary_verdict,
                "verdict_title": v.verdict_title,
                "severity": v.severity,
                "mathematical_root_cause": v.root_cause,
                "contributing_factors": v.contributing_factors,
                "metrics": v.metrics_summary,
                "optimization_rule": v.optimization_recommendation
            })

        system_instruction = (
            "You are the Chief Risk Officer for an institutional proprietary trading firm running the DCC algorithmic strategy.\n"
            "Output ONLY the final executive markdown briefing directly. DO NOT output any planning, scratchpad, reasoning, or drafting steps.\n"
            "CRITICAL: DO NOT alter, recalculate, or fabricate any numbers, prices, or PnL figures. Use the exact data provided."
        )

        user_prompt = (
            f"Below is the verified mathematical autopsy of today's trading session ({date_str}).\n\n"
            f"Session Summary:\n"
            f"{json.dumps(summary_stats, indent=2)}\n\n"
            f"Trade Autopsies:\n"
            f"{json.dumps(trades_payload, indent=2)}\n\n"
            f"Write a crisp, institutional 3-paragraph executive memo:\n"
            f"1. Overall session financial performance (PnL, win rate, drawdown, and portfolio impact).\n"
            f"2. Technical autopsy of why winning/losing trades occurred (referencing 1H EMA stretch, ADX momentum, sweep depth, and MFE/MAE excursions).\n"
            f"3. Strategic takeaway and actionable risk mandate.\n\n"
            f"Format with clean GitHub markdown, bold headers, and bullet points."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/RyzEnHunTer/Dc-Bot",
            "X-Title": "DCC Trade Forensics"
        }

        # Try user model first, then fallback models if rate-limited
        models_to_try = [self.model] + [m for m in self.FALLBACK_MODELS if m != self.model]

        import time

        for target_model in models_to_try:
            # Allow up to 3 attempts with backoff if rate-limited (HTTP 429)
            max_attempts = 3 if target_model == self.model else 1
            for attempt in range(max_attempts):
                try:
                    body = {
                        "model": target_model,
                        "messages": [
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.2,
                        "max_tokens": 1800
                    }

                    req = urllib.request.Request(
                        "https://openrouter.ai/api/v1/chat/completions",
                        data=json.dumps(body).encode("utf-8"),
                        headers=headers,
                        method="POST"
                    )

                    with urllib.request.urlopen(req, timeout=25) as response:
                        if response.status == 200:
                            resp_data = json.loads(response.read().decode("utf-8"))
                            msg = resp_data["choices"][0]["message"]
                            ai_content = msg.get("content") or ""
                            if not ai_content and msg.get("reasoning"):
                                ai_content = msg.get("reasoning")

                            ai_content = self._clean_ai_output(ai_content.strip())
                            if ai_content:
                                print(f"[OpenRouterPolisher] Executive briefing generated via {target_model}.")
                                return ai_content

                except urllib.error.HTTPError as e:
                    err_body = e.read().decode("utf-8", errors="ignore")
                    if e.code == 429 and attempt < max_attempts - 1:
                        print(f"[OpenRouterPolisher] Model {target_model} rate-limited (429). Retrying in 5s (attempt {attempt+1}/{max_attempts})...")
                        time.sleep(5)
                        continue
                    print(f"[OpenRouterPolisher] Model {target_model} HTTP {e.code}: {err_body[:100]}... Trying fallback...")
                    break
                except Exception as e:
                    print(f"[OpenRouterPolisher] Model {target_model} error: {e}. Trying fallback...")
                    break

        print("[OpenRouterPolisher] All online LLM endpoints exhausted. Using institutional offline template.")
        return self._generate_offline_briefing(date_str, summary_stats, verdicts)

    @staticmethod
    def _clean_ai_output(raw_text: str) -> str:
        """Strips out internal model reasoning, think tags, or drafting steps if returned by reasoning models."""
        if not raw_text:
            return ""

        text = raw_text.strip()

        # Strip think tags if present
        if "<think>" in text and "</think>" in text:
            text = text.split("</think>", 1)[1].strip()

        # If output contains "Draft: **", extract the clean drafted paragraphs
        if "Draft: **" in text:
            blocks = []
            for part in text.split("Draft:"):
                p = part.strip()
                if p.startswith("**"):
                    p_clean = p.split("*   *Paragraph")[0].split("4.  **")[0].split("*Revised Draft")[0].strip()
                    if p_clean:
                        blocks.append(p_clean)
            if blocks:
                return "### 🏛️ Executive Trade Desk Briefing\n\n" + "\n\n".join(blocks)

        for marker in ["**Session Financial Performance**", "**Overall Session Financial Performance**", "# Executive Trade", "### Executive Trade"]:
            idx = text.find(marker)
            if idx != -1:
                clean = text[idx:].strip()
                for cut_marker in ["*Revised Draft", "Revised Draft:", "5.  **Review", "4.  **"]:
                    if cut_marker in clean:
                        clean = clean.split(cut_marker)[0].strip()
                return clean

        return text

    def _generate_offline_briefing(
        self,
        date_str: str,
        summary_stats: Dict[str, Any],
        verdicts: List[TradeForensicVerdict]
    ) -> str:
        """Deterministic institutional memo generator that requires zero internet or external APIs."""
        lines = [
            f"### 🌙 Executive Trade Desk Briefing ({date_str})",
            "",
            f"**Session Metrics:** Net PnL: `${summary_stats.get('net_pnl', 0.0):+,.2f}` | "
            f"Trades: `{len(verdicts)}` | Win Rate: `{summary_stats.get('win_rate_pct', 0.0)}%` | "
            f"Daily DD: `-{summary_stats.get('daily_dd_pct', 0.0):.2f}%`",
            "",
            "#### 🔍 Forensic Trade Autopsy Breakdown"
        ]

        for idx, v in enumerate(verdicts, 1):
            status_icon = "🟢" if "WIN" in v.outcome else "🔴"
            ticket_str = f"Ticket #{v.ticket}" if v.ticket else f"Setup #{idx}"
            lines.append(f"##### {status_icon} {v.symbol} {v.direction} ({ticket_str}) — {v.verdict_title}")
            lines.append(f"- **Primary Root Cause:** {v.root_cause}")
            if v.contributing_factors:
                lines.append(f"- **Contributing Factors:**")
                for factor in v.contributing_factors:
                    lines.append(f"  • {factor}")
            lines.append(f"- **Key Metrics:** " + " | ".join(f"**{k}:** {val}" for k, val in v.metrics_summary.items()))
            lines.append(f"- **Strategy Optimization:** *{v.optimization_recommendation}*")
            lines.append("")

        lines.append("---")
        lines.append("💡 **Desk Conclusion:** All risk protocols operated within designed parameters. Review flagged exhaustion traps to optimize high-water mark preservation.")
        return "\n".join(lines)
