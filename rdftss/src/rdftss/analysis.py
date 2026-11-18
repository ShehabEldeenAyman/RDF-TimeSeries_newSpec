"""Analysing points: statistics, expected ranges, gaps, missing steps and resampling.

These functions work on :class:`~rdftss.points.Points`; you usually call them as methods::

    points = snippet.read()
    points.statistics("value")            # Statistics(count=85, missing=0, mean=4750.1, ...)
    points.outside_expected_range()       # values outside tss:minimum / tss:maximum
    points.gaps()                         # usual step 0:15:00, 3 gaps, longest first
    points.missing_steps()                # on a time grid: the steps without a row
    points.resample("1h", "value", how="mean")

The statistics use the definitions of the stored summaries (``tss:summary``): ``count`` is how
many points have a value, ``missing`` how many have none; minimum, maximum, mean and median only
exist for numeric fields; the median of an even number of values is the mean of the middle two.
Decimal and integer fields are computed exactly (as :class:`~decimal.Decimal`), double and float
fields with :func:`math.fsum`. A NaN makes the minimum, maximum, mean and median NaN.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

from . import xsd
from .times import Duration, instant_to_datetime

# ---------------------------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Statistics:
    """The statistics of one field's values, defined as the stored summaries define them.

    ``first_time`` and ``last_time`` are the times of the first and last point with a value
    (``first_time_text`` and ``last_time_text`` as written). ``minimum``, ``maximum``, ``mean``
    and ``median`` are ``None`` for a field that is not numeric, or that has no values."""

    field: object
    count: int
    missing: int
    first_time: datetime | None = None
    last_time: datetime | None = None
    first_time_text: str | None = None
    last_time_text: str | None = None
    minimum: object = None
    maximum: object = None
    mean: object = None
    median: object = None

    def as_dict(self) -> dict:
        """The numbers as a plain dict (times as texts)."""
        return {"field": self.field.name, "count": self.count, "missing": self.missing,
                "first_time": self.first_time_text, "last_time": self.last_time_text,
                "minimum": self.minimum, "maximum": self.maximum, "mean": self.mean, "median": self.median}

    def differences(self, summary=None) -> dict:
        """The attributes in which a stored summary (by default the field's ``tss:summary``)
        differs from these statistics, as ``{name: (stored, computed)}``. Attributes the summary
        does not state are not compared. An empty dict means the summary is up to date."""
        summary = summary if summary is not None else self.field.summary
        if summary is None:
            return {}
        pairs = [("count", summary.count, self.count), ("missing", summary.missing, self.missing),
                 ("first_time", summary.first_time, self.first_time_text),
                 ("last_time", summary.last_time, self.last_time_text),
                 ("observed_minimum", summary.observed_minimum, self.minimum),
                 ("observed_maximum", summary.observed_maximum, self.maximum),
                 ("mean", summary.mean, self.mean), ("median", summary.median, self.median)]
        found = {}
        for name, stored, computed in pairs:
            if stored is None:
                continue
            if name in ("first_time", "last_time"):
                same = computed is not None and stored.instant == _instant_of(computed)
                stored_shown = stored.text
            else:
                same = _same_number(stored, computed)
                stored_shown = stored
            if not same:
                found[name] = (stored_shown, computed)
        return found

    def __str__(self) -> str:
        lines = [f"Statistics of {self.field.name!r}:",
                 f"  count:   {self.count}", f"  missing: {self.missing}"]
        if self.first_time_text:
            lines += [f"  first:   {self.first_time_text}", f"  last:    {self.last_time_text}"]
        for name in ("minimum", "maximum", "mean", "median"):
            value = getattr(self, name)
            if value is not None:
                lines.append(f"  {name + ':':<8} {value}")
        return "\n".join(lines)


def _instant_of(text):
    from .times import to_instant

    return to_instant(text)


def _same_number(stored, computed) -> bool:
    if computed is None:
        return False
    try:
        if isinstance(stored, float) or isinstance(computed, float):
            a, b = float(stored), float(computed)
            if math.isnan(a) and math.isnan(b):
                return True
            return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
        return Decimal(stored) == Decimal(computed)
    except (TypeError, ValueError, ArithmeticError):
        return stored == computed


def _is_nan(value) -> bool:
    return (isinstance(value, float) and math.isnan(value)) or (isinstance(value, Decimal) and value.is_nan())


def mean_of(values: list, floating: bool):
    """The unweighted mean: with :func:`math.fsum` for floats, exactly (Decimal) otherwise."""
    if floating:
        return math.fsum(values) / len(values)
    with localcontext() as context:
        context.prec = 50
        return sum((Decimal(value) for value in values), Decimal(0)) / Decimal(len(values))


def median_of(values: list, floating: bool):
    """The middle value, or the mean of the two middle values when their number is even."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    if floating:
        return (ordered[middle - 1] + ordered[middle]) / 2
    with localcontext() as context:
        context.prec = 50
        return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / 2


def statistics(points, field) -> Statistics:
    """The :class:`Statistics` of ``field`` over ``points``."""
    count = missing = 0
    first = last = None
    numbers = []
    numeric = field.is_numeric
    for point in points:
        value = point.values.get(field)
        if value is None:
            missing += 1
            continue
        count += 1
        if first is None or point.instant < first.instant:
            first = point
        if last is None or point.instant > last.instant:
            last = point
        if numeric:
            numbers.append(value)
    result = dict(field=field, count=count, missing=missing)
    if first is not None:
        result.update(first_time=first.time, last_time=last.time,
                      first_time_text=first.time_text, last_time_text=last.time_text)
    if numeric and numbers:
        floating = field.datatype in xsd.FLOATING_TYPES
        if any(_is_nan(value) for value in numbers):
            nan = float("nan")
            result.update(minimum=nan, maximum=nan, mean=nan, median=nan)
        else:
            result.update(minimum=min(numbers), maximum=max(numbers),
                          mean=mean_of(numbers, floating), median=median_of(numbers, floating))
    return Statistics(**result)


# ---------------------------------------------------------------------------------------------
# Expected ranges
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RangeFlag:
    """A value outside its field's expected range (``tss:minimum``, ``tss:maximum``).

    The specification calls such a value suspicious, not wrong: it stays in the data."""

    point: object
    field: object
    value: object
    minimum: object
    maximum: object

    @property
    def side(self) -> str:
        """``"below"`` the minimum or ``"above"`` the maximum."""
        return "below" if self.minimum is not None and self.value < self.minimum else "above"

    def __str__(self) -> str:
        bound = self.minimum if self.side == "below" else self.maximum
        return f"{self.point.time_text}: {self.field.name} = {self.value} is {self.side} {bound}"


def outside_expected_range(points, field=None) -> list[RangeFlag]:
    """The values outside the expected range of their field, in time order. With ``field=None``,
    every numeric field that states a ``tss:minimum`` or ``tss:maximum`` is checked."""
    snippet = points.snippet
    if field is not None:
        fields = [field]
    else:
        fields = [f for f in snippet.fields if f.is_numeric and (f.minimum is not None or f.maximum is not None)]
    flags = []
    for point in points:
        for target in fields:
            value = point.values.get(target)
            if value is None or _is_nan(value):
                continue
            if (target.minimum is not None and value < target.minimum) or \
                    (target.maximum is not None and value > target.maximum):
                flags.append(RangeFlag(point, target, value, target.minimum, target.maximum))
    return flags


# ---------------------------------------------------------------------------------------------
# Gaps and missing steps
# ---------------------------------------------------------------------------------------------


def _timedelta(seconds: Decimal) -> timedelta:
    return timedelta(seconds=float(seconds)) if abs(seconds) > 10**9 else timedelta(
        seconds=int(seconds), microseconds=int((seconds - int(seconds)) * 1_000_000))


@dataclass(frozen=True)
class Gap:
    """A longer interval between two points than the usual step: from the point at ``start`` to
    the next point at ``end``. ``missing`` is ``length`` minus the usual step."""

    start: datetime
    end: datetime
    length: timedelta
    missing: timedelta

    def __str__(self) -> str:
        return f"{_utc(self.start)} .. {_utc(self.end)} ({describe_duration(self.length)})"


@dataclass(frozen=True)
class GapReport:
    """The usual time between two points (the most frequent interval), and every longer interval
    (a :class:`Gap`), longest first. ``total_missing`` adds up the time the gaps miss."""

    usual_step: timedelta | None
    gaps: list
    total_missing: timedelta

    @property
    def count(self) -> int:
        """The number of gaps."""
        return len(self.gaps)

    def longest(self, n: int = 5) -> list:
        """The ``n`` longest gaps."""
        return self.gaps[:n]

    def __str__(self) -> str:
        if self.usual_step is None:
            return "Too few points to find gaps."
        lines = [f"Usual step: {describe_duration(self.usual_step)}; {self.count} gap(s), "
                 f"{describe_duration(self.total_missing)} missing in total."]
        for gap in self.longest():
            lines.append(f"  {gap}")
        if self.count > 5:
            lines.append(f"  ... and {self.count - 5} shorter gap(s)")
        return "\n".join(lines)


def gaps(points) -> GapReport:
    """The usual step and the gaps of a series of points (in time order)."""
    instants = [point.instant for point in points]
    if len(instants) < 2:
        return GapReport(None, [], timedelta(0))
    intervals = [instants[i] - instants[i - 1] for i in range(1, len(instants))]
    counts = Counter(intervals)
    most = max(counts.values())
    usual = min(interval for interval, count in counts.items() if count == most)
    found = []
    total = Decimal(0)
    for i, interval in enumerate(intervals, 1):
        if interval > usual:
            found.append(Gap(points[i - 1].time, points[i].time, _timedelta(interval), _timedelta(interval - usual)))
            total += interval - usual
    found.sort(key=lambda gap: (-gap.length, gap.start))
    return GapReport(_timedelta(usual), found, _timedelta(total))


@dataclass(frozen=True)
class MissingSteps:
    """A run of steps of a time grid without a row: steps ``first`` to ``last`` (``count``
    steps), at the times ``start`` to ``end``."""

    first: int
    last: int
    start: datetime
    end: datetime

    @property
    def count(self) -> int:
        return self.last - self.first + 1

    def __str__(self) -> str:
        steps = f"step {self.first}" if self.count == 1 else f"steps {self.first}-{self.last}"
        return f"{steps} ({_utc(self.start)}" + ("" if self.count == 1 else f" .. {_utc(self.end)}") + ")"


def missing_steps(points, *, window: bool = False) -> list[MissingSteps]:
    """The runs of grid steps without a row, between the first and the last row (with
    ``window=True``: between the first and the last step inside the time window)."""
    snippet = points.snippet
    grid = snippet.time_grid
    if grid is None:
        raise ValueError(f"{snippet.name} has no time grid; use gaps() to find the gaps between its points")
    steps = sorted(point.step for point in points if point.step is not None)
    if not steps:
        return []
    first, last = steps[0], steps[-1]
    if window:
        if snippet.window.start is not None:
            first = min(first, _first_step_from(grid, snippet.window.start))
        if snippet.window.end is not None:
            last = max(last, _last_step_before(grid, snippet.window.end))
    present = set(steps)
    runs = []
    n = first
    while n <= last:
        if n in present:
            n += 1
            continue
        start = n
        while n <= last and n not in present:
            n += 1
        runs.append(MissingSteps(start, n - 1, grid.time_of(start).datetime, grid.time_of(n - 1).datetime))
    return runs


def _first_step_from(grid, time) -> int:
    """The first step at or after ``time`` (0 at the earliest)."""
    if not grid.step.has_calendar_part:
        quotient = (time.instant - grid.base.instant) / grid.step.total_seconds
        return max(0, int(quotient.to_integral_value(rounding="ROUND_CEILING")))
    n = 0
    while grid.time_of(n).instant < time.instant:
        n += 1
    return n


def _last_step_before(grid, time) -> int:
    """The last step before ``time``."""
    return _first_step_from(grid, time) - 1


# ---------------------------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------------------------

_SHORT_PERIODS = {
    "s": 1, "sec": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}
_SHORT_MONTHS = {"mo": 1, "month": 1, "months": 1, "y": 12, "year": 12, "years": 12}
# 1970-01-05 was a Monday: weeks are aligned to Mondays.
_MONDAY = Decimal(4 * 86400)


def parse_period(every) -> tuple[int, Decimal]:
    """A period as (months, seconds). ``every`` is a :class:`~datetime.timedelta`, a
    :class:`~rdftss.times.Duration`, an ``xsd:duration`` text (``"PT15M"``, ``"P1M"``) or a short
    text: ``"15min"``, ``"1h"``, ``"1d"``, ``"1w"``, ``"1 month"``, ``"1y"``, ``"hour"``, ``"day"``."""
    if isinstance(every, timedelta):
        seconds = Decimal(every.days * 86400 + every.seconds) + Decimal(every.microseconds) / 1_000_000
        months = 0
    else:
        text = str(every).strip()
        if text.startswith(("P", "-P")):
            duration = every if isinstance(every, Duration) else Duration.parse(text)
            months, seconds = duration.total_months, duration.total_seconds
        else:
            match = re.fullmatch(r"(\d*\.?\d*)\s*([A-Za-z]+)", text)
            if match is None:
                raise ValueError(f"{every!r} is not a period such as '15min', '1h', '1d', '1 month' or 'PT15M'")
            number = Decimal(match.group(1) or "1")
            unit = match.group(2).lower()
            if unit in _SHORT_PERIODS:
                months, seconds = 0, number * _SHORT_PERIODS[unit]
            elif unit in _SHORT_MONTHS:
                if number != number.to_integral_value():
                    raise ValueError(f"{every!r}: months and years must be whole numbers")
                months, seconds = int(number) * _SHORT_MONTHS[unit], Decimal(0)
            else:
                raise ValueError(f"{every!r}: unknown unit {match.group(2)!r}; use s, min, h, d, w, month or y")
    if months and seconds:
        raise ValueError(f"{every!r}: a period is either months and years, or days, hours, minutes and seconds")
    if months < 0 or seconds < 0 or (months == 0 and seconds == 0):
        raise ValueError(f"{every!r}: a period must be more than zero")
    return months, seconds


def period_key(point, months: int, seconds: Decimal) -> int:
    """The number of the period a point lies in: calendar months (in UTC) when ``months`` is set,
    else periods of ``seconds`` counted from 1970-01-01T00:00:00Z (weeks from a Monday)."""
    if months:
        moment = point.moment.datetime.astimezone(timezone.utc)
        return (moment.year * 12 + moment.month - 1) // months
    origin = _MONDAY if seconds % 604800 == 0 else Decimal(0)
    return int(((point.instant - origin) / seconds).to_integral_value(rounding="ROUND_FLOOR"))


def period_bounds(key: int, months: int, seconds: Decimal) -> tuple[datetime, datetime]:
    """The start and end (in UTC) of period number ``key`` (see :func:`period_key`)."""
    if months:
        start_month, end_month = key * months, (key + 1) * months
        return (datetime(start_month // 12, start_month % 12 + 1, 1, tzinfo=timezone.utc),
                datetime(end_month // 12, end_month % 12 + 1, 1, tzinfo=timezone.utc))
    origin = _MONDAY if seconds % 604800 == 0 else Decimal(0)
    return instant_to_datetime(origin + key * seconds), instant_to_datetime(origin + (key + 1) * seconds)


@dataclass(frozen=True)
class Bin:
    """One period of a resampled series: ``[start, end)``, the aggregated ``value`` (``None``
    when the period has no values) and the ``count`` of values in it."""

    start: datetime
    end: datetime
    value: object
    count: int

    def __str__(self) -> str:
        return f"{_utc(self.start)}  {self.value}  ({self.count} values)"


class Resampled(list):
    """The :class:`Bin` objects of a resampled field, in time order."""

    def __init__(self, bins, field, every, how):
        super().__init__(bins)
        self.field = field
        self.every = every
        self.how = how

    def to_pandas(self):
        """The bins as a pandas DataFrame with the columns start, end, value and count."""
        from .errors import require

        pandas = require("pandas", "Resampled.to_pandas", "pandas")
        return pandas.DataFrame({"start": [b.start for b in self], "end": [b.end for b in self],
                                 "value": [b.value for b in self], "count": [b.count for b in self]})

    def __str__(self) -> str:
        return "\n".join(str(item) for item in self)


_AGGREGATIONS = ("mean", "min", "max", "sum", "count", "median", "first", "last")


def resample(points, every, field, *, how: str = "mean", include_empty: bool = False) -> Resampled:
    """Group the values of ``field`` per period and aggregate each group.

    - ``every``: the period (see :func:`parse_period`). Fixed periods are aligned to UTC
      midnight (weeks to Mondays); months and years are calendar months and years in UTC.
    - ``how``: ``"mean"``, ``"min"``, ``"max"``, ``"sum"``, ``"count"``, ``"median"``,
      ``"first"`` or ``"last"``. Mean, sum and median need a numeric field.
    - ``include_empty``: also give the periods between the first and the last group that have
      no point (with ``value=None`` and ``count=0``).
    """
    if how not in _AGGREGATIONS:
        raise ValueError(f"how={how!r}: use one of {', '.join(_AGGREGATIONS)}")
    if how in ("mean", "sum", "median") and not field.is_numeric:
        raise ValueError(f"how={how!r} needs a numeric field; {field.name!r} is {xsd.short_name(field.datatype)}")
    months, seconds = parse_period(every)
    floating = field.datatype in xsd.FLOATING_TYPES
    groups: dict[int, list] = {}
    for point in points:
        values = groups.setdefault(period_key(point, months, seconds), [])
        value = point.values.get(field)
        if value is not None:
            values.append(value)
    keys = sorted(groups)
    if include_empty and keys:
        keys = list(range(keys[0], keys[-1] + 1))
    bins = []
    for key in keys:
        values = groups.get(key, [])
        start, end = period_bounds(key, months, seconds)
        bins.append(Bin(start, end, _aggregate(values, how, floating), len(values)))
    return Resampled(bins, field, every, how)


def _aggregate(values: list, how: str, floating: bool):
    if how == "count":
        return len(values)
    if not values:
        return None
    if how == "first":
        return values[0]
    if how == "last":
        return values[-1]
    if any(_is_nan(value) for value in values) and how in ("mean", "sum", "median", "min", "max"):
        return float("nan")
    if how == "min":
        return min(values)
    if how == "max":
        return max(values)
    if how == "sum":
        return math.fsum(values) if floating else sum((Decimal(v) for v in values), Decimal(0))
    if how == "median":
        return median_of(values, floating)
    return mean_of(values, floating)


# ---------------------------------------------------------------------------------------------
# Texts
# ---------------------------------------------------------------------------------------------


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def describe_duration(length: timedelta) -> str:
    """A duration in words people read easily: ``2 d 3 h 15 min``."""
    seconds = length.total_seconds()
    minutes, rest = divmod(int(seconds), 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} d")
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    if rest or (seconds % 1) or not parts:
        parts.append(f"{rest + (seconds % 1):g} s")
    return " ".join(parts)
