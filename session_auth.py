"""Single-user dashboard password sessions."""

import base64
import hashlib
import hmac
import os
import time


SESSION_COOKIE = "signal_desk_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


def password_is_configured():
    return bool(os.environ.get("DASHBOARD_PASSWORD", "").strip())


def _signature(payload):
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        return None
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_token(now=None):
    issued_at = int(now if now is not None else time.time())
    payload = str(issued_at)
    signature = _signature(payload)
    if signature is None:
        return None
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{signature}"


def is_session_token_valid(token, now=None):
    if not token or not password_is_configured():
        return False
    try:
        encoded, supplied_signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        issued_at = int(payload)
    except (ValueError, UnicodeDecodeError):
        return False
    expected_signature = _signature(payload)
    current_time = int(now if now is not None else time.time())
    if expected_signature is None or not hmac.compare_digest(supplied_signature, expected_signature):
        return False
    return issued_at <= current_time and current_time - issued_at <= SESSION_TTL_SECONDS


def check_password(candidate):
    expected = os.environ.get("DASHBOARD_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(str(candidate), expected)
