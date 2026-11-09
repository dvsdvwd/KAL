import unittest
from datetime import datetime, timezone

from dashboard import analyze_price_action, calculate_session_vwap
from indicators import bollinger_bands, rsi, score_signal, stochastic
from kalshi_perps import (
    PaperAccount,
    calculate_all_indicators,
    format_probability,
    get_direction,
    market_yes_quotes,
)


class DirectionTests(unittest.TestCase):
    def test_uptrend_signal(self):
        closes = [100 + index for index in range(30)]
        self.assertEqual(get_direction(closes)[0], "UP")

    def test_downtrend_signal(self):
        closes = [130 - index for index in range(30)]
        self.assertEqual(get_direction(closes)[0], "DOWN")

    def test_kalshi_dollar_quotes_format_as_percentages(self):
        bid, ask = market_yes_quotes({"yes_bid_dollars": "0.4200", "yes_ask_dollars": "0.4700"})
        self.assertEqual((format_probability(bid), format_probability(ask)), ("42%", "47%"))

    def test_price_action_flags_range_and_volume_expansion(self):
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 100}
            for _ in range(20)
        ]
        candles.append({"open": 100, "high": 110, "low": 100, "close": 109, "volume": 300})
        result = analyze_price_action(candles)
        self.assertEqual(result["state"], "RANGE + VOLUME EXPANSION")
        self.assertEqual(result["direction"], "UP")

    def test_session_vwap_uses_typical_price_weighted_by_volume(self):
        session_start = 1794182400000
        candles = [
            {"time": session_start, "high": 12, "low": 9, "close": 9, "volume": 1},
            {"time": session_start + 900000, "high": 24, "low": 21, "close": 21, "volume": 3},
        ]
        self.assertAlmostEqual(calculate_session_vwap(candles), 19)


class IndicatorTests(unittest.TestCase):
    def test_rsi_neutral_at_50_with_no_movement(self):
        closes = [100] * 20
        self.assertAlmostEqual(rsi(closes), 50.0)

    def test_rsi_100_with_strong_uptrend(self):
        closes = [100 + i for i in range(20)]
        self.assertAlmostEqual(rsi(closes), 100.0)

    def test_bollinger_bands_return_upper_middle_lower(self):
        closes = [100 + i * 0.1 for i in range(20)]
        bb = bollinger_bands(closes)
        self.assertIsNotNone(bb)
        self.assertGreater(bb["upper"], bb["middle"])
        self.assertLess(bb["lower"], bb["middle"])

    def test_stochastic_oversold_at_zero(self):
        candles = [{"high": 100, "low": 90, "close": 90, "volume": 1, "open": 95}] * 14
        self.assertAlmostEqual(stochastic(candles), 0.0)

    def test_stochastic_overbought_at_100(self):
        candles = [{"high": 100, "low": 90, "close": 100, "volume": 1, "open": 95}] * 14
        self.assertAlmostEqual(stochastic(candles), 100.0)

    def test_score_signal_returns_flat_with_no_data(self):
        direction, score, confidence, detail = score_signal({})
        self.assertEqual(direction, "FLAT")
        self.assertEqual(score, 0)

    def test_score_signal_bullish_with_multiple_bullish_indicators(self):
        indicators = {
            "price": 95,
            "emaSignal": "UP",
            "rsi14": 25,
            "macd": {"line": 1, "signal": 0, "histogram": 1},
            "bollinger": {"upper": 105, "middle": 100, "lower": 95, "bandwidth": 0.1},
            "stochastic": 15,
            "momentum": 5,
            "roc": 3,
            "vwap": 90,
            "adx": 30,
        }
        direction, score, confidence, detail = score_signal(indicators)
        self.assertEqual(direction, "UP")
        self.assertGreater(score, 50)

    def test_calculate_all_indicators_returns_composite(self):
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100 + i * 0.5, "volume": 100, "time": 1700000000000 + i * 60000}
            for i in range(30)
        ]
        closes = [float(c["close"]) for c in candles]
        indicators, composite = calculate_all_indicators(candles, closes)
        self.assertIn("compositeScore", indicators)
        self.assertIn("compositeConfidence", indicators)
        self.assertIn("signalDetail", indicators)
        self.assertEqual(composite, indicators["compositeDirection"])


class PaperAccountTests(unittest.TestCase):
    def test_position_auto_sized_with_10_percent_margin(self):
        account = PaperAccount()
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.assertEqual(account.update("UP", 100, now), "opened paper long")
        # 10% margin x 1x leverage = $1000 notional at $100 = 10 contracts
        self.assertAlmostEqual(account.position.quantity * 100, 1000)

    def test_leverage_scales_notional(self):
        account = PaperAccount(leverage=5)
        account.update("UP", 100)
        # 10% margin x 5x leverage = $5,000 notional
        self.assertAlmostEqual(account.position.quantity * 100, 5000)
        self.assertEqual(account.position.leverage, 5)

    def test_max_leverage_cap(self):
        account = PaperAccount(leverage=50, max_leverage=20)
        self.assertEqual(account.leverage, 20)

    def test_positions_below_one_dollar_are_not_opened(self):
        account = PaperAccount(starting_cash=5)
        # 10% of $5 = $0.50 margin, below $1 minimum
        self.assertEqual(account.update("UP", 100), "paper order below $1 minimum")
        self.assertIsNone(account.position)

    def test_manual_notional_is_capped_by_equity_margin_and_leverage(self):
        account = PaperAccount(leverage=2, auto_size=False, order_notional=5000)
        account.update("UP", 100)
        # 10% x $10,000 x 2x = $2,000 cap; requested $5,000 -> capped
        self.assertAlmostEqual(account.position.quantity * 100, 2000)

    def test_daily_loss_halts_trading(self):
        account = PaperAccount(daily_loss_limit=0.05)
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        account.update("UP", 100, now)
        # 5% daily loss on $10,000 = equity drops below $9,500
        account.update("UP", 50, now)
        self.assertTrue(account.halted)
        self.assertIsNone(account.position)

    def test_win_rate_tracking(self):
        account = PaperAccount()
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        account.update("UP", 100, now)
        account.update("FLAT", 110, now)  # winning trade
        account.update("DOWN", 110, now)
        account.update("FLAT", 120, now)  # losing trade
        self.assertEqual(account.total_trades, 2)
        self.assertEqual(account.winning_trades, 1)
        self.assertAlmostEqual(account.win_rate(), 50.0)


if __name__ == "__main__":
    unittest.main()
