from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional

from app.market_data import fetch_market_data
from app.strategy import MovingAverageStrategy
from app.portfolio import PaperPortfolio


@dataclass
class DemoConfig:
    symbol: str = "AAPL"
    cash: float = 1000.0
    short_window: int = 5
    long_window: int = 10
    start_date: str = "2024-01-01"
    end_date: Optional[str] = None
    max_position_fraction: float = 0.25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple paper trading demo")
    parser.add_argument("--symbol", default="AAPL", help="Ticker symbol to trade")
    parser.add_argument("--cash", type=float, default=1000.0, help="Starting cash balance")
    parser.add_argument("--short-window", type=int, default=5, help="Short SMA window")
    parser.add_argument("--long-window", type=int, default=10, help="Long SMA window")
    parser.add_argument("--start-date", default="2024-01-01", help="Data start date YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="Optional end date YYYY-MM-DD")
    parser.add_argument("--max-position-fraction", type=float, default=0.25, help="Fraction of cash to allocate per position")
    return parser.parse_args()


def run_demo(config: DemoConfig) -> dict:
    market_data = fetch_market_data(config.symbol, config.start_date, config.end_date)
    strategy = MovingAverageStrategy(config.short_window, config.long_window)
    signals = strategy.generate_signals(market_data)

    portfolio = PaperPortfolio(
        cash=config.cash,
        max_position_fraction=config.max_position_fraction,
    )

    result = portfolio.simulate(signals)
    result["symbol"] = config.symbol
    result["short_window"] = config.short_window
    result["long_window"] = config.long_window
    result["rows_processed"] = len(signals)
    return result


if __name__ == "__main__":
    args = parse_args()
    config = DemoConfig(
        symbol=args.symbol,
        cash=args.cash,
        short_window=args.short_window,
        long_window=args.long_window,
        start_date=args.start_date,
        end_date=args.end_date,
        max_position_fraction=args.max_position_fraction,
    )

    result = run_demo(config)
    print("Paper Trading Demo Report")
    print("=" * 32)
    print(f"Symbol: {result['symbol']}")
    print(f"Initial cash: ${result['initial_cash']:.2f}")
    print(f"Final cash: ${result['final_cash']:.2f}")
    print(f"Final shares: {result['final_shares']:.4f}")
    print(f"Portfolio value: ${result['portfolio_value']:.2f}")
    print(f"Net return: {result['net_return_pct']:.2f}%")
    print(f"Trades executed: {result['trades_executed']}")
    print(f"Rows processed: {result['rows_processed']}")
