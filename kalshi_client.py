import base64
import json
import time
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

KALSHI_PROD_BASE = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_DEMO_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"


def _pss_sign(private_key_pem, payload):
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise RuntimeError("pip install cryptography to enable Kalshi live trading")

    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    signature = key.sign(payload.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size), hashes.SHA256())
    return base64.b64encode(signature).decode()


class KalshiClient:
    """Signed REST client for Kalshi's authenticated portfolio endpoints.

    Requires a verified Kalshi account, an API key ID, and the matching RSA
    private key in PEM format.  The key is read from a file path so it is never
    stored in the browser or committed to the repository.
    """

    def __init__(self, key_id, key_path, demo=False, base_url=None):
        self.key_id = key_id
        self.key_path = key_path
        self.demo = demo
        self.base_url = base_url or (KALSHI_DEMO_BASE if demo else KALSHI_PROD_BASE)
        self._key_cache = None
        self._key_mtime = 0

    def _private_key_pem(self):
        try:
            mtime = __import__("os").path.getmtime(self.key_path)
        except OSError:
            raise RuntimeError(f"Kalshi private key not found at {self.key_path}")
        if self._key_cache is None or mtime != self._key_mtime:
            with open(self.key_path) as key_file:
                self._key_cache = key_file.read()
            self._key_mtime = mtime
        return self._key_cache

    def _headers(self, method, path):
        timestamp = str(int(time.time() * 1000))
        pre_sign = f"{timestamp}{method}{path}"
        signature = _pss_sign(self._private_key_pem(), pre_sign)
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
            "User-Agent": "kalshi-perps-live-monitor/2.0",
        }

    def _request(self, method, path, body=None, query=None):
        if query:
            full_path = f"{path}?{urlencode(query)}"
        else:
            full_path = path
        url = urljoin(self.base_url + "/", full_path.lstrip("/"))
        headers = self._headers(method, full_path)
        data = json.dumps(body).encode() if body else None
        request = Request(url, data=data, method=method, headers=headers)
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    def get_balance(self):
        return self._request("GET", "/portfolio/balance")

    def get_positions(self, ticker=None):
        query = {"ticker": ticker} if ticker else None
        return self._request("GET", "/portfolio/positions", query=query)

    def get_orders(self, ticker=None, limit=50):
        query = {"limit": limit}
        if ticker:
            query["ticker"] = ticker
        return self._request("GET", "/portfolio/orders", query=query)

    def place_order(self, ticker, side, count, price, time_in_force="good_till_canceled", post_only=False, client_order_id=None):
        body = {
            "ticker": ticker,
            "side": side,
            "count": str(int(count)),
            "price": f"{float(price):.4f}",
            "time_in_force": time_in_force,
            "post_only": post_only,
        }
        if client_order_id:
            body["client_order_id"] = client_order_id
        return self._request("POST", "/portfolio/events/orders", body=body)

    def cancel_order(self, order_id):
        return self._request("DELETE", f"/portfolio/events/orders/{order_id}")

    def get_markets(self, limit=50, status="open"):
        return self._request("GET", "/markets", query={"limit": limit, "status": status})

    def get_market(self, ticker):
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker):
        return self._request("GET", f"/markets/{ticker}/orderbook")


def load_client_from_env():
    """Build a KalshiClient from environment variables, or return None."""
    import os

    key_id = os.environ.get("KALSHI_KEY_ID", "").strip()
    key_path = os.environ.get("KALSHI_KEY_PATH", "").strip()
    if not key_id or not key_path:
        return None
    demo = os.environ.get("KALSHI_ENV", "demo").strip().lower() == "demo"
    return KalshiClient(key_id, key_path, demo=demo)
