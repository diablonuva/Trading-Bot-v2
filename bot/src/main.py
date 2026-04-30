"""
Trading Bot v2 — fully automatic, telemetry-wired orchestrator.

All entry/exit decisions are driven strictly by risk_manager.py rules.
No human intervention required. Every event is posted to the API.

Daily schedule (ET — Docker TZ=America/New_York):
  06:45  → pre-market scan + watchlist build
  07:00  → session open, risk manager initialized
  09:30  → market open rescan
  11:00  → stop new entries
  12:00  → close all positions, EOD telemetry, stop feed

The bot runs inside Docker with TZ=America/New_York, so all schedule
times are interpreted as Eastern Time automatically.

Flow per bar:
  DataFeed fires _on_bar(symbol, df)
    → Strategy.evaluate(symbol, df) → Signal or None
    → RiskManager.can_enter(...) gate
    → Broker.place_bracket_order(...)
    → Notifier.trade_entry(...)

  Separately, open positions are monitored each bar for soft exits
  (VWAP breach, topping tail, MACD crossover) that supplement the
  bracket order's hard stop/target.
"""

import logging
import logging.config
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# Local imports
from broker import AlpacaBroker
from data_feed import get_feed
from notifier import Notifier
from risk_manager import RiskManager
from scanner import CandidateStock, Scanner
from strategy import Strategy
from telemetry import Telemetry


def _candidate_to_dict(
    c: CandidateStock,
    rank: Optional[int],
    passed: bool = True,
    failed_pillar: Optional[str] = None,
) -> dict:
    """Convert scanner output to the API's expected /telemetry/scan shape.

    The API's ScanCandidate schema requires volume / avgDailyVolume /
    premarketGap as non-null Floats — they were missing here previously, which
    would have caused every candidate insert to fail Prisma validation on the
    first day a watchlist actually formed.
    """
    return {
        "symbol":         c.symbol,
        "price":          c.price,
        "pctChange":      c.pct_change,
        "volume":         c.volume,
        "avgDailyVolume": c.avg_daily_volume,
        "relativeVolume": c.relative_volume,
        "floatShares":    c.float_shares,
        "hasNews":        c.has_news,
        "premarketGap":   c.premarket_gap_pct,
        "score":          c.score,
        "passedFilters":  passed,
        "failedPillar":   failed_pillar,
        "rank":           rank,
    }

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    log_cfg_path = Path(__file__).parent.parent / "config" / "logging.yaml"
    logs_dir = Path("/app/logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    if log_cfg_path.exists():
        with open(log_cfg_path) as f:
            cfg = yaml.safe_load(f)
        logging.config.dictConfig(cfg)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler("/app/logs/trading_bot.log"),
            ],
        )

_setup_logging()
logger = logging.getLogger("main")

ET = ZoneInfo("America/New_York")

# NYSE full-closure holidays for the next two years. Mirrors the dashboard's
# MarketClock list. Extend yearly. Early-close days (Black Friday, Christmas
# Eve) are NOT tracked — bot scans/closes on those days as usual but the
# 12:00 ET close-all happens before the 13:00 early close anyway.
NYSE_HOLIDAYS: set[str] = {
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
    # 2028
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29",
    "2028-06-19", "2028-07-04", "2028-09-04", "2028-11-23",
    "2028-12-25",
}


def _is_market_holiday_today() -> bool:
    today_et = datetime.now(ET).date().isoformat()
    return today_et in NYSE_HOLIDAYS


# ---------------------------------------------------------------------------
# Bot orchestrator
# ---------------------------------------------------------------------------

class TradingBot:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._t = cfg["trading"]

        self._broker = AlpacaBroker()
        self._scanner = Scanner(cfg)
        self._strategy = Strategy(cfg)
        self._risk = RiskManager(cfg, self._broker)
        self._notifier = Notifier(cfg)
        self._tel = Telemetry()
        self._feed = get_feed()

        self._watchlist: list[str] = []
        # symbol → {entry: float, trade_id: str|None, qty: int}
        self._open_positions: dict = {}
        self._entries_allowed: bool = False
        self._session_active: bool = False

        self._feed.register_on_bar(self._on_bar)

    # ------------------------------------------------------------------
    # Scheduled jobs
    # ------------------------------------------------------------------

    def job_pre_market_scan(self) -> None:
        """06:45 — scan and build watchlist."""
        if _is_market_holiday_today():
            logger.info("Skipping pre-market scan — NYSE holiday")
            return
        logger.info("=== PRE-MARKET SCAN STARTED ===")
        self._tel.event("PRE_MARKET_SCAN", "Pre-market scan started")
        candidates = self._scanner.scan()
        near_misses = self._scanner.last_near_misses()
        # Telemetry: passing candidates + near-misses (passedFilters=false,
        # failedPillar tagged) so the dashboard can show why each near-miss
        # didn't make the watchlist.
        scan_payload = (
            [_candidate_to_dict(c, i + 1, True) for i, c in enumerate(candidates)] +
            [_candidate_to_dict(c, None, False, getattr(c, "_failed_pillar", None))
             for c in near_misses]
        )
        stats = self._scanner.last_stats()
        self._tel.scan_result(scan_payload, stats={
            "universeSize":   stats.universe_size,
            "evaluated":      stats.evaluated,
            "passed":         stats.passed,
            "rejectedPrice":  stats.rejected_price,
            "rejectedPct":    stats.rejected_pct,
            "rejectedRvol":   stats.rejected_rvol,
            "rejectedFloat":  stats.rejected_float,
            "durationMs":     stats.duration_ms,
        })

        self._watchlist = [c.symbol for c in candidates]
        if not self._watchlist:
            logger.warning("No candidates passed scanner — watchlist empty")
            self._notifier.info("Pre-market scan: no candidates found")
            self._tel.event("SCAN_EMPTY", "Pre-market scan: 0 candidates passed filters")
            return

        self._feed.subscribe(self._watchlist)
        logger.info("Watchlist: %s", self._watchlist)
        self._notifier.info(f"Watchlist built: {', '.join(self._watchlist)}")
        self._tel.event(
            "WATCHLIST_BUILT",
            f"Watchlist built: {len(self._watchlist)} symbols",
            metadata={"symbols": self._watchlist},
        )

    def job_session_open(self) -> None:
        """07:00 — start risk session, allow entries."""
        if _is_market_holiday_today():
            logger.info("Skipping session open — NYSE holiday")
            return
        logger.info("=== TRADING SESSION OPEN ===")
        self._risk.start_session()
        self._entries_allowed = True
        self._session_active = True

        starting_equity = self._broker.get_equity()
        mode = os.environ.get("TRADING_MODE", "paper").lower()
        self._tel.session_start(equity=starting_equity, trading_mode=mode)
        self._notifier.info("Trading session open — entries allowed")

    def job_market_open(self) -> None:
        """09:30 — market open, rescan for any new movers."""
        if _is_market_holiday_today():
            return
        logger.info("=== MARKET OPEN ===")
        self._tel.event("MARKET_OPEN", "Market open — re-scanning for new movers")
        candidates = self._scanner.scan()
        near_misses = self._scanner.last_near_misses()
        scan_payload = (
            [_candidate_to_dict(c, i + 1, True) for i, c in enumerate(candidates)] +
            [_candidate_to_dict(c, None, False, getattr(c, "_failed_pillar", None))
             for c in near_misses]
        )
        stats = self._scanner.last_stats()
        self._tel.scan_result(scan_payload, stats={
            "universeSize":   stats.universe_size,
            "evaluated":      stats.evaluated,
            "passed":         stats.passed,
            "rejectedPrice":  stats.rejected_price,
            "rejectedPct":    stats.rejected_pct,
            "rejectedRvol":   stats.rejected_rvol,
            "rejectedFloat":  stats.rejected_float,
            "durationMs":     stats.duration_ms,
        })

        new_symbols = [c.symbol for c in candidates if c.symbol not in self._watchlist]
        if new_symbols:
            self._watchlist.extend(new_symbols)
            self._feed.subscribe(new_symbols)
            logger.info("Added market-open movers: %s", new_symbols)
            self._tel.event(
                "WATCHLIST_EXTENDED",
                f"Added {len(new_symbols)} market-open movers",
                metadata={"symbols": new_symbols},
            )

    def job_stop_entries(self) -> None:
        """11:00 — stop new entries, manage only open positions."""
        if _is_market_holiday_today():
            return
        logger.info("=== NEW ENTRIES STOPPED (11:00 AM) ===")
        self._entries_allowed = False
        self._notifier.info("11:00 AM — no new entries, managing open positions only")
        self._tel.event(
            "ENTRIES_STOPPED",
            "11:00 AM — no new entries, managing open positions only",
        )

    def job_close_all(self) -> None:
        """12:00 — close all positions, EOD cleanup."""
        if _is_market_holiday_today():
            return
        logger.info("=== END OF DAY — CLOSING ALL POSITIONS ===")
        self._tel.event("EOD_CLOSE_ALL", "End of day — closing all positions")
        self._entries_allowed = False
        self._session_active = False

        self._broker.close_all_positions()
        self._open_positions.clear()
        self._feed.stop()

        summary = self._risk.daily_summary()
        # Stamp ending equity onto the summary for the API
        try:
            summary["ending_equity"] = self._broker.get_equity()
        except Exception as e:
            logger.warning("Could not fetch ending equity: %s", e)

        logger.info("EOD Summary: %s", summary)
        self._notifier.eod_summary(summary)
        self._tel.session_end(summary)

    def job_equity_snapshot(self) -> None:
        """Periodic equity snapshot during the active session (drives dashboard chart)."""
        if not self._session_active:
            return
        try:
            equity = self._broker.get_equity()
            day_pnl = self._risk.daily_summary().get("pnl", 0.0)
            self._tel.equity_snapshot(
                equity=equity,
                day_pnl=day_pnl,
                open_position_count=len(self._open_positions),
            )
        except Exception as e:
            logger.debug("Equity snapshot failed: %s", e)

    # ------------------------------------------------------------------
    # Bar event handler (real-time, called from DataFeed thread)
    # ------------------------------------------------------------------

    def _on_bar(self, symbol: str, df) -> None:
        # Monitor open positions for soft exits regardless of entry gate
        if symbol in self._open_positions:
            self._monitor_open_position(symbol, df)

        if not self._entries_allowed:
            return
        if self._risk.is_halted():
            return
        if symbol not in self._watchlist:
            return
        if symbol in self._open_positions:
            return  # already in this trade

        signal = self._strategy.evaluate(symbol, df)

        # Push the per-bar gate state to the dashboard so it's visible whether
        # a signal was produced or not. Cheap call — fire-and-forget thread.
        last = self._strategy.last_gates(symbol)
        if last:
            self._tel.gate_check(
                symbol=symbol,
                gates=last["gates"],
                setup=last.get("setup"),
                confidence=last.get("confidence"),
            )

        if signal is None:
            return

        # Skip B-quality but still record them for analysis
        if signal.confidence != "A":
            logger.debug("Skipping B-quality signal: %s %s", symbol, signal.setup)
            self._tel.signal(
                symbol=symbol, setup=signal.setup, confidence=signal.confidence,
                entry_price=signal.entry_price, stop_price=signal.stop_price,
                target_price=signal.target_price,
                acted=False, rejection_reason="B_QUALITY",
                vwap=getattr(signal, "vwap", None),
                macd_line=getattr(signal, "macd_line", None),
                rvol=getattr(signal, "rvol", None),
                price=signal.entry_price,
            )
            return

        allowed, reason = self._risk.can_enter(
            signal.entry_price, signal.stop_price, signal.target_price
        )
        if not allowed:
            logger.info("Entry blocked [%s]: %s", symbol, reason)
            self._tel.signal(
                symbol=symbol, setup=signal.setup, confidence=signal.confidence,
                entry_price=signal.entry_price, stop_price=signal.stop_price,
                target_price=signal.target_price,
                acted=False, rejection_reason=reason,
                vwap=getattr(signal, "vwap", None),
                macd_line=getattr(signal, "macd_line", None),
                rvol=getattr(signal, "rvol", None),
                price=signal.entry_price,
            )
            return

        qty = self._risk.calculate_shares(signal.entry_price, signal.stop_price)
        if qty == 0:
            self._tel.signal(
                symbol=symbol, setup=signal.setup, confidence=signal.confidence,
                entry_price=signal.entry_price, stop_price=signal.stop_price,
                target_price=signal.target_price,
                acted=False, rejection_reason="ZERO_QTY",
            )
            return

        # Record the signal as acted=True FIRST so we can link the trade to it
        signal_id = self._tel.signal(
            symbol=symbol, setup=signal.setup, confidence=signal.confidence,
            entry_price=signal.entry_price, stop_price=signal.stop_price,
            target_price=signal.target_price,
            acted=True,
            vwap=getattr(signal, "vwap", None),
            macd_line=getattr(signal, "macd_line", None),
            rvol=getattr(signal, "rvol", None),
            price=signal.entry_price,
        )

        try:
            order = self._broker.place_bracket_order(
                symbol=symbol,
                qty=qty,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
            )
            order_id = getattr(order, "id", None)

            trade_id = self._tel.trade_entry(
                symbol=symbol, setup=signal.setup, qty=qty,
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                order_id=str(order_id) if order_id else None,
                vwap=getattr(signal, "vwap", None),
                macd=getattr(signal, "macd_line", None),
                rvol=getattr(signal, "rvol", None),
                signal_id=signal_id,
            )

            self._open_positions[symbol] = {
                "entry": signal.entry_price,
                "trade_id": trade_id,
                "qty": qty,
            }
            self._notifier.trade_entry(
                symbol, signal.setup, qty,
                signal.entry_price, signal.stop_price, signal.target_price,
            )
            logger.info(
                "ORDER PLACED: %s %s x%d @ %.2f | stop=%.2f target=%.2f",
                signal.setup, symbol, qty,
                signal.entry_price, signal.stop_price, signal.target_price,
            )
        except Exception as e:
            logger.error("Order failed for %s: %s", symbol, e)
            self._tel.error(f"Order failed for {symbol}: {e}")

    def _monitor_open_position(self, symbol: str, df) -> None:
        """Check soft exit conditions for open positions."""
        if symbol not in self._open_positions:
            return

        record = self._open_positions[symbol]
        entry = record["entry"]
        should_exit, reason = self._strategy.should_exit(symbol, df, entry, 0)

        if should_exit:
            position = self._broker.get_position(symbol)
            if position is None:
                # Bracket order already closed it
                self._open_positions.pop(symbol, None)
                return

            qty = int(position.qty)
            exit_price = float(df["close"].iloc[-1])
            pnl = (exit_price - entry) * qty

            self._broker.close_position(symbol)
            self._open_positions.pop(symbol, None)
            self._risk.record_trade(symbol, entry, exit_price, qty, "buy")
            self._notifier.trade_exit(symbol, qty, exit_price, pnl, reason)

            if record.get("trade_id"):
                self._tel.trade_exit(
                    trade_id=record["trade_id"],
                    exit_price=exit_price,
                    exit_reason=reason,
                )

            logger.info(
                "SOFT EXIT: %s x%d @ %.2f | pnl=%.2f | reason=%s",
                symbol, qty, exit_price, pnl, reason,
            )

            if self._risk.is_halted():
                self._notifier.daily_halt(
                    self._risk.daily_summary()["pnl"],
                    "daily loss limit reached",
                )
                self._tel.event(
                    "DAILY_HALT",
                    f"Daily halt — P&L ${self._risk.daily_summary()['pnl']:+.2f}",
                    severity="WARNING",
                )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("Trading bot starting...")
        self._notifier.info("Trading bot starting")

        # Open a session record now so the 06:45 pre-market scan has somewhere
        # to write to. The 07:00 session_open job will upsert with fresh equity.
        try:
            startup_equity = self._broker.get_equity()
            mode = os.environ.get("TRADING_MODE", "paper").lower()
            self._tel.session_start(equity=startup_equity, trading_mode=mode)
            logger.info("Telemetry session opened (equity=%.2f, mode=%s)", startup_equity, mode)
        except Exception as e:
            logger.warning("Could not open telemetry session at startup: %s", e)

        # Restart-safety: if Docker restarted us mid-session and Alpaca
        # already has open positions, resurrect them into self._open_positions
        # so the soft-exit monitor and trade_exit telemetry work for them.
        # The bracket order on Alpaca's side keeps the hard stop in place.
        try:
            existing = self._broker.get_positions()
            for symbol, position in existing.items():
                qty = int(position.qty)
                entry = float(position.avg_entry_price)
                self._open_positions[symbol] = {
                    "entry": entry,
                    "trade_id": None,   # not linked to a DB row from this session
                    "qty": qty,
                }
                logger.info("Resurrected position from Alpaca: %s x%d @ %.2f", symbol, qty, entry)
            if existing:
                # Subscribe the data feed to these symbols so soft-exit logic fires
                self._watchlist = list(existing.keys())
                self._feed.subscribe(self._watchlist)
                self._notifier.info(f"Resurrected {len(existing)} position(s) on startup: {', '.join(existing.keys())}")
        except Exception as e:
            logger.warning("Could not resurrect open positions: %s", e)

        # If we're starting up mid-session (e.g. Docker restarted us at 11:30
        # ET because of a crash), the 07:00 job_session_open has already
        # passed for today. Without bootstrapping, _session_active stays False
        # and the periodic_rescan / equity_snapshot jobs early-exit forever
        # until the next morning's session_open. Run the catch-up jobs now.
        self._bootstrap_session_state()

        scheduler = BackgroundScheduler(timezone=ET)

        # All sessions jobs run Mon-Fri only. Holiday dates are filtered
        # at the top of each job_* function via _is_market_holiday_today().
        t = self._t
        wkd = "mon-fri"
        scheduler.add_job(self.job_pre_market_scan, "cron",
                          hour=6, minute=45, day_of_week=wkd,
                          id="pre_market_scan")
        scheduler.add_job(self.job_session_open, "cron",
                          hour=int(t["trading_start_time"].split(":")[0]),
                          minute=int(t["trading_start_time"].split(":")[1]),
                          day_of_week=wkd, id="session_open")
        scheduler.add_job(self.job_market_open, "cron",
                          hour=9, minute=30, day_of_week=wkd,
                          id="market_open")
        scheduler.add_job(self.job_stop_entries, "cron",
                          hour=int(t["stop_entries_time"].split(":")[0]),
                          minute=int(t["stop_entries_time"].split(":")[1]),
                          day_of_week=wkd, id="stop_entries")
        scheduler.add_job(self.job_close_all, "cron",
                          hour=int(t["close_all_time"].split(":")[0]),
                          minute=int(t["close_all_time"].split(":")[1]),
                          day_of_week=wkd, id="close_all")

        # Re-scan every minute during active session to catch new movers.
        # 7-14 covers the full entry window (07:00 to 15:00 stop_entries),
        # extended from the old 7-11 morning-only window.
        scheduler.add_job(self._periodic_rescan, "cron",
                          minute="*/1", hour="7-14", day_of_week=wkd,
                          id="periodic_rescan")

        # Equity snapshot every minute through the entire session including
        # the 15:00-15:30 wind-down so the dashboard chart stays current.
        scheduler.add_job(self.job_equity_snapshot, "cron",
                          minute="*/1", hour="7-15", day_of_week=wkd,
                          id="equity_snapshot")

        scheduler.start()
        self._feed.start()

        # SIGTERM handler — close all positions before exiting
        def _handle_sigterm(signum, frame):
            logger.info("SIGTERM received — closing all positions and shutting down")
            try:
                self._broker.close_all_positions()
                self._open_positions.clear()
                logger.info("All positions closed via SIGTERM handler")
                self._notifier.info("Bot received SIGTERM — all positions closed, shutting down")
            except Exception as e:
                logger.error("Error closing positions on SIGTERM: %s", e)
            finally:
                scheduler.shutdown(wait=False)
                self._feed.stop()
                sys.exit(0)

        signal.signal(signal.SIGTERM, _handle_sigterm)

        logger.info("Scheduler running. Waiting for market events...")
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received")
            scheduler.shutdown()
            self._feed.stop()
            logger.info("Bot stopped cleanly")

    def _bootstrap_session_state(self) -> None:
        """When the bot starts up inside the trading window, run the catch-up
        jobs to bring _session_active / _entries_allowed / _watchlist up to date.
        Without this, periodic_rescan and equity_snapshot early-exit because
        _session_active is False until the next morning's job_session_open."""
        if _is_market_holiday_today():
            return
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:  # Sat/Sun
            return

        t = self._t
        def _at(hh_mm: str) -> datetime:
            h, m = hh_mm.split(":")
            return now_et.replace(hour=int(h), minute=int(m), second=0, microsecond=0)

        session_open_t = _at(t["trading_start_time"])
        stop_entries_t = _at(t["stop_entries_time"])
        close_all_t    = _at(t["close_all_time"])

        if now_et < session_open_t:
            return  # too early — scheduler will fire on time
        if now_et >= close_all_t:
            return  # too late — today's session is already over

        logger.info("Bootstrap: starting mid-session, running catch-up jobs")
        try:
            self.job_session_open()
        except Exception as e:
            logger.warning("Bootstrap session_open failed: %s", e)

        # Run a pre-market scan now since we missed the 06:45 firing
        if not self._watchlist:
            try:
                self.job_pre_market_scan()
            except Exception as e:
                logger.warning("Bootstrap pre_market_scan failed: %s", e)

        # If we're past 15:00 ET (stop entries), mirror that state
        if now_et >= stop_entries_t:
            self._entries_allowed = False
            logger.info("Bootstrap: past stop_entries — blocking new entries")

    def _periodic_rescan(self) -> None:
        """Minute-level rescan to catch new movers that emerged during session."""
        if not self._session_active:
            return
        try:
            candidates = self._scanner.scan()
            for c in candidates:
                if c.symbol not in self._watchlist:
                    self._watchlist.append(c.symbol)
                    self._feed.subscribe([c.symbol])
                    logger.info("New mover added to watchlist: %s", c.symbol)
            # Push scan stats so the dashboard's 'Last Scan' line stays fresh
            near_misses = self._scanner.last_near_misses()
            scan_payload = (
                [_candidate_to_dict(c, i + 1, True) for i, c in enumerate(candidates)] +
                [_candidate_to_dict(c, None, False, getattr(c, "_failed_pillar", None))
                 for c in near_misses]
            )
            stats = self._scanner.last_stats()
            self._tel.scan_result(scan_payload, stats={
                "universeSize":   stats.universe_size,
                "evaluated":      stats.evaluated,
                "passed":         stats.passed,
                "rejectedPrice":  stats.rejected_price,
                "rejectedPct":    stats.rejected_pct,
                "rejectedRvol":   stats.rejected_rvol,
                "rejectedFloat":  stats.rejected_float,
                "durationMs":     stats.duration_ms,
            })
        except Exception as e:
            logger.warning("Periodic rescan error: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    load_dotenv()

    # Validate required env vars early
    required = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        logger.error("Missing required environment variables: %s", missing)
        sys.exit(1)

    cfg = load_config()
    bot = TradingBot(cfg)
    bot.run()


if __name__ == "__main__":
    main()
