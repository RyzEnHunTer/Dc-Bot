"""
DCC Algorithmic Trading Bot - Remote Notification Engine
Supports dual-engine remote alerts:
1. Telegram Bot (Bot Token & Chat ID)
2. Discord Webhook (Webhook URL)

Constraints & Architecture:
- Mutual Exclusivity: Only 1 platform active at any time (none, telegram, discord).
- Non-Blocking: Dispatches all HTTP requests asynchronously in daemon worker threads
  so market tick execution in MT5 is NEVER delayed by internet latency.
- Zero External Dependencies: Uses standard library urllib.request.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import ssl
import urllib.error
import urllib.parse
import urllib.request

TZ_IST = timezone(timedelta(hours=5, minutes=30))


def _get_ssl_context() -> ssl.SSLContext:
    """Returns an SSL context that prioritizes certifi CA bundle with fallback."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def format_dual_time(dt: Optional[datetime] = None, include_date: bool = True) -> str:
    """Formats datetime into dual UTC + IST string for clear local time visibility."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    dt_ist = dt.astimezone(TZ_IST)
    if include_date:
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} UTC ({dt_ist.strftime('%Y-%m-%d %H:%M:%S')} IST)"
    return f"{dt.strftime('%H:%M:%S')} UTC ({dt_ist.strftime('%H:%M:%S')} IST)"


def format_dual_hhmm(utc_hhmm: str) -> str:
    """Converts a UTC HH:MM string to dual 'HH:MM UTC (HH:MM IST)' string."""
    if "IST" in utc_hhmm:
        return utc_hhmm
    try:
        raw = utc_hhmm.replace("UTC", "").strip()
        parts = raw.split(":")
        h, m = int(parts[0]), int(parts[1][:2])
        now_d = datetime.now(timezone.utc).date()
        dt_utc = datetime(now_d.year, now_d.month, now_d.day, h, m, tzinfo=timezone.utc)
        dt_ist = dt_utc.astimezone(TZ_IST)
        return f"{h:02d}:{m:02d} UTC ({dt_ist.strftime('%H:%M')} IST)"
    except Exception:
        return f"{utc_hhmm}"


def format_dollar(val: float, force_sign: bool = True) -> str:
    """Formats dollar value cleanly with proper +$ or -$ placement."""
    if val >= 0:
        return f"+${val:,.2f}" if force_sign else f"${val:,.2f}"
    return f"-${abs(val):,.2f}"


class NotificationManager:
    """Manages remote alerts for Telegram or Discord in a non-blocking background queue."""

    @staticmethod
    def _load_default_config() -> Dict:
        import os
        project_root = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(project_root, "bot_accounts_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    all_cfg = json.load(f)
                for v in all_cfg.values():
                    if isinstance(v, dict) and "notifications" in v:
                        return v["notifications"]
            except Exception:
                pass
        return {
            "active_platform": "none",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "discord_webhook_url": ""
        }

    def __init__(self, config: Optional[Dict] = None):
        if config is not None:
            self.config = config
        else:
            self.config = self._load_default_config()
        self.msg_queue: queue.Queue = queue.Queue(maxsize=100)
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def update_config(self, config: Dict):
        self.config = config

    @property
    def active_platform(self) -> str:
        return self.config.get("active_platform", "none").lower()

    def _worker_loop(self):
        """Background worker that drains the message queue and sends HTTP requests."""
        while True:
            try:
                item = self.msg_queue.get()
                if item is None:
                    break

                platform, payload = item
                if platform == "telegram":
                    token = self.config.get("telegram_bot_token", "").strip()
                    chat_id = self.config.get("telegram_chat_id", "").strip()
                    if token and chat_id:
                        self._send_telegram_http(token, chat_id, payload.get("text", ""))
                elif platform == "discord":
                    webhook_url = self.config.get("discord_webhook_url", "").strip()
                    if webhook_url:
                        self._send_discord_http(webhook_url, payload.get("content", ""), payload.get("embed", None))

                self.msg_queue.task_done()
            except Exception as e:
                print(f"[Notifier Worker Error] {e}")
                time.sleep(0.5)

    def _enqueue(self, payload: Dict):
        """Enqueues message without blocking the caller."""
        platform = self.active_platform
        if platform in ("telegram", "discord"):
            try:
                self.msg_queue.put_nowait((platform, payload))
            except queue.Full:
                print("[Notifier] Warning: Alert queue full, dropping alert to protect MT5 tick speed.")

    # -------------------------------------------------------------------------
    # HTTP Senders (Executed in background thread)
    # -------------------------------------------------------------------------
    @staticmethod
    def _send_telegram_http(token: str, chat_id: str, text: str, timeout: int = 5) -> Tuple[bool, str]:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context()) as response:
                if response.status == 200:
                    return True, "Telegram message sent successfully."
                return False, f"HTTP Status {response.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"Telegram HTTPError: {err_msg}"
        except Exception as e:
            err_str = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in err_str or "certificate verify failed" in err_str.lower():
                try:
                    fallback_ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=timeout, context=fallback_ctx) as response:
                        if response.status == 200:
                            return True, "Telegram message sent successfully (via fallback SSL)."
                        return False, f"HTTP Status {response.status}"
                except Exception as retry_err:
                    return False, f"Telegram SSL Error: {retry_err}"
            return False, f"Telegram Error: {e}"

    @staticmethod
    def _send_discord_http(webhook_url: str, content: str, embed: Optional[Dict] = None, timeout: int = 5) -> Tuple[bool, str]:
        body = {"content": content}
        if embed:
            body["embeds"] = [embed]

        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "InstitutionalDCCBot/2.0"
        }
        req = urllib.request.Request(webhook_url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context()) as response:
                if response.status in (200, 204):
                    return True, "Discord notification sent successfully."
                return False, f"HTTP Status {response.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"Discord HTTPError: {err_msg}"
        except Exception as e:
            err_str = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in err_str or "certificate verify failed" in err_str.lower():
                try:
                    fallback_ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=timeout, context=fallback_ctx) as response:
                        if response.status in (200, 204):
                            return True, "Discord notification sent successfully (via fallback SSL)."
                        return False, f"HTTP Status {response.status}"
                except Exception as retry_err:
                    return False, f"Discord SSL Error: {retry_err}"
            return False, f"Discord Error: {e}"

    # -------------------------------------------------------------------------
    # Public Connection Test
    # -------------------------------------------------------------------------
    @classmethod
    def test_connection(cls, platform: str, token_or_url: str, chat_id: Optional[str] = None) -> Tuple[bool, str]:
        """Synchronous connection test triggered from the interactive menu."""
        test_msg = f"🔔 <b>[DCC BOT CONNECTION TEST]</b>\n\nYour remote notification channel is active and operational!\nTime: {format_dual_time(include_date=True)}"
        if platform == "telegram":
            if not token_or_url or not chat_id:
                return False, "Bot Token and Chat ID cannot be empty."
            return cls._send_telegram_http(token_or_url.strip(), chat_id.strip(), test_msg, timeout=8)
        elif platform == "discord":
            if not token_or_url:
                return False, "Discord Webhook URL cannot be empty."
            embed = {
                "title": "🔔 DCC BOT CONNECTION TEST",
                "description": "Your remote Discord notification channel is active and operational!",
                "color": 0x00FFAA,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": f"Tested at {format_dual_time(include_date=True)}"}
            }
            return cls._send_discord_http(token_or_url.strip(), "", embed=embed, timeout=8)
        return False, f"Unknown platform: {platform}"

    # -------------------------------------------------------------------------
    # Event Notification Formatters (Dual UTC & IST Timestamps)
    # -------------------------------------------------------------------------
    def notify_startup(self, account_id: int, server: str, mode: str, equity: float, balance: float,
                       risk_pct: float, daily_dd: float, max_dd: float, symbols: list,
                       daily_cb: Optional[float] = None, max_cb: Optional[float] = None):
        """Notifies that the bot has started."""
        mode_tag = "DRY RUN (Paper)" if mode == "dry_run" else "LIVE EXECUTION"
        now_str = format_dual_time(include_date=True)

        d_cb_str = f" | CB: -{daily_cb:.1f}%" if daily_cb is not None else ""
        m_cb_str = f" | CB: -{max_cb:.1f}%" if max_cb is not None else ""

        tg_text = (
            f"🚀 <b>DCC BOT STARTED [{mode_tag}]</b>\n\n"
            f"• <b>Account:</b> {account_id} ({server})\n"
            f"• <b>Equity:</b> ${equity:,.2f} | <b>Balance:</b> ${balance:,.2f}\n"
            f"• <b>Risk/Trade:</b> {risk_pct:.1f}%\n"
            f"• <b>Daily DD Limit:</b> -{daily_dd:.1f}%{d_cb_str}\n"
            f"• <b>Max Total DD:</b> -{max_dd:.1f}%{m_cb_str}\n"
            f"• <b>Assets:</b> {', '.join(symbols)}\n"
            f"• <b>Started:</b> {now_str}"
        )

        discord_embed = {
            "title": f"🚀 DCC BOT STARTED [{mode_tag}]",
            "color": 0x00FF88 if mode == "live" else 0xFFAA00,
            "fields": [
                {"name": "Account", "value": f"{account_id} ({server})", "inline": True},
                {"name": "Equity / Balance", "value": f"${equity:,.2f} / ${balance:,.2f}", "inline": True},
                {"name": "Risk / Trade", "value": f"{risk_pct:.1f}%", "inline": True},
                {"name": "Daily DD (Limit / CB)", "value": f"-{daily_dd:.1f}%" + (f" / -{daily_cb:.1f}%" if daily_cb else ""), "inline": True},
                {"name": "Max DD (Limit / CB)", "value": f"-{max_dd:.1f}%" + (f" / -{max_cb:.1f}%" if max_cb else ""), "inline": True},
                {"name": "Assets", "value": ", ".join(symbols), "inline": True}
            ],
            "footer": {"text": f"Started at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_shutdown(self, reason: str = "User Stopped"):
        now_str = format_dual_time(include_date=True)
        tg_text = f"🛑 <b>DCC BOT SHUTDOWN</b>\n\n• <b>Reason:</b> {reason}\n• <b>Time:</b> {now_str}"
        discord_embed = {
            "title": "🛑 DCC BOT SHUTDOWN",
            "description": f"**Reason:** {reason}",
            "color": 0xFF5555,
            "footer": {"text": f"Stopped at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_session(self, session_name: str, status: str):
        now_str = format_dual_time(include_date=False)
        tg_text = f"⏰ <b>SESSION UPDATE: {session_name} {status}</b>\nTime: {now_str}"
        discord_embed = {
            "title": f"⏰ SESSION UPDATE: {session_name} {status}",
            "color": 0x3399FF,
            "footer": {"text": f"Time: {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_nightly_audit(self, title: str, summary_text: str, discord_fields: Optional[List[Dict]] = None):
        """Dispatches nightly audit scorecard to Telegram/Discord."""
        tg_text = f"<b>{title}</b>\n\n{summary_text}"
        discord_embed = {
            "title": title,
            "description": summary_text,
            "color": 0x9B59B6,
            "fields": discord_fields or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "DCC Nightly Audit & Reconciliation Engine"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_daily_summary(
        self,
        date_str: str,
        starting_equity: float,
        closing_equity: float,
        balance: float,
        trades_count: int,
        winning_trades: int,
        losing_trades: int,
        daily_pnl: float,
        daily_pnl_pct: float,
        daily_dd_pct: float,
        cushion_remaining: float,
        closed_trades_details: Optional[List[Dict]] = None
    ):
        """Broadcasts end-of-day summary scorecard with realized PnL, trades count, and cushion status."""
        pnl_str = format_dollar(daily_pnl, force_sign=True)
        sign_char = "+" if daily_pnl >= 0 else "-"
        pnl_pct_str = f"{sign_char}{abs(daily_pnl_pct):.2f}%"
        now_str = format_dual_time(include_date=True)

        if daily_pnl > 0:
            status_icon = "🟢"
            status_text = "PROFITABLE SESSION"
            discord_color = 0x00FF88
        elif daily_pnl < 0:
            status_icon = "🔴"
            status_text = "DEFENSIVE DRAWDOWN (PROTECTED)"
            discord_color = 0xFF4444
        else:
            status_icon = "⚪"
            status_text = "CAPITAL PRESERVED (FLAT)"
            discord_color = 0x3399FF

        tg_lines = [
            f"{status_icon} <b>DCC DAILY PERFORMANCE & PnL SUMMARY</b>",
            f"<b>Date:</b> <code>{date_str}</code> | <b>Status:</b> <code>{status_text}</code>\n",
            f"💰 <b>Net Realized PnL:</b> <code>{pnl_str} ({pnl_pct_str})</code>",
            f"📊 <b>Total Trades Today:</b> <code>{trades_count}</code> (✅ {winning_trades} W | ❌ {losing_trades} L)",
            f"📉 <b>Daily Peak DD:</b> <code>-{daily_dd_pct:.2f}%</code>",
            f"🛡️ <b>Remaining 3% CB Cushion:</b> <code>${cushion_remaining:,.2f}</code>\n",
            f"🏦 <b>Starting Equity Anchor:</b> <code>${starting_equity:,.2f}</code>",
            f"💼 <b>Closing Equity:</b> <code>${closing_equity:,.2f}</code>",
            f"💳 <b>Account Balance:</b> <code>${balance:,.2f}</code>\n",
        ]

        if closed_trades_details and len(closed_trades_details) > 0:
            tg_lines.append("<b>Closed Trades Breakdown:</b>")
            for tr in closed_trades_details:
                tr_pnl = format_dollar(tr.get('profit', 0.0))
                tg_lines.append(f"• #{tr.get('ticket', 'N/A')} {tr.get('symbol', '')} {tr.get('type', '')} {tr.get('volume', '')}L: <b>{tr_pnl}</b> ({tr.get('comment', '')})")
        else:
            tg_lines.append("<i>No strategy trades triggered today. Capital was 100% protected.</i>")

        tg_lines.append(f"\n🕒 <i>Session closed at {now_str}</i>")
        tg_text = "\n".join(tg_lines)

        discord_fields = [
            {"name": "Net Realized PnL", "value": f"**{pnl_str}** ({pnl_pct_str})", "inline": True},
            {"name": "Trades Count", "value": f"**{trades_count}** ({winning_trades}W / {losing_trades}L)", "inline": True},
            {"name": "Daily DD", "value": f"**-{daily_dd_pct:.2f}%**", "inline": True},
            {"name": "Starting Equity", "value": f"${starting_equity:,.2f}", "inline": True},
            {"name": "Closing Equity", "value": f"${closing_equity:,.2f}", "inline": True},
            {"name": "3% CB Cushion", "value": f"${cushion_remaining:,.2f}", "inline": True},
        ]

        if closed_trades_details and len(closed_trades_details) > 0:
            trade_summary_lines = []
            for tr in closed_trades_details[:8]:
                tr_pnl = format_dollar(tr.get('profit', 0.0))
                trade_summary_lines.append(f"`#{tr.get('ticket', 'N/A')}` **{tr.get('symbol', '')}** {tr.get('type', '')} ({tr.get('volume', '')}L) -> **{tr_pnl}**")
            discord_fields.append({
                "name": "Closed Trades Detail",
                "value": "\n".join(trade_summary_lines),
                "inline": False
            })
        else:
            discord_fields.append({
                "name": "Trade Activity",
                "value": "Zero strategy setups met strict institutional criteria today. Capital 100% preserved.",
                "inline": False
            })

        discord_embed = {
            "title": f"🌙 DCC Daily Performance & PnL Summary ({date_str})",
            "description": f"**Session Status:** `{status_text}`\nInstitutional session close reached. All risk cushions verified.",
            "color": discord_color,
            "fields": discord_fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": f"DCC Automated Trading System • {now_str}"}
        }

        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trading_paused(self, zone_title: str, reason: str, resume_time_str: str, is_startup: bool = False):
        """Notifies when trading surveillance and execution are paused (Killzone Pause / Dead Trap Hour / Asian Range)."""
        now_str = format_dual_time(include_date=False)
        res_dual = format_dual_hhmm(resume_time_str)
        prefix = "CURRENT STATUS: " if is_startup else ""

        tg_text = (
            f"⏸️ <b>{prefix}TRADING PAUSED: {zone_title}</b>\n\n"
            f"• <b>Reason:</b> {reason}\n"
            f"• <b>Status:</b> Scanning & trade execution suspended\n"
            f"• <b>Resumes:</b> {res_dual}\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_embed = {
            "title": f"⏸️ {prefix}TRADING PAUSED: {zone_title}",
            "description": f"**Status:** Scanning & trade execution suspended to protect capital.\n**Reason:** {reason}",
            "color": 0xFFAA00,
            "fields": [
                {"name": "Status", "value": "PAUSED", "inline": True},
                {"name": "Resumes At", "value": res_dual, "inline": True}
            ],
            "footer": {"text": f"Paused at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trading_resumed(self, zone_title: str, session_name: str, details: str = "Active market surveillance and trade execution restored."):
        """Notifies when trading surveillance and execution resume after a pause or session transition."""
        now_str = format_dual_time(include_date=False)

        tg_text = (
            f"▶️ <b>TRADING RESUMED: {zone_title}</b>\n\n"
            f"• <b>Active Window:</b> {session_name}\n"
            f"• <b>Status:</b> {details}\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_embed = {
            "title": f"▶️ TRADING RESUMED: {zone_title}",
            "description": f"**Status:** {details}",
            "color": 0x00FF88,
            "fields": [
                {"name": "Active Window", "value": session_name, "inline": True},
                {"name": "Status", "value": "ACTIVE", "inline": True}
            ],
            "footer": {"text": f"Resumed at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    # Killzone convenience aliases for ICT terminology
    notify_killzone_paused = notify_trading_paused
    notify_killzone_resumed = notify_trading_resumed

    def notify_trade_opened(self, symbol: str, direction: str, entry_price: float, spread: float,
                            total_lots: float, partial_lots: float, runner_lots: float,
                            sl: float, tp1: float, tp2: float, ticket_a: int, ticket_b: int, is_dry_run: bool = False):
        """Notifies that a new twin-ticket trade has been entered."""
        icon = "🟢" if direction == "BUY" else "🔴"
        mode_lbl = " [SIMULATED]" if is_dry_run else ""
        now_str = format_dual_time(include_date=True)

        tg_text = (
            f"{icon} <b>NEW TRADE ENTERED: {symbol} {direction}{mode_lbl}</b>\n\n"
            f"• <b>Entry Price:</b> {entry_price:,.2f} (Spread: {spread:.2f})\n"
            f"• <b>Total Volume:</b> {total_lots} lots (A: {partial_lots} | B: {runner_lots})\n"
            f"• <b>Stop Loss (SL):</b> {sl:,.2f}\n"
            f"• <b>Target 1 (TP1 50%):</b> {tp1:,.2f}\n"
            f"• <b>Target 2 (Runner TP2):</b> {tp2:,.2f}\n"
            f"• <b>Tickets:</b> #{ticket_a} (Part) / #{ticket_b} (Runner)\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_embed = {
            "title": f"{icon} NEW TRADE ENTERED: {symbol} {direction}{mode_lbl}",
            "color": 0x00FF88 if direction == "BUY" else 0xFF4444,
            "fields": [
                {"name": "Entry", "value": f"{entry_price:,.2f} (Spr: {spread:.2f})", "inline": True},
                {"name": "Lots", "value": f"Total: {total_lots} (A: {partial_lots} / B: {runner_lots})", "inline": True},
                {"name": "Stop Loss", "value": f"{sl:,.2f}", "inline": True},
                {"name": "Target 1 (TP1 50%)", "value": f"{tp1:,.2f}", "inline": True},
                {"name": "Target 2 (TP2 Runner)", "value": f"{tp2:,.2f}", "inline": True},
                {"name": "Order Tickets", "value": f"A: #{ticket_a} | B: #{ticket_b}", "inline": True}
            ],
            "footer": {"text": f"Filled at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_tp1_breakeven(self, symbol: str, ticket_a: int, ticket_b: int, be_sl: float, profit_a: float = 0.0):
        """Notifies when Ticket A hits TP1 and Runner SL is moved to Breakeven."""
        now_str = format_dual_time(include_date=False)
        pnl_str = f"• 💰 <b>Profit Banked (Order A):</b> <b>{format_dollar(profit_a)}</b>\n" if profit_a != 0.0 else ""
        tg_text = (
            f"🎯 <b>TARGET 1 HIT: {symbol} (+50% PROFIT LOCKED)</b>\n\n"
            f"• Ticket #{ticket_a} closed at TP1!\n"
            f"{pnl_str}"
            f"• 🛡️ <b>BREAKEVEN AUTOMATOR:</b> Runner #{ticket_b} Stop Loss shifted to <b>{be_sl:,.2f}</b> (Entry + Spread)!\n"
            f"• <b>Status: Trade is now 100% RISK-FREE!</b>\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_fields = [
            {"name": "Order A Closed", "value": f"#{ticket_a} (TP1 Hit)", "inline": True},
            {"name": "Runner SL (BE)", "value": f"`{be_sl:,.2f}`", "inline": True}
        ]
        if profit_a != 0.0:
            discord_fields.insert(1, {"name": "Profit Locked", "value": f"**{format_dollar(profit_a)}**", "inline": True})
        else:
            discord_fields.insert(1, {"name": "Status", "value": "100% Risk-Free", "inline": True})

        pnl_desc = f"**Profit Banked: {format_dollar(profit_a)}**\n" if profit_a != 0.0 else ""
        discord_embed = {
            "title": f"🎯 TARGET 1 HIT: {symbol} (+50% PROFIT LOCKED)",
            "description": f"Ticket #{ticket_a} hit TP1!\n{pnl_desc}🛡️ Runner #{ticket_b} SL shifted to **{be_sl:,.2f}** (Entry + Spread). **Trade is now 100% Risk-Free!**",
            "color": 0x00FFAA,
            "fields": discord_fields,
            "footer": {"text": f"Locked at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trade_closed(
        self,
        symbol: str,
        ticket: int,
        exit_reason: str,
        pnl: float,
        ticket_a: Optional[int] = None,
        pnl_a: Optional[float] = None,
        pnl_b: Optional[float] = None,
        day_pnl: Optional[float] = None,
        day_pnl_pct: Optional[float] = None,
        daily_dd_pct: Optional[float] = None,
        remaining_cushion: Optional[float] = None,
        daily_cb_pct: Optional[float] = None
    ):
        """Notifies when an open position or runner is closed with full breakdown & daily metrics."""
        now_str = format_dual_time(include_date=False)
        
        # Determine Icon & Status Color
        clean_reason = exit_reason.upper()
        if "TP2" in clean_reason or "TAKE PROFIT" in clean_reason:
            icon = "🎯"
            color = 0x00FF88
            header_status = "FULL TP2 WINNER"
        elif "BREAKEVEN" in clean_reason:
            icon = "🛡️"
            color = 0x00D4FF
            header_status = "RUNNER AT BREAKEVEN"
        elif pnl < 0 or "STOP LOSS" in clean_reason:
            icon = "🛑"
            color = 0xFF4444
            header_status = "STOP LOSS HIT"
        else:
            icon = "🚀" if pnl > 0 else ("🛡️" if abs(pnl) < 1.0 else "🛑")
            color = 0x00FF88 if pnl > 0 else (0x888888 if abs(pnl) < 1.0 else 0xFF4444)
            header_status = "TRADE CLOSED"

        # Build Order Breakdown
        breakdown_lines = []
        if pnl_a is not None and pnl_b is not None and ticket_a is not None:
            breakdown_lines.append(f"• <b>Order A (#{ticket_a}):</b> {format_dollar(pnl_a)}")
            breakdown_lines.append(f"• <b>Order B (#{ticket}):</b> {format_dollar(pnl_b)}")
        breakdown_str = ("\n" + "\n".join(breakdown_lines) + "\n") if breakdown_lines else ""

        # Build Daily Performance Metrics
        daily_lines = []
        if day_pnl is not None:
            pct_str = f" ({day_pnl_pct:+.2f}%)" if day_pnl_pct is not None else ""
            daily_lines.append(f"• <b>Today's Net PnL:</b> <b>{format_dollar(day_pnl)}</b>{pct_str}")
        if daily_dd_pct is not None:
            daily_lines.append(f"• <b>Daily Drawdown:</b> -{daily_dd_pct:.2f}%")
        if remaining_cushion is not None:
            cb_str = f"{daily_cb_pct:.1f}% CB" if daily_cb_pct else "Circuit Breaker"
            daily_lines.append(f"• <b>Daily CB Cushion:</b> ${remaining_cushion:,.2f} remaining before {cb_str}")
        daily_str = ("\n📊 <b>Daily Performance Summary:</b>\n" + "\n".join(daily_lines) + "\n") if daily_lines else ""

        tg_text = (
            f"{icon} <b>TRADE CLOSED: {symbol} — {header_status}</b>\n\n"
            f"• <b>Outcome:</b> {exit_reason}\n"
            f"{breakdown_str}"
            f"• <b>Total Trade PnL:</b> <b>{format_dollar(pnl)}</b>\n"
            f"{daily_str}"
            f"• <b>Closed at:</b> {now_str}"
        )

        discord_fields = [
            {"name": "Outcome", "value": exit_reason, "inline": True},
            {"name": "Total Trade PnL", "value": f"**{format_dollar(pnl)}**", "inline": True}
        ]

        if pnl_a is not None and pnl_b is not None and ticket_a is not None:
            discord_fields.append({
                "name": "Order Breakdown",
                "value": f"Order A (#{ticket_a}): `{format_dollar(pnl_a)}`\nOrder B (#{ticket}): `{format_dollar(pnl_b)}`",
                "inline": False
            })

        if day_pnl is not None:
            pct_str = f" ({day_pnl_pct:+.2f}%)" if day_pnl_pct is not None else ""
            discord_fields.append({
                "name": "Today's Net PnL",
                "value": f"**{format_dollar(day_pnl)}**{pct_str}",
                "inline": True
            })

        if daily_dd_pct is not None:
            discord_fields.append({
                "name": "Daily Drawdown",
                "value": f"`-{daily_dd_pct:.2f}%`",
                "inline": True
            })

        if remaining_cushion is not None:
            cb_str = f"{daily_cb_pct:.1f}% CB" if daily_cb_pct else "Circuit Breaker"
            discord_fields.append({
                "name": "Remaining CB Cushion",
                "value": f"`${remaining_cushion:,.2f}` (to {cb_str})",
                "inline": True
            })

        discord_embed = {
            "title": f"{icon} TRADE CLOSED: {symbol} — {header_status}",
            "color": color,
            "fields": discord_fields,
            "footer": {"text": f"Closed at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_challenge_passed(self, account_id: str, phase: Any, target_pct: float, current_balance: float, profit_dollar: float, profit_pct: float):
        """Notifies when a prop firm evaluation challenge target is successfully reached."""
        now_str = format_dual_time(include_date=True)
        tg_text = (
            f"🏆🏆 <b>PROP FIRM CHALLENGE TARGET REACHED! (PHASE {phase})</b> 🏆🏆\n\n"
            f"• <b>Account:</b> {account_id}\n"
            f"• <b>Target:</b> +{target_pct:.1f}% PASSED!\n"
            f"• <b>Current Balance:</b> ${current_balance:,.2f}\n"
            f"• <b>Total Profit:</b> +${profit_dollar:,.2f} (+{profit_pct:.2f}%)\n"
            f"• <b>Status:</b> Trading HALTED to protect your pass and prevent overtrading.\n"
            f"• <b>Time:</b> {now_str}"
        )
        discord_embed = {
            "title": f"🏆 PROP FIRM CHALLENGE PASSED! (Phase {phase}: +{target_pct:.1f}%) 🏆",
            "description": f"**Evaluation target reached! Trading halted to lock in the pass.**\n\n• **Account:** `{account_id}`\n• **Balance:** `${current_balance:,.2f}`\n• **Profit:** `+${profit_dollar:,.2f} (+{profit_pct:.2f}%)`",
            "color": 0x00FF00,
            "footer": {"text": f"Passed at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_circuit_breaker(self, cb_type: str, current_equity: float, limit_pct: float, loss_amount: float, hard_limit_pct: Optional[float] = None):
        """Emergency notification when a circuit breaker triggers."""
        now_str = format_dual_time(include_date=True)
        hard_note = f" (Hard Limit: -{hard_limit_pct:.1f}%)" if hard_limit_pct is not None else ""
        if cb_type == "daily":
            tg_text = (
                f"🚨 <b>DAILY CIRCUIT BREAKER TRIGGERED (-{limit_pct:.1f}%){hard_note}</b> 🚨\n\n"
                f"• Current Equity: ${current_equity:,.2f}\n"
                f"• Today's Loss: -${loss_amount:,.2f} (-{limit_pct:.1f}%)\n"
                f"• <b>ACTION: Trading HALTED for remainder of day.</b> Account protected before hitting hard DD limit.\n"
                f"• <b>Time:</b> {now_str}"
            )
            discord_embed = {
                "title": f"🚨 DAILY CIRCUIT BREAKER TRIGGERED (-{limit_pct:.1f}%){hard_note} 🚨",
                "description": f"**Trading HALTED for remainder of day.** Protected before hitting hard DD limit.\nCurrent Equity: ${current_equity:,.2f} | Loss: -${loss_amount:,.2f}",
                "color": 0xFF9900,
                "footer": {"text": f"Triggered at {now_str}"}
            }
        else:
            tg_text = (
                f"🚨🚨 <b>EMERGENCY: MAX ACCOUNT CIRCUIT BREAKER (-{limit_pct:.1f}%){hard_note} REACHED</b> 🚨🚨\n\n"
                f"• Current Equity: ${current_equity:,.2f}\n"
                f"• <b>ACTION: All positions liquidated. Bot permanently halted to protect against hard limit breach.</b>\n"
                f"• <b>Time:</b> {now_str}"
            )
            discord_embed = {
                "title": f"🚨🚨 EMERGENCY: MAX ACCOUNT CIRCUIT BREAKER (-{limit_pct:.1f}%){hard_note} REACHED 🚨🚨",
                "description": f"**All positions liquidated. Bot permanently halted before hitting hard DD limit.**\nCurrent Equity: ${current_equity:,.2f}",
                "color": 0xFF0000,
                "footer": {"text": f"Triggered at {now_str}"}
            }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_news_shield_activated(self, title: str, country: str, release_time_str: str, resume_time_str: str):
        """Notifies when the High-Impact News Shield is activated (15m before news)."""
        now_str = format_dual_time(include_date=False)
        rel_dual = format_dual_hhmm(release_time_str)
        res_dual = format_dual_hhmm(resume_time_str)
        tg_text = (
            f"⚠️ <b>HIGH-IMPACT NEWS SHIELD ACTIVATED</b>\n\n"
            f"• <b>Event:</b> {country} - {title}\n"
            f"• <b>Release Time:</b> {rel_dual}\n"
            f"• <b>Trading Suspended:</b> 15m before & after news\n"
            f"• <b>Resumes At:</b> {res_dual}\n"
            f"• <b>Status:</b> All trade arming paused to protect against news spread spikes.\n"
            f"• <b>Time:</b> {now_str}"
        )
        discord_embed = {
            "title": f"⚠️ HIGH-IMPACT NEWS SHIELD ACTIVATED ({country})",
            "description": f"**Event:** {country} - {title}\n**Release Time:** {rel_dual}\n**Trading Suspended:** Until {res_dual} (15m before & after)",
            "color": 0xFFAA00,
            "fields": [
                {"name": "Impact", "value": "HIGH (Red Folder)", "inline": True},
                {"name": "Status", "value": "Trade Arming Paused", "inline": True},
                {"name": "Resumes", "value": res_dual, "inline": True}
            ],
            "footer": {"text": f"Activated at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_news_shield_lifted(self, title: str, resume_time_str: str):
        """Notifies when the High-Impact News Shield is lifted (15m after news)."""
        now_str = format_dual_time(include_date=False)
        res_dual = format_dual_hhmm(resume_time_str)
        tg_text = (
            f"✅ <b>HIGH-IMPACT NEWS SHIELD LIFTED</b>\n\n"
            f"• <b>Event Passed:</b> {title}\n"
            f"• <b>Resumed At:</b> {res_dual}\n"
            f"• <b>Status:</b> Normal market surveillance and trade execution restored.\n"
            f"• <b>Time:</b> {now_str}"
        )
        discord_embed = {
            "title": "✅ HIGH-IMPACT NEWS SHIELD LIFTED",
            "description": f"**Event Passed:** {title}\n**Resumed At:** {res_dual}\n**Status:** Trading resumed. Normal market surveillance restored.",
            "color": 0x00FF88,
            "footer": {"text": f"Lifted at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_setup_armed(self, symbol: str, direction: str, planned_entry: float, planned_sl: float,
                           planned_tp1: float, planned_lots: float, reason: str, bar_time: Optional[datetime] = None):
        """Notifies when a high-probability DCC candidate setup is armed on 5M close."""
        now_str = format_dual_time(include_date=False)
        bar_str = format_dual_time(bar_time, include_date=False) if bar_time else ""
        icon = "🎯"
        dir_icon = "🟢" if direction == "BUY" else "🔴"
        digits = 2 if "XAU" in symbol else 1

        bar_line = f"• <b>Bar Close (5M):</b> {bar_str}\n" if bar_str else ""

        tg_text = (
            f"{icon} <b>SETUP ARMED: {symbol} {direction} {dir_icon}</b>\n\n"
            f"{bar_line}"
            f"• <b>Condition:</b> {reason}\n"
            f"• <b>Planned Entry:</b> {planned_entry:.{digits}f}\n"
            f"• <b>Planned SL:</b> {planned_sl:.{digits}f}\n"
            f"• <b>Planned TP1:</b> {planned_tp1:.{digits}f}\n"
            f"• <b>Calculated Volume:</b> {planned_lots} lots\n"
            f"• <b>Action:</b> Streaming live ticks in final 2 minutes for breakout confirmation...\n"
            f"• <b>Time:</b> {now_str}"
        )
        fields = [
            {"name": "Planned Entry", "value": f"{planned_entry:.{digits}f}", "inline": True},
            {"name": "Planned SL", "value": f"{planned_sl:.{digits}f}", "inline": True},
            {"name": "Planned TP1", "value": f"{planned_tp1:.{digits}f}", "inline": True},
            {"name": "Volume", "value": f"{planned_lots} lots", "inline": True},
            {"name": "Trigger Condition", "value": "Projected EMA9/20 Flip + Clean Spread", "inline": True}
        ]
        if bar_str:
            fields.insert(0, {"name": "Bar Close (5M)", "value": bar_str, "inline": True})

        discord_embed = {
            "title": f"{icon} SETUP ARMED: {symbol} {direction} {dir_icon}",
            "description": f"**Setup Confirmed:** {reason}\n*Streaming ticks in final 2 mins for breakout trigger...*",
            "color": 0x33CCFF,
            "fields": fields,
            "footer": {"text": f"Armed at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_setup_aborted(self, symbol: str, direction: str, abort_reason: str,
                             last_price: float, last_spread: float):
        """Notifies when an armed setup fails to trigger and is safely dropped."""
        now_str = format_dual_time(include_date=False)
        digits = 2 if "XAU" in symbol else 1

        tg_text = (
            f"❌ <b>ARMED SETUP DROPPED: {symbol} {direction}</b>\n\n"
            f"• <b>Reason for Dropping:</b> {abort_reason}\n"
            f"• <b>Price at Boundary:</b> {last_price:.{digits}f} (Spread: {last_spread:.2f})\n"
            f"• <b>Action:</b> Order NOT placed. Capital safely preserved.\n"
            f"• <b>Time:</b> {now_str}"
        )
        discord_embed = {
            "title": f"❌ ARMED SETUP DROPPED: {symbol} {direction}",
            "description": f"**Trade Aborted:** {abort_reason}\n*Order was NOT placed. Account capital safely preserved.*",
            "color": 0xFF8800,
            "fields": [
                {"name": "Symbol", "value": symbol, "inline": True},
                {"name": "Direction", "value": direction, "inline": True},
                {"name": "Spread at Close", "value": f"{last_spread:.2f}", "inline": True}
            ],
            "footer": {"text": f"Dropped at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

