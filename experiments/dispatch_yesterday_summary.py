import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import json
import time
from datetime import datetime, timezone
from notifier import NotificationManager

def main():
    nm = NotificationManager()

    with open('reports/daily_audits/audit_reconciliation_20260915.json', 'r') as f:
        audit = json.load(f)

    s = audit['summary']
    alert_text = (
        f"**Match Rate:** {s['match_rate_pct']}%\n"
        f"• **Strategy Setups:** {s['total_backtest_signals']}\n"
        f"• **Live Broker Deals:** {s['total_live_entries']}\n"
        f"• **Reconciled Matches:** {s['matched_trades']}\n"
        f"• **Discrepancies:** {s['discrepancies']}\n"
        f"• **Backtest Net PnL:** ${s['backtest_net_pnl']:,.2f}\n"
        f"• **Live Broker Net PnL:** ${s['live_broker_net_pnl']:,.2f}\n\n"
        f"📁 *Scorecard archived to Google Drive: bot backtest/2026-09-15/*"
    )

    print("Dispatching Nightly Audit Scorecard...")
    nm.notify_nightly_audit("🌙 DCC Nightly Audit Scorecard (2026-09-15)", alert_text)

    print("Dispatching Session Close PnL Card...")
    nm.notify_daily_summary(
        date_str="2026-09-15",
        starting_equity=5318.04,
        closing_equity=5202.16,
        balance=5202.16,
        trades_count=3,
        winning_trades=0,
        losing_trades=3,
        daily_pnl=-115.88,
        daily_pnl_pct=-2.18,
        daily_dd_pct=2.18,
        cushion_remaining=0.82
    )

    print("Waiting 5 seconds for Discord webhook delivery...")
    time.sleep(5)
    print("[SUCCESS] Dispatched yesterday's summary and scorecard to Discord channel!")

if __name__ == "__main__":
    main()
