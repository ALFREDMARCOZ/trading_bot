from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Optional

from app.market_data import fetch_market_data
from app.portfolio import PaperPortfolio
from app.strategy import MovingAverageStrategy


@dataclass
class DemoConfig:
    symbol: str = "AAPL"
    cash: float = 10.0
    short_window: int = 5
    long_window: int = 10
    start_date: str = "2024-01-01"
    end_date: Optional[str] = None
    max_position_fraction: float = 0.25
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    output: Optional[str] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Historical paper-trading backtest (never places real orders)."
    )
    parser.add_argument("--symbol", default="AAPL", help="Yahoo Finance ticker")
    parser.add_argument("--cash", type=float, default=10.0, help="Starting simulated cash")
    parser.add_argument("--short-window", type=int, default=5, help="Short SMA length in candles")
    parser.add_argument("--long-window", type=int, default=10, help="Long SMA length in candles")
    parser.add_argument("--start-date", default="2024-01-01", help="First date, YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="Exclusive end date, YYYY-MM-DD")
    parser.add_argument(
        "--max-position-fraction", type=float, default=0.25,
        help="Maximum available cash allocated on one entry (0 to 1)",
    )
    parser.add_argument("--fee-rate", type=float, default=0.001, help="Simulated fee fraction per order")
    parser.add_argument("--slippage-rate", type=float, default=0.0005, help="Simulated adverse price impact per order")
    parser.add_argument("--output", default=None, help="Optional path to save equity curve CSV")
    return parser.parse_args()


def run_demo(config: DemoConfig) -> dict:
    if config.cash <= 0:
        raise ValueError("--cash must be greater than zero")
    if not 0 < config.max_position_fraction <= 1:
        raise ValueError("--max-position-fraction must be greater than 0 and at most 1")
    if config.fee_rate < 0 or config.slippage_rate < 0:
        raise ValueError("Fee and slippage rates cannot be negative")

    data = fetch_market_data(config.symbol, config.start_date, config.end_date)
    strategy = MovingAverageStrategy(config.short_window, config.long_window)
    signals = strategy.generate_signals(data)
    portfolio = PaperPortfolio(
        cash=config.cash,
        max_position_fraction=config.max_position_fraction,
        fee_rate=config.fee_rate,
        slippage_rate=config.slippage_rate,
    )
    result = portfolio.simulate(signals)
    result.update({
        "symbol": config.symbol.upper(),
        "short_window": config.short_window,
        "long_window": config.long_window,
        "rows_processed": len(signals),
        "start_date": str(signals["Date"].iloc[0].date()) if "Date" in signals else config.start_date,
        "end_date": str(signals["Date"].iloc[-1].date()) if "Date" in signals else "unknown",
    })
    if config.output:
        portfolio.save_equity_curve(config.output)
    return result


def main() -> int:
    args = parse_args()
    config = DemoConfig(**vars(args))
    try:
        result = run_demo(config)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print("Historical Paper-Trading Backtest — simulated only")
    print("=" * 54)
    print(f"Symbol / period: {result['symbol']} / {result['start_date']} to {result['end_date']}")
    print(f"Candles processed: {result['rows_processed']}")
    print(f"Starting cash:     ${result['initial_cash']:.2f}")
    print(f"Ending cash:       ${result['final_cash']:.2f}")
    print(f"Ending shares:     {result['final_shares']:.6f}")
    print(f"Portfolio value:   ${result['portfolio_value']:.2f}")
    print(f"Buy-and-hold:      ${result['buy_and_hold_value']:.2f}")
    print(f"Total return:      {result['total_return_pct']:.2f}%")
    print(f"Max drawdown:      {result['max_drawdown_pct']:.2f}%")
    print(f"Orders / trades:   {result['orders']} / {result['closed_trades']}")
    print(f"Win rate:          {result['win_rate_pct']:.2f}%")
    print(f"Fees paid:         ${result['fees_paid']:.4f}")
    if args.output:
        print(f"Equity CSV:        {args.output}")
    print("\nHistorical results are not predictions; this program does not place real trades.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
