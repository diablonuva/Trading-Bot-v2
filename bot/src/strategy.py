"""
Signal engine — micro pullback and bull flag detection.

Both setups follow the same entry gate:
  1. MACD > 0 (front side of move only)
  2. Price above VWAP
  3. Volume on breakout candle >= avg_volume * surge_multiplier
  4. Pattern detected (micro pullback OR bull flag)

Returns a Signal dataclass or None.

Designed to be called on every new 1-minute bar close.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from indicators import (
    current_vwap,
    current_macd,
    relative_volume,
    is_topping_tail,
    detect_flagpole,
    pullback_depth_pct,
)

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    setup: str            # "micro_pullback", "bull_flag", or "abcd"
    entry_price: float
    stop_price: float
    target_price: float
    confidence: str       # "A" | "B"  (only A signals should be traded)


class Strategy:
    def __init__(self, cfg: dict):
        ind = cfg["indicators"]
        self._macd_fast = ind["macd_fast"]
        self._macd_slow = ind["macd_slow"]
        self._macd_signal = ind["macd_signal"]
        self._vol_surge = ind["volume_surge_multiplier"]
        self._vol_avg_period = ind["volume_avg_period"]
        self._pullback_max_candles = ind["pullback_max_candles"]
        self._pullback_max_retrace = ind["pullback_max_retrace_pct"]
        self._flag_max_candles = ind["flag_max_candles"]
        self._topping_tail_ratio = ind["topping_tail_ratio"]
        self._rr_min = cfg["risk"]["reward_to_risk_min"]

    # ------------------------------------------------------------------
    # Main entry point — called on every new bar
    # ------------------------------------------------------------------

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        """
        Returns a Signal if an A-quality setup is found, else None.
        Requires at least 30 candles for reliable indicator values.
        """
        if len(df) < 30:
            return None

        # Gate 1: MACD must be positive (front side of the move)
        macd = current_macd(df, self._macd_fast, self._macd_slow, self._macd_signal)
        if not macd["is_positive"]:
            return None

        # Gate 2: Price must be above VWAP
        vwap = current_vwap(df)
        last_close = float(df["close"].iloc[-1])
        if last_close < vwap:
            return None

        # Gate 3: Volume surge on the most recent candle
        rvol = relative_volume(df, self._vol_avg_period)
        has_vol_surge = rvol >= self._vol_surge

        # Try micro pullback first (higher frequency, lower hold time)
        signal = self._detect_micro_pullback(symbol, df, vwap, has_vol_surge)
        if signal:
            return signal

        # Try bull flag (slower-forming, equally reliable)
        signal = self._detect_bull_flag(symbol, df, vwap, has_vol_surge)
        if signal:
            return signal

        # Try ABCD pattern (Fibonacci-based reversal)
        signal = self._detect_abcd(symbol, df, vwap, has_vol_surge)
        return signal

    # ------------------------------------------------------------------
    # Exit signals — checked against open positions each bar
    # ------------------------------------------------------------------

    def should_exit(self, symbol: str, df: pd.DataFrame, entry_price: float, stop_price: float) -> tuple[bool, str]:
        """
        Returns (True, reason) if the position should be closed NOW.
        The broker's bracket order handles the stop and target automatically,
        but this catches additional exit conditions.
        """
        if len(df) < 2:
            return False, ""

        last = df.iloc[-1]
        vwap = current_vwap(df)

        # VWAP breach — hard exit
        if float(last["close"]) < vwap:
            return True, "price closed below VWAP"

        # Topping tail on the last candle
        if is_topping_tail(last, self._topping_tail_ratio):
            return True, "topping tail / shooting star detected"

        # MACD turned negative
        macd = current_macd(df, self._macd_fast, self._macd_slow, self._macd_signal)
        if macd["crossed_negative"]:
            return True, "MACD crossed below zero (back side of move)"

        # Three consecutive red candles — bearish momentum shift
        if len(df) >= 3:
            last3 = df.iloc[-3:]
            all_red = all(float(row["close"]) < float(row["open"]) for _, row in last3.iterrows())
            if all_red:
                return True, "three_red_candles"

        return False, ""

    # ------------------------------------------------------------------
    # Micro pullback detector
    # ------------------------------------------------------------------

    def _detect_micro_pullback(
        self, symbol: str, df: pd.DataFrame, vwap: float, has_vol_surge: bool
    ) -> Optional[Signal]:
        """
        Pattern:
          1. Flagpole: strong upward move in last ~15 candles
          2. Pullback: price retreats <= 50% of flagpole, volume decreasing
          3. Trigger: current candle closes above the pullback high (new high)
        """
        pole = detect_flagpole(df, lookback=15)
        if pole is None:
            return None

        # Find the pullback phase — candles after the flagpole high
        high_idx_loc = df.index.get_loc(pole["high_idx"])
        post_pole = df.iloc[high_idx_loc + 1 :]

        if len(post_pole) < 2 or len(post_pole) > self._pullback_max_candles:
            return None

        pullback_low = float(post_pole["low"].min())
        retrace = pullback_depth_pct(pole["high"], pullback_low, pole["height"])

        if retrace > self._pullback_max_retrace:
            return None  # too deep — not a micro pullback

        # Volume should be lighter during pullback
        pullback_avg_vol = float(post_pole["volume"].mean())
        pole_candles = df.iloc[
            df.index.get_loc(pole["low_idx"]) : df.index.get_loc(pole["high_idx"]) + 1
        ]
        pole_avg_vol = float(pole_candles["volume"].mean())

        # Trigger: last candle broke back above the most recent swing high
        recent_high = float(post_pole["high"].max())
        last_close = float(df["close"].iloc[-1])
        last_high = float(df["high"].iloc[-1])

        if last_close <= recent_high:
            return None  # hasn't broken out yet

        if not has_vol_surge:
            return None  # no volume confirmation

        # Calculate entry / stop / target
        entry = last_close
        stop = pullback_low - 0.02  # 2 cents below pullback low
        stop = max(stop, entry - 0.30)  # absolute max stop: 30 cents
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + (risk * self._rr_min)

        # Must have clear room to target (no obvious resistance in the way)
        confidence = "A" if (
            has_vol_surge
            and pullback_avg_vol < pole_avg_vol * 0.8  # lighter volume on pullback
            and retrace < 40.0  # shallow retrace = healthier pattern
        ) else "B"

        logger.debug(
            "Micro pullback signal: %s | entry=%.2f stop=%.2f target=%.2f retrace=%.1f%% conf=%s",
            symbol, entry, stop, target, retrace, confidence,
        )

        return Signal(
            symbol=symbol,
            setup="micro_pullback",
            entry_price=round(entry, 2),
            stop_price=round(stop, 2),
            target_price=round(target, 2),
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Bull flag detector
    # ------------------------------------------------------------------

    def _detect_bull_flag(
        self, symbol: str, df: pd.DataFrame, vwap: float, has_vol_surge: bool
    ) -> Optional[Signal]:
        """
        Pattern:
          1. Flagpole: strong upward move
          2. Flag: tight consolidation (high-low range < 30% of flagpole)
          3. Trigger: breakout above flag high on volume
          4. Flag must not break below VWAP
        """
        pole = detect_flagpole(df, lookback=20)
        if pole is None:
            return None

        high_idx_loc = df.index.get_loc(pole["high_idx"])
        flag_candles = df.iloc[high_idx_loc + 1 :]

        if len(flag_candles) < 3 or len(flag_candles) > self._flag_max_candles:
            return None

        flag_high = float(flag_candles["high"].max())
        flag_low = float(flag_candles["low"].min())
        flag_range = flag_high - flag_low

        # Flag must be tight — range < 30% of flagpole height
        if pole["height"] == 0 or (flag_range / pole["height"]) > 0.30:
            return None

        # Flag must stay above VWAP
        if flag_low < vwap:
            return None

        # Breakout: last close above flag high
        last_close = float(df["close"].iloc[-1])
        if last_close <= flag_high:
            return None

        if not has_vol_surge:
            return None

        entry = last_close
        stop = flag_low - 0.02
        stop = max(stop, entry - 0.30)
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + (risk * self._rr_min)

        confidence = "A" if (
            has_vol_surge
            and flag_range / pole["height"] < 0.20  # very tight flag
        ) else "B"

        logger.debug(
            "Bull flag signal: %s | entry=%.2f stop=%.2f target=%.2f flag_range=%.3f conf=%s",
            symbol, entry, stop, target, flag_range, confidence,
        )

        return Signal(
            symbol=symbol,
            setup="bull_flag",
            entry_price=round(entry, 2),
            stop_price=round(stop, 2),
            target_price=round(target, 2),
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # ABCD pattern detector
    # ------------------------------------------------------------------

    def _detect_abcd(
        self, symbol: str, df: pd.DataFrame, vwap: float, has_vol_surge: bool
    ) -> Optional[Signal]:
        """
        ABCD pattern:
          A — recent swing low
          B — strong push to a new high (flagpole)
          C — pullback to ~61.8% Fibonacci of A→B
          D — break above B (entry trigger)

        Requires at least 30 bars. Looks back up to 40 candles for A.
        """
        if len(df) < 30:
            return None

        lookback = min(40, len(df) - 1)
        subset = df.iloc[-lookback:]

        # Find A: lowest low in the lookback window (excluding last 3 bars)
        search = subset.iloc[:-3]
        if search.empty:
            return None
        a_idx = search["low"].idxmin()
        a_price = float(df.loc[a_idx, "low"])

        # Find B: highest high after A
        after_a = df.loc[a_idx:].iloc[1:]
        if after_a.empty:
            return None
        b_idx = after_a["high"].idxmax()
        b_price = float(df.loc[b_idx, "high"])

        ab_height = b_price - a_price
        if ab_height <= 0:
            return None

        # C must be after B: pullback to 50–78.6% Fibonacci of A→B
        after_b = df.loc[b_idx:].iloc[1:]
        if after_b.empty or len(after_b) < 2:
            return None
        c_price = float(after_b["low"].min())
        fib_retrace = (b_price - c_price) / ab_height

        if not (0.50 <= fib_retrace <= 0.786):
            return None

        # D: last close must break above B
        last_close = float(df["close"].iloc[-1])
        if last_close <= b_price:
            return None

        if not has_vol_surge:
            return None

        entry = last_close
        stop = c_price - 0.02
        stop = max(stop, entry - 0.30)
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + (risk * self._rr_min)

        # A-quality if the Fibonacci retrace is close to the ideal 61.8%
        confidence = "A" if (has_vol_surge and 0.55 <= fib_retrace <= 0.70) else "B"

        logger.debug(
            "ABCD signal: %s | A=%.2f B=%.2f C=%.2f D=%.2f fib=%.1f%% conf=%s",
            symbol, a_price, b_price, c_price, last_close, fib_retrace * 100, confidence,
        )

        return Signal(
            symbol=symbol,
            setup="abcd",
            entry_price=round(entry, 2),
            stop_price=round(stop, 2),
            target_price=round(target, 2),
            confidence=confidence,
        )
