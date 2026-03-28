"""Small authentication helpers for the web service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


def hash_client_ip(client_ip: str, salt: str) -> str:
    """Hash a client IP before persisting it."""
    return hashlib.sha256(f"{salt}:{client_ip}".encode("utf-8")).hexdigest()


def hash_secret_value(value: str) -> str:
    """Hash a secret token into a stable identifier."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_signed_token(secret: str, purpose: str, job_id: str, expires_at: int) -> str:
    """Create a short signed token tied to a job and purpose."""
    message = f"{purpose}:{job_id}:{expires_at}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{expires_at}.{encoded}"


def verify_signed_token(
    secret: str,
    purpose: str,
    job_id: str,
    token: str,
    now: int | None = None,
) -> bool:
    """Verify a token created by :func:`create_signed_token`."""
    if now is None:
        now = int(time.time())

    try:
        expires_part, signature_part = token.split(".", 1)
        expires_at = int(expires_part)
    except (ValueError, AttributeError):
        return False

    if now > expires_at:
        return False

    expected = create_signed_token(secret, purpose, job_id, expires_at)
    return hmac.compare_digest(expected, token)
