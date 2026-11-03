from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import websockets


WS_BASE = "wss://fstream.binance.com/stream?streams="


@dataclass
class Position:
    side: int  # +1 long, -1 short
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
        self.cash = float(cash)  # available USDT collateral
        self.max_position_fraction = max_position_fraction
        self.fee_rate = fee_rate
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.entries = 0
        self.closed_trades = 0
        self.winning_trades = 0
        self.trade_log: list[dict[str, Any]] = []

    def max_entry_usdt(self) -> float:
        return self.cash * self.max_position_fraction

    def unrealized_pnl(self, marks: dict[str, float]) -> float:
        total = 0.0
        for symbol, position in self.positions.items():
            mark = marks.get(symbol, position.entry_price)
            total += position.side * (mark - position.entry_price) * position.quantity
        return total

    def equity(self, marks: dict[str, float]) -> float:
        reserved_margin = sum(p.margin for p in self.positions.values())
        return self.cash + reserved_margin + self.unrealized_pnl(marks)

    def close(self, symbol: str, price: float, timestamp: str) -> None:
        position = self.positions.pop(symbol, None)
        if position is None:
            return
        gross_pnl = position.side * (price - position.entry_price) * position.quantity
        exit_fee = position.quantity * price * self.fee_rate
        net_pnl = gross_pnl - position.entry_fee - exit_fee
        self.cash += position.margin + gross_pnl - exit_fee
        self.realized_pnl += net_pnl
        self.fees_paid += exit_fee
        self.closed_trades += 1
        if net_pnl > 0:
            self.winning_trades += 1
        self.trade_log.append({
            "time": timestamp,
            "symbol": symbol,
            "action": "CLOSE_LONG" if position.side == 1 else "CLOSE_SHORT",
            "price": price,
            "quantity": position.quantity,
            "pnl": net_pnl,
            "fee": exit_fee,
        })
        self._print_status(symbol, timestamp, price, "closed")

    def open(self, symbol: str, side: int, price: float, timestamp: str) -> None:
        if symbol in self.positions or self.cash <= 0 or price <= 0:
            return
        margin = self.max_entry_usdt()
        if margin <= 0:
            return
        quantity = margin / price  # 1x notional; no leverage
        entry_fee = margin * self.fee_rate
        if margin + entry_fee > self.cash:
            margin = self.cash / (1 + self.fee_rate)
            quantity = margin / price
            entry_fee = margin * self.fee_rate
        self.cash -= margin + entry_fee
        self.fees_paid += entry_fee
        self.entries += 1
        self.positions[symbol] = Position(side, quantity, price, margin, entry_fee, timestamp)
        action = "OPEN_LONG" if side == 1 else "OPEN_SHORT"
        self.trade_log.append({
            "time": timestamp,
            "symbol": symbol,
            "action": action,
            "price": price,
            "quantity": quantity,
            "notional": margin,
            "fee": entry_fee,
        })
        self._print_status(symbol, timestamp, price, action.lower())

    def _print_status(self, symbol: str, timestamp: str, price: float, event: str) -> None:
        marks = {symbol: price}
        equity = self.equity(marks)
        print(
            f"[{timestamp}] {symbol} {event} | price={price:.8g} | "
            f"available=${self.cash:.2f} | equity=${equity:.2f} | "
            f"realized PnL=${self.realized_pnl:+.2f} | "
            f"unrealized PnL=${self.unrealized_pnl(marks):+.2f} | "
            f"max next entry=${self.max_entry_usdt():.2f}",
            flush=True,
        )


class CrossoverScanner:
    def __init__(self, symbols: list[str], interval: str, short_window: int, long_window: int, account: PaperAccount):
        if short_window < 1 or long_window <= short_window:
            raise ValueError("Windows must satisfy 1 <= short-window < long-window")
        self.symbols = symbols
        self.interval = interval
        self.short_window = short_window
        self.long_window = long_window
        self.account = account
        self.closes: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=long_window + 1))
        self.previous_state: dict[str, int] = {}
        self.marks: dict[str, float] = {}

    def on_candle(self, symbol: str, candle: dict[str, Any]) -> None:
        close = float(candle["c"])
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(candle["T"]) / 1000))
        self.marks[symbol] = close
        values = self.closes[symbol]
        values.append(close)
        if len(values) < self.long_window:
            print(f"[{timestamp}] {symbol} warming up ({len(values)}/{self.long_window} candles)", flush=True)
            return

        closes = list(values)
        short_now = sum(closes[-self.short_window:]) / self.short_window
        long_now = sum(closes[-self.long_window:]) / self.long_window
        prior = closes[:-1]
        if len(prior) < self.long_window:
            return
        short_before = sum(prior[-self.short_window:]) / self.short_window
        long_before = sum(prior[-self.long_window:]) / self.long_window
        state = 1 if short_now > long_now else (-1 if short_now < long_now else 0)
        prior_state = 1 if short_before > long_before else (-1 if short_before < long_before else 0)
        self.previous_state[symbol] = state

        if prior_state <= 0 < state:
            if symbol in self.account.positions:
                self.account.close(symbol, close, timestamp)
            self.account.open(symbol, 1, close, timestamp)
        elif prior_state >= 0 > state:
            if symbol in self.account.positions:
                self.account.close(symbol, close, timestamp)
            self.account.open(symbol, -1, close, timestamp)

    def report(self) -> None:
        equity = self.account.equity(self.marks)
        unrealized = self.account.unrealized_pnl(self.marks)
        print("\nPaper Futures Scanner stopped")
        print(f"Starting balance: ${self.account.starting_cash:.2f} USDT")
        print(f"Available balance: ${self.account.cash:.2f} USDT")
        print(f"Account equity:    ${equity:.2f} USDT")
        print(f"Realized PnL:      ${self.account.realized_pnl:+.2f} USDT")
        print(f"Unrealized PnL:    ${unrealized:+.2f} USDT")
        print(f"Entries / closed:  {self.account.entries} / {self.account.closed_trades}")
        print(f"Fees paid:         ${self.account.fees_paid:.4f} USDT")
        print(f"Max next entry:    ${self.account.max_entry_usdt():.2f} USDT (1x simulated, no leverage)")
        for symbol, position in self.account.positions.items():
            side = "LONG" if position.side == 1 else "SHORT"
            mark = self.marks.get(symbol, position.entry_price)
            pnl = position.side * (mark - position.entry_price) * position.quantity
            print(f"Open: {symbol} {side}, qty={position.quantity:.8g}, entry={position.entry_price:.8g}, mark={mark:.8g}, unrealized=${pnl:+.2f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous Binance USD-M Futures public-data paper scanner; no real orders.")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT", help="Comma-separated Binance USDT-margined futures symbols")
    parser.add_argument("--interval", default="1m", choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"], help="Kline interval")
    parser.add_argument("--cash", type=float, default=10.0, help="Starting simulated USDT")
    parser.add_argument("--short-window", type=int, default=5, help="Short SMA candles")
    parser.add_argument("--long-window", type=int, default=20, help="Long SMA candles")
    parser.add_argument("--max-position-fraction", type=float, default=0.25, help="Max entry as fraction of available balance")
    parser.add_argument("--fee-rate", type=float, default=0.0004, help="Estimated fee fraction per side; verify your own fee tier")
    return parser.parse_args()


async def scan_forever(scanner: CrossoverScanner) -> None:
    streams = "/".join(f"{symbol.lower()}@kline_{scanner.interval}" for symbol in scanner.symbols)
    url = WS_BASE + quote(streams, safe="@/")
    delay = 1
    while True:
        try:
            print(f"Connecting to Binance USD-M Futures public stream for {', '.join(scanner.symbols)} ...", flush=True)
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as socket:
                print("Connected. Paper mode only; press Ctrl+C to stop.", flush=True)
                delay = 1
                async for raw in socket:
                    message = json.loads(raw)
                    payload = message.get("data", message)
                    candle = payload.get("k")
                    if not candle or not candle.get("x"):
                        continue  # process completed candles only
                    scanner.on_candle(payload.get("s", candle.get("s", "")), candle)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Stream disconnected: {exc}. Reconnecting in {delay}s...", file=sys.stderr, flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def main() -> int:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols or len(symbols) > 50:
        print("Provide between 1 and 50 symbols.", file=sys.stderr)
        return 2
    if any(not symbol.isalnum() for symbol in symbols):
        print("Symbols must contain only letters and numbers, e.g. BTCUSDT.", file=sys.stderr)
        return 2
    try:
        account = PaperAccount(args.cash, args.max_position_fraction, args.fee_rate)
        scanner = CrossoverScanner(symbols, args.interval, args.short_window, args.long_window, account)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print("CONTINUOUS BINANCE USD-M FUTURES PAPER SCANNER")
    print("Public market data only. No API keys, no exchange orders, no leverage.")
    print(f"Symbols: {', '.join(symbols)} | interval: {args.interval} | SMA: {args.short_window}/{args.long_window}")
    print(f"Starting balance: ${args.cash:.2f} USDT | max entry now: ${account.max_entry_usdt():.2f} USDT")
    try:
        asyncio.run(scan_forever(scanner))
    except KeyboardInterrupt:
        scanner.report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
