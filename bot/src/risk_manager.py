"""
Risk Manager — position sizing, daily loss limit, trade gates.

Key rules encoded from DaytradeWarrior methodology:
  - Risk exactly 1% of account equity per trade (configurable)
  - Hard stop: 10 cents per share (configurable in settings.yaml)
  - Minimum 2:1 risk-to-reward ratio required before entry
  - Halt all trading if daily loss exceeds max_daily_loss_pct
  - Max N trades per day enforced
  - After hitting daily goal, allow max 20% giveback before stopping

All state resets at the start of each trading day.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    symbol: str
    entry_price: float
    exit_price: float
    qty: int
    side: str
    pnl: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskManager:
    def __init__(self, cfg: dict, broker):
        self._cfg = cfg["risk"]
        self._broker = broker

        # Day-level state — reset each morning
        self._session_date: date | None = None
        self._starting_equity: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_peak_pnl: float = 0.0
        self._trades_today: int = 0
        self._trade_log: list[TradeRecord] = []
        self._halted: bool = False

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def start_session(self) -> None:
        """Call once at the start of each trading day."""
        self._session_date = date.today()
        self._starting_equity = self._broker.get_equity()
        self._daily_pnl = 0.0
        self._daily_peak_pnl = 0.0
        self._trades_today = 0
        self._trade_log = []
        self._halted = False
        logger.info(
            "Risk session started | equity=%.2f | max_loss=%.2f | max_trades=%d",
            self._starting_equity,
            self._max_daily_loss_dollars(),
            self._cfg["max_trades_per_day"],
        )

    # ------------------------------------------------------------------
    # Entry gate — call BEFORE placing any order
    # ------------------------------------------------------------------

    def can_enter(self, entry_price: float, stop_price: float, target_price: float) -> tuple[bool, str]:
        """
        Returns (True, "") if allowed, or (False, reason) if blocked.
        Also returns the approved share quantity as a side effect — see
        `calculate_shares` for the quantity.
        """
        if self._halted:
            return False, "trading halted for the day (daily loss limit hit)"

        if self._trades_today >= self._cfg["max_trades_per_day"]:
            return False, f"max trades per day reached ({self._cfg['max_trades_per_day']})"

        stop_dist = entry_price - stop_price
        if stop_dist <= 0:
            return False, "stop price must be below entry price"

        reward = target_price - entry_price
        rr_ratio = reward / stop_dist if stop_dist > 0 else 0
        if rr_ratio < self._cfg["reward_to_risk_min"]:
            return False, f"R:R {rr_ratio:.2f} below minimum {self._cfg['reward_to_risk_min']}"

        # Check daily giveback rule: if we've hit a peak, don't give back too much
        if self._daily_peak_pnl > 0:
            giveback_pct = self._cfg["daily_giveback_pct"] / 100
            max_giveback = self._daily_peak_pnl * giveback_pct
            if self._daily_pnl < self._daily_peak_pnl - max_giveback:
                self._halted = True
                return False, (
                    f"daily giveback limit hit — peak={self._daily_peak_pnl:.2f}, "
                    f"current={self._daily_pnl:.2f}"
                )

        return True, ""

    def calculate_shares(self, entry_price: float, stop_price: float) -> int:
        """
        Position size = account_risk_dollars / stop_distance_per_share.
        Rounded down to whole shares, minimum 1.
        """
        equity = self._broker.get_equity()
        risk_dollars = equity * (self._cfg["account_risk_pct"] / 100)
        stop_dist = entry_price - stop_price
        if stop_dist <= 0:
            return 0

        # Also cap at the hard-coded stop in cents (safety floor)
        min_stop = self._cfg["stop_loss_per_share"]
        effective_stop = max(stop_dist, min_stop)

        shares = int(risk_dollars / effective_stop)
        shares = max(1, shares)
        logger.info(
            "Position size: %d shares | risk=%.2f | stop_dist=%.3f",
            shares, risk_dollars, effective_stop,
        )
        return shares

    # ------------------------------------------------------------------
    # Exit monitoring — call after each closed trade
    # ------------------------------------------------------------------

    def record_trade(self, symbol: str, entry: float, exit_: float, qty: int, side: str) -> None:
        pnl = (exit_ - entry) * qty if side == "buy" else (entry - exit_) * qty
        self._daily_pnl += pnl
        self._daily_peak_pnl = max(self._daily_peak_pnl, self._daily_pnl)
        self._trades_today += 1

        rec = TradeRecord(symbol=symbol, entry_price=entry, exit_price=exit_,
                          qty=qty, side=side, pnl=pnl)
        self._trade_log.append(rec)

        logger.info(
            "Trade recorded: %s %s %dx @ %.2f → %.2f | pnl=%.2f | day_pnl=%.2f",
            side.upper(), symbol, qty, entry, exit_, pnl, self._daily_pnl,
        )

        self._check_daily_loss_limit()

    def _check_daily_loss_limit(self) -> None:
        max_loss = self._max_daily_loss_dollars()
        if self._daily_pnl <= -max_loss:
            self._halted = True
            logger.warning(
                "DAILY LOSS LIMIT HIT — day_pnl=%.2f max_loss=%.2f — TRADING HALTED",
                self._daily_pnl, max_loss,
            )

    def _max_daily_loss_dollars(self) -> float:
        return self._starting_equity * (self._cfg["max_daily_loss_pct"] / 100)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_halted(self) -> bool:
        return self._halted

    def daily_summary(self) -> dict:
        winners = [t for t in self._trade_log if t.pnl > 0]
        losers = [t for t in self._trade_log if t.pnl <= 0]
        return {
            "date": str(self._session_date),
            "trades": self._trades_today,
            "pnl": round(self._daily_pnl, 2),
            "winners": len(winners),
            "losers": len(losers),
            "accuracy_pct": round(len(winners) / max(self._trades_today, 1) * 100, 1),
            "avg_winner": round(sum(t.pnl for t in winners) / max(len(winners), 1), 2),
            "avg_loser": round(sum(t.pnl for t in losers) / max(len(losers), 1), 2),
            "halted": self._halted,
        }
