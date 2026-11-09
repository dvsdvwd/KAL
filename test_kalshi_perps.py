import unittest
from datetime import datetime, timezone

from dashboard import analyze_price_action, calculate_session_vwap
from kalshi_perps import PaperAccount, format_probability, get_direction, market_yes_quotes


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


class PaperAccountTests(unittest.TestCase):
    def test_position_is_unleveraged_and_daily_loss_halts(self):
        account = PaperAccount()
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.assertEqual(account.update("UP", 100, now), "opened paper long")
        self.assertAlmostEqual(account.position.quantity * 100, 500)
        self.assertEqual(account.update("UP", 50, now), "daily loss limit reached")
        self.assertIsNone(account.position)
        self.assertTrue(account.halted)

    def test_leverage_scales_auto_notional_within_five_percent_margin_cap(self):
        account = PaperAccount(leverage=5)
        account.update("UP", 100)
        self.assertAlmostEqual(account.position.quantity * 100, 2500)
        self.assertEqual(account.position.leverage, 5)

    def test_positions_below_one_dollar_are_not_opened(self):
        account = PaperAccount(starting_cash=10)
        self.assertEqual(account.update("UP", 100), "paper order below $1 minimum")
        self.assertIsNone(account.position)

    def test_manual_notional_is_capped_by_equity_margin_and_leverage(self):
        account = PaperAccount(leverage=2, auto_size=False, order_notional=5000)
        account.update("UP", 100)
        self.assertAlmostEqual(account.position.quantity * 100, 1000)


if __name__ == "__main__":
    unittest.main()