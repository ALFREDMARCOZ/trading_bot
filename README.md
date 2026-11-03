# Trading Bot Repository

This repository includes a historical Yahoo Finance backtest (`main.py`) and a continuous, paper-only Binance USD-M Futures scanner (`futures_paper.py`). The scanner discovers active USDT perpetuals and checks public market data for squeeze-like setups.

## Continuous paper squeeze scanner

Windows CMD, from the extracted repository folder:

```cmd
py -m venv .futures-venv
.futures-venv\Scripts\activate
py -m pip install -r requirements-futures.txt
```

Run against all active USDT-margined perpetual contracts:

```cmd
py futures_paper.py --symbols ALL --interval 1m --cash 10 --max-position-fraction 0.25
```

It checks completed candles for RSI, short-term pump/dump move, relative volume, candle wick rejection, and combines those with public 5-minute global long/short account ratio, taker buy/sell ratio, and open-interest change. A downside flush with crowded longs and declining OI can produce a paper SHORT setup; an upside squeeze with crowded shorts and declining OI can produce a paper LONG setup. Metrics refresh on a staggered schedule; missing or stale context is not used for new entries. Press `Ctrl+C` to stop.

These squeeze labels and filters are heuristics, not proof of liquidations, wick hunts, or a squeeze, and they are not investment recommendations. The defaults are configurable with `--min-move-pct`, `--min-volume-ratio`, `--max-wick-ratio`, `--rsi-period`, and `--metrics-refresh-seconds`. The program uses public data only, no API keys, no leverage, and no exchange orders. It has no exchange-side stop loss/take profit. Paper results may differ from actual fills.

The futures list is discovered at startup; restart to refresh new listings. This scans USDT perpetuals only, not COIN-M futures.

## Historical stock backtest

```cmd
py -m pip install -r requirements.txt
py main.py --symbol AAPL --cash 10 --short-window 5 --long-window 10 --start-date 2024-01-01
```
