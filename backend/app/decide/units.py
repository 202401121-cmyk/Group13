"""Numbers, units and words for the decide stage (US-17). Plain code: nothing here asks a model anything.

A resolved value or a rule's value may be a bare number (16), or text with a unit ("16 GB", "0.28 kg", "₹84,990",
"68.6 cm"). `quantity` reads either. Units convert within their dimension, by decimal factors: 1 TB is 1,000 GB, as
makers label storage. A unit we don't know compares only with itself, never with anything else.
"""

from __future__ import annotations

import math
import re
import unicodedata
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

_CURRENCY = r"(?P<currency>(?<![A-Za-z])(?:₹|rs\.?|inr|mrp|\$|usd))"
# A whole number only: never the 7 of "i7", the 5 of "DDR5", or the 990 left after a comma or a point.
_NUMBER = r"(?<![A-Za-z\d,.])(?:\d[\d,]*(?:\.\d+)?|\.\d+)"
_ONE_NUMBER = re.compile(rf"^(?P<before>.*?){_CURRENCY}?\s*(?P<number>{_NUMBER})\s*(?P<after>.*)$", re.IGNORECASE)
_ANY_NUMBER = re.compile(_NUMBER)
# A currency or price word written against its number ("Rs84,990", "MRP84,990") gets a space, so the number is whole.
_GLUED_PRICE = re.compile(r"(?<![A-Za-z])(₹|rs\.?|inr|mrp|\$|usd)(?=\d)", re.IGNORECASE)
_UNIT_WORD = re.compile(r'^(?:[A-Za-z]+|"|″|”)')
_PAIRS = re.compile(r"(\d)(?=(\d{2})+$)")  # where Indian grouping puts its commas, two digits at a time

# Money (US-17, DR-01): a price is never read smaller than its text means. Text is money when a mark stands next to
# its number: ₹, Rs, Rs., INR, MRP, rupee(s), $ or USD, just before it, or just after it or its scale word. A mark
# anywhere else ("RS-232 port") leaves the text as it was read before.
_MONEY_MARK = re.compile(r"(?<![A-Za-z])(?:₹|rs\.?|inr|mrp|rupees?|\$|usd)(?![A-Za-z])", re.IGNORECASE)
_DOTTED_MRP = re.compile(r"(?<![A-Za-z])m\.\s?r\.\s?p\.?", re.IGNORECASE)  # "M.R.P." is written MRP
_LEADING_MARK = re.compile(r"^(?:₹|\$|[A-Za-z]+\.?)")  # the mark or word right after a number
# How many rupees one of each scale word is (DECIDED 1, and the words intake reads in a budget). Read only in money,
# or under the price's own key. Whole numbers, so 1.15 lakh is exactly ₹1,15,000.
_SCALES: dict[str, int] = {
    "k": 1_000,
    "thousand": 1_000,
    "thousands": 1_000,
    "hazaar": 1_000,
    "hazar": 1_000,
    "hajar": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "l": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
    "crs": 10_000_000,
}
# Size words the list above doesn't name: never guessed, so the amount is Unknown rather than read too small.
_UNSURE_SCALES = frozenset(
    {"m", "mn", "mil", "million", "millions", "b", "bn", "bil", "billion", "billions", "tn", "trillion"}
    | {"hundred", "hundreds", "grand"}
)
# Text that is not a selling price (DECIDED 2, 3), so it is no price at all:
# - a discount, saving or offer: off, discount, cashback, coupon, exchange bonus, an "effective" price, money taken off;
# - an EMI or an amount per period: per month, /mo, p.m., a month, yearly, p.a., per day;
# - an estimate or a bound, which is a range with one end: around, approx., under, up to, from, onwards, "25k+".
# A range with both ends has two numbers, so the reader already gives nothing for it.
_NOT_A_PRICE = re.compile(
    r"\b(?:off|discount|save|saving|savings|cashback|coupons?|rebate|bonus|exchange|effective|less"
    r"|emis?|monthly|yearly|annually|weekly|daily|approx|approximately|around|about|roughly|nearly|expected"
    r"|estimated|est|under|below|upto|within|max|maximum|min|minimum|onwards?|from|starting|starts)\b"
    r"|\b(?:up\s+to|and\s+above|or\s+(?:more|less)|at\s+least|price\s+drop|bank\s+offers?|cash\s+back)\b"
    r"|\ba\s+(?:month|year|week|day)\b|\bper[\s-]*(?:month|mo|mth|year|yr|annum|week|wk|day)\b"
    r"|/\s*(?:month|mo|mth|m|year|yr|annum|week|wk|day)\b|\bp\.\s?[ma]\.|\bp[ma]\b|[~≈+]"
    # money taken off: a minus at the start, glued to its amount, or after the mark ("-₹2,000", "₹-2,000"). A dash
    # with spaces round it after a word ("Price - ₹24,999") only separates the two.
    r"|^\s*[-−–—]|\(\s*[-−–—]|\s[-−–—](?:₹|rs\.?|inr)?\d|(?:₹|rs\.?|inr)\s*[-−–—]\s*\d",
    re.IGNORECASE,
)
# Another currency anywhere in a price: never read as rupees (DECIDED 3).
_OTHER_CURRENCY = re.compile(
    r"(?<![A-Za-z])(?:usd|us\$|eur|euros?|gbp|pounds?|aed|dirhams?|sgd|aud|cad|jpy|yen|cny|yuan|sar|riyals?"
    r"|dollars?|bucks)(?![A-Za-z])",
    re.IGNORECASE,
)


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
    text = _GLUED_PRICE.sub(r"\1 ", _DOTTED_MRP.sub("MRP", value.strip()))
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
    if match["currency"] or _mark_after(after) is not None:
        glued = text[match.end("number") : match.end("number") + 1].isalpha()
        return _money(text, number, _mark_unit(match["currency"]), before, after, glued)
    word = _UNIT_WORD.match(after)
    unit = word.group(0) if word else ""
    return Quantity(number, unit_key(unit), plain=not before and after == unit)


def _is_mark(word: str) -> bool:
    return bool(word) and (
        _MONEY_MARK.fullmatch(word) is not None or _MONEY_MARK.fullmatch(word.rstrip(".")) is not None
    )


def _mark_unit(mark: str | None) -> str | None:
    """Our unit for a money mark: MRP is rupees too ("inr"), "$" is "usd"."""
    if not mark:
        return None
    return "inr" if mark.casefold().rstrip(".") == "mrp" else unit_key(mark)


def _first(after: str) -> str:
    """The mark or word right after a number: "₹", "$", "k", "Rs.", "lakh"; or "" for anything else."""
    found = _LEADING_MARK.match(after)
    return found.group(0) if found else ""


def _mark_after(after: str) -> str | None:
    """A money mark right after a number, or right after its scale word ("24,999 INR", "25k INR", "24,999₹")."""
    word = _first(after)
    if _is_mark(word):
        return word
    if word.casefold().rstrip(".") in _SCALES:
        rest = _first(after[len(word) :].strip())
        return rest if _is_mark(rest) else None
    return None


def _scaled(number: float, scale: int) -> float:
    """number x scale, exactly as written: 1.15 lakh is 115000, never 114999.99999999999."""
    return float(Decimal(repr(number)) * scale)


def _money(text: str, number: float, unit: str | None, before: str, after: str, glued: bool) -> Quantity | None:
    """An amount of money, or None when the text isn't surely a selling price (DECIDED 1 to 3). `unit` is the mark
    before the number; a mark after it ("24,999 INR") counts the same. "₹25k" is ₹25,000. A size word the list
    doesn't name, or a letter glued to the number that is no scale word ("₹2m"), makes it Unknown, never smaller."""
    if _NOT_A_PRICE.search(text):
        return None
    word = _first(after)
    scale = _SCALES.get(word.casefold().rstrip(".")) if word else None
    if scale is not None:
        number = _scaled(number, scale)
        after = after[len(word) :].strip()
        word = _first(after)
    if _is_mark(word):
        unit = unit or _mark_unit(word)
        after = after[len(word) :].strip()
    elif word.casefold().rstrip(".") in _UNSURE_SCALES or (glued and word and scale is None):
        return None
    if (scale is not None and unit not in (None, "inr")) or not math.isfinite(number):
        return None  # only rupees are scaled: another currency's "k" is never guessed
    return Quantity(number, unit, plain=not before and not after.strip("/-"))


def _other_currency(text: str) -> bool:
    """Does a price name a currency other than rupees, by its code, name or symbol ($, €, £...)?"""
    return _OTHER_CURRENCY.search(text) is not None or any(
        unicodedata.category(ch) == "Sc" and ch != "₹" for ch in text
    )


def rupee_price(value: object) -> float | None:
    """A price_inr value as a selling price in rupees, or None (Unknown) when it isn't surely one (US-17, DR-01).

    A bare number is rupees, as it always was; zero or less is no price. Text is read by quantity(): rupees, or no unit
    at all. Under the price's own key a bare scale word is money too, so "25k" is ₹25,000 and "1.2 lakh" ₹1,20,000; any
    other unit ("120k mAh") or currency ("$299", "€999") is Unknown. A discount, an EMI, a range, a bound or an
    estimate is never a price.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    if not isinstance(value, str) or _NOT_A_PRICE.search(value) or _other_currency(value):
        return None
    amount = quantity(value)
    if amount is None or not math.isfinite(amount.number) or amount.number <= 0:
        return None
    if amount.unit in (None, "inr"):
        return amount.number
    scale = _SCALES.get(amount.unit) if amount.plain else None
    return _scaled(amount.number, scale) if scale is not None else None


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
