# Paper Trading CLI Demo

This is a small command-line paper trading demo built for the `trading_bot` repository. It fetches historical market data, applies a moving average crossover strategy, and simulates a paper portfolio with buy/sell decisions.

## Features

- Command-line interface with `argparse`
- Downloads OHLCV data from Yahoo Finance
- Calculates short/long moving average signals
- Runs a paper trading simulation with cash and shares
- Prints trade summary and final portfolio value

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python main.py --symbol AAPL --cash 1000 --short-window 5 --long-window 10 --start-date 2024-01-01
```

You can also use a longer run period:

```bash
python main.py --symbol MSFT --cash 2000 --short-window 10 --long-window 30 --start-date 2023-01-01
```

## Notes

This is a demo for learning and simulation only. It does not execute real trades and should not be treated as financial advice.
