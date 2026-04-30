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
        # 50-day avg volume — also cached per day. Avoids 50+ Alpaca bar
        # fetches per scan when the same survivor list is rescanned every
        # minute during the active session.
        self._avg_vol_cache: dict[str, float] = {}
        self._avg_vol_cache_date: Optional[date] = None
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

        # Cheap pre-filter on % change before the expensive per-symbol API
        # calls in _build_candidate. Keep candidates within 50% of the
        # pct_change threshold so near-misses on pct are still captured (a
        # 9.2% mover passes a 10% × 0.5 = 5% floor).
        pct_min = self._cfg["scanner"]["pct_change_min"]
        pct_floor = pct_min * 0.5
        deep_eval: dict = {}
        skipped_no_prev = 0  # symbols missing prev_daily_bar (no pct calc possible)
        prefilter_errors = 0
        for symbol, snap in snapshots.items():
            try:
                # alpaca-py renamed prev_daily_bar -> previous_daily_bar somewhere
                # along the way; tolerate both so we don't silently drop everything.
                prev_bar = getattr(snap, "prev_daily_bar", None) \
                    or getattr(snap, "previous_daily_bar", None)
                if snap.daily_bar is None or prev_bar is None:
                    skipped_no_prev += 1
                    continue
                prev_close = float(prev_bar.close)
                if prev_close <= 0:
                    skipped_no_prev += 1
                    continue
                pct = ((float(snap.daily_bar.close) - prev_close) / prev_close) * 100
                if pct < pct_floor:
                    stats.rejected_pct += 1
                    continue
                deep_eval[symbol] = snap
            except Exception as e:
                prefilter_errors += 1
                if prefilter_errors <= 3:
                    logger.warning("pct pre-filter error for %s: %s (%s)",
                                   symbol, e, type(e).__name__)
                continue
        if prefilter_errors:
            logger.warning("Pre-filter errors total: %d", prefilter_errors)

        if skipped_no_prev:
            logger.info("Skipped %d symbols with no prev_daily_bar", skipped_no_prev)

        # Pre-populate avg-vol cache for all survivors in a few batched calls
        # rather than 50+ sequential ones inside the candidate loop.
        if deep_eval:
            t_avg = time.time()
            self._batch_avg_daily_volume(list(deep_eval.keys()))
            logger.info(
                "Batched avg-vol fetched for %d symbols in %dms",
                len(deep_eval), int((time.time() - t_avg) * 1000),
            )

        candidates = []
        near_misses: list[CandidateStock] = []
        build_failed = 0  # _build_candidate returned None despite passing pre-filter
        for symbol, snap in deep_eval.items():
            candidate = self._build_candidate(symbol, snap)
            if candidate is None:
                build_failed += 1
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
            "Scan complete: %d passed, top %d selected (universe=%d, "
            "evaluated=%d, deep=%d, no_prev=%d, build_fail=%d, "
            "rejected price=%d pct=%d rvol=%d float=%d, %dms)",
            len(candidates), len(watchlist),
            stats.universe_size, stats.evaluated, len(deep_eval),
            skipped_no_prev, build_failed,
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

            prev_bar = getattr(snap, "prev_daily_bar", None) \
                or getattr(snap, "previous_daily_bar", None)
            prev_close = float(prev_bar.close) if prev_bar else None
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
            min_bar = getattr(snap, "minute_bar", None) or getattr(snap, "latest_bar", None)
            if min_bar and prev_close and prev_close > 0:
                premarket_gap = ((float(min_bar.open) - prev_close) / prev_close) * 100

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

    def _ensure_avg_vol_day(self) -> None:
        today = date.today()
        if self._avg_vol_cache_date != today:
            self._avg_vol_cache.clear()
            self._avg_vol_cache_date = today

    def _batch_avg_daily_volume(self, symbols: list[str]) -> None:
        """Pre-populate self._avg_vol_cache for many symbols in one call.
        Alpaca accepts a list of symbols on get_stock_bars; limit=55 applies
        per symbol. Cuts scan time from ~100s to ~5-10s on the first scan."""
        self._ensure_avg_vol_day()
        to_fetch = [s for s in symbols if s not in self._avg_vol_cache]
        if not to_fetch:
            return

        BATCH = 100  # Alpaca recommends staying under a few hundred per request
        diag_done = False
        for i in range(0, len(to_fetch), BATCH):
            batch = to_fetch[i:i + BATCH]
            try:
                req = StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Day,
                    limit=55,
                )
                result = self._data.get_stock_bars(req)
                bars_map = self._barset_to_dict(result)

                # One-time diagnostic so we can see what alpaca-py's BarSet
                # actually contains — type, dict size, sample keys, sample
                # bar count for the first symbol of the first batch.
                if not diag_done:
                    diag_done = True
                    sample_sym = batch[0] if batch else None
                    sample_bars = bars_map.get(sample_sym, [])
                    logger.info(
                        "DIAG bars: result_type=%s, parsed_len=%d, sample=%s, "
                        "sample_bar_count=%d, intersection_with_request=%d/%d",
                        type(result).__name__, len(bars_map), sample_sym,
                        len(sample_bars),
                        len(set(bars_map.keys()) & set(batch)),
                        len(batch),
                    )

                for sym in batch:
                    bars = bars_map.get(sym, [])
                    if len(bars) < 5:
                        self._avg_vol_cache[sym] = 0.0
                    else:
                        vols = [float(b.volume) for b in bars[:-1]]
                        self._avg_vol_cache[sym] = sum(vols) / len(vols)
            except Exception as e:
                logger.warning("Batch avg-vol fetch failed (offset %d): %s (%s)",
                               i, e, type(e).__name__)
                for sym in batch:
                    self._avg_vol_cache.setdefault(sym, 0.0)

    @staticmethod
    def _barset_to_dict(result) -> dict:
        """Coerce alpaca-py's BarSet/dict/list response into a plain dict
        of {symbol: list[Bar]}. Tries every access pattern observed across
        alpaca-py versions:
          - result.data is a dict (most modern versions)
          - result.data is a flat list grouped by Bar.symbol (some versions)
          - result is itself a dict subclass
          - result is iterable yielding Bar objects with a .symbol attribute
        """
        # Pattern 1: result.data is a dict[str, list[Bar]]
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            return {k: list(v) if v is not None else [] for k, v in data.items()}

        # Pattern 2: result.data is a flat list — group by Bar.symbol
        if isinstance(data, list):
            grouped: dict = {}
            for b in data:
                sym = getattr(b, "symbol", None)
                if sym is None:
                    continue
                grouped.setdefault(sym, []).append(b)
            return grouped

        # Pattern 3: result is itself a dict subclass
        if isinstance(result, dict):
            return {k: list(v) if v is not None else [] for k, v in result.items()}

        # Pattern 4: result is iterable yielding Bar objects
        try:
            grouped = {}
            for b in result:
                sym = getattr(b, "symbol", None)
                if sym is None:
                    continue
                grouped.setdefault(sym, []).append(b)
            if grouped:
                return grouped
        except Exception:
            pass

        return {}

    def _get_avg_daily_volume(self, symbol: str) -> float:
        """50-day avg volume. Reads from the cache populated by
        _batch_avg_daily_volume. Falls back to a single fetch if not
        pre-populated (e.g. when called outside scan()).
        """
        self._ensure_avg_vol_day()
        if symbol in self._avg_vol_cache:
            return self._avg_vol_cache[symbol]
        # Fallback: single fetch (shouldn't happen during scan() now)
        self._batch_avg_daily_volume([symbol])
        return self._avg_vol_cache.get(symbol, 0.0)

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
