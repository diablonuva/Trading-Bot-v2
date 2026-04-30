"""
Telemetry client — bot → API bridge.

Every significant event the bot produces is POSTed to the Node.js API,
which persists it to PostgreSQL and broadcasts it to dashboard WebSocket clients.

All calls are fire-and-forget (background thread) so the trading loop
is never blocked by network latency.

Usage:
    from telemetry import Telemetry
    tel = Telemetry()
    tel.session_start(equity=50000.0)
    tel.trade_entry(symbol="NVAX", ...)
"""

import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = os.environ.get("API_BASE_URL", "http://api:4000")
TIMEOUT = 5  # seconds — never block the trading loop longer than this


class Telemetry:
    def __init__(self):
        self._session_date = str(date.today())
        self._enabled = bool(API_BASE)
        # Outstanding fire-and-forget threads — flush() waits for these.
        # The bot itself never calls flush (it runs forever, threads complete
        # naturally), but standalone scripts (test_trade, multi_test_trade)
        # need it so their final trade_exit POSTs land before exit.
        self._pending: list[threading.Thread] = []
        logger.info("Telemetry target: %s", API_BASE)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def session_start(self, equity: float, trading_mode: str = "paper") -> None:
        self._session_date = str(date.today())
        # Synchronous so the session row exists before any subsequent event/scan
        # is posted — otherwise the API can't link them back to the session.
        self._post_sync("/telemetry/session/start", {
            "date": self._session_date,
            "startingEquity": equity,
            "tradingMode": trading_mode,
        })
        self.event("SESSION_START", f"Session started. Equity: ${equity:.2f}")

    def session_end(self, summary: dict) -> None:
        payload = {
            "date": self._session_date,
            "endingEquity": summary.get("ending_equity"),
            "realizedPnl": summary.get("pnl", 0),
            "totalTrades": summary.get("trades", 0),
            "winningTrades": summary.get("winners", 0),
            "losingTrades": summary.get("losers", 0),
            "accuracyPct": summary.get("accuracy_pct"),
            "avgWinner": summary.get("avg_winner"),
            "avgLoser": summary.get("avg_loser"),
            "halted": summary.get("halted", False),
            "haltReason": summary.get("halt_reason"),
        }
        self._post("/telemetry/session/end", payload)
        self.event("SESSION_END", f"Session ended. Day P&L: ${summary.get('pnl', 0):+.2f}")

    # ------------------------------------------------------------------
    # Trade lifecycle
    # ------------------------------------------------------------------

    def trade_entry(
        self,
        symbol: str,
        setup: str,
        qty: int,
        entry_price: float,
        stop_price: float,
        target_price: float,
        order_id: Optional[str] = None,
        vwap: Optional[float] = None,
        macd: Optional[float] = None,
        rvol: Optional[float] = None,
        signal_id: Optional[str] = None,
    ) -> Optional[str]:
        """Posts trade entry. Returns trade DB id (or None on failure)."""
        payload = {
            "sessionDate": self._session_date,
            "symbol": symbol,
            "setup": setup,
            "qty": qty,
            "entryPrice": entry_price,
            "stopPrice": stop_price,
            "targetPrice": target_price,
            "entryOrderId": order_id,
            "entryVwap": vwap,
            "entryMacd": macd,
            "entryRvol": rvol,
            "signalId": signal_id,
        }
        result = self._post_sync("/telemetry/trade/entry", payload)
        return result.get("id") if result else None

    def trade_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        order_id: Optional[str] = None,
    ) -> None:
        self._post("/telemetry/trade/exit", {
            "tradeId": trade_id,
            "exitPrice": exit_price,
            "exitReason": exit_reason,
            "exitOrderId": order_id,
        })

    # ------------------------------------------------------------------
    # Signal evaluation
    # ------------------------------------------------------------------

    def signal(
        self,
        symbol: str,
        setup: str,
        confidence: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        acted: bool,
        rejection_reason: Optional[str] = None,
        vwap: Optional[float] = None,
        macd_line: Optional[float] = None,
        macd_histogram: Optional[float] = None,
        rvol: Optional[float] = None,
        price: Optional[float] = None,
        pct_change: Optional[float] = None,
    ) -> Optional[str]:
        """Posts signal. Returns signal DB id (or None on failure)."""
        payload = {
            "sessionDate": self._session_date,
            "symbol": symbol,
            "setup": setup,
            "confidence": confidence,
            "entryPrice": entry_price,
            "stopPrice": stop_price,
            "targetPrice": target_price,
            "acted": acted,
            "rejectionReason": rejection_reason,
            "vwap": vwap,
            "macdLine": macd_line,
            "macdHistogram": macd_histogram,
            "rvolAtSignal": rvol,
            "price": price,
            "pctChange": pct_change,
        }
        result = self._post_sync("/telemetry/signal", payload)
        return result.get("id") if result else None

    # ------------------------------------------------------------------
    # Scanner
    # ------------------------------------------------------------------

    def scan_result(self, candidates: list[dict], stats: Optional[dict] = None) -> None:
        self._post("/telemetry/scan", {
            "sessionDate": self._session_date,
            "candidates": candidates,
            "stats": stats or {},
        })

    # ------------------------------------------------------------------
    # Equity + positions
    # ------------------------------------------------------------------

    def equity_snapshot(
        self,
        equity: float,
        buying_power: Optional[float] = None,
        day_pnl: Optional[float] = None,
        open_position_count: int = 0,
    ) -> None:
        self._post("/telemetry/equity", {
            "sessionDate": self._session_date,
            "equity": equity,
            "buyingPower": buying_power,
            "dayPnl": day_pnl,
            "openPositionCount": open_position_count,
        })

    def position_update(self, symbol: str, current_price: float, unrealized_pnl: float) -> None:
        self._post("/telemetry/position", {
            "symbol": symbol,
            "currentPrice": current_price,
            "unrealizedPnl": unrealized_pnl,
        })

    def gate_check(
        self,
        symbol: str,
        gates: dict,
        setup: Optional[str] = None,
        confidence: Optional[str] = None,
    ) -> None:
        """Per-bar strategy gate evaluation. Fires on every evaluate() call."""
        self._post("/telemetry/gate-check", {
            "symbol": symbol,
            "gates": gates,
            "setup": setup,
            "confidence": confidence,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    # ------------------------------------------------------------------
    # Generic events
    # ------------------------------------------------------------------

    def event(
        self,
        event_type: str,
        message: str,
        severity: str = "INFO",
        metadata: Optional[dict] = None,
    ) -> None:
        self._post("/telemetry/event", {
            "sessionDate": self._session_date,
            "eventType": event_type,
            "severity": severity,
            "message": message,
            "metadata": metadata,
        })

    def error(self, message: str, metadata: Optional[dict] = None) -> None:
        self.event("INFO", message, severity="ERROR", metadata=metadata)
        logger.error("Telemetry ERROR: %s", message)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict) -> None:
        """Fire-and-forget in background thread."""
        if not self._enabled:
            return
        t = threading.Thread(
            target=self._post_sync, args=(path, payload), daemon=True
        )
        t.start()
        # Track for flush(); cap the list so a long-running bot doesn't grow
        # this unbounded. We only really need recent threads for flush at exit.
        self._pending.append(t)
        if len(self._pending) > 200:
            self._pending = [x for x in self._pending if x.is_alive()][-100:]

    def flush(self, timeout: float = 10.0) -> None:
        """Wait up to `timeout` seconds for outstanding fire-and-forget POSTs
        to complete. Call this from a standalone script before exiting so
        the final trade_exit / event posts have a chance to land."""
        deadline = time.time() + timeout
        for t in list(self._pending):
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            t.join(timeout=remaining)
        self._pending = [t for t in self._pending if t.is_alive()]

    def _post_sync(self, path: str, payload: dict) -> dict:
        """Blocking POST — use only when return value is needed."""
        if not self._enabled:
            return {}
        try:
            resp = requests.post(
                f"{API_BASE}{path}",
                json=payload,
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                logger.warning("Telemetry POST %s → %d: %s", path, resp.status_code, resp.text[:200])
                return {}
            return resp.json()
        except Exception as e:
            logger.debug("Telemetry POST failed (%s): %s", path, e)
            return {}
