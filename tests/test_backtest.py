from __future__ import annotations

import unittest

import pandas as pd

from app.portfolio import PaperPortfolio
from app.strategy import MovingAverageStrategy


class StrategyTests(unittest.TestCase):
    def test_strategy_marks_crossover(self):
        data = pd.DataFrame({"Close": [3, 2, 1, 2, 4, 5, 3, 1]})
        signals = MovingAverageStrategy(short_window=2, long_window=3).generate_signals(data)
        self.assertIn(1, signals["signal"].tolist())
        self.assertIn(-1, signals["signal"].tolist())

    def test_rejects_invalid_windows(self):
        with self.assertRaises(ValueError):
            MovingAverageStrategy(5, 5)


class PortfolioTests(unittest.TestCase):
    def test_simulation_has_metrics_and_respects_cash(self):
        data = pd.DataFrame({
            "Date": pd.date_range("2024-01-01", periods=5),
            "Open": [10, 10, 11, 12, 11],
            "Close": [10, 10, 11, 12, 11],
            "signal": [1, 0, 0, -1, 0],
        })
        portfolio = PaperPortfolio(cash=10, max_position_fraction=0.5, fee_rate=0.001, slippage_rate=0.001)
        result = portfolio.simulate(data)
        self.assertEqual(result["orders"], 2)
        self.assertGreaterEqual(result["final_cash"], 0)
        self.assertIn("max_drawdown_pct", result)
        self.assertIn("buy_and_hold_value", result)

    def test_rejects_empty_data(self):
        with self.assertRaises(ValueError):
            PaperPortfolio().simulate(pd.DataFrame())


if __name__ == "__main__":
    unittest.main()
