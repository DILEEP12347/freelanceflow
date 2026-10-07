"""Stripe webhook signature check (no Django, no Stripe SDK: pure and unit-tested).

Stripe signs every webhook: header `Stripe-Signature: t=<unix time>,v1=<hex hmac>`, where
hmac = HMAC-SHA256(webhook_secret, f"{t}.{raw_body}"). We recompute it and compare in constant time.
A timestamp older than 5 minutes is rejected so a captured request cannot be replayed later.
"""
import hashlib
import hmac
import time

TOLERANCE_SECONDS = 300


class SignatureError(Exception):
    pass


def verify_signature(payload: bytes, header: str, secret: str, now=None, tolerance=TOLERANCE_SECONDS) -> None:
    if not secret:
        raise SignatureError("Webhook secret is not configured.")
    if not header:
        raise SignatureError("Missing Stripe-Signature header.")
    timestamp, signatures = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        raise SignatureError("Malformed Stripe-Signature header.")
    try:
        sent_at = int(timestamp)
    except ValueError:
        raise SignatureError("Malformed timestamp.") from None
    if abs((time.time() if now is None else now) - sent_at) > tolerance:
        raise SignatureError("Timestamp outside the tolerance window.")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise SignatureError("Signature does not match.")
