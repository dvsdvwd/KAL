import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


KALSHI_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
KRAKEN_CANDLES_URL = "https://futures.kraken.com/api/charts/v1/trade"
SUPPORTED_INTERVALS = {"1m", "5m", "15m", "1h"}


def get_json(url):
    request = Request(url, headers={"User-Agent": "kalshi-perps-paper-monitor/1.0"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def fetch_candles(symbol, interval="1m"):
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"unsupported interval: {interval}")
    instrument = quote(symbol.upper(), safe="_")
    payload = get_json(f"{KRAKEN_CANDLES_URL}/{instrument}/{interval}")
    return payload.get("candles", [])[-120:]


def fetch_closes(symbol):
    return [float(candle["close"]) for candle in fetch_candles(symbol)]


def fetch_kalshi_markets(limit):
    query = urlencode({"status": "open", "limit": limit})
    payload = get_json(f"{KALSHI_MARKETS_URL}?{query}")
    return payload.get("markets", [])


def ema(values, period):
    if not values:
        raise ValueError("values must not be empty")
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def get_direction(closes):
    if len(closes) < 21:
        raise ValueError("at least 21 perp candles are required")
    fast = ema(closes, 9)
    slow = ema(closes, 21)
    price = closes[-1]
    if fast > slow and price > slow:
        direction = "UP"
    elif fast < slow and price < slow:
        direction = "DOWN"
    else:
        direction = "FLAT"
    return direction, fast, slow


@dataclass
class Position:
    side: int
    quantity: float
    entry_price: float
    leverage: float


class PaperAccount:
    def __init__(self, starting_cash=10_000.0, position_fraction=0.05, daily_loss_limit=0.02, leverage=1, auto_size=True, order_notional=100.0):
        self.position_fraction = position_fraction
        self.daily_loss_limit = daily_loss_limit
        self.reset(starting_cash, leverage, auto_size, order_notional)

    def reset(self, starting_cash, leverage, auto_size, order_notional):
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.leverage = float(leverage)
        self.auto_size = bool(auto_size)
        self.order_notional = float(order_notional)
        self.position = None
        self.day = None
        self.day_start_equity = self.starting_cash
        self.halted = False

    def equity(self, price):
        if self.position is None:
            return self.cash
        pnl = self.position.side * self.position.quantity * (price - self.position.entry_price)
        return self.cash + pnl

    def _close(self, price):
        if self.position is None:
            return
        pnl = self.position.side * self.position.quantity * (price - self.position.entry_price)
        self.cash += pnl
        self.position = None

    def _order_notional(self, price):
        risk_cap = max(0.0, self.equity(price)) * self.position_fraction * self.leverage
        if risk_cap < 1:
            return 0.0
        if self.auto_size:
            return risk_cap
        return min(max(1.0, self.order_notional), risk_cap)

    def update(self, direction, price, now=None):
        now = now or datetime.now(timezone.utc)
        current_day = now.date()
        if current_day != self.day:
            self.day = current_day
            self.day_start_equity = self.equity(price)
            self.halted = False

        if self.equity(price) <= self.day_start_equity * (1 - self.daily_loss_limit):
            self._close(price)
            self.halted = True

        desired_side = {"UP": 1, "DOWN": -1}.get(direction, 0)
        if self.halted:
            return "daily loss limit reached"

        if self.position and self.position.side != desired_side:
            self._close(price)
        if desired_side and self.position is None:
            notional = self._order_notional(price)
            if notional < 1:
                return "paper order below $1 minimum"
            self.position = Position(desired_side, notional / price, price, self.leverage)
            return "opened paper long" if desired_side > 0 else "opened paper short"
        if self.position is None:
            return "no paper position"
        return "holding paper long" if self.position.side > 0 else "holding paper short"


def format_probability(value):
    if value is None:
        return "?"
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "?"


def market_yes_quotes(market):
    bid = market.get("yes_bid_dollars")
    ask = market.get("yes_ask_dollars")
    if bid is not None or ask is not None:
        return (
            None if bid is None else float(bid) * 100,
            None if ask is None else float(ask) * 100,
        )
    return market.get("yes_bid"), market.get("yes_ask")


def print_snapshot(symbol, markets, closes, account):
    direction, fast, slow = get_direction(closes)
    price = closes[-1]
    action = account.update(direction, price)
    position = account.position
    position_text = "none"
    if position:
        position_text = f"{'long' if position.side > 0 else 'short'} {position.quantity:.6f}"

    print(f"\n{datetime.now(timezone.utc).isoformat(timespec='seconds')}  PAPER ONLY")
    print(f"Perp {symbol.upper()}: {price:.4f} | signal {direction} | EMA9 {fast:.4f} | EMA21 {slow:.4f}")
    print(f"Paper: {action} | equity ${account.equity(price):.2f} | position {position_text} | leverage 1x max")
    print("Kalshi open markets (context only):")
    for market in markets:
        title = market.get("title", market.get("ticker", "Untitled market"))
        bid, ask = market_yes_quotes(market)
        yes_bid = format_probability(bid)
        yes_ask = format_probability(ask)
        print(f"  {title} | YES bid/ask {yes_bid}/{yes_ask}")


def main():
    parser = argparse.ArgumentParser(description="Kalshi and crypto-perps paper monitor")
    parser.add_argument("--symbol", default="PF_XBTUSD", help="Kraken Futures perpetual instrument, e.g. PF_XBTUSD")
    parser.add_argument("--watch", type=int, default=60, help="poll interval in seconds; use 0 for one snapshot")
    parser.add_argument("--kalshi-limit", type=int, default=5, help="number of open Kalshi markets to display")
    args = parser.parse_args()
    if args.watch < 0 or args.kalshi_limit < 1:
        parser.error("--watch must be >= 0 and --kalshi-limit must be >= 1")

    account = PaperAccount()
    while True:
        closes = fetch_closes(args.symbol)
        markets = fetch_kalshi_markets(args.kalshi_limit)
        print_snapshot(args.symbol, markets, closes, account)
        if args.watch == 0:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()