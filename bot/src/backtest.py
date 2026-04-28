"""
Backtest module — replay historical 1-min bars through the strategy.

Fetches bars from Alpaca REST API, feeds them chronologically to
Strategy.evaluate() and RiskManager, and outputs a performance summary.

Usage:
    python src/backtest.py --symbol NVAX --start 2025-01-01 --end 2025-03-31

Output:
    - Console summary (accuracy, profit factor, avg winner/loser)
    - data/backtest_{symbol}_{start}_{end}.csv  — all simulated trades
    - data/backtest_{symbol}_{start}_{end}.json — machine-readable summary
"""

import argparse
import csv
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

from strategy import Strategy
from risk_manager import RiskManager

logger = logging.getLogger("backtest")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


# ---------------------------------------------------------------------------
# Mock broker — never places real orders
# ---------------------------------------------------------------------------

class MockBroker:
    """Simulates broker responses for backtesting. No real orders placed."""

    def __init__(self, starting_equity: float):
        self._equity = starting_equity

    def get_equity(self) -> float:
        return self._equity

    def get_buying_power(self) -> float:
        return self._equity * 4  # 4:1 margin assumption

    def get_positions(self) -> dict:
        return {}

    def set_equity(self, equity: float) -> None:
        self._equity = equity


# ---------------------------------------------------------------------------
# Simulated trade record
# ---------------------------------------------------------------------------

@dataclass
class BacktestTrade:
    symbol: str
    setup: str
    entry_time: str
    exit_time: str
    qty: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    realized_pnl: float
    exit_reason: str          # "target", "stop", "soft_exit", "eod"
    hold_minutes: int


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

class Backtester:
    def __init__(self, cfg: dict, symbol: str, start: date, end: date):
        self._cfg = cfg
        self._symbol = symbol
        self._start = start
        self._end = end

        api_key = os.environ["ALPACA_API_KEY"]
        secret_key = os.environ["ALPACA_SECRET_KEY"]
        self._data_client = StockHistoricalDataClient(api_key, secret_key)

        starting_equity = 25_000.0  # standard day trader minimum
        self._mock_broker = MockBroker(starting_equity)
        self._strategy = Strategy(cfg)
        self._risk = RiskManager(cfg, self._mock_broker)

        self._trades: list[BacktestTrade] = []

    def run(self) -> dict:
        logger.info("Fetching 1-min bars for %s from %s to %s", self._symbol, self._start, self._end)
        all_bars = self._fetch_bars()
        if all_bars.empty:
            logger.error("No bars returned — check symbol and date range")
            return {}

        logger.info("Total bars: %d", len(all_bars))

        # Group by calendar day and process each trading day
        all_bars.index = pd.to_datetime(all_bars.index)
        for day, day_bars in all_bars.groupby(all_bars.index.date):
            self._process_day(day, day_bars)

        return self._compute_summary()

    def _fetch_bars(self) -> pd.DataFrame:
        try:
            req = StockBarsRequest(
                symbol_or_symbols=self._symbol,
                timeframe=TimeFrame.Minute,
                start=datetime.combine(self._start, datetime.min.time()),
                end=datetime.combine(self._end, datetime.max.time()),
            )
            bars = self._data_client.get_stock_bars(req)[self._symbol]
            records = [
                {
                    "timestamp": b.timestamp,
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": float(b.volume),
                }
                for b in bars
            ]
            df = pd.DataFrame(records).set_index("timestamp")
            return df
        except Exception as e:
            logger.error("Failed to fetch bars: %s", e)
            return pd.DataFrame()

    def _process_day(self, day: date, bars: pd.DataFrame) -> None:
        t = self._cfg["trading"]
        stop_entries_hour, stop_entries_min = map(int, t["stop_entries_time"].split(":"))
        close_all_hour, close_all_min = map(int, t["close_all_time"].split(":"))

        self._risk.start_session()
        open_position: Optional[dict] = None  # {entry_price, stop, target, entry_time, qty, setup}

        for i in range(30, len(bars)):
            df_window = bars.iloc[: i + 1]
            ts = df_window.index[-1]
            bar_hour = ts.hour
            bar_min = ts.minute

            # Check if we should stop entries
            entries_allowed = (
                bar_hour < stop_entries_hour
                or (bar_hour == stop_entries_hour and bar_min < stop_entries_min)
            )

            # Check if we hit EOD close time
            if bar_hour >= close_all_hour and bar_min >= close_all_min:
                if open_position:
                    self._close_position(open_position, df_window, ts, "eod")
                    open_position = None
                break

            # Manage open position
            if open_position:
                last_close = float(df_window["close"].iloc[-1])
                last_high = float(df_window["high"].iloc[-1])
                last_low = float(df_window["low"].iloc[-1])

                # Check bracket: stop hit
                if last_low <= open_position["stop"]:
                    self._close_position(open_position, df_window, ts, "stop",
                                        exit_price=open_position["stop"])
                    open_position = None
                    continue

                # Check bracket: target hit
                if last_high >= open_position["target"]:
                    self._close_position(open_position, df_window, ts, "target",
                                        exit_price=open_position["target"])
                    open_position = None
                    continue

                # Check soft exits
                should_exit, reason = self._strategy.should_exit(
                    self._symbol, df_window, open_position["entry_price"], open_position["stop"]
                )
                if should_exit:
                    self._close_position(open_position, df_window, ts, reason)
                    open_position = None
                    continue

            # Look for new entry
            if (
                open_position is None
                and entries_allowed
                and not self._risk.is_halted()
            ):
                signal = self._strategy.evaluate(self._symbol, df_window)
                if signal and signal.confidence == "A":
                    allowed, _ = self._risk.can_enter(
                        signal.entry_price, signal.stop_price, signal.target_price
                    )
                    if allowed:
                        qty = self._risk.calculate_shares(signal.entry_price, signal.stop_price)
                        if qty > 0:
                            open_position = {
                                "entry_price": signal.entry_price,
                                "stop": signal.stop_price,
                                "target": signal.target_price,
                                "entry_time": ts,
                                "qty": qty,
                                "setup": signal.setup,
                            }
                            logger.debug("ENTRY: %s %s x%d @ %.2f", self._symbol, signal.setup, qty, signal.entry_price)

        # If still open at end of day, close at last bar
        if open_position and not bars.empty:
            self._close_position(open_position, bars, bars.index[-1], "eod")

    def _close_position(
        self,
        pos: dict,
        df: pd.DataFrame,
        exit_time,
        reason: str,
        exit_price: Optional[float] = None,
    ) -> None:
        if exit_price is None:
            exit_price = float(df["close"].iloc[-1])

        pnl = (exit_price - pos["entry_price"]) * pos["qty"]
        hold = int((exit_time - pos["entry_time"]).total_seconds() / 60)

        self._risk.record_trade(self._symbol, pos["entry_price"], exit_price, pos["qty"], "buy")
        new_equity = self._mock_broker.get_equity() + pnl
        self._mock_broker.set_equity(new_equity)

        trade = BacktestTrade(
            symbol=self._symbol,
            setup=pos["setup"],
            entry_time=str(pos["entry_time"]),
            exit_time=str(exit_time),
            qty=pos["qty"],
            entry_price=round(pos["entry_price"], 2),
            exit_price=round(exit_price, 2),
            stop_price=round(pos["stop"], 2),
            target_price=round(pos["target"], 2),
            realized_pnl=round(pnl, 2),
            exit_reason=reason,
            hold_minutes=hold,
        )
        self._trades.append(trade)
        logger.debug("EXIT: %s @ %.2f pnl=%.2f reason=%s", self._symbol, exit_price, pnl, reason)

    def _compute_summary(self) -> dict:
        if not self._trades:
            logger.info("No trades generated during backtest period")
            return {"total_trades": 0}

        winners = [t for t in self._trades if t.realized_pnl > 0]
        losers  = [t for t in self._trades if t.realized_pnl <= 0]
        gross_wins = sum(t.realized_pnl for t in winners)
        gross_loss = abs(sum(t.realized_pnl for t in losers))

        summary = {
            "symbol": self._symbol,
            "start": str(self._start),
            "end": str(self._end),
            "total_trades": len(self._trades),
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "accuracy_pct": round(len(winners) / len(self._trades) * 100, 1),
            "total_pnl": round(sum(t.realized_pnl for t in self._trades), 2),
            "avg_winner": round(gross_wins / len(winners), 2) if winners else 0,
            "avg_loser": round(-gross_loss / len(losers), 2) if losers else 0,
            "profit_factor": round(gross_wins / gross_loss, 2) if gross_loss > 0 else 0,
            "avg_hold_minutes": round(sum(t.hold_minutes for t in self._trades) / len(self._trades), 1),
        }
        return summary

    def save_results(self, summary: dict) -> None:
        data_dir = Path(__file__).parent.parent / "data"
        data_dir.mkdir(exist_ok=True)
        slug = f"{self._symbol}_{self._start}_{self._end}"

        csv_path = data_dir / f"backtest_{slug}.csv"
        with open(csv_path, "w", newline="") as f:
            if self._trades:
                writer = csv.DictWriter(f, fieldnames=asdict(self._trades[0]).keys())
                writer.writeheader()
                writer.writerows(asdict(t) for t in self._trades)

        json_path = data_dir / f"backtest_{slug}.json"
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)

        logger.info("Saved %d trades → %s", len(self._trades), csv_path)
        logger.info("Saved summary → %s", json_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Trading-Bot-v2 backtester")
    parser.add_argument("--symbol", required=True, help="Ticker symbol e.g. NVAX")
    parser.add_argument("--start",  required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",    required=True, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    load_dotenv()
    required = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        logger.error("Missing required environment variables: %s", missing)
        sys.exit(1)

    cfg_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)

    engine = Backtester(cfg, args.symbol.upper(), start, end)
    summary = engine.run()
    engine.save_results(summary)

    print("\n" + "=" * 60)
    print(f"  BACKTEST RESULTS — {args.symbol.upper()}")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<22} {v}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
