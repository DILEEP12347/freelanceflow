"""Make a request body safe to store in the audit log: no secrets, bounded size. Pure."""
import json

SENSITIVE = ("password", "token", "secret", "authorization", "key", "card", "cvv", "otp")
MAX_STRING, MAX_ITEMS, MAX_DEPTH, MAX_JSON = 200, 20, 3, 4000


def _clean(value, depth=0):
    if isinstance(value, dict):
        if depth >= MAX_DEPTH:
            return "[nested]"
        out = {}
        for key, item in list(value.items())[:MAX_ITEMS]:
            out[str(key)] = "[redacted]" if any(s in str(key).lower() for s in SENSITIVE) else _clean(item, depth + 1)
        return out
    if isinstance(value, list):
        if depth >= MAX_DEPTH:
            return "[nested]"
        return [_clean(v, depth + 1) for v in value[:MAX_ITEMS]]
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + "…"
    return value


def sanitize_body(raw: bytes):
    """-> dict to store. Non-JSON or non-object bodies give {}."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = _clean(data)
    if len(json.dumps(cleaned, default=str)) > MAX_JSON:
        return {"_truncated": True, "keys": list(cleaned)[:50]}
    return cleaned
