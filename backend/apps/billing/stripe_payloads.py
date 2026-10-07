"""Small, defensive readers for Stripe objects (plain dicts parsed from webhook JSON).

Stripe changes shapes between API versions. Example: since API 2025-03-31 `current_period_end` moved from
the subscription to its items. Webhook payloads use the version set on the webhook endpoint (or your account),
so we accept both shapes instead of betting on one.
"""
from datetime import datetime, timezone


def ts_to_dt(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None


def id_of(value) -> str:
    """Stripe fields are an id string, or an expanded object with an `id`."""
    if isinstance(value, dict):
        return value.get("id") or ""
    return value or ""


def first_item(obj: dict) -> dict:
    data = ((obj.get("items") or {}).get("data")) or []
    return data[0] if data and isinstance(data[0], dict) else {}


def extract_price_id(obj: dict) -> str:
    item = first_item(obj)
    price = item.get("price") or item.get("plan") or {}
    return id_of(price)


def extract_period_end(obj: dict):
    return ts_to_dt(obj.get("current_period_end") or first_item(obj).get("current_period_end"))


def extract_tenant_id(obj: dict):
    raw = (obj.get("metadata") or {}).get("tenant_id") or obj.get("client_reference_id")
    return int(raw) if raw is not None and str(raw).isdigit() else None
