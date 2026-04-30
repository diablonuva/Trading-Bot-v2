"""
Real-time market data feed via Alpaca WebSocket stream.

Maintains a rolling candle buffer (last N 1-minute bars) per symbol.
Calls registered callbacks whenever a bar closes so the strategy can
recalculate signals immediately.

Architecture:
  DataFeed.subscribe(symbols)    — start streaming these symbols
  DataFeed.get_bars(symbol)      — returns DataFrame of buffered candles
  DataFeed.register_on_bar(cb)   — cb(symbol, df) called on every new bar
"""

import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable

import pandas as pd
from alpaca.data.live import StockDataStream

logger = logging.getLogger(__name__)

# Number of 1-min candles to keep in memory per symbol
BUFFER_SIZE = 100
# Reconnect backoff schedule when the WS drops
RECONNECT_BACKOFFS = [1, 2, 5, 10, 30, 60, 120, 300]


def _new_stream() -> StockDataStream:
    return StockDataStream(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
    )


class DataFeed:
    def __init__(self):
        self._stream = _new_stream()
        # deque of dicts per symbol — rolling buffer
        self._buffers: dict[str, deque] = defaultdict(lambda: deque(maxlen=BUFFER_SIZE))
        self._callbacks: list[Callable] = []
        self._subscribed: set[str] = set()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopping = False

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, symbols: list[str]) -> None:
        new_symbols = [s for s in symbols if s not in self._subscribed]
        if not new_symbols:
            return

        self._stream.subscribe_bars(self._on_bar, *new_symbols)
        with self._lock:
            self._subscribed.update(new_symbols)
        logger.info("Subscribed to bars: %s", new_symbols)

    def unsubscribe(self, symbols: list[str]) -> None:
        self._stream.unsubscribe_bars(*symbols)
        with self._lock:
            for s in symbols:
                self._subscribed.discard(s)
        logger.info("Unsubscribed from: %s", symbols)

    def start(self) -> None:
        """Run the WebSocket stream with auto-reconnect in a background thread."""
        self._stopping = False
        self._thread = threading.Thread(target=self._run_with_reconnect, daemon=True)
        self._thread.start()
        logger.info("DataFeed WebSocket thread started")

    def stop(self) -> None:
        self._stopping = True
        try:
            self._stream.stop()
        except Exception as e:
            logger.debug("Stream stop raised (expected during shutdown): %s", e)
        logger.info("DataFeed stopped")

    def _run_with_reconnect(self) -> None:
        """Wrap stream.run() in a reconnect loop with exponential backoff.

        Alpaca's WebSocket can drop for many reasons over a multi-week run
        (network blips, their maintenance, idle timeouts). When that happens
        the underlying .run() returns or raises. Without this loop the bot
        silently stops receiving bars until the container is restarted.
        """
        attempt = 0
        while not self._stopping:
            try:
                logger.info("DataFeed: starting WebSocket stream (attempt %d)", attempt + 1)
                self._stream.run()
                # If run() returns cleanly while we're not stopping, treat it
                # as an unexpected disconnect.
                if self._stopping:
                    return
                logger.warning("DataFeed: stream returned without error — reconnecting")
            except Exception as e:
                if self._stopping:
                    return
                logger.error("DataFeed: stream errored: %s — reconnecting", e)

            # Backoff before reconnect
            wait = RECONNECT_BACKOFFS[min(attempt, len(RECONNECT_BACKOFFS) - 1)]
            attempt += 1
            logger.info("DataFeed: reconnecting in %ds", wait)
            time.sleep(wait)

            # Rebuild the stream client and re-subscribe to whatever we had.
            # alpaca-py's StockDataStream is single-shot per .run() call, so
            # we need a fresh instance after a disconnect.
            try:
                self._stream = _new_stream()
                with self._lock:
                    symbols = list(self._subscribed)
                if symbols:
                    self._stream.subscribe_bars(self._on_bar, *symbols)
                    logger.info("DataFeed: re-subscribed to %d symbols after reconnect", len(symbols))
                attempt = 0  # Successful reconnect — reset backoff
            except Exception as e:
                logger.error("DataFeed: failed to rebuild stream: %s", e)

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def register_on_bar(self, callback: Callable) -> None:
        """Register a function(symbol: str, df: pd.DataFrame) called on every new bar."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_bars(self, symbol: str) -> pd.DataFrame:
        """Return buffered candles for symbol as a DataFrame."""
        with self._lock:
            buf = list(self._buffers[symbol])
        if not buf:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(buf)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        return df

    def latest_price(self, symbol: str) -> float | None:
        df = self.get_bars(symbol)
        if df.empty:
            return None
        return float(df["close"].iloc[-1])

    # ------------------------------------------------------------------
    # Internal bar handler
    # ------------------------------------------------------------------

    def _on_bar(self, bar) -> None:
        symbol = bar.symbol
        record = {
            "timestamp": bar.timestamp,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        with self._lock:
            self._buffers[symbol].append(record)

        df = self.get_bars(symbol)
        for cb in self._callbacks:
            try:
                cb(symbol, df)
            except Exception as e:
                logger.exception("Error in bar callback for %s: %s", symbol, e)

        logger.debug(
            "Bar: %s | close=%.2f vol=%.0f",
            symbol, record["close"], record["volume"],
        )


# Singleton used by all modules
_feed: DataFeed | None = None


def get_feed() -> DataFeed:
    global _feed
    if _feed is None:
        _feed = DataFeed()
    return _feed
