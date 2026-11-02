from __future__ import annotations

from typing import Optional

import pandas as pd
import yfinance as yf


def fetch_market_data(symbol: str, start_date: str, end_date: Optional[str] = None) -> pd.DataFrame:
    data = yf.download(
        symbol,
        start=start_date,
        end=end_date,
        auto_adjust=False,
        progress=False,
        actions=False,
    )
    if data.empty:
        raise ValueError(f"No market data available for {symbol} between {start_date} and {end_date}")
    return data.reset_index()
