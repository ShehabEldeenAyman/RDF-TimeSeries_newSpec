"""Times, durations and the time grid.

A time of a source is an ``xsd:dateTime`` with a timezone (or an ``xsd:dateTimeStamp``, which
says the same): a date and a time of day, such as ``2024-05-12T08:00:00Z`` or
``2024-05-12T10:00:00+02:00``. Times are compared as the
moments they stand for, so those two are the same time.

Python's :class:`~datetime.datetime` stops at microseconds, while an XSD time may have more
fraction digits (and Parquet and Arrow timestamps have nanoseconds). So rdftss keeps three
things for every time (see :class:`Time`):

- ``text``: the time exactly as written;
- ``datetime``: a timezone-aware :class:`~datetime.datetime`, for convenience;
- ``instant``: the exact number of seconds since 1970-01-01T00:00:00Z, as a
  :class:`~decimal.Decimal`. Times are compared, sorted and checked for duplicates by it.

A :class:`Duration` is an ``xsd:duration`` such as ``PT15M`` or ``P1M``. :func:`add_duration`
adds one to a time as XML Schema does, which is how the steps of a time grid are computed:
step n is at ``tss:timeBase`` + n × ``tss:timeStep``.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal, localcontext

_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_DATE_TIME = re.compile(
    r"(-?)([0-9]{4,})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})?"
)
_DURATION = re.compile(
    r"(-)?P(?:([0-9]+)Y)?(?:([0-9]+)M)?(?:([0-9]+)D)?"
    r"(?:(T)(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+(?:\.[0-9]*)?|\.[0-9]+)S)?)?"
)


def days_in_month(year: int, month: int) -> int:
    """The number of days of a month (28 to 31), with leap years."""
    return calendar.monthrange(year, month)[1]


@dataclass(frozen=True)
class Time:
    """A time: its text, a datetime and its exact instant.

    - ``text``: the time exactly as written, or as computed (on a time grid, or from a Parquet
      or Arrow timestamp).
    - ``datetime``: a :class:`~datetime.datetime`, timezone-aware when the text has a timezone.
      Fraction digits beyond microseconds are dropped.
    - ``instant``: the exact seconds since 1970-01-01T00:00:00Z (a :class:`~decimal.Decimal`),
      or ``None`` for a time without a timezone, which is no moment.

    Two times are equal when they are the same moment, whatever their texts.
    """

    text: str = field(compare=False)
    datetime: datetime = field(compare=False)
    instant: Decimal | None
    # The wall-clock parts (year, month, day, hour, minute, second as a Decimal), the timezone
    # as written ("Z", "+02:00" or None) and its offset in seconds. Used for duration arithmetic.
    parts: tuple = field(compare=False, repr=False)
    zone: str | None = field(compare=False, repr=False)
    offset: int = field(compare=False, repr=False, default=0)

    def __lt__(self, other: "Time") -> bool:
        return self.instant < other.instant

    def __le__(self, other: "Time") -> bool:
        return self.instant <= other.instant

    def __str__(self) -> str:
        return self.text


def parse_time(text: str, *, require_timezone: bool = True) -> Time:
    """Read an ``xsd:dateTime`` text, checking that it is a real date and time.

    With ``require_timezone=True`` (the default) the text must have a timezone, as every time of
    a source has: it must end with ``Z`` or an offset such as ``+02:00``.

    Raises :class:`ValueError`, with the reason, for a text that is not a valid time.

    >>> parse_time("2024-05-12T10:00:00+02:00").instant == parse_time("2024-05-12T08:00:00Z").instant
    True
    """
    match = _DATE_TIME.fullmatch(text)
    if match is None:
        raise ValueError(f"{text!r} is not a date and time such as 2024-05-12T08:00:00Z")
    sign, year_text, month_text, day_text, hour_text, minute_text, second_text, fraction, zone = match.groups()
    if require_timezone and zone is None:
        raise ValueError(f"{text!r} has no timezone: add Z or an offset such as +02:00")
    if sign or len(year_text) > 4 or year_text == "0000":
        raise ValueError(f"{text!r}: years before 0001 and after 9999 are not supported")
    year, month, day = int(year_text), int(month_text), int(day_text)
    hour, minute, second = int(hour_text), int(minute_text), int(second_text)
    if not 1 <= month <= 12:
        raise ValueError(f"{text!r} is not a valid time: there is no month {month_text}")
    if not 1 <= day <= days_in_month(year, month):
        raise ValueError(f"{text!r} is not a valid time: {year_text}-{month_text} has no day {day_text}")
    fraction_value = Decimal(fraction) if fraction else Decimal(0)
    if hour == 24:
        if minute != 0 or second != 0 or fraction_value != 0:
            raise ValueError(f"{text!r} is not a valid time: 24 is only allowed as 24:00:00")
    elif hour > 23:
        raise ValueError(f"{text!r} is not a valid time: there is no hour {hour_text}")
    if minute > 59:
        raise ValueError(f"{text!r} is not a valid time: there is no minute {minute_text}")
    if second > 59:
        raise ValueError(f"{text!r} is not a valid time: there is no second {second_text}")
    offset = 0
    tzinfo = None
    if zone is not None:
        if zone == "Z":
            tzinfo = timezone.utc
        else:
            hours, minutes = int(zone[1:3]), int(zone[4:6])
            if hours > 14 or minutes > 59 or (hours == 14 and minutes > 0):
                raise ValueError(f"{text!r} is not a valid time: the offset {zone} is out of range (-14:00 to +14:00)")
            offset = (hours * 3600 + minutes * 60) * (-1 if zone[0] == "-" else 1)
            tzinfo = timezone(timedelta(seconds=offset))

    # 24:00:00 is the first moment of the next day
    local_seconds = (date(year, month, day).toordinal() - _EPOCH_ORDINAL) * 86400 + hour * 3600 + minute * 60 + second
    try:
        moment = datetime(year, month, day, tzinfo=tzinfo) + timedelta(
            hours=hour, minutes=minute, seconds=second, microseconds=_microseconds(fraction))
    except OverflowError:
        raise ValueError(f"{text!r}: years after 9999 are not supported") from None
    with localcontext() as context:
        context.prec = 60
        instant = Decimal(local_seconds - offset) + fraction_value if zone is not None else None
        parts = _parts_from_local(Decimal(local_seconds) + fraction_value)
    return Time(text, moment, instant, parts, zone, offset)


def _microseconds(fraction: str | None) -> int:
    """The microseconds of a fraction text such as ".123456789" (digits after 6 are dropped)."""
    if not fraction:
        return 0
    return int(fraction[1:7].ljust(6, "0"))


def _parts_from_local(local: Decimal) -> tuple:
    """Wall-clock parts from local seconds since 1970-01-01T00:00:00 (of the time's own timezone)."""
    whole = int(local.to_integral_value(rounding=ROUND_FLOOR))
    fraction = local - whole
    days, rest = divmod(whole, 86400)
    day = date.fromordinal(days + _EPOCH_ORDINAL)
    hour, rest = divmod(rest, 3600)
    minute, second = divmod(rest, 60)
    return (day.year, day.month, day.day, hour, minute, Decimal(second) + fraction)


def _local_seconds(parts: tuple) -> Decimal:
    year, month, day, hour, minute, second = parts
    return Decimal((date(year, month, day).toordinal() - _EPOCH_ORDINAL) * 86400 + hour * 3600 + minute * 60) + second


def format_time(parts: tuple, zone: str | None) -> str:
    """The lexical form of wall-clock parts and a timezone text, such as ``2024-05-12T08:15:00Z``.
    The seconds get a fraction only when they have one, without trailing zeros."""
    year, month, day, hour, minute, second = parts
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{seconds_text(second)}{zone or ''}"


def seconds_text(second: Decimal) -> str:
    """Seconds as two digits and, when needed, a fraction without trailing zeros: ``05``, ``05.25``."""
    whole = int(second)
    fraction = second - whole
    if fraction == 0:
        return f"{whole:02d}"
    digits = format(fraction, "f").split(".")[1].rstrip("0")
    return f"{whole:02d}.{digits}"


def time_from_epoch(value: int, per_second: int, *, utc: bool) -> str:
    """The lexical form of a count of seconds, milliseconds, microseconds or nanoseconds since
    1970-01-01T00:00:00 (``per_second`` is 1, 1000, 10**6 or 10**9), as Parquet and Arrow store
    timestamps. With ``utc=True`` the text ends with ``Z``; without, it has no timezone.
    The fraction is written only when there is one, without trailing zeros."""
    seconds, rest = divmod(value, per_second)
    try:
        moment = datetime(1970, 1, 1) + timedelta(seconds=seconds)
    except OverflowError:
        raise ValueError(f"the timestamp {value} is outside the years 0001-9999") from None
    text = (f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
            f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}")
    if rest:
        width = len(str(per_second)) - 1
        text += "." + str(rest).rjust(width, "0").rstrip("0")
    return text + ("Z" if utc else "")


def time_of_day_from_count(value: int, per_second: int) -> str:
    """The lexical form ``hh:mm:ss[.fraction]`` of a time of day stored as a count of seconds,
    milliseconds, microseconds or nanoseconds since midnight."""
    seconds, rest = divmod(value, per_second)
    hour, seconds = divmod(seconds, 3600)
    minute, second = divmod(seconds, 60)
    text = f"{hour:02d}:{minute:02d}:{second:02d}"
    if rest:
        width = len(str(per_second)) - 1
        text += "." + str(rest).rjust(width, "0").rstrip("0")
    return text


def utc_text(instant: Decimal) -> str:
    """The time of an instant in UTC, such as ``2024-05-12T08:00:00Z``."""
    return format_time(_parts_from_local(instant), "Z")


def time_from_instant(instant: Decimal) -> Time:
    """A :class:`Time` in UTC for an instant."""
    return parse_time(utc_text(instant))


def to_instant(value) -> Decimal:
    """The instant of a time given by a user: a :class:`Time`, a ``datetime``, a ``date``, or a
    text such as ``"2024-05-12T08:00:00Z"``, ``"2024-05-12T08:00"`` or ``"2024-05-12"``.

    A time without a timezone, and a date, are taken as UTC.
    """
    if isinstance(value, Time):
        if value.instant is None:
            return to_instant(value.datetime)
        return value.instant
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        delta = value - _EPOCH
        with localcontext() as context:
            context.prec = 60
            return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1_000_000)
    if isinstance(value, date):
        return Decimal((value.toordinal() - _EPOCH_ORDINAL) * 86400)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
            return to_instant(date.fromisoformat(text))
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}", text):
            text += ":00"
        parsed = parse_time(text, require_timezone=False)
        if parsed.instant is None:
            return to_instant(parsed.datetime)
        return parsed.instant
    raise TypeError(f"Expected a time (a datetime, a date or a text such as '2024-05-12T08:00:00Z'), not {value!r}")


def instant_to_datetime(instant: Decimal) -> datetime:
    """A UTC datetime for an instant (fraction digits beyond microseconds are dropped)."""
    whole = int(instant.to_integral_value(rounding=ROUND_FLOOR))
    micro = int((instant - whole) * 1_000_000)
    return _EPOCH + timedelta(seconds=whole, microseconds=micro)


@dataclass(frozen=True)
class Duration:
    """An ``xsd:duration``, such as ``PT15M``, ``P1D`` or ``P1M``.

    Years and months are calendar steps: adding ``P1M`` to 2021-01-31 gives 2021-02-28.
    ``total_months`` and ``total_seconds`` are the two parts XML Schema adds separately.
    """

    text: str
    negative: bool
    years: int
    months: int
    days: int
    hours: int
    minutes: int
    seconds: Decimal

    @classmethod
    def parse(cls, text: str) -> "Duration":
        """Read an ``xsd:duration`` text. Raises :class:`ValueError` for an invalid one."""
        match = _DURATION.fullmatch(text)
        if match is None or text in ("P", "-P") or text.endswith("T"):
            raise ValueError(f"{text!r} is not an xsd:duration such as PT15M, P1D or P1M")
        sign, years, months, days, t, hours, minutes, seconds = match.groups()
        if t and hours is None and minutes is None and seconds is None:
            raise ValueError(f"{text!r} is not an xsd:duration: T must be followed by hours, minutes or seconds")
        return cls(text, bool(sign), int(years or 0), int(months or 0), int(days or 0),
                   int(hours or 0), int(minutes or 0), Decimal(seconds) if seconds else Decimal(0))

    @classmethod
    def of(cls, months: int = 0, seconds: Decimal | int | float | timedelta = 0) -> "Duration":
        """A positive duration from a number of months and a number of seconds, written in its
        shortest form: ``Duration.of(seconds=900).text == "PT15M"``."""
        if isinstance(seconds, timedelta):
            seconds = Decimal(seconds.days * 86400 + seconds.seconds) + Decimal(seconds.microseconds) / 1_000_000
        seconds = Decimal(str(seconds)) if isinstance(seconds, float) else Decimal(seconds)
        if months < 0 or seconds < 0:
            raise ValueError("Duration.of makes positive durations only")
        years, months = divmod(months, 12)
        whole = int(seconds)
        fraction = seconds - whole
        days, rest = divmod(whole, 86400)
        hours, rest = divmod(rest, 3600)
        minutes, secs = divmod(rest, 60)
        text = "P"
        text += f"{years}Y" if years else ""
        text += f"{months}M" if months else ""
        text += f"{days}D" if days else ""
        time_part = ""
        time_part += f"{hours}H" if hours else ""
        time_part += f"{minutes}M" if minutes else ""
        if secs or fraction:
            value = Decimal(secs) + fraction
            if value == value.to_integral_value():
                time_part += f"{int(value)}S"
            else:
                time_part += format(value.normalize(), "f") + "S"
        if time_part:
            text += "T" + time_part
        if text == "P":
            text = "PT0S"
        return cls.parse(text)

    @property
    def total_months(self) -> int:
        """Years and months as a number of months (negative for a negative duration)."""
        months = self.years * 12 + self.months
        return -months if self.negative else months

    @property
    def total_seconds(self) -> Decimal:
        """Days, hours, minutes and seconds as a number of seconds (negative for a negative duration)."""
        seconds = Decimal(self.days * 86400 + self.hours * 3600 + self.minutes * 60) + self.seconds
        return -seconds if self.negative else seconds

    @property
    def is_positive(self) -> bool:
        """Whether the duration is more than zero."""
        return not self.negative and (self.total_months > 0 or self.total_seconds > 0)

    @property
    def has_calendar_part(self) -> bool:
        """Whether the duration has years or months, whose length in seconds varies."""
        return self.total_months != 0

    def __str__(self) -> str:
        return self.text


def add_duration(time: Time, months: int, seconds: Decimal | int) -> Time:
    """Add months, then seconds, to a time, as XML Schema adds a duration to a dateTime.

    The months are added first, and a day past the end of the new month becomes its last day
    (2021-01-31 + 1 month is 2021-02-28). Then the seconds are added. The result keeps the
    timezone of ``time``.
    """
    year, month, day, hour, minute, second = time.parts
    total = year * 12 + (month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    if not 1 <= year <= 9999:
        raise ValueError("the result is outside the years 0001-9999, which are not supported")
    day = min(day, days_in_month(year, month))
    with localcontext() as context:
        context.prec = 60
        local = _local_seconds((year, month, day, hour, minute, second)) + Decimal(seconds)
        try:
            parts = _parts_from_local(local)
        except (OverflowError, ValueError):
            raise ValueError("the result is outside the years 0001-9999, which are not supported") from None
        text = format_time(parts, time.zone)
        instant = local - time.offset if time.zone is not None else None
    whole = int(parts[5])
    micro = int((parts[5] - whole) * 1_000_000)
    tzinfo = time.datetime.tzinfo
    moment = datetime(parts[0], parts[1], parts[2], parts[3], parts[4], whole, micro, tzinfo=tzinfo)
    return Time(text, moment, instant, parts, time.zone, time.offset)


@dataclass(frozen=True)
class TimeGridMath:
    """The arithmetic of a time grid: step n is at ``base`` + n × ``step``."""

    base: Time
    step: Duration

    def time_of(self, n: int) -> Time:
        """The time of step ``n``: each part of the step is multiplied by n, and the result is
        added to the base as XML Schema adds a duration to a dateTime."""
        return add_duration(self.base, n * self.step.total_months, n * self.step.total_seconds)

    def step_of(self, time: Time) -> int | None:
        """The step whose time is ``time``, or ``None`` when ``time`` is not on the grid
        (or lies before the base)."""
        if time.instant is None or self.base.instant is None:
            return None
        if time.instant < self.base.instant:
            return None
        if not self.step.has_calendar_part:
            quotient = (time.instant - self.base.instant) / self.step.total_seconds
            if quotient != quotient.to_integral_value():
                return None
            return int(quotient)
        # Calendar steps: estimate from the months between the two times, then check around it.
        b, t = self.base.parts, time.parts
        months_between = (t[0] - b[0]) * 12 + (t[1] - b[1])
        guess = max(0, months_between // self.step.total_months) if self.step.total_months else 0
        for n in range(max(0, guess - 2), guess + 3):
            candidate = self.time_of(n)
            if candidate.instant == time.instant:
                return n
            if candidate.instant > time.instant:
                break
        return None
