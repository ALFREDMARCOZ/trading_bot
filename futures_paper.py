from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import websockets


EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
WS_BASE = "wss://fstream.binance.com/market/stream?streams="
STREAMS_PER_CONNECTION = 100


def discover_usdt_perpetual_symbols() -> list[str]:
    """Fetch all currently trading USDT-quoted perpetual contracts from Binance."""
    request = urllib.request.Request(EXCHANGE_INFO_URL, headers={"User-Agent": "paper-futures-scanner/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            info = json.load(response)
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
        return sum(
            pos.side * (marks.get(symbol, pos.entry_price) - pos.entry_price) * pos.quantity
            for symbol, pos in self.positions.items()
        )

    def floating_pnl(self, marks: dict[str, float]) -> float:
        # Includes entry fee already paid; excludes a hypothetical exit fee.
        return self.gross_unrealized_pnl(marks) - sum(p.entry_fee for p in self.positions.values())

    def equity(self, marks: dict[str, float]) -> float:
        reserved_margin = sum(pos.margin for pos in self.positions.values())
        return self.cash + reserved_margin + self.gross_unrealized_pnl(marks)

    def close(self, symbol: str, price: float, timestamp: str, marks: dict[str, float]) -> None:
        position = self.positions.pop(symbol, None)
        if position is None:
            return
        gross = position.side * (price - position.entry_price) * position.quantity
        exit_fee = position.quantity * price * self.fee_rate
        net = gross - position.entry_fee - exit_fee
        self.cash += position.margin + gross - exit_fee
        self.realized_pnl += net
        self.fees_paid += exit_fee
        self.closed_trades += 1
        self.winning_trades += int(net > 0)
        action = "CLOSE_LONG" if position.side == 1 else "CLOSE_SHORT"
        print(
            f"[{timestamp}] {symbol} {action} price={price:.8g} qty={position.quantity:.8g} "
            f"tradePnL=${net:+.4f} available=${self.cash:.2f} "
            f"equity=${self.equity(marks):.2f} realizedPnL=${self.realized_pnl:+.4f} "
            f"floatingPnL=${self.floating_pnl(marks):+.4f} maxNextEntry=${self.max_entry_usdt():.2f}",
            flush=True,
        )

    def open(self, symbol: str, side: int, price: float, timestamp: str, marks: dict[str, float]) -> None:
        if symbol in self.positions or self.cash <= 0 or price <= 0:
            return
        margin = min(self.max_entry_usdt(), self.cash / (1 + self.fee_rate))
        if margin <= 0:
            return
        quantity = margin / price  # 1x simulated notional; no leverage
        fee = margin * self.fee_rate
        self.cash -= margin + fee
        self.fees_paid += fee
        self.entries += 1
        self.positions[symbol] = Position(side, quantity, price, margin, fee, timestamp)
        action = "OPEN_LONG" if side == 1 else "OPEN_SHORT"
        print(
            f"[{timestamp}] {symbol} {action} price={price:.8g} qty={quantity:.8g} "
            f"entryNotional=${margin:.2f} fee=${fee:.4f} available=${self.cash:.2f} "
            f"equity=${self.equity(marks):.2f} realizedPnL=${self.realized_pnl:+.4f} "
            f"floatingPnL=${self.floating_pnl(marks):+.4f} maxNextEntry=${self.max_entry_usdt():.2f}",
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
        self.marks: dict[str, float] = {}
        self.completed_candles = 0
        self.last_dashboard = 0.0

    def on_mark_price(self, symbol: str, price: float) -> None:
        if price > 0:
            self.marks[symbol] = price

    def on_candle(self, symbol: str, candle: dict[str, Any]) -> None:
        close = float(candle["c"])
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(candle["T"]) / 1000))
        self.marks[symbol] = close
        self.completed_candles += 1
        values = self.closes[symbol]
        values.append(close)
        if len(values) < self.long_window + 1:
            return

        closes = list(values)
        short_now = sum(closes[-self.short_window:]) / self.short_window
        long_now = sum(closes[-self.long_window:]) / self.long_window
        previous = closes[:-1]
        short_before = sum(previous[-self.short_window:]) / self.short_window
        long_before = sum(previous[-self.long_window:]) / self.long_window
        state = 1 if short_now > long_now else (-1 if short_now < long_now else 0)
        old_state = 1 if short_before > long_before else (-1 if short_before < long_before else 0)

        if old_state <= 0 < state:
            if symbol in self.account.positions:
                self.account.close(symbol, close, timestamp, self.marks)
            self.account.open(symbol, 1, close, timestamp, self.marks)
        elif old_state >= 0 > state:
            if symbol in self.account.positions:
                self.account.close(symbol, close, timestamp, self.marks)
            self.account.open(symbol, -1, close, timestamp, self.marks)

    def print_dashboard(self) -> None:
        now = time.time()
        if now - self.last_dashboard < 5:
            return
        self.last_dashboard = now
        equity = self.account.equity(self.marks)
        floating = self.account.floating_pnl(self.marks)
        total_pnl = equity - self.account.starting_cash
        timestamp = time.strftime("%H:%M:%S UTC", time.gmtime())
        print(
            f"\n[{timestamp}] LIVE PAPER ACCOUNT | scanned={len(self.symbols)} "
            f"candles={self.completed_candles} positions={len(self.account.positions)}\n"
            f"Balance available: ${self.account.cash:.4f} USDT | Equity: ${equity:.4f} USDT | "
            f"Total PnL: ${total_pnl:+.4f} USDT\n"
            f"Realized PnL: ${self.account.realized_pnl:+.4f} USDT | "
            f"Floating PnL (entry fee included): ${floating:+.4f} USDT | "
            f"Fees paid: ${self.account.fees_paid:.4f} | Max next entry: ${self.account.max_entry_usdt():.4f} USDT",
            flush=True,
        )
        if not self.account.positions:
            print("Open positions: none yet (waiting for crossover signals)", flush=True)
            return
        ranked = sorted(
            self.account.positions.items(),
            key=lambda item: abs(item[1].side * (self.marks.get(item[0], item[1].entry_price) - item[1].entry_price) * item[1].quantity),
            reverse=True,
        )
        print("OPEN POSITIONS: SYMBOL | SIDE | ENTRY | LIVE MARK | FLOATING PNL", flush=True)
        for symbol, position in ranked[:25]:
            mark = self.marks.get(symbol, position.entry_price)
            pnl = position.side * (mark - position.entry_price) * position.quantity - position.entry_fee
            side = "LONG" if position.side == 1 else "SHORT"
            print(f"  {symbol:16} {side:5} entry={position.entry_price:.8g} mark={mark:.8g} floating=${pnl:+.4f}", flush=True)
        if len(ranked) > 25:
            print(f"  ... and {len(ranked) - 25} more open positions", flush=True)

    def report(self) -> None:
        self.print_dashboard()
        equity = self.account.equity(self.marks)
        print("\nPaper scanner stopped")
        print(f"Starting balance:  ${self.account.starting_cash:.2f} USDT")
        print(f"Available balance: ${self.account.cash:.2f} USDT")
        print(f"Account equity:    ${equity:.4f} USDT")
        print(f"Total PnL:         ${equity - self.account.starting_cash:+.4f} USDT")
        print(f"Realized PnL:      ${self.account.realized_pnl:+.4f} USDT")
        print(f"Floating PnL:      ${self.account.floating_pnl(self.marks):+.4f} USDT")
        print(f"Entries / closed:  {self.account.entries} / {self.account.closed_trades}")
        print(f"Fees paid:         ${self.account.fees_paid:.4f} USDT")


async def consume_chunk(scanner: CrossoverScanner, streams: list[str]) -> None:
    url = WS_BASE + quote("/".join(streams), safe="@/!")
    delay = 1
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as socket:
                print(f"Connected stream group ({len(streams)} streams)", flush=True)
                delay = 1
                async for raw in socket:
                    message = json.loads(raw)
                    payload = message.get("data", message)
                    event = payload.get("e")
                    if event == "markPriceUpdate":
                        scanner.on_mark_price(payload.get("s", ""), float(payload.get("p", 0)))
                    else:
                        candle = payload.get("k")
                        if candle and candle.get("x"):
                            scanner.on_candle(payload.get("s", candle.get("s", "")), candle)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Stream group disconnected: {exc}; reconnecting in {delay}s", file=sys.stderr, flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def dashboard_loop(scanner: CrossoverScanner) -> None:
    while True:
        scanner.print_dashboard()
        await asyncio.sleep(1)


async def scan_forever(scanner: CrossoverScanner) -> None:
    stream_names = []
    for symbol in scanner.symbols:
        lower = symbol.lower()
        stream_names.append(f"{lower}@kline_{scanner.interval}")
        stream_names.append(f"{lower}@markPrice@1s")
    chunks = [stream_names[i:i + STREAMS_PER_CONNECTION] for i in range(0, len(stream_names), STREAMS_PER_CONNECTION)]
    print(f"Opening {len(chunks)} WebSocket connections for {len(scanner.symbols)} symbols / {len(stream_names)} streams.", flush=True)
    jobs = [asyncio.create_task(consume_chunk(scanner, chunk)) for chunk in chunks]
    jobs.append(asyncio.create_task(dashboard_loop(scanner)))
    await asyncio.gather(*jobs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous Binance USD-M Futures paper scanner; public data only, no real orders.")
    parser.add_argument("--symbols", default="ALL", help="ALL or comma-separated Binance USDT perpetual symbols")
    parser.add_argument("--interval", default="1m", choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"], help="Signal candle interval")
    parser.add_argument("--cash", type=float, default=10.0, help="Starting simulated USDT")
    parser.add_argument("--short-window", type=int, default=5, help="Short SMA candles")
    parser.add_argument("--long-window", type=int, default=20, help="Long SMA candles")
    parser.add_argument("--max-position-fraction", type=float, default=0.25, help="Max entry as fraction of available balance")
    parser.add_argument("--fee-rate", type=float, default=0.0004, help="Estimated fee per side")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.symbols.strip().upper() == "ALL":
            symbols = discover_usdt_perpetual_symbols()
        else:
            symbols = sorted(set(s.strip().upper() for s in args.symbols.split(",") if s.strip()))
        if not symbols:
            raise ValueError("No symbols selected")
        account = PaperAccount(args.cash, args.max_position_fraction, args.fee_rate)
        scanner = CrossoverScanner(symbols, args.interval, args.short_window, args.long_window, account)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print("CONTINUOUS BINANCE USD-M FUTURES PAPER SCANNER — LIVE DASHBOARD")
    print("Public market data only. No API keys, no exchange orders, no leverage.")
    print(f"Discovered {len(symbols)} currently trading USDT perpetual contracts.")
    print(f"Interval: {args.interval} | SMA: {args.short_window}/{args.long_window}")
    print(f"Starting balance: ${args.cash:.2f} USDT | max entry now: ${account.max_entry_usdt():.2f} USDT")
    print("Dashboard refreshes every 5 seconds. Signals need warm-up; press Ctrl+C to stop.", flush=True)
    try:
        asyncio.run(scan_forever(scanner))
    except KeyboardInterrupt:
        scanner.report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
