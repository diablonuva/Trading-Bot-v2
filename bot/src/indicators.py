"""
Technical indicator calculations.

All functions accept a pandas DataFrame with columns:
  open, high, low, close, volume  (lowercase, datetime index)

Returns are plain floats or pandas Series — callers decide what to log.
"""

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# VWAP — Volume Weighted Average Price
# Resets each session (intraday use only)
# ---------------------------------------------------------------------------

def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Standard intraday VWAP: cumulative(price * volume) / cumulative(volume).
    Typical price = (high + low + close) / 3.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_tpv = (typical * df["volume"]).cumsum()
    return cum_tpv / cum_vol


def current_vwap(df: pd.DataFrame) -> float:
    return float(vwap(df).iloc[-1])


# ---------------------------------------------------------------------------
# EMA — Exponential Moving Average
# ---------------------------------------------------------------------------

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def current_ema(df: pd.DataFrame, period: int) -> float:
    return float(ema(df["close"], period).iloc[-1])


# ---------------------------------------------------------------------------
# MACD — Moving Average Convergence Divergence
# ---------------------------------------------------------------------------

def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    Returns dict with keys: macd_line, signal_line, histogram (all Series).
    MACD line > 0 means 'front side' of move — OK to trade long.
    """
    close = df["close"]
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
    }


def current_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    result = macd(df, fast, slow, signal)
    return {
        "macd_line": float(result["macd_line"].iloc[-1]),
        "signal_line": float(result["signal_line"].iloc[-1]),
        "histogram": float(result["histogram"].iloc[-1]),
        "is_positive": float(result["macd_line"].iloc[-1]) > 0,
        # True if MACD just crossed DOWN (trend ending — exit signal)
        "crossed_negative": (
            float(result["macd_line"].iloc[-1]) < 0
            and float(result["macd_line"].iloc[-2]) >= 0
            if len(df) >= 2 else False
        ),
    }


# ---------------------------------------------------------------------------
# Relative Volume
# ---------------------------------------------------------------------------

def relative_volume(df: pd.DataFrame, avg_period: int = 10) -> float:
    """
    Current bar volume vs N-bar rolling average.
    A value >= 1.5 on a breakout candle confirms momentum.
    """
    if len(df) < avg_period + 1:
        return 1.0
    avg = df["volume"].iloc[-(avg_period + 1):-1].mean()
    if avg == 0:
        return 1.0
    return float(df["volume"].iloc[-1] / avg)


def historical_relative_volume(current_volume: float, avg_daily_volume: float) -> float:
    """
    Scanner-level relative volume: today's cumulative volume vs 50-day avg daily volume.
    """
    if avg_daily_volume == 0:
        return 1.0
    return current_volume / avg_daily_volume


# ---------------------------------------------------------------------------
# Candle pattern detection
# ---------------------------------------------------------------------------

def is_topping_tail(candle: pd.Series, ratio: float = 2.0) -> bool:
    """
    Shooting star / topping tail: upper wick is N× the candle body.
    Strong exit signal when appearing at highs.
    """
    body = abs(candle["close"] - candle["open"])
    upper_wick = candle["high"] - max(candle["close"], candle["open"])
    if body == 0:
        return upper_wick > 0  # doji with upper wick = topping tail
    return upper_wick >= body * ratio


def is_doji(candle: pd.Series, threshold_pct: float = 0.1) -> bool:
    """Body is < threshold_pct of the high-low range — indecision candle."""
    candle_range = candle["high"] - candle["low"]
    if candle_range == 0:
        return True
    body = abs(candle["close"] - candle["open"])
    return (body / candle_range) < threshold_pct


def is_bullish_candle(candle: pd.Series) -> bool:
    return candle["close"] > candle["open"]


# ---------------------------------------------------------------------------
# Flagpole / micro pullback helpers
# ---------------------------------------------------------------------------

def detect_flagpole(df: pd.DataFrame, lookback: int = 10) -> dict | None:
    """
    Looks back `lookback` candles for a strong move up.
    Returns {'start_idx', 'end_idx', 'low', 'high', 'height'} or None.
    """
    if len(df) < lookback:
        return None

    window = df.iloc[-lookback:]
    low = float(window["low"].min())
    high = float(window["high"].max())
    height = high - low

    low_idx = window["low"].idxmin()
    high_idx = window["high"].idxmax()

    # Flagpole is only valid if the low came BEFORE the high (upward move)
    if low_idx >= high_idx:
        return None

    # Flagpole height must be at least 2% of entry price to be meaningful
    if low == 0 or (height / low) < 0.02:
        return None

    return {
        "low": low,
        "high": high,
        "height": height,
        "low_idx": low_idx,
        "high_idx": high_idx,
    }


def pullback_depth_pct(flagpole_high: float, current_low: float, flagpole_height: float) -> float:
    """How far (%) the stock has retraced from the flagpole high."""
    if flagpole_height == 0:
        return 0.0
    retrace = flagpole_high - current_low
    return (retrace / flagpole_height) * 100
