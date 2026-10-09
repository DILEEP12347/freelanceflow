"""The business's own clock. 'Today' for due dates, overdue checks, reminders and monthly limits is the
calendar day in the timezone set in Invoicing settings (default UTC), not the server's.

The timezone is cached per schema for BUSINESS_TZ_CACHE_SECONDS (30; 0 while testing), because 'overdue' is evaluated for every invoice in a list.
"""
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection
from django.utils import timezone

_cache = {}  # schema_name -> (expires_at, timezone name)


def clear_cache():
    _cache.clear()


def get_timezone_name() -> str:
    schema = getattr(connection, "schema_name", "public")
    hit = _cache.get(schema)
    ttl = settings.BUSINESS_TZ_CACHE_SECONDS
    if ttl and hit and hit[0] > time.monotonic():
        return hit[1]
    from .models import InvoiceSettings  # local import: models import this module

    name = InvoiceSettings.load().timezone
    _cache[schema] = (time.monotonic() + ttl, name)
    return name


def get_zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_timezone_name())
    except Exception:  # an invalid name must never break every request
        return ZoneInfo("UTC")


def business_now() -> datetime:
    return timezone.now().astimezone(get_zone())


def business_today():
    return business_now().date()


def business_month_start() -> datetime:
    """Midnight on the 1st of this month in the business's timezone (an aware datetime)."""
    first = business_today().replace(day=1)
    return datetime.combine(first, dtime.min, tzinfo=get_zone())
