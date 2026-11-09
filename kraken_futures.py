"""Read-only Kraken Futures REST client.

This module intentionally exposes no order-placement methods. Account keys used
here should have read-only permissions.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.request import Request, urlopen


PRODUCTION_BASE = "https://futures.kraken.com/derivatives"
DEMO_BASE = "https://demo-futures.kraken.com/derivatives"


def futures_authent(api_secret, endpoint_path, post_data="", nonce=None):
    """Create Kraken Futures v3 Authent per Kraken's documented algorithm."""
    nonce = str(nonce if nonce is not None else int(time.time() * 1000))
    message = f"{post_data}{nonce}{endpoint_path}".encode()
    try:
        secret_bytes = base64.b64decode(api_secret, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("Kraken API secret must be a valid base64 value") from error
    digest = hashlib.sha256(message).digest()
    return base64.b64encode(
        hmac.new(secret_bytes, digest, hashlib.sha512).digest()
    ).decode()


class KrakenFuturesClient:
    """Small authenticated client for read-only account endpoints."""

    def __init__(self, api_key, api_secret, environment="demo", timeout=12):
        if environment not in {"demo", "production"}:
            raise ValueError("Kraken environment must be demo or production")
        self.api_key = api_key
        self.api_secret = api_secret
        self.environment = environment
        self.timeout = timeout
        base = DEMO_BASE if environment == "demo" else PRODUCTION_BASE
        self.base_url = f"{base}/api/v3"

    def _request(self, endpoint, method="GET"):
        endpoint_path = f"/api/v3/{endpoint.lstrip('/')}"
        nonce = str(int(time.time() * 1000))
        authent = futures_authent(self.api_secret, endpoint_path, nonce=nonce)
        request = Request(
            f"{self.base_url}/{endpoint.lstrip('/')}",
            method=method,
            headers={
                "APIKey": self.api_key,
                "Authent": authent,
                "Nonce": nonce,
                "Accept": "application/json",
                "User-Agent": "signal-desk-read-only/1.0",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        if payload.get("result") != "success":
            raise RuntimeError("Kraken Futures returned an unsuccessful response")
        return payload

    def get_accounts(self):
        return self._request("accounts")

    def get_open_positions(self):
        return self._request("openpositions")

    def get_account_summary(self):
        account_response = self.get_accounts()
        position_response = self.get_open_positions()
        raw_accounts = account_response.get("accounts", {})
        wallets = []
        for name, account in raw_accounts.items():
            if not isinstance(account, dict):
                continue
            auxiliary = account.get("auxiliary", {})
            balances = account.get("balances", {})
            wallets.append({
                "name": name,
                "currency": account.get("currency") or "",
                "available": auxiliary.get("af"),
                "valuation": auxiliary.get("pv"),
                "balances": {
                    str(currency): str(amount)
                    for currency, amount in balances.items()
                    if isinstance(amount, (str, int, float))
                },
            })
        positions = []
        for position in position_response.get("openPositions", []):
            if not isinstance(position, dict):
                continue
            positions.append({
                "symbol": position.get("symbol", ""),
                "side": position.get("side", ""),
                "size": position.get("size"),
                "entryPrice": position.get("price"),
                "unrealizedPnl": position.get("unrealizedPnl"),
                "currency": position.get("pnlCurrency"),
            })
        return {
            "connected": True,
            "environment": self.environment,
            "wallets": wallets,
            "positions": positions,
        }


def load_client_from_env():
    api_key = os.environ.get("KRAKEN_API_KEY", "").strip()
    api_secret = os.environ.get("KRAKEN_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return None
    environment = os.environ.get("KRAKEN_ENV", "demo").strip().lower()
    if environment in {"prod", "live"}:
        environment = "production"
    return KrakenFuturesClient(api_key, api_secret, environment=environment)
