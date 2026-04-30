"""
Stock scanner — DaytradeWarrior's 5-Pillar filter.

Runs every `scan_interval_seconds` during pre-market and market hours.
Returns a ranked watchlist of up to `watchlist_size` symbols.

The 5 pillars:
  1. Price: $2 – $20
  2. Relative volume: >= 5x 50-day average daily volume
  3. % change intraday: >= 10% up
  4. Float: < 20M shares
  5. Catalyst: recent news preferred (flagged, not mandatory)

Data sources:
  - Alpaca snapshot API: price, volume, % change, VWAP
  - Alpaca screener (assets endpoint) + daily bars for 50-day avg volume
  - Alpaca news endpoint for catalyst flag

Float data note: Alpaca doesn't provide float. We use a free fallback
  from Finviz's public page scraper. If unavailable, the filter is skipped
  and a warning is logged — trader should verify float manually.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import requests
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockSnapshotRequest,
    StockBarsRequest,
    NewsRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass, AssetStatus

logger = logging.getLogger(__name__)


@dataclass
class ScanStats:
    """Aggregate counters from a single scan run, for the dashboard's
    'Last Scan' detail line."""
    universe_size: int = 0      # all tradeable US equities Alpaca returned
    evaluated: int = 0          # made it past the cheap price pre-filter
    passed: int = 0             # passed all 5 pillars
    rejected_price: int = 0
    rejected_pct: int = 0
    rejected_rvol: int = 0
    rejected_float: int = 0
    duration_ms: int = 0


@dataclass
class CandidateStock:
    symbol: str
    price: float
    pct_change: float
    volume: float
    avg_daily_volume: float
    relative_volume: float
    float_shares: Optional[float]   # None if unavailable
    has_news: bool
    premarket_gap_pct: float
    score: float = 0.0             # composite ranking score

    def passes_filters(self, cfg: dict) -> bool:
        return not self.all_failures(cfg)

    def failed_pillar(self, cfg: dict) -> Optional[str]:
        """First pillar this candidate fails, or None if it passes all."""
        fails = self.all_failures(cfg)
        return fails[0] if fails else None

    def all_failures(self, cfg: dict) -> list[str]:
        """Every pillar this candidate fails, in pillar order."""
        s = cfg["scanner"]
        fails: list[str] = []
        if not (s["price_min"] <= self.price <= s["price_max"]):
            fails.append("price")
        if self.pct_change < s["pct_change_min"]:
            fails.append("pct")
        if self.relative_volume < s["relative_volume_min"]:
            fails.append("rvol")
        if self.float_shares is not None and self.float_shares > s["float_max_millions"] * 1_000_000:
            fails.append("float")
        return fails

    def compute_score(self) -> float:
        """Higher is better. Weights align with Ross Cameron's priorities."""
        score = 0.0
        score += self.pct_change * 1.0
        score += min(self.relative_volume, 50.0) * 2.0   # cap at 50x to avoid outlier dominance
        score += 10.0 if self.has_news else 0.0
        score += min(self.premarket_gap_pct, 20.0) * 1.5
        if self.float_shares is not None:
            # Smaller float = higher score (inverse relationship)
            float_m = self.float_shares / 1_000_000
            score += max(0.0, (20.0 - float_m)) * 0.5
        self.score = score
        return score


class Scanner:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        api_key = os.environ["ALPACA_API_KEY"]
        secret_key = os.environ["ALPACA_SECRET_KEY"]
        paper = os.environ.get("TRADING_MODE", "paper").lower() == "paper"
        self._data = StockHistoricalDataClient(api_key, secret_key)
        self._trading = TradingClient(api_key, secret_key, paper=paper)
        self._float_cache: dict[str, Optional[float]] = {}
        # Date the float cache was populated. Cleared at the start of each
        # new trading day so floats are re-fetched (companies issue secondary
        # offerings, do reverse splits, etc.).
        self._float_cache_date: Optional[date] = None
        self._last_stats: ScanStats = ScanStats()
        # Top near-misses from the last scan. Each carries a `_failed_pillar`
        # tag so the dashboard can show why each one didn't make the watchlist.
        self._last_near_misses: list[CandidateStock] = []

    def last_stats(self) -> ScanStats:
        return self._last_stats

    def last_near_misses(self) -> list[CandidateStock]:
        return self._last_near_misses

    # ------------------------------------------------------------------
    # Main scan method
    # ------------------------------------------------------------------

    def scan(self) -> list[CandidateStock]:
        """
        Returns watchlist sorted by score (best first), capped at watchlist_size.
        Side-effect: populates self._last_stats with scan-run counters.
        Runs in ~ 5–15 seconds depending on universe size.
        """
        t0 = time.time()
        stats = ScanStats()

        universe = self._get_active_symbols()
        stats.universe_size = len(universe)
        if not universe:
            logger.warning("Empty universe — skipping scan")
            stats.duration_ms = int((time.time() - t0) * 1000)
            self._last_stats = stats
            return []

        snapshots = self._get_snapshots(universe)
        stats.evaluated = len(snapshots)
        if not snapshots:
            stats.duration_ms = int((time.time() - t0) * 1000)
            self._last_stats = stats
            return []

        candidates = []
        near_misses: list[CandidateStock] = []
        for symbol, snap in snapshots.items():
            candidate = self._build_candidate(symbol, snap)
            if candidate is None:
                continue
            fails = candidate.all_failures(self._cfg)
            if not fails:
                candidate.compute_score()
                candidates.append(candidate)
                continue

            # Track first failure for the rejection-by-pillar stats line
            first = fails[0]
            if first == "price":
                stats.rejected_price += 1
            elif first == "pct":
                stats.rejected_pct += 1
            elif first == "rvol":
                stats.rejected_rvol += 1
            elif first == "float":
                stats.rejected_float += 1

            # Near-miss = failed exactly one pillar. Carry a `_failed_pillar`
            # tag so the dashboard can show "this missed because of X".
            if len(fails) == 1:
                candidate.compute_score()
                candidate._failed_pillar = fails[0]  # type: ignore[attr-defined]
                near_misses.append(candidate)

        stats.passed = len(candidates)
        stats.duration_ms = int((time.time() - t0) * 1000)
        self._last_stats = stats

        # Top 10 near-misses by composite score
        near_misses.sort(key=lambda c: c.score, reverse=True)
        self._last_near_misses = near_misses[:10]

        candidates.sort(key=lambda c: c.score, reverse=True)
        watchlist = candidates[: self._cfg["scanner"]["watchlist_size"]]

        logger.info(
            "Scan complete: %d passed filters, top %d selected (universe=%d, "
            "evaluated=%d, rejected price=%d pct=%d rvol=%d float=%d, %dms)",
            len(candidates), len(watchlist),
            stats.universe_size, stats.evaluated,
            stats.rejected_price, stats.rejected_pct,
            stats.rejected_rvol, stats.rejected_float,
            stats.duration_ms,
        )
        for c in watchlist:
            logger.info(
                "  %s | $%.2f | %.1f%% | RVol %.1fx | float=%s | news=%s | score=%.1f",
                c.symbol, c.price, c.pct_change,
                c.relative_volume,
                f"{c.float_shares/1e6:.1f}M" if c.float_shares else "N/A",
                c.has_news,
                c.score,
            )

        return watchlist

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_active_symbols(self) -> list[str]:
        """Get all tradeable US equity symbols from Alpaca."""
        req = GetAssetsRequest(
            asset_class=AssetClass.US_EQUITY,
            status=AssetStatus.ACTIVE,
        )
        assets = self._trading.get_all_assets(req)
        symbols = [a.symbol for a in assets if a.tradable and not a.symbol.startswith(".")]
        logger.debug("Universe size: %d symbols", len(symbols))
        return symbols

    def _get_snapshots(self, symbols: list[str]) -> dict:
        """Batch snapshot request — returns price, volume, % change per symbol."""
        BATCH = 1000
        all_snaps = {}
        for i in range(0, len(symbols), BATCH):
            batch = symbols[i : i + BATCH]
            try:
                req = StockSnapshotRequest(symbol_or_symbols=batch)
                snaps = self._data.get_stock_snapshot(req)
                # Pre-filter aggressively to save time on 50-day avg lookups
                for sym, snap in snaps.items():
                    if snap.daily_bar is None:
                        continue
                    price = float(snap.daily_bar.close)
                    pct = float(snap.daily_bar.vwap or 0)  # rough check
                    # Quick price gate — skip if obviously out of range
                    if not (
                        self._cfg["scanner"]["price_min"] <= price <= self._cfg["scanner"]["price_max"]
                    ):
                        continue
                    all_snaps[sym] = snap
            except Exception as e:
                logger.warning("Snapshot batch error (offset %d): %s", i, e)
        return all_snaps

    def _build_candidate(self, symbol: str, snap) -> Optional[CandidateStock]:
        try:
            daily = snap.daily_bar
            if daily is None:
                return None

            price = float(daily.close)
            volume = float(daily.volume)

            prev_close = float(snap.prev_daily_bar.close) if snap.prev_daily_bar else None
            if prev_close and prev_close > 0:
                pct_change = ((price - prev_close) / prev_close) * 100
            else:
                return None

            # 50-day average volume for relative volume
            avg_vol = self._get_avg_daily_volume(symbol)
            if avg_vol == 0:
                return None
            rel_vol = volume / avg_vol

            float_shares = self._get_float(symbol)

            has_news = self._has_recent_news(symbol)

            premarket_gap = 0.0
            if snap.minute_bar and prev_close and prev_close > 0:
                premarket_gap = ((float(snap.minute_bar.open) - prev_close) / prev_close) * 100

            return CandidateStock(
                symbol=symbol,
                price=price,
                pct_change=pct_change,
                volume=volume,
                avg_daily_volume=avg_vol,
                relative_volume=rel_vol,
                float_shares=float_shares,
                has_news=has_news,
                premarket_gap_pct=premarket_gap,
            )
        except Exception as e:
            logger.debug("Could not build candidate %s: %s", symbol, e)
            return None

    def _get_avg_daily_volume(self, symbol: str) -> float:
        """50-day average daily volume."""
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                limit=55,
            )
            bars = self._data.get_stock_bars(req)[symbol]
            if len(bars) < 5:
                return 0.0
            vols = [float(b.volume) for b in bars[:-1]]  # exclude today
            return sum(vols) / len(vols)
        except Exception as e:
            logger.debug("Avg volume fetch failed for %s: %s", symbol, e)
            return 0.0

    def _get_float(self, symbol: str) -> Optional[float]:
        """
        Attempt to get float from Finviz public page.
        Falls back to finvizfinance screener API if the page scrape fails.
        Returns None on failure — filter is skipped for that symbol.
        Cached per session to avoid hammering the endpoint.
        """
        # Clear cache once per calendar day so floats stay current
        today = date.today()
        if self._float_cache_date != today:
            self._float_cache.clear()
            self._float_cache_date = today

        if symbol in self._float_cache:
            return self._float_cache[symbol]

        time.sleep(1)  # avoid hammering Finviz

        val = self._get_float_via_page(symbol)
        if val is None:
            val = self._get_float_via_api(symbol)

        self._float_cache[symbol] = val
        return val

    def _get_float_via_page(self, symbol: str) -> Optional[float]:
        """Scrape float from Finviz quote page."""
        try:
            import re
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(
                f"https://finviz.com/quote.ashx?t={symbol}",
                headers=headers,
                timeout=5,
            )
            if resp.status_code != 200:
                return None

            match = re.search(r"Shs Float</td><td[^>]*>([^<]+)</td>", resp.text)
            if not match:
                return None

            raw = match.group(1).strip()
            multipliers = {"K": 1e3, "M": 1e6, "B": 1e9}
            for suffix, mult in multipliers.items():
                if raw.upper().endswith(suffix):
                    return float(raw[:-1]) * mult
        except Exception:
            pass
        return None

    def _get_float_via_api(self, symbol: str) -> Optional[float]:
        """Fallback: use finvizfinance package to get float."""
        try:
            from finvizfinance.quote import finvizfinance
            stock = finvizfinance(symbol)
            info = stock.ticker_fundament()
            raw = info.get("Shs Float", "")
            if not raw or raw == "-":
                return None
            multipliers = {"K": 1e3, "M": 1e6, "B": 1e9}
            for suffix, mult in multipliers.items():
                if raw.upper().endswith(suffix):
                    return float(raw[:-1]) * mult
        except Exception as e:
            logger.debug("finvizfinance fallback failed for %s: %s", symbol, e)
        return None

    def _has_recent_news(self, symbol: str) -> bool:
        """Returns True if there's been a news article in the last 2 hours."""
        try:
            from datetime import datetime, timedelta, timezone
            since = datetime.now(timezone.utc) - timedelta(hours=2)
            req = NewsRequest(
                symbols=[symbol],
                start=since,
                limit=1,
            )
            news = self._data.get_news(req)
            articles = news.get(symbol, [])
            return len(articles) > 0
        except Exception:
            return False
