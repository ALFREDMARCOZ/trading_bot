# Trading Bot Repository

This repository includes two separate paper-only tools:

1. `main.py` — a one-shot historical stock backtest using Yahoo Finance.
2. `futures_paper.py` — a continuous Binance USDⓈ-M Futures public-market-data scanner with simulated paper positions.

## Scan every Binance USDT perpetual contract

In Windows CMD, from the extracted repository folder:

```cmd
py -m venv .futures-venv
.futures-venv\Scripts\activate
py -m pip install -r requirements-futures.txt
```

Run the scanner (the default `--symbols ALL` discovers all currently trading USDT-quoted perpetual contracts from Binance exchange information):

```cmd
py futures_paper.py --symbols ALL --interval 1m --cash 10 --short-window 5 --long-window 20 --max-position-fraction 0.25
```

The scanner divides symbols into WebSocket groups of at most 100 streams and scans completed candles continuously. It reconnects disconnected stream groups. The symbol list is discovered at startup; restart the program to pick up newly listed or delisted contracts. After startup, each symbol needs enough completed candles to warm up its moving averages before it can generate signals. Press `Ctrl+C` to stop and print the final simulated balance and P&L.

With `$10` and `--max-position-fraction 0.25`, the first maximum simulated entry is `$2.50` of available balance. Each symbol can have one simulated position, but all positions share the same simulated collateral, so the entry cap shrinks as collateral is allocated. Simulation is 1x notional with no leverage and approximate fees. It reads public market data only: no API keys, account access, or real order placement.

This scanner currently supports active USDT-margined perpetual contracts; it does not include COIN-M futures. SMA crossovers are demonstration signals, not investment recommendations. Paper results are not predictions.

## Historical stock backtest

The existing `main.py` command runs once on Yahoo Finance daily history:

```cmd
py -m pip install -r requirements.txt
py main.py --symbol AAPL --cash 10 --short-window 5 --long-window 10 --start-date 2024-01-01
```

## Freqtrade comparison

Freqtrade is a mature crypto trading framework with exchange integrations, dry-run, backtesting, and operational tooling. This repository is a small educational project: it borrows workflow ideas but is not a Freqtrade replacement and has no live-trading integration.
