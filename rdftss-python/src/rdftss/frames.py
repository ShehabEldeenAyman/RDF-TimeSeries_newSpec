"""Rows as tables: Apache Arrow, pandas and polars.

>>> table = source.to_arrow()            # doctest: +SKIP
>>> frame = source.to_pandas()           # indexed by time   # doctest: +SKIP
>>> frame = source.to_polars()           # doctest: +SKIP

One column per field: the time field first (a UTC timestamp), then the other fields, with their
decoded values typed by their datatype:

=================================  ===============================================
Datatype                           Column type
=================================  ===============================================
the time field                     timestamp in UTC (microseconds by default)
xsd:dateTime                       timestamp in UTC, or without timezone
xsd:double, xsd:float              float64
xsd:decimal                        float64 (decimal128 with ``exact_decimals=True``)
integer types                      int64 (uint64 for the unsigned types)
xsd:boolean                        bool
xsd:date, xsd:time                 date32, time64
anything else                      string
=================================  ===============================================

Each column carries the field's IRI, datatype, unit, observed property and label as Arrow field
metadata (``table.schema.field("value").metadata``); the table carries the source's IRI.
"""
from __future__ import annotations


from . import xsd
from .errors import require
from .vocab import XSD


def _column_names(source, fields, names: str) -> dict:
    if names not in ("name", "iri", "label"):
        raise ValueError("names must be 'name', 'iri' or 'label'")
    chosen = {}
    for field in fields:
        if names == "iri":
            chosen[field] = field.iri or field.name
        elif names == "label":
            chosen[field] = field.label or field.name
        else:
            chosen[field] = field.name
    if len(set(chosen.values())) != len(chosen):  # two equal labels: fall back to the names
        chosen = {field: field.name for field in fields}
    return chosen


def _metadata(field) -> dict:
    items = {"iri": field.iri, "datatype": str(field.datatype) if field.datatype else None, "role": field.role,
             "label": field.label, "unit": field.unit, "observed_property": field.observed_property,
             "sensor": field.sensor, "feature_of_interest": field.feature_of_interest}
    return {key.encode(): str(value).encode() for key, value in items.items() if value is not None}


def _typed_array(pa, field, values: list, exact_decimals: bool, time_unit: str):
    """An Arrow array of a field's decoded values, typed by its datatype (falling back to text)."""
    datatype = field.datatype
    try:
        if datatype in xsd.FLOATING_TYPES:
            return pa.array(values, type=pa.float64())
        if datatype == XSD.decimal:
            if exact_decimals:
                scale = max((-value.as_tuple().exponent for value in values if value is not None), default=0)
                return pa.array(values, type=pa.decimal128(38, max(0, min(scale, 38))))
            return pa.array([None if value is None else float(value) for value in values], type=pa.float64())
        if datatype in xsd.INTEGER_RANGES:
            kind = pa.uint64() if datatype in xsd.UNSIGNED_TYPES else pa.int64()
            return pa.array(values, type=kind)
        if datatype == XSD.boolean:
            return pa.array(values, type=pa.bool_())
        if datatype == XSD.date:
            return pa.array(values, type=pa.date32())
        if datatype == XSD.time:
            return pa.array(values, type=pa.time64("us"))
        if datatype in (XSD.dateTime, XSD.dateTimeStamp):
            present = [value for value in values if value is not None]
            if all(value.tzinfo is not None for value in present):
                return pa.array(values, type=pa.timestamp("us", tz="UTC"))
            if all(value.tzinfo is None for value in present):
                return pa.array(values, type=pa.timestamp("us"))
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError, TypeError, ValueError):
        pass
    return pa.array([None if value is None else xsd.lexical_form(value, datatype) for value in values],
                    type=pa.string())


def to_arrow(rows, *, source, names: str = "name", time_unit: str = "us", exact_decimals: bool = False):
    """The rows as a ``pyarrow.Table`` (see the module documentation).

    - ``rows``: any iterable of rows (a :class:`~rdftss.rows.Rows`, or a
      :class:`~rdftss.rows.RowReader` to build the table while streaming).
    - ``names``: name the columns by field ``"name"``, ``"iri"`` or ``"label"``.
    - ``time_unit``: ``"s"``, ``"ms"``, ``"us"`` or ``"ns"`` for the time column. The time column
      is made from the exact instants, so ``"ns"`` keeps nanoseconds.
    - ``exact_decimals``: ``xsd:decimal`` columns as decimal128 instead of float64.
    """
    pa = require("pyarrow", "Making Arrow tables", "files")
    fields = list(source.fields)
    time_field = source.time_field
    per_second = {"s": 1, "ms": 1000, "us": 1_000_000, "ns": 1_000_000_000}[time_unit]
    instants = []
    columns = {field: [] for field in fields if field is not time_field}
    for row in rows:
        instants.append(int((row.instant * per_second).to_integral_value(rounding="ROUND_FLOOR")))
        for field, values in columns.items():
            values.append(row.values.get(field))
    chosen = _column_names(source, fields, names)
    arrays, schema_fields = [], []
    time_array = pa.array(instants, type=pa.int64()).cast(pa.timestamp(time_unit, tz="UTC"))
    arrays.append(time_array)
    schema_fields.append(pa.field(chosen[time_field], time_array.type, metadata=_metadata(time_field)))
    for field, values in columns.items():
        array = _typed_array(pa, field, values, exact_decimals, time_unit)
        arrays.append(array)
        schema_fields.append(pa.field(chosen[field], array.type, metadata=_metadata(field)))
    metadata = {b"rdftss.source": str(source.node).encode(), b"rdftss.class": str(source.kind.iri).encode()}
    return pa.Table.from_arrays(arrays, schema=pa.schema(schema_fields, metadata=metadata))


def to_pandas(rows, *, source, index: bool = True, **options):
    """The rows as a pandas DataFrame, indexed by time (``index=False`` keeps the time as a
    column). The other options are those of :func:`to_arrow`."""
    require("pandas", "Making pandas DataFrames", "pandas")
    table = to_arrow(rows, source=source, **options)
    frame = table.to_pandas()
    if index:
        frame = frame.set_index(table.schema.names[0])
    return frame


def to_polars(rows, *, source, **options):
    """The rows as a polars DataFrame. The options are those of :func:`to_arrow`."""
    polars = require("polars", "Making polars DataFrames", "polars")
    return polars.from_arrow(to_arrow(rows, source=source, **options))
