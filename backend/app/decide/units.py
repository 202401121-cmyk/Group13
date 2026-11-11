"""Numbers, units and words for the decide stage (US-17). Plain code: nothing here asks a model anything.

A resolved value or a rule's value may be a bare number (16), or text with a unit ("16 GB", "0.28 kg", "₹84,990",
"68.6 cm"). `quantity` reads either. Units convert within their dimension, by decimal factors: 1 TB is 1,000 GB, as
makers label storage. A unit we don't know compares only with itself, never with anything else.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Each unit we convert: (its dimension, how many of the dimension's base unit one of it is).
_UNITS: dict[str, tuple[str, float]] = {
    "kb": ("data", 1e-6),
    "mb": ("data", 1e-3),
    "gb": ("data", 1.0),
    "tb": ("data", 1e3),
    "g": ("mass", 1.0),
    "kg": ("mass", 1e3),
    "lb": ("mass", 453.59237),
    "oz": ("mass", 28.349523125),
    "mm": ("length", 0.1),
    "cm": ("length", 1.0),
    "m": ("length", 100.0),
    "inch": ("length", 2.54),
    "ms": ("time", 1 / 3_600_000),
    "s": ("time", 1 / 3600),
    "min": ("time", 1 / 60),
    "h": ("time", 1.0),
    "hz": ("frequency", 1.0),
    "khz": ("frequency", 1e3),
    "mhz": ("frequency", 1e6),
    "ghz": ("frequency", 1e9),
    "w": ("power", 1.0),
    "kw": ("power", 1e3),
    "wh": ("energy", 1.0),
    "kwh": ("energy", 1e3),
    "mah": ("charge", 1.0),
    "ah": ("charge", 1e3),
    "inr": ("money_inr", 1.0),
    "usd": ("money_usd", 1.0),
}

_ALIASES: dict[str, str] = {
    "kilobyte": "kb",
    "kilobytes": "kb",
    "megabyte": "mb",
    "megabytes": "mb",
    "gigabyte": "gb",
    "gigabytes": "gb",
    "terabyte": "tb",
    "terabytes": "tb",
    "gm": "g",
    "gms": "g",
    "gram": "g",
    "grams": "g",
    "kgs": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "ounce": "oz",
    "ounces": "oz",
    "millimetre": "mm",
    "millimetres": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "centimetre": "cm",
    "centimetres": "cm",
    "centimeter": "cm",
    "centimeters": "cm",
    "metre": "m",
    "metres": "m",
    "meter": "m",
    "meters": "m",
    "in": "inch",
    "inches": "inch",
    '"': "inch",
    "″": "inch",
    "”": "inch",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "mins": "min",
    "minute": "min",
    "minutes": "min",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "hertz": "hz",
    "watt": "w",
    "watts": "w",
    "kilowatt": "kw",
    "kilowatts": "kw",
    "₹": "inr",
    "rs": "inr",
    "rupee": "inr",
    "rupees": "inr",
    "$": "usd",
    "dollar": "usd",
    "dollars": "usd",
}

_LABELS: dict[str, str] = {
    "kb": "KB",
    "mb": "MB",
    "gb": "GB",
    "tb": "TB",
    "hz": "Hz",
    "khz": "kHz",
    "mhz": "MHz",
    "ghz": "GHz",
    "w": "W",
    "kw": "kW",
    "wh": "Wh",
    "kwh": "kWh",
    "mah": "mAh",
    "ah": "Ah",
    "inr": "INR",
    "usd": "USD",
}

# Shown in capitals when an attribute's words are written out ("ram_gb" -> "RAM").
_ACRONYMS = frozenset(
    {"ai", "anc", "bt", "cpu", "gps", "gpu", "hdd", "hdmi", "hdr", "ips", "lcd", "led", "nfc", "oled", "ois", "os"}
    | {"ram", "sd", "ssd", "uhd", "usb", "va", "tn"}
)

_CURRENCY = r"(?P<currency>(?<![A-Za-z])(?:₹|rs\.?|inr|\$|usd))"
# A whole number only: never the 7 of "i7", the 5 of "DDR5", or the 990 left after a comma or a point.
_NUMBER = r"(?<![A-Za-z\d,.])(?:\d[\d,]*(?:\.\d+)?|\.\d+)"
_ONE_NUMBER = re.compile(rf"^(?P<before>.*?){_CURRENCY}?\s*(?P<number>{_NUMBER})\s*(?P<after>.*)$", re.IGNORECASE)
_ANY_NUMBER = re.compile(_NUMBER)
# A currency or price word written against its number ("Rs84,990", "MRP84,990") gets a space, so the number is whole.
_GLUED_PRICE = re.compile(r"(?<![A-Za-z])(₹|rs\.?|inr|mrp|\$|usd)(?=\d)", re.IGNORECASE)
_UNIT_WORD = re.compile(r'^(?:[A-Za-z]+|"|″|”)')
_PAIRS = re.compile(r"(\d)(?=(\d{2})+$)")  # where Indian grouping puts its commas, two digits at a time


@dataclass(frozen=True)
class Quantity:
    """A number and its unit: one of ours ("gb"), the text of one we don't know, or None when none was given.

    `plain` is True when the value is only that: a number, or text such as "16 GB" or "₹84,990". Text with other words
    around its number ("Wi-Fi 6", "up to 10 hours") is not plain, so it is never taken as equal to a bare number.
    """

    number: float
    unit: str | None
    plain: bool = True


def unit_key(text: str | None) -> str | None:
    """Our name for a unit ("GB", "Gigabytes" -> "gb"), or its trimmed lower-case text if we don't know it."""
    if text is None:
        return None
    key = text.strip().casefold().rstrip(".")
    if not key:
        return None
    return _ALIASES.get(key, key)


def unit_label(unit: str | None) -> str:
    """How a unit is written for the shopper: "GB", "kg", "Hz", "inch"."""
    if unit is None:
        return ""
    return _LABELS.get(unit, unit)


def attribute_unit(attribute: str) -> str | None:
    """The unit an attribute's name ends with ("ram_gb" -> "gb", "weight_g" -> "g"), or None."""
    parts = attribute.split("_")
    if len(parts) < 2:
        return None
    unit = unit_key(parts[-1])
    return unit if unit in _UNITS else None


def attribute_words(attribute: str) -> str:
    """An attribute or criterion key in plain words: "ram_gb" -> "RAM", "has_ois" -> "OIS", "refresh_rate_hz" ->
    "refresh rate", "cpu_performance" -> "CPU performance"."""
    parts = [p for p in attribute.split("_") if p]
    if len(parts) > 1 and parts[0] in ("has", "is"):
        parts = parts[1:]
    if len(parts) > 1 and unit_key(parts[-1]) in _UNITS:
        parts = parts[:-1]
    return " ".join(p.upper() if p in _ACRONYMS else p for p in parts) or attribute


def quantity(value: object) -> Quantity | None:
    """The number in `value` and its unit, or None when it holds no single number (text such as "16 GB + 8 GB")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        return Quantity(number, None) if math.isfinite(number) else None
    if not isinstance(value, str):
        return None
    text = _GLUED_PRICE.sub(r"\1 ", value.strip())
    if len(_ANY_NUMBER.findall(text)) != 1:
        return None
    match = _ONE_NUMBER.match(text)
    if match is None:
        return None
    try:
        number = float(match["number"].replace(",", ""))
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    before, after = match["before"].strip(), match["after"].strip()
    if match["currency"]:
        return Quantity(number, unit_key(match["currency"]), plain=not before and not after.strip("/-"))
    word = _UNIT_WORD.match(after)
    unit = word.group(0) if word else ""
    return Quantity(number, unit_key(unit), plain=not before and after == unit)


def known_unit(unit: str | None) -> bool:
    """Is this one of the units we convert ("gb", "kg", "inch"...)?"""
    return unit in _UNITS


def in_base(amount: Quantity, unit: str | None) -> tuple[str, float]:
    """The amount in its dimension's base unit, with the dimension's name. `unit` is the one to assume when the
    amount has none. An unknown unit is its own dimension; no unit at all is the dimension ""."""
    use = amount.unit or unit
    if use is None:
        return "", amount.number
    if use in _UNITS:
        dimension, factor = _UNITS[use]
        return dimension, amount.number * factor
    return f"unit:{use}", amount.number


def number_text(number: float) -> str:
    """16.0 -> "16", 23.8 -> "23.8", 0.16500 -> "0.165". Never raises, even on infinity or NaN."""
    if not math.isfinite(number):
        return str(number)
    if number == int(number):
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def rupees(amount: float) -> str:
    """₹ with Indian digit grouping: 94990 -> "₹94,990", 124990 -> "₹1,24,990". Paise only when they are not zero.

    Never raises: an amount too large to group, or not a number at all, is written as it is."""
    if not math.isfinite(amount) or abs(amount) >= 1e15:
        return f"₹{number_text(amount)}"
    exact = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, paise = divmod(exact, 1)
    digits = str(int(whole))
    head, tail = digits[:-3], digits[-3:]
    grouped = _PAIRS.sub(r"\1,", head) + "," + tail if head else tail
    return f"₹{grouped}" + (f".{int(paise * 100):02d}" if paise else "")


def money(amount: float, currency: str) -> str:
    return rupees(amount) if currency == "INR" else f"{currency} {number_text(amount)}"
