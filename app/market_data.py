from __future__ import annotations

from typing import Optional

import pandas as pd
import yfinance as yf


def fetch_market_data(symbol: str, start_date: str, end_date: Optional[str] = None) -> pd.DataFrame:
    """Download historical daily candles from Yahoo Finance for simulation only."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Symbol cannot be empty")
    try:
        data = yf.download(
            symbol,
            start=start_date,
            end=end_date,
            auto_adjust=False,
            progress=False,
            actions=False,
            multi_level_index=False,
        )
    except Exception as exc:
        raise RuntimeError(f"Could not download data for {symbol}: {exc}") from exc
    if data.empty:
        raise ValueError(f"No market data available for {symbol} between {start_date} and {end_date or 'today'}")

    # yfinance may return MultiIndex columns depending on its version/options.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data = data.reset_index()
    if "Close" not in data:
        raise ValueError(f"Downloaded data for {symbol} has no Close column")
    data = data.dropna(subset=["Close"]).reset_index(drop=True)
    if data.empty:
        raise ValueError(f"Downloaded data for {symbol} contains no usable closing prices")
    return data
