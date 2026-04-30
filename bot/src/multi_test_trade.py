"""
Multi-symbol live trading verification.

Places real bracket orders on Alpaca PAPER for N symbols, holds for the
specified duration with periodic state snapshots posted to telemetry,
then force-closes any still-open positions.

Used by scripts/tradingHealthcheck.sh to verify the whole trading
pipeline end-to-end:
    bot.broker -> Alpaca order  -> fill events
    bot.telemetry -> API event/trade/equity rows
    Dashboard reflects the trade in real time
    Exit logic closes everything before script ends

CLI:
    docker exec ross-bot-1 python /app/src/multi_test_trade.py \\
        --symbols SPY,AAPL,MSFT --qty 1 --hold-minutes 10

Output is JSON to stdout for the wrapping shell script to parse.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# /app/src on path because we live there
from broker import AlpacaBroker
from telemetry import Telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | multi_test | %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],  # stderr so JSON to stdout is clean
)
log = logging.getLogger(__name__)


def get_latest_price(broker: AlpacaBroker, symbol: str) -> Optional[float]:
    """Fetch the most recent traded price."""
    try:
        return broker.get_latest_price(symbol)
    except AttributeError:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        c = StockHistoricalDataClient(
            os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
        )
        q = c.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=symbol))
        return float(q[symbol].ask_price or q[symbol].bid_price)


def place_one(broker: AlpacaBroker, tel: Telemetry, symbol: str, qty: int,
              stop_pct: float, target_pct: float) -> dict:
    """Place a single bracket order. Returns a dict describing what happened."""
    try:
        last_price = get_latest_price(broker, symbol)
        if not last_price or last_price <= 0:
            return {"symbol": symbol, "ok": False, "error": "could not resolve price"}
    except Exception as e:
        return {"symbol": symbol, "ok": False, "error": f"price fetch failed: {e}"}

    entry  = round(last_price, 2)
    stop   = round(entry * (1 - stop_pct  / 100), 2)
    target = round(entry * (1 + target_pct / 100), 2)

    log.info("Placing %s qty=%d entry≈%.2f stop=%.2f target=%.2f",
             symbol, qty, entry, stop, target)

    signal_id = tel.signal(
        symbol=symbol, setup="multi_smoke_test", confidence="A",
        entry_price=entry, stop_price=stop, target_price=target,
        acted=True, price=entry,
    )

    try:
        order = broker.place_bracket_order(
            symbol=symbol, qty=qty,
            stop_price=stop, target_price=target,
        )
    except Exception as e:
        tel.error(f"Multi-test bracket order failed for {symbol}: {e}")
        return {"symbol": symbol, "ok": False, "error": str(e)}

    order_id = getattr(order, "id", None) or getattr(order, "order_id", None)

    trade_id = tel.trade_entry(
        symbol=symbol, setup="multi_smoke_test", qty=qty,
        entry_price=entry, stop_price=stop, target_price=target,
        order_id=str(order_id) if order_id else None,
        signal_id=signal_id,
    )

    return {
        "symbol":   symbol,
        "ok":       True,
        "qty":      qty,
        "entry":    entry,
        "stop":     stop,
        "target":   target,
        "order_id": str(order_id) if order_id else None,
        "trade_id": trade_id,
    }


def snapshot_state(broker: AlpacaBroker) -> dict:
    """One-shot summary of account + positions for logging."""
    try:
        eq = broker.get_equity()
    except Exception:
        eq = None
    try:
        pos = broker.get_positions()
        positions = {
            sym: {
                "qty":           int(p.qty),
                "avg_entry":     float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pl": float(p.unrealized_pl),
            }
            for sym, p in pos.items()
        }
    except Exception:
        positions = {}
    return {"equity": eq, "positions": positions}


def main() -> int:
    p = argparse.ArgumentParser(description="Multi-symbol live trading test")
    p.add_argument("--symbols", default="SPY,AAPL,MSFT",
                   help="Comma-separated list of symbols (default SPY,AAPL,MSFT)")
    p.add_argument("--qty",     type=int,   default=1,    help="Shares per symbol (default 1)")
    p.add_argument("--stop-pct",   type=float, default=0.3, help="Stop %% below entry")
    p.add_argument("--target-pct", type=float, default=0.5, help="Target %% above entry")
    p.add_argument("--hold-minutes", type=int, default=10, help="Hold duration before force-close")
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print(json.dumps({"ok": False, "error": "no symbols provided"}))
        return 1

    broker = AlpacaBroker()
    tel = Telemetry()

    if not broker._paper:
        print(json.dumps({"ok": False, "error": "REFUSING — not in paper mode"}))
        return 1

    # Open today's session so trade rows can attach
    try:
        equity = broker.get_equity()
        tel.session_start(equity=equity, trading_mode="paper")
        tel.event("MULTI_TEST_INIT", f"Starting multi-symbol test on {symbols}")
    except Exception as e:
        log.error("Could not open session: %s", e)
        print(json.dumps({"ok": False, "error": f"session_start failed: {e}"}))
        return 1

    started_at = datetime.now(timezone.utc).isoformat()
    starting_equity = equity
    log.info("Starting equity: $%.2f", starting_equity)

    # ---- 1. Place orders ----
    placements = []
    for sym in symbols:
        result = place_one(broker, tel, sym, args.qty, args.stop_pct, args.target_pct)
        placements.append(result)
        if not result["ok"]:
            log.error("Failed to place %s: %s", sym, result.get("error"))

    accepted = [r for r in placements if r["ok"]]
    if not accepted:
        print(json.dumps({"ok": False, "error": "no orders placed", "placements": placements}))
        return 1

    log.info("Placed %d/%d orders", len(accepted), len(placements))

    # ---- 2. Hold + monitor ----
    snapshots = []
    for tick in range(args.hold_minutes):
        time.sleep(60)
        state = snapshot_state(broker)
        snapshots.append({"t": tick + 1, **state})
        if state["equity"] is not None:
            tel.equity_snapshot(equity=state["equity"],
                                open_position_count=len(state["positions"]))
        log.info("tick %d/%d | equity=%.2f | open=%d",
                 tick + 1, args.hold_minutes,
                 state["equity"] or 0,
                 len(state["positions"]))
        for sym, p in state["positions"].items():
            tel.position_update(sym, current_price=p["current_price"],
                                unrealized_pnl=p["unrealized_pl"])

    # ---- 3. Force-close anything still open ----
    final_state_before_close = snapshot_state(broker)
    closed = []
    for sym in symbols:
        if sym not in final_state_before_close["positions"]:
            continue
        try:
            broker.close_position(sym)
            closed.append(sym)
        except Exception as e:
            log.error("Close failed for %s: %s", sym, e)

    # Wait for closes to settle
    if closed:
        time.sleep(5)

    # ---- 4. Post trade_exit telemetry for any rows we own ----
    final_state = snapshot_state(broker)
    for placement in accepted:
        sym = placement["symbol"]
        trade_id = placement.get("trade_id")
        if not trade_id:
            continue
        try:
            exit_price = get_latest_price(broker, sym) or placement["entry"]
        except Exception:
            exit_price = placement["entry"]
        # If the bracket already fired (sym not in positions), the original
        # bracket-handler closed it. Otherwise we just force-closed it above.
        reason = "test_force_close" if sym in closed else "bracket_natural"
        tel.trade_exit(trade_id=trade_id, exit_price=exit_price, exit_reason=reason)

    ending_equity = final_state.get("equity") or starting_equity

    tel.event("MULTI_TEST_DONE",
              f"Multi-test complete. {len(accepted)}/{len(symbols)} placed, "
              f"PnL: ${ending_equity - starting_equity:+.2f}")

    output = {
        "ok": True,
        "started_at":      started_at,
        "ended_at":        datetime.now(timezone.utc).isoformat(),
        "symbols":         symbols,
        "starting_equity": starting_equity,
        "ending_equity":   ending_equity,
        "pnl":             round(ending_equity - starting_equity, 2),
        "placements":      placements,
        "force_closed":    closed,
        "snapshots":       snapshots,
        "final_positions": list(final_state["positions"].keys()),
    }
    # Wait for fire-and-forget telemetry posts (final trade_exit + event)
    # to actually reach the API before we exit. Without this the daemon
    # threads get killed mid-POST and trade rows stay 'OPEN' forever.
    log.info("Flushing telemetry...")
    tel.flush(timeout=10.0)

    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
