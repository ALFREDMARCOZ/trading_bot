from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import websockets


EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
FUTURES_DATA_URL = "https://fapi.binance.com/futures/data/"
WS_BASE = "wss://fstream.binance.com/market/stream?streams="
STREAMS_PER_CONNECTION = 100


def get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "paper-futures-scanner/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def discover_usdt_perpetual_symbols() -> list[str]:
    try:
        info = get_json(EXCHANGE_INFO_URL)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not retrieve Binance Futures contract list: {exc}") from exc
    symbols = [
        item["symbol"] for item in info.get("symbols", [])
        if item.get("status") == "TRADING"
        and item.get("contractType") == "PERPETUAL"
        and item.get("quoteAsset") == "USDT"
    ]
    if not symbols:
        raise RuntimeError("Binance returned no trading USDT perpetual contracts")
    return sorted(set(symbols))


def fetch_sentiment_metrics(symbol: str) -> dict[str, float] | None:
    """Fetch public 5m long/short, taker-flow and open-interest context for a contract."""
    query = urllib.parse.urlencode({"symbol": symbol, "period": "5m", "limit": 2})
    try:
        ls = get_json(f"{FUTURES_DATA_URL}globalLongShortAccountRatio?{query}")
        taker = get_json(f"{FUTURES_DATA_URL} takerlongshortRatio?{query}".replace("/ taker", "/taker"))
        oi = get_json(f"{FUTURES_DATA_URL}openInterestHist?{query}")
        if len(ls) < 1 or len(taker) < 1 or len(oi) < 2:
            return None
        oi_old = float(oi[-2].get("sumOpenInterest", 0))
        oi_new = float(oi[-1].get("sumOpenInterest", 0))
        oi_change_pct = ((oi_new / oi_old) - 1) * 100 if oi_old > 0 else 0.0
        return {
            "long_short_ratio": float(ls[-1]["longShortRatio"]),
            "taker_buy_sell_ratio": float(taker[-1]["buySellRatio"]),
            "oi_change_pct": oi_change_pct,
            "updated_at": time.time(),
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError, IndexError):
        return None


@dataclass
class Position:
    side: int
    quantity: float
    entry_price: float
    margin: float
    entry_fee: float
    entry_time: str


class PaperAccount:
    """One-way, 1x paper futures account. Never sends orders to Binance."""

    def __init__(self, cash: float, max_position_fraction: float, fee_rate: float):
        if cash <= 0:
            raise ValueError("Starting cash must be positive")
        if not 0 < max_position_fraction <= 1:
            raise ValueError("Position fraction must be in (0, 1]")
        if fee_rate < 0:
            raise ValueError("Fee rate cannot be negative")
        self.starting_cash = float(cash)
        self.cash = float(cash)
        self.max_position_fraction = max_position_fraction
        self.fee_rate = fee_rate
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.entries = 0
        self.closed_trades = 0
        self.winning_trades = 0

    def max_entry_usdt(self) -> float:
        return self.cash * self.max_position_fraction

    def gross_unrealized_pnl(self, marks: dict[str, float]) -> float:
        return sum(pos.side * (marks.get(sym, pos.entry_price) - pos.entry_price) * pos.quantity
                   for sym, pos in self.positions.items())

    def floating_pnl(self, marks: dict[str, float]) -> float:
        return self.gross_unrealized_pnl(marks) - sum(p.entry_fee for p in self.positions.values())

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + sum(p.margin for p in self.positions.values()) + self.gross_unrealized_pnl(marks)

    def close(self, symbol: str, price: float, timestamp: str, marks: dict[str, float], reason: str) -> None:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return
        gross = pos.side * (price - pos.entry_price) * pos.quantity
        exit_fee = pos.quantity * price * self.fee_rate
        net = gross - pos.entry_fee - exit_fee
        self.cash += pos.margin + gross - exit_fee
        self.realized_pnl += net
        self.fees_paid += exit_fee
        self.closed_trades += 1
        self.winning_trades += int(net > 0)
        side = "LONG" if pos.side == 1 else "SHORT"
        print(f"\n[{timestamp}] EXIT {side} {symbol} ({reason}) at {price:.8g} | trade PnL ${net:+.4f} | realized ${self.realized_pnl:+.4f}", flush=True)

    def open(self, symbol: str, side: int, price: float, timestamp: str, reason: str, marks: dict[str, float]) -> None:
        if symbol in self.positions or self.cash <= 0 or price <= 0:
            return
        margin = min(self.max_entry_usdt(), self.cash / (1 + self.fee_rate))
        if margin <= 0:
            return
        qty = margin / price  # 1x simulated notional; no leverage
        fee = margin * self.fee_rate
        self.cash -= margin + fee
        self.fees_paid += fee
        self.entries += 1
        self.positions[symbol] = Position(side, qty, price, margin, fee, timestamp)
        side_name = "LONG" if side == 1 else "SHORT"
        print(f"\n[{timestamp}] SQUEEZE SETUP -> PAPER {side_name} {symbol} | entry {price:.8g} | notional ${margin:.2f} | fee ${fee:.4f} | {reason}", flush=True)


class SqueezeScanner:
    def __init__(self, symbols: list[str], interval: str, short_window: int, long_window: int,
                 account: PaperAccount, min_move_pct: float, min_volume_ratio: float,
                 max_wick_ratio: float, rsi_period: int):
        if short_window < 1 or long_window <= short_window:
            raise ValueError("Windows must satisfy 1 <= short-window < long-window")
        self.symbols, self.interval = symbols, interval
        self.short_window, self.long_window = short_window, long_window
        self.account = account
        self.min_move_pct = min_move_pct
        self.min_volume_ratio = min_volume_ratio
        self.max_wick_ratio = max_wick_ratio
        self.rsi_period = rsi_period
        self.candles: dict[str, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=max(40, long_window + 2, rsi_period + 2)))
        self.metrics: dict[str, dict[str, float]] = {}
        self.marks: dict[str, float] = {}
        self.completed_candles = 0
        self.last_dashboard = 0.0
        self.last_signal: dict[str, str] = {}

    @staticmethod
    def rsi(closes: list[float], period: int) -> float:
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        if len(changes) < period:
            return 50.0
        gains = sum(max(x, 0) for x in changes[-period:]) / period
        losses = sum(max(-x, 0) for x in changes[-period:]) / period
        if losses == 0:
            return 100.0 if gains > 0 else 50.0
        rs = gains / losses
        return 100 - 100 / (1 + rs)

    def on_mark_price(self, symbol: str, price: float) -> None:
        if price > 0:
            self.marks[symbol] = price

    def on_candle(self, symbol: str, candle: dict[str, Any]) -> None:
        o, h, low, close = (float(candle[k]) for k in ("o", "h", "l", "c"))
        volume = float(candle["v"])
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(candle["T"]) / 1000))
        self.marks[symbol] = close
        self.completed_candles += 1
        rows = self.candles[symbol]
        rows.append({"o": o, "h": h, "l": low, "c": close, "v": volume})
        if len(rows) < max(22, self.rsi_period + 2):
            return

        data = list(rows)
        closes = [x["c"] for x in data]
        # Short-horizon displacement, recent volume expansion, RSI, candle wick rejection.
        lookback = min(5, len(data) - 1)
        move_pct = (close / closes[-lookback - 1] - 1) * 100 if closes[-lookback - 1] else 0.0
        prior_volumes = [x["v"] for x in data[-21:-1]]
        avg_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0.0
        volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0
        rsi = self.rsi(closes, self.rsi_period)
        candle_range = max(h - low, 1e-12)
        upper_wick = (h - max(o, close)) / candle_range
        lower_wick = (min(o, close) - low) / candle_range
        m = self.metrics.get(symbol)
        if not m or time.time() - m["updated_at"] > 900:
            return

        # A squeeze is only a heuristic: sharp move + expanding volume + falling OI,
        # with positioning/taker flow aligned and the candle not strongly rejecting.
        oi_falling = m["oi_change_pct"] <= -0.5
        long_squeeze = (
            move_pct <= -self.min_move_pct and volume_ratio >= self.min_volume_ratio and oi_falling
            and m["long_short_ratio"] >= 1.2 and m["taker_buy_sell_ratio"] <= 0.8
            and 20 <= rsi <= 48 and lower_wick <= self.max_wick_ratio
        )
        short_squeeze = (
            move_pct >= self.min_move_pct and volume_ratio >= self.min_volume_ratio and oi_falling
            and m["long_short_ratio"] <= 0.8 and m["taker_buy_sell_ratio"] >= 1.2
            and 52 <= rsi <= 80 and upper_wick <= self.max_wick_ratio
        )
        # A long squeeze is a downside flush; paper short may participate. A short
        # squeeze is an upside flush; paper long may participate.
        if long_squeeze:
            reason = f"long squeeze/downside flush move={move_pct:.2f}% vol={volume_ratio:.1f}x RSI={rsi:.1f} L/S={m['long_short_ratio']:.2f} taker={m['taker_buy_sell_ratio']:.2f} OI={m['oi_change_pct']:.2f}% wick={lower_wick:.2f}"
            self._handle_signal(symbol, -1, close, timestamp, reason)
        elif short_squeeze:
            reason = f"short squeeze/upside flush move=+{move_pct:.2f}% vol={volume_ratio:.1f}x RSI={rsi:.1f} L/S={m['long_short_ratio']:.2f} taker={m['taker_buy_sell_ratio']:.2f} OI={m['oi_change_pct']:.2f}% wick={upper_wick:.2f}"
            self._handle_signal(symbol, 1, close, timestamp, reason)

    def _handle_signal(self, symbol: str, side: int, price: float, timestamp: str, reason: str) -> None:
        current = self.account.positions.get(symbol)
        if current and current.side == side:
            return
        if current:
            self.account.close(symbol, price, timestamp, self.marks, "opposite squeeze setup")
        self.last_signal[symbol] = "LONG" if side == 1 else "SHORT"
        self.account.open(symbol, side, price, timestamp, reason, self.marks)

    def print_dashboard(self) -> None:
        if time.time() - self.last_dashboard < 5:
            return
        self.last_dashboard = time.time()
        equity = self.account.equity(self.marks)
        floating = self.account.floating_pnl(self.marks)
        print(f"\n[{time.strftime('%H:%M:%S UTC', time.gmtime())}] PAPER SQUEEZE SCANNER | contracts={len(self.symbols)} candles={self.completed_candles} positions={len(self.account.positions)}")
        print(f"Available ${self.account.cash:.4f} | Equity ${equity:.4f} | Total PnL ${equity-self.account.starting_cash:+.4f} | Realized ${self.account.realized_pnl:+.4f} | Floating ${floating:+.4f} | Fees ${self.account.fees_paid:.4f} | Max entry ${self.account.max_entry_usdt():.4f}")
        if not self.account.positions:
            print("Open positions: none; waiting for all squeeze filters to align.", flush=True)
            return
        print("OPEN POSITIONS: SYMBOL | SIDE | ENTRY | LIVE MARK | FLOATING PNL | RSI / L-S / OI")
        for symbol, pos in list(self.account.positions.items())[:30]:
            mark = self.marks.get(symbol, pos.entry_price)
            pnl = pos.side * (mark - pos.entry_price) * pos.quantity - pos.entry_fee
            met = self.metrics.get(symbol, {})
            side = "LONG" if pos.side == 1 else "SHORT"
            print(f"{symbol:16} {side:5} entry={pos.entry_price:.8g} mark={mark:.8g} PnL=${pnl:+.4f} RSI={met.get('rsi', 0):.1f} L/S={met.get('long_short_ratio', 0):.2f} OIΔ={met.get('oi_change_pct', 0):+.2f}%")
        if len(self.account.positions) > 30:
            print(f"... plus {len(self.account.positions)-30} other positions")
        print("Metrics refresh in progress; stale/missing metrics are never used for new entries.", flush=True)

    def report(self) -> None:
        equity = self.account.equity(self.marks)
        print("\nPaper squeeze scanner stopped")
        print(f"Contracts scanned:  {len(self.symbols)}")
        print(f"Starting balance:   ${self.account.starting_cash:.2f} USDT")
        print(f"Available balance:  ${self.account.cash:.4f} USDT")
        print(f"Account equity:     ${equity:.4f} USDT")
        print(f"Total PnL:          ${equity-self.account.starting_cash:+.4f} USDT")
        print(f"Realized PnL:       ${self.account.realized_pnl:+.4f} USDT")
        print(f"Floating PnL:       ${self.account.floating_pnl(self.marks):+.4f} USDT")
        print(f"Entries / closed:   {self.account.entries} / {self.account.closed_trades}")
        print(f"Fees paid:          ${self.account.fees_paid:.4f} USDT")


async def metrics_refresh_loop(scanner: SqueezeScanner, refresh_seconds: int) -> None:
    # Spread public REST calls out to avoid a startup burst across hundreds of contracts.
    min_request_gap = 0.12
    last_request = 0.0
    for symbol in scanner.symbols:
        for endpoint in ("globalLongShortAccountRatio", "takerlongshortRatio", "openInterestHist"):
            await asyncio.sleep(max(0.0, min_request_gap - (time.monotonic() - last_request)))
            query = urllib.parse.urlencode({"symbol": symbol, "period": "5m", "limit": 2})
            url = f"{FUTURES_DATA_URL}{endpoint}?{query}"
            try:
                result = await asyncio.to_thread(get_json, url)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                result = None
            last_request = time.monotonic()
            cache = scanner.metrics.setdefault(symbol, {})
            if endpoint == "globalLongShortAccountRatio" and result:
                try:
                    cache["long_short_ratio"] = float(result[-1]["longShortRatio"])
                except (KeyError, TypeError, ValueError, IndexError):
                    pass
            elif endpoint == "takerlongshortRatio" and result:
                try:
                    cache["taker_buy_sell_ratio"] = float(result[-1]["buySellRatio"])
                except (KeyError, TypeError, ValueError, IndexError):
                    pass
            elif endpoint == "openInterestHist" and result and len(result) >= 2:
                try:
                    old, new = float(result[-2]["sumOpenInterest"]), float(result[-1]["sumOpenInterest"])
                    cache["oi_change_pct"] = ((new / old) - 1) * 100 if old > 0 else 0.0
                except (KeyError, TypeError, ValueError, IndexError):
                    pass
            cache["updated_at"] = time.time()
        # Continue walking the universe; metrics are refreshed on a rolling cadence.
    while True:
        for symbol in scanner.symbols:
            fresh = await asyncio.to_thread(fetch_sentiment_metrics, symbol)
            if fresh:
                scanner.metrics[symbol] = fresh
            await asyncio.sleep(min_request_gap * 3)
        await asyncio.sleep(max(0, refresh_seconds - len(scanner.symbols) * min_request_gap * 3))


async def consume_chunk(scanner: SqueezeScanner, streams: list[str]) -> None:
    url = WS_BASE + quote("/".join(streams), safe="@/!")
    delay = 1
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as socket:
                print(f"Connected stream group ({len(streams)} streams)", flush=True)
                delay = 1
                async for raw in socket:
                    payload = json.loads(raw).get("data", {})
                    symbol = payload.get("s", "")
                    if payload.get("e") == "markPriceUpdate":
                        scanner.on_mark_price(symbol, float(payload.get("p", 0)))
                    else:
                        candle = payload.get("k")
                        if candle and candle.get("x"):
                            scanner.on_candle(symbol, candle)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Stream group disconnected: {exc}; reconnecting in {delay}s", file=sys.stderr, flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def dashboard_loop(scanner: SqueezeScanner) -> None:
    while True:
        scanner.print_dashboard()
        await asyncio.sleep(1)


async def scan_forever(scanner: SqueezeScanner, refresh_seconds: int) -> None:
    stream_names = []
    for symbol in scanner.symbols:
        stream_names.extend((f"{symbol.lower()}@kline_{scanner.interval}", f"{symbol.lower()}@markPrice@1s"))
    chunks = [stream_names[i:i + STREAMS_PER_CONNECTION] for i in range(0, len(stream_names), STREAMS_PER_CONNECTION)]
    print(f"Opening {len(chunks)} WebSocket connections for {len(scanner.symbols)} contracts / {len(stream_names)} streams.", flush=True)
    tasks = [asyncio.create_task(consume_chunk(scanner, chunk)) for chunk in chunks]
    tasks.extend((asyncio.create_task(dashboard_loop(scanner)), asyncio.create_task(metrics_refresh_loop(scanner, refresh_seconds))))
    await asyncio.gather(*tasks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous paper scanner for Binance USDT perpetuals; squeeze-filter demo, no real orders.")
    parser.add_argument("--symbols", default="ALL", help="ALL or comma-separated Binance USDT perpetual symbols")
    parser.add_argument("--interval", default="1m", choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"])
    parser.add_argument("--cash", type=float, default=10.0)
    parser.add_argument("--short-window", type=int, default=5, help="Retained for compatibility; squeeze filters use price action, not MA crossover")
    parser.add_argument("--long-window", type=int, default=20, help="Retained for compatibility")
    parser.add_argument("--max-position-fraction", type=float, default=0.25)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--min-move-pct", type=float, default=1.5, help="Minimum 5-candle pump/dump percentage")
    parser.add_argument("--min-volume-ratio", type=float, default=1.5, help="Current candle volume / previous 20-candle average")
    parser.add_argument("--max-wick-ratio", type=float, default=0.45, help="Reject entries if adverse wick exceeds this share of candle range")
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--metrics-refresh-seconds", type=int, default=300)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_move_pct <= 0 or args.min_volume_ratio <= 0 or not 0 < args.max_wick_ratio <= 1 or args.rsi_period < 2 or args.metrics_refresh_seconds < 60:
        print("Invalid filter values; refresh period must be at least 60 seconds.", file=sys.stderr)
        return 2
    try:
        symbols = discover_usdt_perpetual_symbols() if args.symbols.strip().upper() == "ALL" else sorted(set(x.strip().upper() for x in args.symbols.split(",") if x.strip()))
        if not symbols:
            raise ValueError("No symbols selected")
        account = PaperAccount(args.cash, args.max_position_fraction, args.fee_rate)
        scanner = SqueezeScanner(symbols, args.interval, args.short_window, args.long_window, account,
                                 args.min_move_pct, args.min_volume_ratio, args.max_wick_ratio, args.rsi_period)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print("BINANCE USD-M PAPER SQUEEZE SCANNER")
    print("Checks: global L/S ratio, taker buy/sell, RSI, 5-candle pump/dump, volume surge, wick rejection, and OI flush.")
    print("Squeeze definitions are heuristic; public data only, no API key, no leverage, no real orders.")
    print(f"Discovered {len(symbols)} trading USDT perpetuals. Metrics refresh every {args.metrics_refresh_seconds}s; startup metrics are staggered.")
    print(f"Starting balance ${args.cash:.2f} USDT | max entry ${account.max_entry_usdt():.2f} USDT | press Ctrl+C to stop.", flush=True)
    try:
        asyncio.run(scan_forever(scanner, args.metrics_refresh_seconds))
    except KeyboardInterrupt:
        scanner.report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
