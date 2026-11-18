"""Points: the readings of a snippet, one time and the values of its fields.

>>> points = snippet.read()                      # doctest: +SKIP
>>> point = points[0]                            # doctest: +SKIP
>>> point.time                                   # a datetime   # doctest: +SKIP
>>> point["value"]                               # the decoded value, e.g. 4810.5   # doctest: +SKIP
>>> point.text("value")                          # its text, e.g. "4810.5"   # doctest: +SKIP

Every snippet class is read the same way (see :mod:`rdftss.readers` for how each class becomes
rows of texts). Then, for every row:

1. The time: the value of the time field, an ``xsd:dateTimeStamp`` with a timezone, or, on a
   time grid, the time of the row's step. A point without a time is an error.
2. The window: a point outside ``[tss:from, tss:until)`` is skipped (external snippets) or is an
   error (embedded snippets).
3. The key: two points with the same time (the same moment) are an error.
4. The values: an empty text is no value; a value must be in the lexical form of its field's
   datatype; a field with a value encoding is decoded first.

In strict mode (the default) the first error raises a :class:`~rdftss.errors.DataError` that
says where it is. With ``strict=False`` the bad point or value is left out, and every problem
is listed in ``points.issues``.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from . import xsd
from .errors import Issues
from .readers import Reporter, raw_rows
from .times import Time, parse_time, to_instant
from .vocab import XSD


class Point:
    """One point of a snippet.

    - ``time``: its time as a timezone-aware :class:`~datetime.datetime`;
      ``time_text``: as written; ``instant``: exact seconds since 1970 (a Decimal).
    - ``point[field]``: the decoded Python value of a field (``None`` when it has no value).
      ``field`` is a name, an IRI or a :class:`~rdftss.model.Field`.
    - ``point.text(field)``: the lexical form of the decoded value;
      ``point.raw(field)``: the text as it is in the data (before decoding).
    - ``step``: the step on the time grid, or ``None``; ``where``: where the point is.
    """

    __slots__ = ("snippet", "moment", "values", "texts", "where", "step")

    def __init__(self, snippet, moment: Time, values: dict, texts: dict, where: str, step: int | None = None):
        self.snippet = snippet
        self.moment = moment
        self.values = values
        self.texts = texts
        self.where = where
        self.step = step

    @property
    def time(self) -> datetime:
        """The time as a timezone-aware datetime (fraction digits beyond microseconds dropped)."""
        return self.moment.datetime

    @property
    def time_text(self) -> str:
        """The time exactly as written (or, on a time grid, as computed)."""
        return self.moment.text

    @property
    def instant(self) -> Decimal:
        """The time as exact seconds since 1970-01-01T00:00:00Z."""
        return self.moment.instant

    def _field(self, key):
        return self.snippet.field(key)

    def __getitem__(self, key):
        return self.values.get(self._field(key))

    def get(self, key, default=None):
        """The decoded value of a field, or ``default`` when it has no value."""
        value = self.values.get(self._field(key))
        return default if value is None else value

    def has(self, key) -> bool:
        """Whether the point has a value for a field."""
        return self._field(key) in self.values

    def raw(self, key) -> str | None:
        """The text of a field's value as it is in the data (before decoding), or ``None``."""
        return self.texts.get(self._field(key))

    def text(self, key) -> str | None:
        """The lexical form of a field's decoded value, or ``None``. Without a value encoding it is
        the text as written."""
        field = self._field(key)
        text = self.texts.get(field)
        if text is None or field.value_encoding is None:
            return text
        return field.value_encoding.decode(text)

    def items(self):
        """(field, decoded value) for every field with a value, the time field first."""
        return self.values.items()

    def as_dict(self, names: str = "name") -> dict:
        """The point as a dict: the time, and the decoded value of each field with a value.
        ``names`` is ``"name"`` (the field names) or ``"iri"``."""
        result = {}
        for field, value in self.values.items():
            key = field.name if names == "name" else (field.iri or field.name)
            result[key] = value
        return result

    def __repr__(self) -> str:
        shown = [f"{field.name}={value!r}" for field, value in self.values.items() if not field.is_time]
        return f"Point({self.moment.text}{', ' if shown else ''}{', '.join(shown)})"


class Points(list):
    """The points of a snippet, in time order: a list of :class:`Point` with methods to select,
    analyse and export them.

    - ``issues``: the problems found while reading (with ``strict=False``).
    - ``outside_window``: how many rows of the data file lay outside the time window.

    Selecting (each gives new :class:`Points`): :meth:`between`, :meth:`where`, :meth:`having`.
    Analysing: :meth:`statistics`, :meth:`outside_expected_range`, :meth:`gaps`,
    :meth:`missing_steps`, :meth:`resample`. Exporting: :meth:`to_arrow`, :meth:`to_pandas`,
    :meth:`to_polars`, :meth:`values`, :meth:`times`.
    """

    def __init__(self, points=(), snippet=None, issues=None, outside_window: int = 0):
        super().__init__(points)
        self.snippet = snippet
        self.issues = issues if issues is not None else Issues()
        self.outside_window = outside_window

    def _new(self, points) -> "Points":
        return Points(points, self.snippet, self.issues, self.outside_window)

    def __getitem__(self, item):
        result = super().__getitem__(item)
        return self._new(result) if isinstance(item, slice) else result

    # -- selecting -------------------------------------------------------------------------------

    def between(self, start=None, end=None) -> "Points":
        """The points with a time in ``[start, end)``. ``start`` and ``end`` are datetimes, dates
        or texts such as ``"2024-05-12T08:00:00Z"`` (no timezone means UTC); ``None`` is open."""
        low = to_instant(start) if start is not None else None
        high = to_instant(end) if end is not None else None
        return self._new(point for point in self
                         if (low is None or point.instant >= low) and (high is None or point.instant < high))

    def where(self, field=None, *, equals=None, one_of=None, above=None, below=None,
              at_least=None, at_most=None) -> "Points":
        """The points whose value for ``field`` meets every condition given; a point without a
        value for the field never does. ``field`` may also be a function of a point::

            points.where("quality", equals="good")
            points.where("value", at_least=4700, below=4800)
            points.where(lambda point: point["value"] > point["threshold"])
        """
        if callable(field) and not isinstance(field, str):
            return self._new(point for point in self if field(point))
        target = self.snippet.default_field(field)

        def keep(point) -> bool:
            value = point.values.get(target)
            if value is None:
                return False
            if equals is not None and value != equals:
                return False
            if one_of is not None and value not in one_of:
                return False
            if above is not None and not value > above:
                return False
            if below is not None and not value < below:
                return False
            if at_least is not None and not value >= at_least:
                return False
            if at_most is not None and not value <= at_most:
                return False
            return True

        return self._new(point for point in self if keep(point))

    def having(self, field) -> "Points":
        """The points that have a value for ``field``."""
        target = self.snippet.field(field)
        return self._new(point for point in self if target in point.values)

    # -- columns ---------------------------------------------------------------------------------

    def times(self) -> list[datetime]:
        """The time of every point."""
        return [point.time for point in self]

    def values(self, field=None) -> list:
        """The decoded value of ``field`` for every point (``None`` where it has none)."""
        target = self.snippet.default_field(field)
        return [point.values.get(target) for point in self]

    # -- analysis --------------------------------------------------------------------------------

    def statistics(self, field=None):
        """Count, missing, first and last time, minimum, maximum, mean and median of a field, with
        the definitions of the stored summaries. See :func:`rdftss.analysis.statistics`."""
        from .analysis import statistics

        return statistics(self, self.snippet.default_field(field))

    def outside_expected_range(self, field=None):
        """The values outside their field's ``tss:minimum`` and ``tss:maximum``.
        See :func:`rdftss.analysis.outside_expected_range`."""
        from .analysis import outside_expected_range

        return outside_expected_range(self, None if field is None else self.snippet.field(field))

    def gaps(self):
        """The usual time between two points, and the longer intervals. See :func:`rdftss.analysis.gaps`."""
        from .analysis import gaps

        return gaps(self)

    def missing_steps(self):
        """The steps of a time grid that have no row. See :func:`rdftss.analysis.missing_steps`."""
        from .analysis import missing_steps

        return missing_steps(self)

    def resample(self, every, field=None, how: str = "mean", include_empty: bool = False):
        """Aggregate a field per period. See :func:`rdftss.analysis.resample`."""
        from .analysis import resample

        return resample(self, every, self.snippet.default_field(field), how=how, include_empty=include_empty)

    # -- export ----------------------------------------------------------------------------------

    def to_arrow(self, **options):
        """The points as a ``pyarrow.Table``. See :func:`rdftss.frames.to_arrow`."""
        from .frames import to_arrow

        return to_arrow(self, snippet=self.snippet, **options)

    def to_pandas(self, **options):
        """The points as a pandas DataFrame. See :func:`rdftss.frames.to_pandas`."""
        from .frames import to_pandas

        return to_pandas(self, snippet=self.snippet, **options)

    def to_polars(self, **options):
        """The points as a polars DataFrame. See :func:`rdftss.frames.to_polars`."""
        from .frames import to_polars

        return to_polars(self, snippet=self.snippet, **options)

    def __repr__(self) -> str:
        name = self.snippet.name if self.snippet is not None else "?"
        if not self:
            return f"<Points of {name}: none>"
        return f"<Points of {name}: {len(self)}, {self[0].time_text} .. {self[-1].time_text}>"


class PointReader:
    """Reads the points of a snippet one by one (see :meth:`rdftss.model.Snippet.iter_points`).

    Iterate over it to get the points. Afterwards, ``issues`` holds the problems found (with
    ``strict=False``) and ``outside_window`` the rows skipped because they lay outside the window.
    """

    def __init__(self, snippet, *, strict: bool = True, window: bool = True, fields=None, issues=None):
        snippet.check_readable()
        self.snippet = snippet
        self.strict = strict
        self.window = window
        self.issues = issues if issues is not None else Issues()
        self.outside_window = 0
        self.fields = _selected_fields(snippet, fields)

    def __iter__(self):
        snippet = self.snippet
        reporter = Reporter(self.strict, self.issues)
        rows = raw_rows(snippet, self.fields, reporter)
        if snippet.kind.is_embedded:
            points = list(self._points(rows, reporter))
            points.sort(key=lambda point: point.instant)
            yield from points
        else:
            yield from self._points(rows, reporter)

    def _points(self, rows, reporter: Reporter):
        snippet = self.snippet
        time_field = snippet.time_field
        grid = snippet.time_grid
        window = snippet.window if self.window else None
        embedded = snippet.kind.is_embedded
        seen: set = set()
        value_fields = [field for field in self.fields if field is not time_field]
        for row in rows:
            where = row.where
            texts = row.texts
            step = None
            # 1. The time
            if grid is not None:
                step_text = texts.get(grid.step_field)
                if step_text is None:
                    reporter.error("has no step: its value for the step field is empty", where)
                    continue
                try:
                    step = xsd.read_value(step_text, XSD.integer)
                    if step < 0:
                        raise ValueError("it is negative")
                    moment = grid.time_of(step)
                except ValueError as error:
                    reporter.error(f"its step {step_text!r} is not a whole number of 0 or more ({error})", where)
                    continue
            else:
                time_text = texts.get(time_field)
                if time_text is None:
                    reporter.error("has no time: its value for the time field is empty", where)
                    continue
                try:
                    moment = parse_time(time_text, require_timezone=True)
                except ValueError as error:
                    reporter.error(f"its time is not an xsd:dateTimeStamp: {error}", where)
                    continue
            # 2. The window
            if window is not None:
                if (window.start is not None and moment.instant < window.start.instant) or \
                        (window.end is not None and moment.instant >= window.end.instant):
                    if embedded:
                        reporter.error(f"lies outside the time window {window}; every point of an embedded "
                                       "snippet must lie in it", where)
                    else:
                        self.outside_window += 1
                    continue
            # 3. The key
            if moment.instant in seen:
                reporter.error(f"has the same time ({moment.text}) as another point; no two points may have "
                               "the same time", where)
                continue
            seen.add(moment.instant)
            # 4. The values
            values = {time_field: moment.datetime}
            point_texts = {time_field: moment.text}
            for field in value_fields:
                text = texts.get(field)
                if text is None:
                    continue
                try:
                    decoded = field.value_encoding.decode(text) if field.value_encoding is not None else text
                    values[field] = xsd.read_value(decoded, field.datatype)
                except ValueError as error:
                    reporter.error(f"field {field.name!r}: {error}", where)
                    continue
                point_texts[field] = text
            yield Point(snippet, moment, values, point_texts, where, step)


def _selected_fields(snippet, fields) -> list:
    if fields is None:
        return list(snippet.fields)
    selected = [snippet.field(key) for key in fields]
    for required in (snippet.time_field, snippet.step_field):
        if required is not None and required not in selected:
            selected.insert(0, required)
    return selected


def iter_points(snippet, *, strict: bool = True, window: bool = True, fields=None, issues=None) -> PointReader:
    """A :class:`PointReader` over the points of a snippet (see :meth:`Snippet.iter_points`)."""
    return PointReader(snippet, strict=strict, window=window, fields=fields, issues=issues)


def read_points(snippet, *, strict: bool = True, window: bool = True, fields=None) -> Points:
    """All points of a snippet in time order, as :class:`Points` (see :meth:`Snippet.read`)."""
    reader = PointReader(snippet, strict=strict, window=window, fields=fields)
    points = list(reader)
    points.sort(key=lambda point: point.instant)
    return Points(points, snippet, reader.issues, reader.outside_window)
