"""XSD datatypes: which texts are valid values, and which Python values they stand for.

Every value of a snippet is a text: a CSV cell, a JSON string, the lexical form of a literal, or
a Parquet or Arrow value written in the lexical form of its type. A field's ``tss:datatype``
says how to read that text. rdftss checks the lexical forms the specification lists, and keeps
every text exactly as written: ``" 12 "`` is no ``xsd:double``, so it is reported, not read as 12.

======================================  ==========================================
Datatype                                Python value
======================================  ==========================================
``xsd:double``, ``xsd:float``           :class:`float` (``INF``, ``-INF``, ``NaN``)
``xsd:decimal``                         :class:`decimal.Decimal`
``xsd:integer`` and its subtypes        :class:`int` (checked against the type's range)
``xsd:boolean``                         :class:`bool` (``true``, ``false``, ``1``, ``0``)
``xsd:dateTime``, ``xsd:dateTimeStamp``  :class:`datetime.datetime` (a real date)
``xsd:date``                            :class:`datetime.date` (a real date)
``xsd:time``                            :class:`datetime.time`
anything else (``xsd:string``, ...)     :class:`str`, any text
======================================  ==========================================

>>> from rdftss.xsd import read_value
>>> from rdftss.vocab import XSD
>>> read_value("4810.5", XSD.double)
4810.5
>>> read_value(" 12 ", XSD.double)
Traceback (most recent call last):
...
ValueError: ' 12 ' is not an xsd:double
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from rdflib import URIRef
from rdflib.namespace import RDF

from .times import days_in_month, format_time, parse_time
from .vocab import XSD

#: The XSD integer types and their ranges (``None`` is unbounded).
INTEGER_RANGES: dict[URIRef, tuple[int | None, int | None]] = {
    XSD.integer: (None, None),
    XSD.long: (-2**63, 2**63 - 1),
    XSD.int: (-2**31, 2**31 - 1),
    XSD.short: (-2**15, 2**15 - 1),
    XSD.byte: (-2**7, 2**7 - 1),
    XSD.nonNegativeInteger: (0, None),
    XSD.positiveInteger: (1, None),
    XSD.nonPositiveInteger: (None, 0),
    XSD.negativeInteger: (None, -1),
    XSD.unsignedLong: (0, 2**64 - 1),
    XSD.unsignedInt: (0, 2**32 - 1),
    XSD.unsignedShort: (0, 2**16 - 1),
    XSD.unsignedByte: (0, 2**8 - 1),
}
#: The unsigned XSD integer types.
UNSIGNED_TYPES = frozenset({XSD.unsignedLong, XSD.unsignedInt, XSD.unsignedShort, XSD.unsignedByte})
#: ``xsd:double`` and ``xsd:float``.
FLOATING_TYPES = frozenset({XSD.double, XSD.float})
#: The numeric datatypes: only a numeric field has a minimum, maximum, mean and median.
NUMERIC_TYPES = frozenset(set(INTEGER_RANGES) | FLOATING_TYPES | {XSD.decimal})
#: The datatypes of a date and time.
DATE_TIME_TYPES = frozenset({XSD.dateTime, XSD.dateTimeStamp})

_INTEGER = re.compile(r"[+-]?[0-9]+")
_DECIMAL = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)")
_DOUBLE = re.compile(r"[+-]?(([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?|INF)|NaN")
_BOOLEANS = {"true": True, "false": False, "1": True, "0": False}
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(Z|[+-][0-9]{2}:[0-9]{2})?")
_TIME = re.compile(r"([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})?")

_XSD_NS = str(XSD)


def short_name(datatype) -> str:
    """A short name for a datatype in messages: ``xsd:double`` or the full IRI."""
    if datatype is None:
        return "(no datatype)"
    text = str(datatype)
    if text.startswith(_XSD_NS):
        return "xsd:" + text[len(_XSD_NS):]
    if datatype == RDF.langString:
        return "rdf:langString"
    return f"<{text}>"


def is_numeric(datatype) -> bool:
    """Whether a datatype is numeric: ``xsd:double``, ``xsd:float``, ``xsd:decimal`` or an XSD integer type."""
    return datatype in NUMERIC_TYPES


def is_integer(datatype) -> bool:
    """Whether a datatype is ``xsd:integer`` or one of its subtypes, such as ``xsd:int``."""
    return datatype in INTEGER_RANGES


def read_value(text: str, datatype):
    """The Python value that ``text`` stands for in ``datatype`` (see the table above).

    Raises :class:`ValueError`, with the reason, when the text is not in the lexical form of
    the datatype. A datatype that is not in the table accepts any text and gives it back.
    """
    if datatype in INTEGER_RANGES:
        if not _INTEGER.fullmatch(text):
            raise ValueError(f"{text!r} is not an {short_name(datatype)}")
        value = int(text)
        low, high = INTEGER_RANGES[datatype]
        if (low is not None and value < low) or (high is not None and value > high):
            span = f"{low if low is not None else '-infinity'} to {high if high is not None else 'infinity'}"
            raise ValueError(f"{text!r} is not an {short_name(datatype)}: it is outside {span}")
        return value
    if datatype in FLOATING_TYPES:
        if not _DOUBLE.fullmatch(text):
            raise ValueError(f"{text!r} is not an {short_name(datatype)}")
        return float(text)
    if datatype == XSD.decimal:
        if not _DECIMAL.fullmatch(text):
            raise ValueError(f"{text!r} is not an xsd:decimal")
        return Decimal(text)
    if datatype == XSD.boolean:
        if text not in _BOOLEANS:
            raise ValueError(f"{text!r} is not an xsd:boolean (true, false, 1 or 0)")
        return _BOOLEANS[text]
    if datatype == XSD.dateTimeStamp:
        try:
            return parse_time(text, require_timezone=True).datetime
        except ValueError as error:
            raise ValueError(f"{text!r} is not an xsd:dateTimeStamp: {error}") from None
    if datatype == XSD.dateTime:
        try:
            return parse_time(text, require_timezone=False).datetime
        except ValueError as error:
            raise ValueError(f"{text!r} is not an xsd:dateTime: {error}") from None
    if datatype == XSD.date:
        return _read_date(text)
    if datatype == XSD.time:
        return _read_time(text)
    return text


def check(text: str, datatype) -> None:
    """Raise :class:`ValueError` when ``text`` is not in the lexical form of ``datatype``."""
    read_value(text, datatype)


def _read_date(text: str) -> date:
    match = _DATE.fullmatch(text)
    if match is None:
        raise ValueError(f"{text!r} is not an xsd:date such as 2024-05-12")
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if year == 0 or not 1 <= month <= 12 or not 1 <= day <= days_in_month(year, month):
        raise ValueError(f"{text!r} is not an xsd:date: that day does not exist")
    return date(year, month, day)


def _read_time(text: str) -> time:
    match = _TIME.fullmatch(text)
    if match is None:
        raise ValueError(f"{text!r} is not an xsd:time such as 08:15:00")
    hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3))
    fraction, zone = match.group(4), match.group(5)
    if hour == 24 and minute == 0 and second == 0 and (not fraction or Decimal(fraction) == 0):
        hour = 0
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"{text!r} is not an xsd:time: that time does not exist")
    tzinfo = None
    if zone == "Z":
        tzinfo = timezone.utc
    elif zone:
        offset = int(zone[1:3]) * 3600 + int(zone[4:6]) * 60
        tzinfo = timezone(timedelta(seconds=offset if zone[0] == "+" else -offset))
    micro = int(fraction[1:7].ljust(6, "0")) if fraction else 0
    return time(hour, minute, second, micro, tzinfo=tzinfo)


def decimal_text(value: Decimal) -> str:
    """A decimal in plain notation, never with an exponent: ``Decimal("1E+3")`` gives ``"1000"``."""
    if value.is_nan():
        return "NaN"
    if value.is_infinite():
        return "INF" if value > 0 else "-INF"
    return format(value, "f")


def float_text(value: float) -> str:
    """The XSD lexical form of a float: the shortest text that reads back as the same number,
    with ``INF``, ``-INF`` and ``NaN`` for the special values."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "INF" if value > 0 else "-INF"
    return repr(float(value))


def lexical_form(value, datatype=None) -> str:
    """The XSD text of a Python value, the inverse of :func:`read_value`.

    >>> lexical_form(True)
    'true'
    >>> lexical_form(float("inf"))
    'INF'
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return float_text(value)
    if isinstance(value, Decimal):
        if datatype in FLOATING_TYPES:
            return float_text(float(value))
        return decimal_text(value)
    if isinstance(value, datetime):
        second = Decimal(value.second) + Decimal(value.microsecond) / Decimal(1_000_000)
        parts = (value.year, value.month, value.day, value.hour, value.minute, second)
        zone = None
        if value.tzinfo is not None:
            offset = value.utcoffset()
            seconds = int(offset.total_seconds())
            if seconds == 0:
                zone = "Z"
            else:
                sign = "+" if seconds > 0 else "-"
                hours, minutes = divmod(abs(seconds) // 60, 60)
                zone = f"{sign}{hours:02d}:{minutes:02d}"
        return format_time(parts, zone)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
