"""
CLI Orchestrator for Institutional DCC Trade Forensics.
Dissects both winning and losing trades, extracts 5-pillar microstructure metrics,
generates interactive HTML visualizers, structured JSON autopsies, and executive briefings,
and archives daily reports to Google Drive.

Usage:
  python experiments/trade_forensics/analyze.py --date 2026-09-15
  python experiments/trade_forensics/analyze.py --yesterday
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import MetaTrader5 as mt5
import pandas as pd

# Fix Windows console utf-8 encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from experiments.trade_forensics.forensic_extractor import ForensicContextExtractor, TradeForensicProfile
from experiments.trade_forensics.verdict_classifier import TradeVerdictClassifier, TradeForensicVerdict
from experiments.trade_forensics.llm_polisher import OpenRouterPolisher
from experiments.trade_forensics.dashboard_generator import ForensicDashboardGenerator
from storage_manager import StorageManager


def analyze_session_date(
    target_date: datetime.date,
    openrouter_key: Optional[str] = None,
    upload_to_drive: bool = True
) -> Dict[str, Any]:
    date_str = target_date.strftime("%Y%m%d")
    date_iso = target_date.strftime("%Y-%m-%d")
    print(f"\n================================================================================")
    print(f"  >>> RUNNING INSTITUTIONAL DCC TRADE FORENSICS: {date_iso} <<<")
    print(f"================================================================================\n")

    # 1. Locate Market Calculations CSV
    csv_candidates = [
        os.path.join(PROJECT_ROOT, "logs", f"vps_calculations_{date_str}.csv"),
        os.path.join(PROJECT_ROOT, "logs", f"market_calculations_{date_str}.csv"),
        os.path.join(PROJECT_ROOT, "logs", "vps logs", f"market_calculations_{date_str}.csv"),
    ]
    csv_path = next((p for p in csv_candidates if os.path.exists(p)), None)
    if not csv_path:
        print(f"[Forensics] Warning: Calculation CSV for {date_str} not found locally.")
        calc_df = None
    else:
        print(f"[*] Loaded market calculations: {csv_path}")
        calc_df = pd.read_csv(csv_path)
        calc_df["timestamp_utc"] = pd.to_datetime(calc_df["timestamp_utc"], utc=True)

    extractor = ForensicContextExtractor()

    # 2. Extract trade setups from audit reconciliation JSON or calculation CSV
    trades_to_analyze = []

    # Check if we have audit reconciliation JSON
    audit_json_path = os.path.join(PROJECT_ROOT, "reports", "daily_audits", f"audit_reconciliation_{date_str}.json")
    if os.path.exists(audit_json_path):
        with open(audit_json_path, "r", encoding="utf-8") as f:
            audit_data = json.load(f)

        bt_trades = audit_data.get("backtest_trades", [])

        for d in audit_data.get("reconciliation_details", []):
            sym = d["symbol"]
            d_str = d["direction"]
            sig_time = pd.to_datetime(d["signal_time"], utc=True)

            # Match with backtest trade if available to get exact execution parameters
            matched_bt = None
            for bt in bt_trades:
                bt_t = pd.to_datetime(bt["entry_time"], utc=True)
                if bt["symbol"] == sym and abs((bt_t - sig_time).total_seconds()) <= 600:
                    matched_bt = bt
                    break

            entry_p = float(d.get("live_entry") or d.get("backtest_entry") or (matched_bt["actual_entry"] if matched_bt else 0.0))
            pnl_val = float(d.get("live_pnl") or d.get("backtest_pnl") or (matched_bt["net_pnl"] if matched_bt else 0.0))
            sl_p = float(matched_bt["sl_price"]) if matched_bt and "sl_price" in matched_bt else None
            tp1_p = float(matched_bt["tp1_price"]) if matched_bt and "tp1_price" in matched_bt else None
            tp2_p = float(matched_bt["tp2_price"]) if matched_bt and "tp2_price" in matched_bt else None
            exit_dt = pd.to_datetime(matched_bt["exit_time"], utc=True).to_pydatetime() if matched_bt and matched_bt.get("exit_time") else None
            exit_reason = matched_bt.get("exit_reason") if matched_bt else None

            trades_to_analyze.append({
                "symbol": sym,
                "direction": d_str,
                "ticket": d.get("live_ticket"),
                "entry_time": sig_time,
                "entry_price": entry_p,
                "sl_price": sl_p,
                "tp1_price": tp1_p,
                "tp2_price": tp2_p,
                "exit_time": exit_dt,
                "exit_reason": exit_reason,
                "pnl": pnl_val
            })

    # If no audit JSON, extract directly from calculation CSV fired signals
    if not trades_to_analyze and calc_df is not None:
        fired_signals = calc_df[calc_df["decision"].str.startswith("FIRED")]
        for _, row in fired_signals.iterrows():
            sym = row["symbol"]
            d_str = "BUY" if "BUY" in row["decision"] else "SELL"
            trades_to_analyze.append({
                "symbol": sym,
                "direction": d_str,
                "ticket": None,
                "entry_time": row["timestamp_utc"],
                "entry_price": float(row["planned_entry"]),
                "sl_price": float(row["planned_sl"]),
                "tp1_price": float(row["planned_tp1"]),
                "tp2_price": float(row["planned_tp2"]),
                "exit_time": None,
                "exit_reason": None,
                "pnl": -50.0  # Default estimate if not closed
            })

    if not trades_to_analyze:
        print(f"[Forensics] No strategy signals or executed trades found for {date_iso}.")
        return {}

    print(f"[*] Found {len(trades_to_analyze)} trade setup(s) to analyze.\n")

    # 3. Dissect each trade (both winning and losing setups)
    profiles: List[TradeForensicProfile] = []
    verdicts: List[TradeForensicVerdict] = []

    for t in trades_to_analyze:
        sym = t["symbol"]
        entry_p = t["entry_price"]
        sl_p = t.get("sl_price") or (entry_p - 15.0 if t["direction"] == "BUY" else entry_p + 15.0)
        tp1_p = t.get("tp1_price") or (entry_p + 20.0 if t["direction"] == "BUY" else entry_p - 20.0)
        tp2_p = t.get("tp2_price") or (entry_p + 30.0 if t["direction"] == "BUY" else entry_p - 30.0)

        entry_dt = t["entry_time"].to_pydatetime() if hasattr(t["entry_time"], "to_pydatetime") else t["entry_time"]
        exit_dt = t.get("exit_time") or (entry_dt + timedelta(hours=2))

        prof = extractor.extract_from_live_trade(
            symbol=sym,
            ticket=t.get("ticket"),
            direction=t["direction"],
            entry_time=entry_dt,
            entry_price=entry_p,
            sl_price=sl_p,
            tp1_price=tp1_p,
            tp2_price=tp2_p,
            exit_time=exit_dt,
            realized_pnl=t["pnl"],
            calc_df=calc_df
        )

        # If backtest exit reason indicates full winner, ensure outcome reflects it
        if t.get("exit_reason") and "WINNER" in t["exit_reason"]:
            prof.outcome = "WIN_TP2"

        verd = TradeVerdictClassifier.classify(prof)
        profiles.append(prof)
        verdicts.append(verd)

    # 4. Compute Session Summary Stats
    tot_pnl = sum(v.metrics_summary.get("PnL ($)", 0.0) for v in verdicts)
    win_trades = sum(1 for v in verdicts if "WIN" in v.outcome or "WIN" in v.primary_verdict or v.metrics_summary.get("PnL ($)", 0.0) > 0.05)
    loss_trades = len(verdicts) - win_trades
    win_rate = (win_trades / len(verdicts) * 100.0) if verdicts else 0.0

    summary_stats = {
        "net_pnl": round(tot_pnl, 2),
        "total_trades": len(verdicts),
        "winning_trades": win_trades,
        "losing_trades": loss_trades,
        "win_rate_pct": round(win_rate, 1),
        "daily_dd_pct": round(max(0.0, -tot_pnl) / 5300.0 * 100.0, 2)
    }

    # 5. Generate Polished Executive Briefing (OpenRouter or Offline Fallback)
    print("[*] Generating Executive Trade Desk Briefing...")
    polisher = OpenRouterPolisher(api_key=openrouter_key)
    memo = polisher.polish_session_briefing(date_iso, summary_stats, verdicts)

    # Ensure reports/forensics directory exists
    forensics_dir = os.path.join(PROJECT_ROOT, "reports", "forensics")
    os.makedirs(forensics_dir, exist_ok=True)

    # 6. Generate Interactive Visual HTML Dashboard
    dash_path = os.path.join(forensics_dir, f"forensics_dashboard_{date_str}.html")
    ForensicDashboardGenerator.generate_html_dashboard(
        date_str=date_iso,
        summary_stats=summary_stats,
        verdicts=verdicts,
        executive_memo=memo,
        output_path=dash_path
    )
    print(f"[OK] Interactive Forensic Dashboard generated: {dash_path}")

    # 7. Generate Structured JSON Autopsy Dataset
    autopsy_json_path = os.path.join(forensics_dir, f"forensic_autopsy_{date_str}.json")
    trades_dataset = []
    for p, v in zip(profiles, verdicts):
        trades_dataset.append({
            "ticket": v.ticket,
            "symbol": v.symbol,
            "direction": v.direction,
            "outcome": v.outcome,
            "primary_verdict": v.primary_verdict,
            "verdict_title": v.verdict_title,
            "severity": v.severity,
            "root_cause_or_edge_mechanics": v.root_cause,
            "catalysts_and_confluences": v.contributing_factors,
            "strategy_rule": v.optimization_recommendation,
            "metrics_summary": v.metrics_summary,
            "forensic_profile": asdict(p)
        })

    autopsy_payload = {
        "date": date_iso,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary_stats,
        "executive_memo": memo,
        "trades": trades_dataset
    }
    with open(autopsy_json_path, "w", encoding="utf-8") as f:
        json.dump(autopsy_payload, f, indent=2, default=str)
    print(f"[OK] Forensic Autopsy JSON dataset saved: {autopsy_json_path}")

    # 8. Save Executive Memo Markdown
    memo_path = os.path.join(forensics_dir, f"executive_memo_{date_str}.md")
    with open(memo_path, "w", encoding="utf-8") as f:
        f.write(memo)
    print(f"[OK] Executive Memo Markdown saved: {memo_path}")

    # 9. Cloud Archival to Google Drive under 'bot backtest/YYYY-MM-DD/'
    drive_results = {}
    if upload_to_drive:
        storage = StorageManager()
        target_subfolder = date_iso

        print(f"[*] Archiving trade forensics deliverables to Google Drive under 'bot backtest/{target_subfolder}/'...")

        # 9a. HTML Dashboard
        ok_dash, msg_dash = storage.archive_to_google_drive(
            dash_path, folder_name="bot backtest", subfolder_date=target_subfolder
        )
        drive_results["dashboard_html"] = (ok_dash, msg_dash)
        if ok_dash:
            print(f"[Storage] [Drive] HTML Dashboard archived: {msg_dash}")

        # 9b. JSON Autopsy
        ok_json, msg_json = storage.archive_to_google_drive(
            autopsy_json_path, folder_name="bot backtest", subfolder_date=target_subfolder
        )
        drive_results["autopsy_json"] = (ok_json, msg_json)
        if ok_json:
            print(f"[Storage] [Drive] Autopsy JSON archived: {msg_json}")

        # 9c. Markdown Memo
        ok_memo, msg_memo = storage.archive_to_google_drive(
            memo_path, folder_name="bot backtest", subfolder_date=target_subfolder
        )
        drive_results["executive_memo"] = (ok_memo, msg_memo)
        if ok_memo:
            print(f"[Storage] [Drive] Executive Memo archived: {msg_memo}")

    # 10. Print Console Autopsy Cards
    print("\n" + memo + "\n")
    print("================================================================================")
    print("  DETAILED MATHEMATICAL AUTOPSY CARDS (WINS & LOSSES)")
    print("================================================================================")
    for v in verdicts:
        is_win = "WIN" in v.outcome or "WIN" in v.primary_verdict or v.metrics_summary.get("PnL ($)", 0.0) > 0.05
        label = "🏆 WINNING EDGE" if is_win else "⚠️ LOSS AUTOPSY"
        print(f"\n[{label}] [{v.symbol} {v.direction}] {v.verdict_title}")
        print(f"  • Edge/Root Cause: {v.root_cause}")
        for factor in v.contributing_factors:
            print(f"    - {factor}")
        print(f"  • Metrics:         " + " | ".join(f"{k}: {val}" for k, val in v.metrics_summary.items()))
        print(f"  • Strategy Action: {v.optimization_recommendation}")
    print("\n================================================================================\n")

    return {
        "date": date_iso,
        "summary": summary_stats,
        "executive_memo": memo,
        "dashboard_html_path": dash_path,
        "autopsy_json_path": autopsy_json_path,
        "memo_path": memo_path,
        "drive_results": drive_results,
        "verdicts": verdicts
    }


def main():
    parser = argparse.ArgumentParser(description="Institutional DCC Trade Forensics Engine")
    parser.add_argument("--date", type=str, help="Target date in YYYY-MM-DD format")
    parser.add_argument("--yesterday", action="store_true", help="Analyze yesterday's trading session")
    parser.add_argument("--openrouter-key", type=str, help="Optional OpenRouter API key for LLM polishing")
    parser.add_argument("--no-drive", action="store_true", help="Skip Google Drive upload")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    if args.yesterday:
        target_date = (now_utc - timedelta(days=1)).date()
    elif args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = now_utc.date()

    analyze_session_date(
        target_date=target_date,
        openrouter_key=args.openrouter_key,
        upload_to_drive=not args.no_drive
    )


if __name__ == "__main__":
    main()
