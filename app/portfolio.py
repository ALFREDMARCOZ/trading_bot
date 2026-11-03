from __future__ import annotations

from pathlib import Path

import pandas as pd


class PaperPortfolio:
    """Long-only paper backtester with next-candle execution and estimated costs."""

    def __init__(
        self,
        cash: float = 10.0,
        max_position_fraction: float = 0.25,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
    ) -> None:
        if cash <= 0:
            raise ValueError("Starting cash must be positive")
        if not 0 < max_position_fraction <= 1:
            raise ValueError("max_position_fraction must be greater than 0 and at most 1")
        if fee_rate < 0 or slippage_rate < 0:
            raise ValueError("Fee and slippage rates cannot be negative")
        self.initial_cash = float(cash)
        self.cash = float(cash)
        self.shares = 0.0
        self.max_position_fraction = max_position_fraction
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.orders = 0
        self.fees_paid = 0.0
        self.trade_log: list[dict] = []
        self.equity_curve: list[dict] = []
        self._entry_cost = 0.0
        self._closed_trade_returns: list[float] = []

    def _order_price(self, price: float, side: str) -> float:
        factor = 1 + self.slippage_rate if side == "buy" else 1 - self.slippage_rate
        return price * factor

    def _buy(self, price: float, date: object) -> None:
        allocation = self.cash * self.max_position_fraction
        execution_price = self._order_price(price, "buy")
        quantity = allocation / (execution_price * (1 + self.fee_rate))
        notional = quantity * execution_price
        fee = notional * self.fee_rate
        if quantity <= 0 or notional + fee > self.cash:
            return
        self.cash -= notional + fee
        self.shares += quantity
        self.orders += 1
        self.fees_paid += fee
        self._entry_cost = notional + fee
        self.trade_log.append({"date": str(date), "side": "BUY", "price": execution_price, "shares": quantity, "fee": fee})

    def _sell(self, price: float, date: object) -> None:
        if self.shares <= 0:
            return
        execution_price = self._order_price(price, "sell")
        notional = self.shares * execution_price
        fee = notional * self.fee_rate
        proceeds = notional - fee
        self.cash += proceeds
        trade_return = (proceeds - self._entry_cost) / self._entry_cost if self._entry_cost else 0.0
        self._closed_trade_returns.append(trade_return)
        self.orders += 1
        self.fees_paid += fee
        self.trade_log.append({"date": str(date), "side": "SELL", "price": execution_price, "shares": self.shares, "fee": fee, "return_pct": trade_return * 100})
        self.shares = 0.0
        self._entry_cost = 0.0

    def simulate(self, signals: pd.DataFrame) -> dict:
        if signals.empty:
            raise ValueError("Cannot backtest an empty price history")
        if "Close" not in signals or "signal" not in signals:
            raise ValueError("Input data must contain Close and signal columns")
        if signals["Close"].isna().any():
            raise ValueError("Price history contains missing Close values")

        frame = signals.reset_index(drop=True).copy()
        open_column = "Open" if "Open" in frame else "Close"
        date_column = "Date" if "Date" in frame else None
        self.equity_curve = []

        for i, row in frame.iterrows():
            date = row[date_column] if date_column else i
            # A signal is known after candle i closes; execute it at candle i+1 open.
            if i > 0:
                previous_signal = int(frame.iloc[i - 1]["signal"])
                execution_price = float(row[open_column])
                if previous_signal == 1 and self.shares == 0:
                    self._buy(execution_price, date)
                elif previous_signal == -1 and self.shares > 0:
                    self._sell(execution_price, date)

            close_price = float(row["Close"])
            equity = self.cash + self.shares * close_price
            self.equity_curve.append({"date": str(date), "cash": self.cash, "shares": self.shares, "close": close_price, "equity": equity})

        final_price = float(frame["Close"].iloc[-1])
        final_value = self.cash + self.shares * final_price
        equity_values = pd.Series([point["equity"] for point in self.equity_curve], dtype=float)
        running_peak = equity_values.cummax()
        drawdowns = (equity_values / running_peak - 1.0) * 100
        first_price = float(frame["Close"].iloc[0])
        buy_hold = self.initial_cash * final_price / first_price
        wins = sum(value > 0 for value in self._closed_trade_returns)
        closed_count = len(self._closed_trade_returns)

        return {
            "initial_cash": self.initial_cash,
            "final_cash": self.cash,
            "final_shares": self.shares,
            "portfolio_value": final_value,
            "buy_and_hold_value": buy_hold,
            "total_return_pct": (final_value / self.initial_cash - 1) * 100,
            "max_drawdown_pct": abs(float(drawdowns.min())) if not drawdowns.empty else 0.0,
            "orders": self.orders,
            "closed_trades": closed_count,
            "win_rate_pct": (wins / closed_count * 100) if closed_count else 0.0,
            "fees_paid": self.fees_paid,
            "trade_log": self.trade_log,
            "equity_curve": self.equity_curve,
        }

    def save_equity_curve(self, path: str) -> None:
        if not self.equity_curve:
            raise RuntimeError("Run simulate() before saving the equity curve")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.equity_curve).to_csv(destination, index=False)


__all__ = ["PaperPortfolio"]
