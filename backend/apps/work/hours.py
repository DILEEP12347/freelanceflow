"""Hours and money for time tracking (pure). Rates are minor units per hour; durations are whole seconds."""
from decimal import ROUND_HALF_UP, Decimal

MAX_ENTRY_SECONDS = 24 * 3600  # one entry can never be longer than a day (a forgotten timer is caught here)


def value_minor(seconds: int, rate_minor: int) -> int:
    """What `seconds` of work is worth at `rate_minor` per hour, rounded half-up to the minor unit."""
    return int((Decimal(int(seconds)) * int(rate_minor) / Decimal(3600)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def hours_quantity(seconds: int) -> Decimal:
    """Invoice quantity in hours with 2 decimals, never below 0.01 (a line must have a quantity)."""
    hours = (Decimal(int(seconds)) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return max(hours, Decimal("0.01"))


def hours_label(seconds: int) -> str:
    """5400 -> '1.50'"""
    return format((Decimal(int(seconds)) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def value_from_product(seconds_times_rate: int) -> int:
    """A database SUM of (seconds x hourly rate) -> money: divide by 3600, rounding half up (integer maths only)."""
    return (int(seconds_times_rate) * 2 + 3600) // 7200
