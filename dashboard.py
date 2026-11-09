import json
import os
import re
import secrets
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kalshi_perps import (
    PaperAccount,
    ema,
    fetch_candles,
    fetch_kalshi_markets,
    get_direction,
    market_yes_quotes,
)


HOST = "127.0.0.1"
PORT = 8765
STATIC_DIR = Path(__file__).parent / "static"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
sessions = {}
account = PaperAccount()
bot = {"running": False, "symbol": "PF_XBTUSD", "interval": "15m", "action": "paused"}


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [current - previous for previous, current in zip(values, values[1:])][-period:]
    gains = sum(change for change in changes if change > 0) / period
    losses = -sum(change for change in changes if change < 0) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)


def calculate_macd(values):
    if len(values) < 26:
        return None
    line = [
        ema(values[:index + 1], 12) - ema(values[:index + 1], 26)
        for index in range(25, len(values))
    ]
    signal = ema(line[-9:], 9)
    return {"line": line[-1], "signal": signal, "histogram": line[-1] - signal}


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


def build_snapshot(symbol, interval):
    candles = fetch_candles(symbol, interval)
    closes = [float(candle["close"]) for candle in candles]
    direction, fast, slow = get_direction(closes)
    price = closes[-1]
    prior_candles = candles[-21:-1]
    range_high = max(float(candle["high"]) for candle in prior_candles)
    range_low = min(float(candle["low"]) for candle in prior_candles)

    if bot["running"] and bot["symbol"] == symbol and bot["interval"] == interval:
        bot["action"] = account.update(direction, price)

    position = account.position
    equity = account.equity(price)
    markets = fetch_kalshi_markets(20)
    return {
        "symbol": symbol,
        "interval": interval,
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
        "signal": direction,
        "indicators": {
            "emaFast": fast,
            "emaSlow": slow,
            "rsi14": calculate_rsi(closes),
            "macd": calculate_macd(closes),
            "vwap": calculate_session_vwap(candles),
            "priceAction": analyze_price_action(candles),
            "rangeHigh": range_high,
            "rangeLow": range_low,
        },
        "account": {
            "equity": equity,
            "cash": account.cash,
            "dailyPnl": equity - account.day_start_equity if account.day else 0,
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

    def session_user(self):
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        session_cookie = cookies.get("kal_session")
        return sessions.get(session_cookie.value) if session_cookie else None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self.send_json(200, {"googleClientId": GOOGLE_CLIENT_ID or None})
            return
        if parsed.path == "/api/auth/me":
            user = self.session_user()
            self.send_json(200, {"authenticated": user is not None, "user": user})
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
        if path == "/api/auth/google":
            self.complete_google_sign_in()
            return
        if path == "/api/auth/logout":
            self.sign_out()
            return
        if path == "/api/settings":
            self.update_settings()
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
            self.send_json(409, {"error": "Stop the paper bot before changing account settings"})
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
                raise ValueError("Starting paper balance must be between $1 and $10,000,000")
            if leverage not in range(1, 6):
                raise ValueError("Simulated leverage must be between 1x and 5x")
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
            self.send_json(400, {"error": "Invalid account settings"})

    def complete_google_sign_in(self):
        if not GOOGLE_CLIENT_ID:
            self.send_json(503, {"error": "Set GOOGLE_CLIENT_ID to enable Google sign-in"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            credential = json.loads(self.rfile.read(length)).get("credential")
            if not credential:
                raise ValueError("Missing Google credential")
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid Google sign-in request"})
            return
        try:
            from google.auth.transport.requests import Request as GoogleRequest
            from google.oauth2 import id_token

            claims = id_token.verify_oauth2_token(credential, GoogleRequest(), GOOGLE_CLIENT_ID)
        except ImportError:
            self.send_json(503, {"error": "Install requirements.txt to enable Google token verification"})
            return
        except Exception:
            self.send_json(401, {"error": "Google credential could not be verified"})
            return
        if not claims.get("email_verified"):
            self.send_json(401, {"error": "Google account email is not verified"})
            return
        session_id = secrets.token_urlsafe(32)
        user = {"name": claims.get("name", ""), "email": claims.get("email", "")}
        sessions[session_id] = user
        cookie = f"kal_session={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=28800"
        self.send_json(200, {"authenticated": True, "user": user}, {"Set-Cookie": cookie})

    def sign_out(self):
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        session_cookie = cookies.get("kal_session")
        if session_cookie:
            sessions.pop(session_cookie.value, None)
        cookie = "kal_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        self.send_json(200, {"authenticated": False}, {"Set-Cookie": cookie})

    def log_message(self, format_string, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Paper trading dashboard: http://{HOST}:{PORT}")
    print("Local-only server. No live orders or exchange balance access.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()