from __future__ import annotations

import re
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

IST = timezone(timedelta(hours=5, minutes=30))


def normalize_header(value: str) -> str:
    return re.sub(r"[_\s]+", " ", value.strip().lower())


def normalize_phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value.strip())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        return None
    return "+91" + digits


def parse_date(value: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_decimal(value: str) -> str | None:
    cleaned = re.sub(r"^\s*(?:\u20b9|â‚¹|Rs\.?|INR)\s*", "", value, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", "").strip()
    try:
        number = Decimal(cleaned)
        if number < 0:
            return None
        return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def parse_timestamp(value: str) -> datetime | None:
    try:
        text = value.strip()
        if text.endswith("Z"):
            parsed = datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST)
    except ValueError:
        return None
