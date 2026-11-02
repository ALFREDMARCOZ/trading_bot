from __future__ import annotations

import pandas as pd


class MovingAverageStrategy:
    def __init__(self, short_window: int = 5, long_window: int = 10):
        if short_window <= 0 or long_window <= 0:
            raise ValueError("Window sizes must be positive integers.")
        if short_window >= long_window:
            raise ValueError("Short window must be smaller than the long window.")

        self.short_window = short_window
        self.long_window = long_window

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        signals = data.copy()
        signals["SMA_short"] = signals["Close"].rolling(window=self.short_window).mean()
        signals["SMA_long"] = signals["Close"].rolling(window=self.long_window).mean()
        signals["signal"] = 0

        # Signal = 1 for bullish crossover, -1 for bearish crossover
        for idx in range(1, len(signals)):
            prev_short = signals.iloc[idx - 1]["SMA_short"]
            prev_long = signals.iloc[idx - 1]["SMA_long"]
            curr_short = signals.iloc[idx]["SMA_short"]
            curr_long = signals.iloc[idx]["SMA_long"]

            if pd.notna(prev_short) and pd.notna(prev_long) and pd.notna(curr_short) and pd.notna(curr_long):
                if prev_short <= prev_long and curr_short > curr_long:
                    signals.at[signals.index[idx], "signal"] = 1
                elif prev_short >= prev_long and curr_short < curr_long:
                    signals.at[signals.index[idx], "signal"] = -1

        # Fill missing values with 0 for first rows
        signals["signal"] = signals["signal"].fillna(0)
        return signals
