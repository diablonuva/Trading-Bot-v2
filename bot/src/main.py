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
from zoneinfo import ZoneInfo

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# Local imports
from broker import AlpacaBroker
from data_feed import get_feed
from notifier import Notifier
from risk_manager import RiskManager
from scanner import Scanner
from strategy import Strategy

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
        self._feed = get_feed()

        self._watchlist: list[str] = []
        self._open_positions: dict = {}  # symbol → entry_price
        self._entries_allowed: bool = False
        self._session_active: bool = False

        self._feed.register_on_bar(self._on_bar)

    # ------------------------------------------------------------------
    # Scheduled jobs
    # ------------------------------------------------------------------

    def job_pre_market_scan(self) -> None:
        """06:45 — scan and build watchlist."""
        logger.info("=== PRE-MARKET SCAN STARTED ===")
        candidates = self._scanner.scan()
        self._watchlist = [c.symbol for c in candidates]
        if not self._watchlist:
            logger.warning("No candidates passed scanner — watchlist empty")
            self._notifier.info("Pre-market scan: no candidates found")
            return

        self._feed.subscribe(self._watchlist)
        logger.info("Watchlist: %s", self._watchlist)
        self._notifier.info(f"Watchlist built: {', '.join(self._watchlist)}")

    def job_session_open(self) -> None:
        """07:00 — start risk session, allow entries."""
        logger.info("=== TRADING SESSION OPEN ===")
        self._risk.start_session()
        self._entries_allowed = True
        self._session_active = True
        self._notifier.info("Trading session open — entries allowed")

    def job_market_open(self) -> None:
        """09:30 — market open, rescan for any new movers."""
        logger.info("=== MARKET OPEN ===")
        candidates = self._scanner.scan()
        new_symbols = [c.symbol for c in candidates if c.symbol not in self._watchlist]
        if new_symbols:
            self._watchlist.extend(new_symbols)
            self._feed.subscribe(new_symbols)
            logger.info("Added market-open movers: %s", new_symbols)

    def job_stop_entries(self) -> None:
        """11:00 — stop new entries, manage only open positions."""
        logger.info("=== NEW ENTRIES STOPPED (11:00 AM) ===")
        self._entries_allowed = False
        self._notifier.info("11:00 AM — no new entries, managing open positions only")

    def job_close_all(self) -> None:
        """12:00 — close all positions, EOD cleanup."""
        logger.info("=== END OF DAY — CLOSING ALL POSITIONS ===")
        self._entries_allowed = False
        self._session_active = False

        self._broker.close_all_positions()
        self._open_positions.clear()
        self._feed.stop()

        summary = self._risk.daily_summary()
        logger.info("EOD Summary: %s", summary)
        self._notifier.eod_summary(summary)

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
        if signal is None:
            return
        if signal.confidence != "A":
            logger.debug("Skipping B-quality signal: %s %s", symbol, signal.setup)
            return

        allowed, reason = self._risk.can_enter(
            signal.entry_price, signal.stop_price, signal.target_price
        )
        if not allowed:
            logger.info("Entry blocked [%s]: %s", symbol, reason)
            return

        qty = self._risk.calculate_shares(signal.entry_price, signal.stop_price)
        if qty == 0:
            return

        try:
            order = self._broker.place_bracket_order(
                symbol=symbol,
                qty=qty,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
            )
            self._open_positions[symbol] = signal.entry_price
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

    def _monitor_open_position(self, symbol: str, df) -> None:
        """Check soft exit conditions for open positions."""
        if symbol not in self._open_positions:
            return

        entry = self._open_positions[symbol]
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

            logger.info(
                "SOFT EXIT: %s x%d @ %.2f | pnl=%.2f | reason=%s",
                symbol, qty, exit_price, pnl, reason,
            )

            if self._risk.is_halted():
                self._notifier.daily_halt(
                    self._risk.daily_summary()["pnl"],
                    "daily loss limit reached",
                )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("Trading bot starting...")
        self._notifier.info("Trading bot starting")

        scheduler = BackgroundScheduler(timezone=ET)

        t = self._t
        scheduler.add_job(self.job_pre_market_scan, "cron",
                          hour=6, minute=45, id="pre_market_scan")
        scheduler.add_job(self.job_session_open, "cron",
                          hour=int(t["trading_start_time"].split(":")[0]),
                          minute=int(t["trading_start_time"].split(":")[1]),
                          id="session_open")
        scheduler.add_job(self.job_market_open, "cron",
                          hour=9, minute=30, id="market_open")
        scheduler.add_job(self.job_stop_entries, "cron",
                          hour=int(t["stop_entries_time"].split(":")[0]),
                          minute=int(t["stop_entries_time"].split(":")[1]),
                          id="stop_entries")
        scheduler.add_job(self.job_close_all, "cron",
                          hour=int(t["close_all_time"].split(":")[0]),
                          minute=int(t["close_all_time"].split(":")[1]),
                          id="close_all")

        # Re-scan every minute during active session to catch new movers
        scheduler.add_job(self._periodic_rescan, "cron",
                          minute="*/1",
                          hour="7-11",
                          id="periodic_rescan")

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
