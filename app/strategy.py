from __future__ import annotations

import pandas as pd


class MovingAverageStrategy:
    """Simple long-only SMA crossover strategy for demonstration/backtesting."""

    def __init__(self, short_window: int = 5, long_window: int = 10):
        if short_window <= 0 or long_window <= 0:
            raise ValueError("Window sizes must be positive integers")
        if short_window >= long_window:
            raise ValueError("Short window must be smaller than long window")
        self.short_window = short_window
        self.long_window = long_window

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        if "Close" not in data:
            raise ValueError("Market data must contain a Close column")
        result = data.copy()
        close = pd.to_numeric(result["Close"], errors="coerce")
        result["SMA_short"] = close.rolling(self.short_window, min_periods=self.short_window).mean()
        result["SMA_long"] = close.rolling(self.long_window, min_periods=self.long_window).mean()
        previous_short = result["SMA_short"].shift(1)
        previous_long = result["SMA_long"].shift(1)
        bullish = (previous_short <= previous_long) & (result["SMA_short"] > result["SMA_long"])
        bearish = (previous_short >= previous_long) & (result["SMA_short"] < result["SMA_long"])
        result["signal"] = 0
        result.loc[bullish.fillna(False), "signal"] = 1
        result.loc[bearish.fillna(False), "signal"] = -1
        return result
