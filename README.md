# Trading Bot Repository

This repository includes two separate paper-only tools:

1. `main.py` — a one-shot historical stock backtest using Yahoo Finance.
2. `futures_paper.py` — a continuous Binance USDⓈ-M Futures public-market-data scanner with simulated paper positions.

## Continuous Binance Futures paper scanner (Windows CMD)

Use a clean Python environment in the extracted repo folder:

```cmd
py -m venv .futures-venv
.futures-venv\Scripts\activate
py -m pip install -r requirements-futures.txt
```

Run a continuous scan of the selected futures symbols:

```cmd
py futures_paper.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --interval 1m --cash 10 --short-window 5 --long-window 20 --max-position-fraction 0.25
```

It connects to Binance USD-M Futures **public WebSocket market data**, processes completed candles, and stays running until you press `Ctrl+C`. It reconnects after a stream interruption. It prints paper entries/exits and, on shutdown, the available balance, account equity, realized/unrealized P&L, and open positions.

With `$10` and `--max-position-fraction 0.25`, the maximum new entry is `$2.50` of available simulated balance, at 1x notional and without leverage. The cap recalculates from available balance before each entry. Fee is an estimate and configurable with `--fee-rate`; actual exchange fees differ.

The scanner only scans the symbols supplied with `--symbols`; it does not automatically subscribe to every futures contract. The SMA crossover is an example signal, not a recommendation. There is no API key, account access, or real order placement. Paper results are not predictions and cannot be relied on for urgent financial needs.

## Historical stock backtest

The existing `main.py` command runs once on Yahoo Finance daily history:

```cmd
py -m pip install -r requirements.txt
py main.py --symbol AAPL --cash 10 --short-window 5 --long-window 10 --start-date 2024-01-01
```

## Freqtrade comparison

Freqtrade is a mature crypto trading framework with exchange integrations, dry-run, backtesting, and operational tooling. This repository is a small educational project: it borrows workflow ideas but is not a Freqtrade replacement and has no live-trading integration.
