"""
Real-time market data via Alpaca REST polling.

Why REST polling instead of WebSocket:
    Alpaca's free tier allows exactly 1 concurrent WebSocket connection
    per API key. After a container restart, ghost connections linger on
    the server side for many minutes and prevent reauthentication. The
    bot was getting stuck in 'connection limit exceeded' loops every
    time the watchlist became non-empty.

    Polling sidesteps the entire problem — every fetch is a fresh HTTP
    request with no stateful slot.

Trade-offs:
    - Latency: bars arrive 0-POLL_INTERVAL seconds after the minute
      closes (default 20s — so the strategy sees each new 1-min bar
      within ~20-60s of close).
    - More HTTP calls: 1 batched call per POLL_INTERVAL covering all
      subscribed symbols. For a 1-5 symbol watchlist, this is trivial
      compared to Alpaca's REST rate limits.
    - For the DTW micro-pullback / bull-flag strategy that evaluates on
      1-minute bar close, sub-second latency is irrelevant. Signal
      generation is unchanged.

Architecture:
    DataFeed.subscribe(symbols)    — start polling these symbols
    DataFeed.get_bars(symbol)      — DataFrame of buffered candles
    DataFeed.register_on_bar(cb)   — cb(symbol, df) called on every NEW bar
"""

import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

logger = logging.getLogger(__name__)

# Number of 1-min candles to keep in memory per symbol
BUFFER_SIZE = 100
# Seconds between REST polls. 20s gives <60s worst-case latency for new bars.
POLL_INTERVAL = 20
# How far back to fetch on each poll (covers gaps from network blips)
LOOKBACK_MINUTES = 10


class DataFeed:
    def __init__(self):
        self._client = StockHistoricalDataClient(
            os.environ["ALPACA_API_KEY"],
            os.environ["ALPACA_SECRET_KEY"],
        )
        self._buffers: dict[str, deque] = defaultdict(lambda: deque(maxlen=BUFFER_SIZE))
        self._callbacks: list[Callable] = []
        self._subscribed: set[str] = set()
        # Last bar timestamp seen per symbol — used to dedupe across polls
        self._last_bar_ts: dict[str, datetime] = {}
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stopping = False

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, symbols: list[str]) -> None:
        with self._lock:
            new = [s for s in symbols if s not in self._subscribed]
            self._subscribed.update(new)
        if new:
            logger.info("Subscribed to bars: %s", new)
        # Lazy start: only spin the polling thread once we have something
        if not self._thread or not self._thread.is_alive():
            self.start()

    def unsubscribe(self, symbols: list[str]) -> None:
        with self._lock:
            for s in symbols:
                self._subscribed.discard(s)
                self._last_bar_ts.pop(s, None)
        logger.info("Unsubscribed from: %s", symbols)

    def start(self) -> None:
        """Idempotent — safe to call multiple times."""
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("DataFeed REST polling thread started (every %ds)", POLL_INTERVAL)

    def stop(self) -> None:
        self._stopping = True
        logger.info("DataFeed stopping")

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def register_on_bar(self, callback: Callable) -> None:
        """Register a function(symbol, df) called once per new bar."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_bars(self, symbol: str) -> pd.DataFrame:
        with self._lock:
            buf = list(self._buffers[symbol])
        if not buf:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(buf)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        return df

    def latest_price(self, symbol: str) -> Optional[float]:
        df = self.get_bars(symbol)
        if df.empty:
            return None
        return float(df["close"].iloc[-1])

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while not self._stopping:
            with self._lock:
                symbols = list(self._subscribed)
            if symbols:
                try:
                    self._fetch_and_dispatch(symbols)
                except Exception as e:
                    logger.warning("DataFeed poll error: %s", e)
            # Sleep in small chunks so stop() takes effect within ~1s
            for _ in range(POLL_INTERVAL):
                if self._stopping:
                    return
                time.sleep(1)

    def _fetch_and_dispatch(self, symbols: list[str]) -> None:
        """Fetch recent minute bars for all subscribed symbols in one call,
        then dispatch new ones to registered callbacks."""
        start = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=start,
        )
        result = self._client.get_stock_bars(req)
        bars_map = _barset_to_dict(result)

        for symbol in symbols:
            bars = bars_map.get(symbol, [])
            if not bars:
                continue

            # Sort chronological and emit only bars newer than what we've seen
            bars_sorted = sorted(bars, key=lambda b: b.timestamp)
            new_bars = []
            last_ts = self._last_bar_ts.get(symbol)
            for bar in bars_sorted:
                if last_ts is not None and bar.timestamp <= last_ts:
                    continue
                new_bars.append(bar)

            if not new_bars:
                continue

            self._last_bar_ts[symbol] = new_bars[-1].timestamp

            # Append to buffer
            with self._lock:
                for bar in new_bars:
                    self._buffers[symbol].append({
                        "timestamp": bar.timestamp,
                        "open":  float(bar.open),
                        "high":  float(bar.high),
                        "low":   float(bar.low),
                        "close": float(bar.close),
                        "volume": float(bar.volume),
                    })

            # Trigger callbacks once per symbol after appending all new bars.
            # Callbacks see the latest df with all new bars in place.
            df = self.get_bars(symbol)
            for cb in self._callbacks:
                try:
                    cb(symbol, df)
                except Exception as e:
                    logger.exception("Error in bar callback for %s: %s", symbol, e)

            logger.debug(
                "Bar(s) for %s: %d new, latest close=%.2f",
                symbol, len(new_bars), float(new_bars[-1].close),
            )


def _barset_to_dict(result) -> dict:
    """Coerce alpaca-py's BarSet response into {symbol: list[Bar]}."""
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return {k: list(v) if v is not None else [] for k, v in data.items()}
    if isinstance(data, list):
        grouped: dict = {}
        for b in data:
            sym = getattr(b, "symbol", None)
            if sym is not None:
                grouped.setdefault(sym, []).append(b)
        return grouped
    if isinstance(result, dict):
        return {k: list(v) if v is not None else [] for k, v in result.items()}
    return {}


# Singleton used by all modules
_feed: Optional[DataFeed] = None


def get_feed() -> DataFeed:
    global _feed
    if _feed is None:
        _feed = DataFeed()
    return _feed
