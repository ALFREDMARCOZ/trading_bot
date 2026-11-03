# Paper Trading Backtest Demo

A small, **paper-only** Python project for exploring algorithmic-trading concepts. It borrows useful workflow ideas from projects such as Freqtrade—configurable strategies, historical backtests, simulated transaction costs, and performance reports—without copying Freqtrade or connecting to an exchange.

## What it does

- Downloads historical daily OHLCV prices using Yahoo Finance.
- Generates long-only moving-average crossover signals.
- Backtests with next-candle execution to avoid trading at a price known only after the signal candle closes.
- Models configurable fees and slippage.
- Reports total return, max drawdown, trade count, win rate, and a buy-and-hold comparison.
- Optionally saves an equity curve as CSV.

## Windows CMD setup

From the extracted repository folder (the one containing `main.py`):

```cmd
py -m venv .venv
.\.venv\Scripts\activate
py -m pip install -r requirements.txt
```

Run a simulation with the story's $10 starting amount:

```cmd
py main.py --symbol AAPL --cash 10 --short-window 5 --long-window 10 --start-date 2024-01-01 --output results\aapl_equity.csv
```

Or try a longer historical period:

```cmd
py main.py --symbol MSFT --cash 1000 --short-window 10 --long-window 30 --start-date 2023-01-01
```

`--end-date` is optional and is exclusive, matching Yahoo Finance's convention. To stop a command that is still running, press `Ctrl+C`.

## Important limitations

This is a one-shot historical backtest, not an always-on scanner. It does not contain AI agents, debt/survival simulation, real-time streaming, exchange integration, or live order execution. The $10 amount is only simulated capital; fractional shares and costs are approximations, and actual broker minimums may make such trades impractical. Backtest results do not predict future results. Do not rely on trading to solve urgent financial hardship.

## Freqtrade comparison

[Frequtrade](https://github.com/freqtrade/freqtrade) is a mature, crypto-focused framework with exchange adapters, dry-run trading, persistent state, backtesting, strategy tools, and UI/control options. This repo adopts a few safe learning concepts—clear strategy/configuration, backtest reporting, costs, and saved results—but remains a small educational stock-data simulator. It is not a replacement for Freqtrade and does not inherit its testing or operational maturity.
