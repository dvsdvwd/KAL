import json
import os
import re
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from kalshi_client import load_client_from_env
from kalshi_perps import (
    PaperAccount,
    calculate_all_indicators,
    fetch_candles,
    fetch_kalshi_markets,
    get_direction,
    market_yes_quotes,
)


HOST = "127.0.0.1"
PORT = 8765
STATIC_DIR = Path(__file__).parent / "static"
MAX_LEVERAGE = 20
account = PaperAccount(max_leverage=MAX_LEVERAGE, position_fraction=0.10, daily_loss_limit=0.05)
bot = {"running": False, "symbol": "PF_XBTUSD", "interval": "15m", "action": "paused", "mode": "paper"}
kalshi_client = load_client_from_env()
trading_mode = "live" if kalshi_client else "paper"


def calculate_session_vwap(candles):
    if not candles:
        return None
    last_day = datetime.fromtimestamp(int(candles[-1]["time"]) / 1000, timezone.utc).date()
    session = [
        candle for candle in candles
        if datetime.fromtimestamp(int(candle["time"]) / 1000, timezone.utc).date() == last_day
    ]
    volume_sum = sum(float(candle["volume"]) for candle in session)
    if volume_sum == 0:
        return None
    weighted_sum = sum(
        ((float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3)
        * float(candle["volume"])
        for candle in session
    )
    return weighted_sum / volume_sum


def analyze_price_action(candles, period=20):
    if len(candles) <= period:
        return {"state": "INSUFFICIENT DATA", "direction": "FLAT", "rangeMultiple": None, "volumeMultiple": None}
    reference = candles[-period - 1:-1]
    current = candles[-1]
    average_range = sum(float(candle["high"]) - float(candle["low"]) for candle in reference) / period
    average_volume = sum(float(candle["volume"]) for candle in reference) / period
    current_range = float(current["high"]) - float(current["low"])
    current_volume = float(current["volume"])
    range_multiple = current_range / average_range if average_range > 0 else None
    volume_multiple = current_volume / average_volume if average_volume > 0 else None
    range_expansion = range_multiple is not None and range_multiple >= 1.5
    volume_expansion = volume_multiple is not None and volume_multiple >= 2
    if range_expansion and volume_expansion:
        state = "RANGE + VOLUME EXPANSION"
    elif range_expansion:
        state = "RANGE EXPANSION"
    elif volume_expansion:
        state = "VOLUME SPIKE"
    else:
        state = "NORMAL"
    direction = "UP" if current["close"] > current["open"] else "DOWN" if current["close"] < current["open"] else "FLAT"
    return {
        "state": state,
        "direction": direction,
        "rangeMultiple": range_multiple,
        "volumeMultiple": volume_multiple,
    }


def get_live_status():
    """Fetch live Kalshi balance and positions if API credentials are configured."""
    if not kalshi_client:
        return {"connected": False, "balance": None, "positions": [], "env": None}
    try:
        balance = kalshi_client.get_balance()
        positions = kalshi_client.get_positions()
        return {
            "connected": True,
            "balance": balance,
            "positions": positions.get("positions", []) if isinstance(positions, dict) else [],
            "env": "demo" if kalshi_client.demo else "prod",
        }
    except Exception as error:
        return {"connected": False, "balance": None, "positions": [], "env": None, "error": str(error)}


def build_snapshot(symbol, interval):
    candles = fetch_candles(symbol, interval)
    closes = [float(candle["close"]) for candle in candles]
    indicators, composite_direction = calculate_all_indicators(candles, closes)
    price = closes[-1]
    prior_candles = candles[-21:-1]
    range_high = max(float(candle["high"]) for candle in prior_candles)
    range_low = min(float(candle["low"]) for candle in prior_candles)

    indicators["rangeHigh"] = range_high
    indicators["rangeLow"] = range_low
    indicators["priceAction"] = analyze_price_action(candles)
    indicators["sessionVwap"] = calculate_session_vwap(candles)

    if bot["running"] and bot["symbol"] == symbol and bot["interval"] == interval:
        bot["action"] = account.update(composite_direction, price)

    position = account.position
    equity = account.equity(price)
    markets = fetch_kalshi_markets(30)
    live = get_live_status()

    return {
        "symbol": symbol,
        "interval": interval,
        "tradingMode": trading_mode,
        "kalshiConnected": live["connected"],
        "kalshiEnv": live.get("env"),
        "kalshiBalance": live["balance"],
        "kalshiPositions": live["positions"],
        "kalshiError": live.get("error"),
        "candles": [
            {
                "time": int(candle["time"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle["volume"]),
            }
            for candle in candles
        ],
        "price": price,
        "signal": composite_direction,
        "indicators": indicators,
        "account": {
            "equity": equity,
            "cash": account.cash,
            "dailyPnl": equity - account.day_start_equity if account.day else 0,
            "peakEquity": account.peak_equity,
            "winRate": account.win_rate(),
            "totalTrades": account.total_trades,
            "position": None if position is None else {
                "side": "long" if position.side > 0 else "short",
                "quantity": position.quantity,
                "entryPrice": position.entry_price,
                "notional": position.quantity * position.entry_price,
                "leverage": position.leverage,
            },
            "halted": account.halted,
            "startingBalance": account.starting_cash,
            "leverage": account.leverage,
            "autoSize": account.auto_size,
            "orderNotional": account.order_notional,
        },
        "bot": dict(bot),
        "markets": [
            {
                "ticker": market.get("ticker", ""),
                "title": market.get("title", "Untitled market"),
                "closeTime": market.get("close_time"),
                "yesBid": market_yes_quotes(market)[0],
                "yesAsk": market_yes_quotes(market)[1],
            }
            for market in markets
        ],
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self.send_json(200, {
                "tradingMode": trading_mode,
                "kalshiConnected": kalshi_client is not None,
                "kalshiEnv": "demo" if kalshi_client and kalshi_client.demo else ("prod" if kalshi_client else None),
                "maxLeverage": MAX_LEVERAGE,
            })
            return
        if parsed.path == "/api/snapshot":
            query = parse_qs(parsed.query)
            symbol = query.get("symbol", ["PF_XBTUSD"])[0].upper()
            interval = query.get("interval", ["15m"])[0]
            if not re.fullmatch(r"[A-Z0-9_]{1,24}", symbol) or interval not in {"1m", "5m", "15m", "1h"}:
                self.send_json(400, {"error": "Invalid symbol or interval"})
                return
            try:
                self.send_json(200, build_snapshot(symbol, interval))
            except Exception as error:
                self.send_json(502, {"error": str(error)})
            return

        assets = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
        filename = assets.get(parsed.path)
        if filename is None:
            self.send_error(404)
            return
        body = (STATIC_DIR / filename).read_bytes()
        content_type = "text/html; charset=utf-8" if filename.endswith(".html") else (
            "text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/settings":
            self.update_settings()
            return
        if path == "/api/kalshi/order":
            self.place_kalshi_order()
            return
        if path != "/api/bot":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            action = payload.get("action")
            if action == "start":
                symbol = str(payload.get("symbol", "PF_XBTUSD")).upper()
                interval = payload.get("interval", "15m")
                if not re.fullmatch(r"[A-Z0-9_]{1,24}", symbol) or interval not in {"1m", "5m", "15m", "1h"}:
                    self.send_json(400, {"error": "Invalid symbol or interval"})
                    return
                bot.update(running=True, symbol=symbol, interval=interval, action="waiting for signal")
            elif action == "stop":
                if account.position is not None:
                    candles = fetch_candles(bot["symbol"], bot["interval"])
                    account.update("FLAT", float(candles[-1]["close"]))
                bot.update(running=False, action="paused")
            else:
                self.send_json(400, {"error": "Action must be start or stop"})
                return
            self.send_json(200, {"bot": dict(bot)})
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid request body"})

    def update_settings(self):
        if bot["running"]:
            self.send_json(409, {"error": "Stop the bot before changing settings"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            starting_balance = float(payload["startingBalance"])
            leverage = int(payload["leverage"])
            auto_size = payload["autoSize"]
            order_notional = float(payload["orderNotional"])
            if not isinstance(auto_size, bool):
                raise ValueError("autoSize must be a boolean")
            if not 1 <= starting_balance <= 10_000_000:
                raise ValueError("Starting balance must be between $1 and $10,000,000")
            if leverage not in range(1, MAX_LEVERAGE + 1):
                raise ValueError(f"Leverage must be between 1x and {MAX_LEVERAGE}x")
            if not 1 <= order_notional <= 1_000_000:
                raise ValueError("Manual order size must be between $1 and $1,000,000")
            account.reset(starting_balance, leverage, auto_size, order_notional)
            self.send_json(200, {"settings": {
                "startingBalance": account.starting_cash,
                "leverage": account.leverage,
                "autoSize": account.auto_size,
                "orderNotional": account.order_notional,
            }})
        except (KeyError, ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid settings"})

    def place_kalshi_order(self):
        if not kalshi_client:
            self.send_json(503, {"error": "Set KALSHI_KEY_ID and KALSHI_KEY_PATH to enable live Kalshi trading"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            ticker = str(payload["ticker"]).upper()
            side = str(payload["side"]).lower()
            count = int(payload["count"])
            price = float(payload["price"])
            if side not in ("bid", "ask"):
                raise ValueError("side must be bid or ask")
            if not re.fullmatch(r"[A-Z0-9\-]{1,40}", ticker):
                raise ValueError("Invalid ticker")
            if count < 1 or count > 100_000:
                raise ValueError("count must be between 1 and 100,000")
            if not 0.01 <= price <= 0.99:
                raise ValueError("price must be between $0.01 and $0.99")
            result = kalshi_client.place_order(
                ticker, side, count, price,
                client_order_id=str(uuid.uuid4()),
            )
            self.send_json(200, {"order": result})
        except (KeyError, ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            self.send_json(502, {"error": str(error)})

    def log_message(self, format_string, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Signal desk: http://{HOST}:{PORT}")
    if trading_mode == "live":
        env_label = "demo" if kalshi_client.demo else "PRODUCTION"
        print(f"Kalshi API connected ({env_label}). Live order placement enabled.")
    else:
        print("Paper mode. Set KALSHI_KEY_ID and KALSHI_KEY_PATH for live Kalshi trading.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
