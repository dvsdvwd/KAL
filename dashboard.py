import json
import os
import re
import threading
import time
from http.cookies import SimpleCookie
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from kraken_futures import load_client_from_env
from kalshi_perps import (
    PaperAccount,
    calculate_all_indicators,
    fetch_candles,
    fetch_kalshi_markets,
    get_direction,
    market_yes_quotes,
)
from session_auth import (
    SESSION_COOKIE,
    check_password,
    is_session_token_valid,
    make_session_token,
    password_is_configured,
)


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "5000"))
STATIC_DIR = Path(__file__).parent / "static"
MAX_LEVERAGE = 2
account = PaperAccount(
    starting_cash=0.01,
    max_leverage=MAX_LEVERAGE,
    position_fraction=0.05,
    daily_loss_limit=0.05,
)
bot = {"running": False, "symbol": "PF_XBTUSD", "interval": "15m", "action": "paused", "mode": "paper"}
kraken_client = load_client_from_env()
LOGIN_FAILURES = {}


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


def get_kraken_status():
    """Return private exchange data only to an authenticated dashboard session."""
    if not kraken_client:
        return {"configured": False, "connected": False, "wallets": [], "positions": []}
    try:
        return {"configured": True, **kraken_client.get_account_summary()}
    except Exception:
        return {
            "configured": True,
            "connected": False,
            "wallets": [],
            "positions": [],
            "error": "Could not read the Kraken Futures account. Check the API key and environment.",
        }


def build_snapshot(symbol, interval, include_private=False):
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

    position = account.position
    equity = account.equity(price)
    markets = fetch_kalshi_markets(30)

    return {
        "symbol": symbol,
        "interval": interval,
        "tradingMode": "paper",
        "authenticated": include_private,
        "kraken": get_kraken_status() if include_private else None,
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
                "tradingMode": "paper",
                "authConfigured": password_is_configured(),
                "krakenConfigured": kraken_client is not None,
                "krakenEnvironment": kraken_client.environment if kraken_client else None,
                "maxLeverage": MAX_LEVERAGE,
            })
            return
        if parsed.path == "/api/auth/me":
            self.send_json(200, {
                "authenticated": self.is_authenticated(),
                "authConfigured": password_is_configured(),
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
                self.send_json(
                    200,
                    build_snapshot(symbol, interval, include_private=self.is_authenticated()),
                )
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
        if path == "/api/auth/login":
            self.login()
            return
        if path == "/api/auth/logout":
            self.send_json(200, {"authenticated": False}, {
                "Set-Cookie": self.session_cookie("", max_age=0),
            })
            return
        if not self.require_auth():
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
                bot.update(running=True, symbol=symbol, interval=interval, action="waiting for signal", mode="paper")
            elif action == "stop":
                if account.position is not None:
                    candles = fetch_candles(bot["symbol"], bot["interval"])
                    account.update("FLAT", float(candles[-1]["close"]))
                bot.update(running=False, action="paused", mode="paper")
            else:
                self.send_json(400, {"error": "Action must be start or stop"})
                return
            self.send_json(200, {"bot": dict(bot)})
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid request body"})

    def is_authenticated(self):
        cookie_header = self.headers.get("Cookie", "")
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header)
        except Exception:
            return False
        morsel = cookies.get(SESSION_COOKIE)
        return bool(morsel and is_session_token_valid(morsel.value))

    def session_cookie(self, value, max_age=43200):
        return f"{SESSION_COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; Secure; SameSite=Strict"

    def require_auth(self):
        if self.is_authenticated():
            return True
        self.send_json(401, {"error": "Sign in to use account and bot controls"})
        return False

    def login(self):
        if not password_is_configured():
            self.send_json(503, {"error": "Set DASHBOARD_PASSWORD in Replit Secrets before signing in"})
            return
        now = time.time()
        ip = self.client_address[0]
        recent = [stamp for stamp in LOGIN_FAILURES.get(ip, []) if now - stamp < 60]
        if len(recent) >= 5:
            self.send_json(429, {"error": "Too many sign-in attempts. Wait one minute and try again."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                self.send_json(413, {"error": "Request is too large"})
                return
            payload = json.loads(self.rfile.read(length))
            if not check_password(payload.get("password", "")):
                recent.append(now)
                LOGIN_FAILURES[ip] = recent
                self.send_json(401, {"error": "Password not accepted"})
                return
            token = make_session_token()
            if token is None:
                self.send_json(503, {"error": "Session signing is not configured"})
                return
            LOGIN_FAILURES.pop(ip, None)
            self.send_json(200, {"authenticated": True}, {
                "Set-Cookie": self.session_cookie(token),
            })
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid sign-in request"})

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
            if not 0.01 <= starting_balance <= 10_000_000:
                raise ValueError("Starting balance must be between $0.01 and $10,000,000")
            if leverage not in range(1, MAX_LEVERAGE + 1):
                raise ValueError(f"Leverage must be between 1x and {MAX_LEVERAGE}x")
            if not 0.01 <= order_notional <= 1_000_000:
                raise ValueError("Paper order size must be between $0.01 and $1,000,000")
            account.reset(starting_balance, leverage, auto_size, order_notional)
            self.send_json(200, {"settings": {
                "startingBalance": account.starting_cash,
                "leverage": account.leverage,
                "autoSize": account.auto_size,
                "orderNotional": account.order_notional,
            }})
        except (KeyError, ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid settings"})

    def log_message(self, format_string, *args):
        return


def paper_bot_worker():
    """Keep paper simulation running even when no browser tab is polling."""
    while True:
        if bot["running"]:
            try:
                candles = fetch_candles(bot["symbol"], bot["interval"])
                if candles:
                    closes = [float(candle["close"]) for candle in candles]
                    indicators, direction = calculate_all_indicators(candles, closes)
                    bot["action"] = account.update(direction, closes[-1])
                    bot["lastUpdate"] = datetime.now(timezone.utc).isoformat()
                    bot["score"] = indicators.get("compositeScore")
                    if account.halted:
                        bot.update(running=False, action="daily loss stop reached")
            except Exception:
                bot["action"] = "feed error; waiting to retry"
        time.sleep(15)


def main():
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    threading.Thread(target=paper_bot_worker, daemon=True).start()
    print(f"Signal desk listening on {HOST}:{PORT}")
    print("Paper trading only. Kraken Futures account access, when configured, is read-only.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
