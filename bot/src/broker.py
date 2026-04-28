"""
Alpaca Markets API wrapper.

Uses alpaca-py (the modern SDK, not the legacy alpaca-trade-api).
Paper trading endpoint is used by default — set ALPACA_BASE_URL in .env
to switch to live trading.

Key design choices:
- Bracket orders: single call places entry + stop-loss + profit-target.
  This ensures the stop is ALWAYS in the market when a position is open.
- All order quantities are integers (whole shares).
- Market orders only during the 7–11 AM window (tight spreads, high volume).
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopOrderRequest,
    OcoOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    qty: int
    filled_avg_price: Optional[float]
    status: str


class AlpacaBroker:
    def __init__(self):
        api_key = os.environ["ALPACA_API_KEY"]
        secret_key = os.environ["ALPACA_SECRET_KEY"]
        paper = os.environ.get("TRADING_MODE", "paper").lower() == "paper"

        self._trading = TradingClient(api_key, secret_key, paper=paper)
        self._data = StockHistoricalDataClient(api_key, secret_key)
        self._paper = paper

        mode = "PAPER" if paper else "LIVE"
        logger.info("AlpacaBroker initialized [%s]", mode)

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_equity(self) -> float:
        account = self._trading.get_account()
        return float(account.equity)

    def get_buying_power(self) -> float:
        account = self._trading.get_account()
        return float(account.buying_power)

    def get_positions(self) -> dict:
        """Returns {symbol: position_obj} for all open positions."""
        positions = self._trading.get_all_positions()
        return {p.symbol: p for p in positions}

    def get_position(self, symbol: str):
        try:
            return self._trading.get_open_position(symbol)
        except Exception:
            return None

    def get_open_orders(self) -> list:
        from alpaca.trading.requests import GetOrdersRequest
        req = GetOrdersRequest(status="open")
        return self._trading.get_orders(filter=req)

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_bracket_order(
        self,
        symbol: str,
        qty: int,
        stop_price: float,
        target_price: float,
    ) -> OrderResult:
        """
        Bracket order = market entry + stop-loss + profit-target in one call.
        This is the preferred order type — stop is always in the market.
        """
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class="bracket",
            stop_loss={"stop_price": round(stop_price, 2)},
            take_profit={"limit_price": round(target_price, 2)},
        )
        order = self._trading.submit_order(req)
        logger.info(
            "Bracket order submitted: %s x%d | stop=%.2f target=%.2f | id=%s",
            symbol, qty, stop_price, target_price, order.id,
        )
        return OrderResult(
            order_id=str(order.id),
            symbol=symbol,
            side="buy",
            qty=qty,
            filled_avg_price=None,
            status=str(order.status),
        )

    def place_market_sell(self, symbol: str, qty: int) -> OrderResult:
        """Emergency full exit — sells at market immediately."""
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = self._trading.submit_order(req)
        logger.info("Market SELL: %s x%d | id=%s", symbol, qty, order.id)
        return OrderResult(
            order_id=str(order.id),
            symbol=symbol,
            side="sell",
            qty=qty,
            filled_avg_price=None,
            status=str(order.status),
        )

    def cancel_all_orders(self) -> None:
        self._trading.cancel_orders()
        logger.info("All open orders cancelled")

    def close_all_positions(self) -> None:
        """End-of-day cleanup — close every open position at market."""
        self._trading.close_all_positions(cancel_orders=True)
        logger.info("All positions closed")

    def close_position(self, symbol: str) -> None:
        try:
            self._trading.close_position(symbol)
            logger.info("Position closed: %s", symbol)
        except Exception as e:
            logger.warning("Could not close %s: %s", symbol, e)

    # ------------------------------------------------------------------
    # Market data helpers (used by scanner + strategy)
    # ------------------------------------------------------------------

    def get_latest_quote(self, symbol: str) -> dict:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = self._data.get_stock_latest_quote(req)[symbol]
        return {
            "ask": float(quote.ask_price),
            "bid": float(quote.bid_price),
            "mid": (float(quote.ask_price) + float(quote.bid_price)) / 2,
        }

    def get_bars(self, symbol: str, timeframe: TimeFrame, limit: int = 60) -> "pd.DataFrame":
        """Returns a DataFrame of OHLCV bars for the given symbol."""
        import pandas as pd
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            limit=limit,
        )
        bars = self._data.get_stock_bars(req)[symbol]
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

    def get_daily_bars(self, symbol: str, limit: int = 55) -> "pd.DataFrame":
        return self.get_bars(symbol, TimeFrame.Day, limit=limit)

    def get_minute_bars(self, symbol: str, limit: int = 60) -> "pd.DataFrame":
        return self.get_bars(symbol, TimeFrame.Minute, limit=limit)
