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

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request


class NotificationManager:
    """Manages remote alerts for Telegram or Discord in a non-blocking background queue."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {
            "active_platform": "none",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "discord_webhook_url": ""
        }
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
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    return True, "Telegram message sent successfully."
                return False, f"HTTP Status {response.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"Telegram HTTPError: {err_msg}"
        except Exception as e:
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
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status in (200, 204):
                    return True, "Discord notification sent successfully."
                return False, f"HTTP Status {response.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"Discord HTTPError: {err_msg}"
        except Exception as e:
            return False, f"Discord Error: {e}"

    # -------------------------------------------------------------------------
    # Public Connection Test
    # -------------------------------------------------------------------------
    @classmethod
    def test_connection(cls, platform: str, token_or_url: str, chat_id: Optional[str] = None) -> Tuple[bool, str]:
        """Synchronous connection test triggered from the interactive menu."""
        test_msg = f"🔔 <b>[DCC BOT CONNECTION TEST]</b>\n\nYour remote notification channel is active and operational!\nTime: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
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
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            return cls._send_discord_http(token_or_url.strip(), "", embed=embed, timeout=8)
        return False, f"Unknown platform: {platform}"

    # -------------------------------------------------------------------------
    # Event Notification Formatters
    # -------------------------------------------------------------------------
    def notify_startup(self, account_id: int, server: str, mode: str, equity: float, balance: float,
                       risk_pct: float, daily_dd: float, max_dd: float, symbols: list):
        """Notifies that the bot has started."""
        mode_tag = "DRY RUN (Paper)" if mode == "dry_run" else "LIVE EXECUTION"
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        tg_text = (
            f"🚀 <b>DCC BOT STARTED [{mode_tag}]</b>\n\n"
            f"• <b>Account:</b> {account_id} ({server})\n"
            f"• <b>Equity:</b> ${equity:,.2f} | <b>Balance:</b> ${balance:,.2f}\n"
            f"• <b>Risk/Trade:</b> {risk_pct:.1f}%\n"
            f"• <b>Daily DD Limit:</b> -{daily_dd:.1f}% (3% Circuit Breaker)\n"
            f"• <b>Max Total DD:</b> -{max_dd:.1f}%\n"
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
                {"name": "Daily DD Breaker", "value": f"-{daily_dd:.1f}%", "inline": True},
                {"name": "Max DD Breaker", "value": f"-{max_dd:.1f}%", "inline": True},
                {"name": "Assets", "value": ", ".join(symbols), "inline": True}
            ],
            "footer": {"text": f"Started at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_shutdown(self, reason: str = "User Stopped"):
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        tg_text = f"🛑 <b>DCC BOT SHUTDOWN</b>\n\n• <b>Reason:</b> {reason}\n• <b>Time:</b> {now_str}"
        discord_embed = {
            "title": "🛑 DCC BOT SHUTDOWN",
            "description": f"**Reason:** {reason}",
            "color": 0xFF5555,
            "footer": {"text": f"Stopped at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_session(self, session_name: str, status: str):
        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        tg_text = f"⏰ <b>SESSION UPDATE: {session_name} {status}</b>\nTime: {now_str}"
        discord_embed = {
            "title": f"⏰ SESSION UPDATE: {session_name} {status}",
            "color": 0x3399FF,
            "footer": {"text": f"Time: {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trade_opened(self, symbol: str, direction: str, entry_price: float, spread: float,
                            total_lots: float, partial_lots: float, runner_lots: float,
                            sl: float, tp1: float, tp2: float, ticket_a: int, ticket_b: int, is_dry_run: bool = False):
        """Notifies that a new twin-ticket trade has been entered."""
        icon = "🟢" if direction == "BUY" else "🔴"
        mode_lbl = " [SIMULATED]" if is_dry_run else ""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

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

    def notify_tp1_breakeven(self, symbol: str, ticket_a: int, ticket_b: int, be_sl: float):
        """Notifies when Ticket A hits TP1 and Runner SL is moved to Breakeven."""
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        tg_text = (
            f"🎯 <b>TARGET 1 HIT: {symbol} (+50% PROFIT LOCKED)</b>\n\n"
            f"• Ticket #{ticket_a} closed at TP1!\n"
            f"• 🛡️ <b>BREAKEVEN AUTOMATOR:</b> Runner #{ticket_b} Stop Loss shifted to <b>{be_sl:,.2f}</b> (Entry + Spread)!\n"
            f"• <b>Status: Trade is now 100% RISK-FREE!</b>\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_embed = {
            "title": f"🎯 TARGET 1 HIT: {symbol} (+50% PROFIT LOCKED)",
            "description": f"Ticket #{ticket_a} hit TP1! Runner #{ticket_b} SL shifted to **{be_sl:,.2f}** (Entry + Spread). **Trade is now 100% Risk-Free!**",
            "color": 0x00FFAA,
            "footer": {"text": f"Locked at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_trade_closed(self, symbol: str, ticket: int, exit_reason: str, pnl: float):
        """Notifies when an open position or runner is closed."""
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        icon = "🚀" if pnl > 0 else ("🛡️" if abs(pnl) < 1.0 else "🛑")
        color = 0x00FF88 if pnl > 0 else (0x888888 if abs(pnl) < 1.0 else 0xFF4444)

        tg_text = (
            f"{icon} <b>TRADE CLOSED: {symbol} #{ticket}</b>\n\n"
            f"• <b>Outcome:</b> {exit_reason}\n"
            f"• <b>Net PnL:</b> {'+' if pnl >= 0 else ''}${pnl:,.2f}\n"
            f"• <b>Time:</b> {now_str}"
        )

        discord_embed = {
            "title": f"{icon} TRADE CLOSED: {symbol} #{ticket}",
            "color": color,
            "fields": [
                {"name": "Outcome", "value": exit_reason, "inline": True},
                {"name": "Net PnL", "value": f"{'+' if pnl >= 0 else ''}${pnl:,.2f}", "inline": True}
            ],
            "footer": {"text": f"Closed at {now_str}"}
        }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})

    def notify_circuit_breaker(self, cb_type: str, current_equity: float, limit_pct: float, loss_amount: float):
        """Emergency notification when a circuit breaker triggers."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if cb_type == "daily":
            tg_text = (
                f"🚨 <b>DAILY CIRCUIT BREAKER TRIGGERED (-{limit_pct:.1f}%)</b> 🚨\n\n"
                f"• Current Equity: ${current_equity:,.2f}\n"
                f"• Today's Loss: -${loss_amount:,.2f} (-{limit_pct:.1f}%)\n"
                f"• <b>ACTION: Trading HALTED for remainder of day.</b> All setup arming disabled until 00:00 UTC.\n"
                f"• <b>Time:</b> {now_str}"
            )
            discord_embed = {
                "title": f"🚨 DAILY CIRCUIT BREAKER TRIGGERED (-{limit_pct:.1f}%) 🚨",
                "description": f"**Trading HALTED for the day until 00:00 UTC.**\nCurrent Equity: ${current_equity:,.2f} | Loss: -${loss_amount:,.2f}",
                "color": 0xFF9900,
                "footer": {"text": f"Triggered at {now_str}"}
            }
        else:
            tg_text = (
                f"🚨🚨 <b>EMERGENCY: MAX ACCOUNT DRAWDOWN (-{limit_pct:.1f}%) REACHED</b> 🚨🚨\n\n"
                f"• Current Equity: ${current_equity:,.2f}\n"
                f"• <b>ACTION: All positions liquidated. Bot permanently halted to protect capital.</b>\n"
                f"• <b>Time:</b> {now_str}"
            )
            discord_embed = {
                "title": f"🚨🚨 EMERGENCY: MAX ACCOUNT DRAWDOWN (-{limit_pct:.1f}%) REACHED 🚨🚨",
                "description": f"**All positions liquidated. Bot permanently halted to protect capital.**\nCurrent Equity: ${current_equity:,.2f}",
                "color": 0xFF0000,
                "footer": {"text": f"Triggered at {now_str}"}
            }
        self._enqueue({"text": tg_text, "content": "", "embed": discord_embed})
