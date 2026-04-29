"""
One-off smoke test. Places a tight bracket order on a liquid symbol, posts
full telemetry (signal + trade_entry + equity_snapshot + trade_exit), then
either lets the bracket play out OR force-closes after N minutes.

Run inside the bot container:
    docker exec ross-bot-1 python /app/src/test_trade.py
    docker exec ross-bot-1 python /app/src/test_trade.py --symbol AAPL --qty 1
    docker exec ross-bot-1 python /app/src/test_trade.py --max-bars 10

This bypasses the scanner / strategy / risk gates on purpose so you can
verify the dashboard wiring end-to-end while the live bot still respects
its production criteria for real entries.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

# /app/src on path because we live there
from broker import AlpacaBroker
from telemetry import Telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | test_trade | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description="One-off test trade with full telemetry")
    p.add_argument("--symbol",  default="AAPL", help="Symbol to trade (default AAPL)")
    p.add_argument("--qty",     type=int, default=1,    help="Share quantity (default 1)")
    p.add_argument("--stop-pct",   type=float, default=0.3,  help="Stop %% below entry (default 0.3)")
    p.add_argument("--target-pct", type=float, default=0.4,  help="Target %% above entry (default 0.4)")
    p.add_argument("--max-bars",   type=int,   default=10,   help="Force-close after N minutes (default 10)")
    args = p.parse_args()

    broker = AlpacaBroker()
    tel = Telemetry()

    # Open / refresh today's session so trade rows can attach to it
    try:
        equity = broker.get_equity()
        mode = os.environ.get("TRADING_MODE", "paper").lower()
        tel.session_start(equity=equity, trading_mode=mode)
        tel.event("TEST_TRADE_INIT", f"Test trade starting on {args.symbol}")
    except Exception as e:
        log.error("Could not open session: %s", e)
        return 1

    # Pricing — use the latest trade price so stop/target are realistic
    try:
        last_price = broker.get_latest_price(args.symbol)
    except AttributeError:
        # Fall back to position lookup or quote — if the broker module doesn't
        # expose get_latest_price, fetch a 1-min bar via a different route.
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        c = StockHistoricalDataClient(
            os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
        )
        q = c.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=args.symbol))
        last_price = float(q[args.symbol].ask_price or q[args.symbol].bid_price)

    if not last_price or last_price <= 0:
        log.error("Could not resolve a price for %s", args.symbol)
        return 1

    entry_price  = round(last_price, 2)
    stop_price   = round(entry_price * (1 - args.stop_pct  / 100), 2)
    target_price = round(entry_price * (1 + args.target_pct / 100), 2)

    log.info(
        "TEST TRADE | %s qty=%d | entry≈%.2f | stop=%.2f (-%.2f%%) | target=%.2f (+%.2f%%)",
        args.symbol, args.qty, entry_price, stop_price, args.stop_pct,
        target_price, args.target_pct,
    )

    # Record the synthetic signal first
    signal_id = tel.signal(
        symbol=args.symbol, setup="manual_smoke_test", confidence="A",
        entry_price=entry_price, stop_price=stop_price, target_price=target_price,
        acted=True, price=entry_price,
    )

    # Place the bracket order
    try:
        order = broker.place_bracket_order(
            symbol=args.symbol,
            qty=args.qty,
            stop_price=stop_price,
            target_price=target_price,
        )
    except Exception as e:
        log.error("Bracket order failed: %s", e)
        tel.error(f"Test bracket order failed for {args.symbol}: {e}")
        return 1

    order_id = getattr(order, "id", None)
    log.info("Order submitted, id=%s", order_id)

    trade_id = tel.trade_entry(
        symbol=args.symbol, setup="manual_smoke_test", qty=args.qty,
        entry_price=entry_price, stop_price=stop_price, target_price=target_price,
        order_id=str(order_id) if order_id else None,
        signal_id=signal_id,
    )
    log.info("Telemetry trade_entry posted, trade_id=%s", trade_id)

    # Wait up to max-bars minutes for the bracket to play out, posting equity
    # snapshots each minute so the dashboard chart moves while we wait.
    started = time.time()
    log.info("Watching for natural fill or %d-minute timeout...", args.max_bars)
    closed_naturally = False

    for tick in range(args.max_bars):
        time.sleep(60)

        try:
            equity_now = broker.get_equity()
            tel.equity_snapshot(equity=equity_now, open_position_count=1)
        except Exception:
            pass

        position = broker.get_position(args.symbol)
        if position is None:
            log.info("Bracket order has filled and closed the position naturally")
            closed_naturally = True
            break

        try:
            current = broker.get_latest_price(args.symbol)
        except Exception:
            current = entry_price
        unrealized = (current - entry_price) * args.qty
        log.info(
            "  tick %d/%d | last≈%.2f | unrealized P&L=%+.2f",
            tick + 1, args.max_bars, current, unrealized,
        )
        tel.position_update(args.symbol, current_price=current, unrealized_pnl=unrealized)

    # Force-close if still open
    exit_price = entry_price
    exit_reason = "bracket_natural" if closed_naturally else "test_timeout"

    if not closed_naturally:
        log.info("Force-closing remaining position after %d-minute timeout", args.max_bars)
        try:
            broker.close_position(args.symbol)
        except Exception as e:
            log.error("close_position failed: %s", e)

        try:
            exit_price = broker.get_latest_price(args.symbol)
        except Exception:
            pass

    # Resolve a real exit price from Alpaca's last filled order if we can
    try:
        position = broker.get_position(args.symbol)
        if position is None:
            # Use the most recent close as our settled price
            exit_price = broker.get_latest_price(args.symbol)
    except Exception:
        pass

    if trade_id:
        tel.trade_exit(trade_id=trade_id, exit_price=exit_price, exit_reason=exit_reason)
    tel.event(
        "TEST_TRADE_DONE",
        f"Test trade closed: {args.symbol} entry={entry_price:.2f} exit={exit_price:.2f} reason={exit_reason}",
    )
    log.info(
        "TEST COMPLETE | entry=%.2f exit=%.2f reason=%s",
        entry_price, exit_price, exit_reason,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
