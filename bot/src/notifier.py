"""
Telegram notification system.

Sends trade alerts and daily summary to a Telegram chat.
All calls are fire-and-forget (no blocking in the trading loop).

Setup:
  1. Message @BotFather on Telegram → create bot → get TELEGRAM_BOT_TOKEN
  2. Message @userinfobot → get your TELEGRAM_CHAT_ID
  3. Set both in .env
"""

import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: dict):
        self._enabled = cfg["notifications"]["telegram_enabled"]
        self._token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self._cfg = cfg["notifications"]

        if self._enabled and (not self._token or not self._chat_id):
            logger.warning("Telegram enabled but credentials missing — notifications disabled")
            self._enabled = False

    # ------------------------------------------------------------------
    # Trade events
    # ------------------------------------------------------------------

    def trade_entry(self, symbol: str, setup: str, qty: int, entry: float, stop: float, target: float) -> None:
        if not self._cfg["alert_on_entry"]:
            return
        risk = round((entry - stop) * qty, 2)
        reward = round((target - entry) * qty, 2)
        msg = (
            f"BUY {symbol} x{qty}\n"
            f"Setup: {setup.replace('_', ' ').title()}\n"
            f"Entry: ${entry:.2f} | Stop: ${stop:.2f} | Target: ${target:.2f}\n"
            f"Risk: -${risk} | Reward: +${reward}\n"
            f"Time: {_now_et()}"
        )
        self._send(msg)

    def trade_exit(self, symbol: str, qty: int, exit_price: float, pnl: float, reason: str) -> None:
        if not self._cfg["alert_on_exit"]:
            return
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"{emoji} SELL {symbol} x{qty}\n"
            f"Exit: ${exit_price:.2f} | PnL: ${pnl:+.2f}\n"
            f"Reason: {reason}\n"
            f"Time: {_now_et()}"
        )
        self._send(msg)

    def daily_halt(self, day_pnl: float, reason: str) -> None:
        if not self._cfg["alert_on_daily_halt"]:
            return
        msg = (
            f"🛑 TRADING HALTED\n"
            f"Reason: {reason}\n"
            f"Day PnL: ${day_pnl:+.2f}\n"
            f"Time: {_now_et()}"
        )
        self._send(msg)

    def eod_summary(self, summary: dict) -> None:
        if not self._cfg["alert_eod_summary"]:
            return
        status = "🟢" if summary["pnl"] >= 0 else "🔴"
        msg = (
            f"{status} EOD Summary — {summary['date']}\n"
            f"PnL: ${summary['pnl']:+.2f}\n"
            f"Trades: {summary['trades']} "
            f"(W:{summary['winners']} L:{summary['losers']} "
            f"Acc:{summary['accuracy_pct']}%)\n"
            f"Avg W: ${summary['avg_winner']:.2f} | Avg L: ${summary['avg_loser']:.2f}\n"
            f"Halted: {'Yes' if summary['halted'] else 'No'}"
        )
        self._send(msg)

    def info(self, message: str) -> None:
        self._send(f"ℹ️ {message}")

    # ------------------------------------------------------------------
    # Internal send (non-blocking)
    # ------------------------------------------------------------------

    def _send(self, text: str) -> None:
        if not self._enabled:
            logger.info("NOTIFY: %s", text)
            return
        threading.Thread(target=self._send_sync, args=(text,), daemon=True).start()

    def _send_sync(self, text: str) -> None:
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            resp = requests.post(
                url,
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning("Telegram send failed: %s", resp.text)
        except Exception as e:
            logger.warning("Telegram error: %s", e)


def _now_et() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")
