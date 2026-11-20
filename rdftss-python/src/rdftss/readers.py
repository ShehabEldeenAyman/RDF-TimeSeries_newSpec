"""The five readers: they turn the points of a snippet into rows of texts.

Each reader yields :class:`RawRow` objects: where the row is (for messages) and, per field, the
value as a text (``None`` for an empty value). :mod:`rdftss.points` then applies the rules that
hold for every class: the time, the window, the key, the lexical forms and the value encodings.

=========================  =================================================================
Class                      How a value becomes a text
=========================  =================================================================
``EmbeddedRDFSnippet``     the lexical form of the literal (the field's IRI is the predicate)
``EmbeddedJSONSnippet``    the JSON string under the field's ``tss:columnName``
``CSVSnippet``             the cell in the field's column (by header name, or by index)
``ParquetSnippet``         the value in the lexical form of its Parquet type
``ArrowSnippet``           the value in the lexical form of its Arrow type
=========================  =================================================================

The Parquet and Arrow lexical forms follow the specification's tables: UTC timestamps end in
``Z``, a DECIMAL has exactly *scale* fraction digits, floats use ``INF``, ``-INF`` and ``NaN``.
"""
from __future__ import annotations

import codecs
import csv
import io
import json
from pathlib import Path

from rdflib import Literal
from rdflib.namespace import RDF

from . import xsd
from .datafiles import data_source, source_name
from .errors import DataError, DataFileError, Issues, UnsupportedError, require
from .model import SnippetKind
from .times import time_from_epoch, time_of_day_from_count
from .vocab import TSS, XSD

#: Rows are read from Parquet and Arrow files in batches of this many rows.
BATCH_ROWS = 65536


class RawRow:
    """One point as texts: ``where`` it is, and ``texts`` by field (``None`` for an empty value)."""

    __slots__ = ("where", "texts")

    def __init__(self, where: str, texts: dict):
        self.where = where
        self.texts = texts


class Reporter:
    """Reports problems: in strict mode an error is raised as a :class:`DataError`; otherwise it
    is added to ``issues``. Warnings are always added."""

    def __init__(self, strict: bool, issues: Issues | list | None = None):
        self.strict = strict
        self.issues = issues if issues is not None else Issues()
        self._once: set = set()

    def _add(self, severity: str, message: str, where: str | None) -> None:
        if hasattr(self.issues, "add"):
            self.issues.add(severity, message, where)
        else:
            from .errors import Issue

            self.issues.append(Issue(severity, message, where))

    def error(self, message: str, where: str | None = None) -> None:
        if self.strict:
            raise DataError(message, where)
        self._add("error", message, where)

    def warning(self, message: str, where: str | None = None) -> None:
        self._add("warning", message, where)

    def warning_once(self, key, message: str, where: str | None = None) -> None:
        """A warning that is reported only the first time (``key`` tells them apart)."""
        if key not in self._once:
            self._once.add(key)
            self.warning(message, where)


def raw_rows(snippet, fields, reporter: Reporter):
    """The rows of a snippet as :class:`RawRow` objects, with texts for ``fields``."""
    readers = {
        SnippetKind.EMBEDDED_RDF: _rdf_rows,
        SnippetKind.EMBEDDED_JSON: _json_rows,
        SnippetKind.CSV: _csv_rows,
        SnippetKind.PARQUET: _parquet_rows,
        SnippetKind.ARROW: _arrow_rows,
    }
    return readers[snippet.kind](snippet, fields, reporter)


# ---------------------------------------------------------------------------------------------
# RDF points
# ---------------------------------------------------------------------------------------------

def _rdf_rows(snippet, fields, reporter: Reporter):
    graph = snippet.graph
    document = snippet.document
    by_predicate = {field.node: field for field in snippet.fields}
    wanted = set(fields)
    time_field = snippet.time_field
    point_nodes = sorted(set(graph.objects(snippet.node, TSS.hasPoint)), key=str)
    for point in point_nodes:
        if isinstance(point, Literal):
            reporter.error(f"tss:hasPoint must link to a point node, not the literal {str(point)!r}",
                           f"snippet {document.short(snippet.node)}")
            continue
        owners = set(graph.subjects(TSS.hasPoint, point))
        if len(owners) > 1:
            reporter.warning_once(("shared point", point), "a point belongs to several snippets; each point belongs "
                                  "to exactly one", f"point {document.short(point)}")
        texts: dict = {}
        problems: list[str] = []
        seen: set = set()
        doubled: set = set()
        for predicate, value in graph.predicate_objects(point):
            field = by_predicate.get(predicate)
            if field is None:
                reporter.warning_once(("not a field", predicate),
                                      f"points use {document.short(predicate)}, which is not a field of the snippet: "
                                      "its values are not values of the snippet",
                                      f"snippet {document.short(snippet.node)}")
                continue
            if not isinstance(value, Literal):
                problems.append(f"field {field.name!r} has the node {document.short(value)} as its value; "
                                "a value must be a literal")
                doubled.add(field)
                continue
            if field in seen:
                problems.append(f"field {field.name!r} has more than one value; a point has at most one value "
                                "for each field")
                doubled.add(field)
                continue
            seen.add(field)
            if value.datatype != field.datatype and not (field.datatype == RDF.langString and value.language):
                problems.append(f"field {field.name!r}: the literal {document.short(value)} must have the field's "
                                f"datatype {xsd.short_name(field.datatype)}")
            if field in wanted or field is time_field:
                text = str(value)
                texts[field] = text if text != "" else None
        for field in doubled:
            texts.pop(field, None)  # (in lenient mode) no value is better than an arbitrary one
        time_text = texts.get(time_field)
        where = f"the point at {time_text}" if time_text else f"a point without a time ({document.short(point)})"
        for message in problems:
            reporter.error(message, where)
        yield RawRow(where, texts)

    # Every value of a field must be in a point of the snippet
    points = set(point_nodes)
    for field in snippet.fields:
        for subject in graph.subjects(field.node, None, unique=True):
            if subject not in points:
                reporter.warning_once(("value outside points", field.node),
                                      f"{document.short(subject)} states a value for field {field.name!r} but is no "
                                      "point of the snippet; every value of a field must be in a point of its snippet",
                                      f"snippet {document.short(snippet.node)}")


# ---------------------------------------------------------------------------------------------
# JSON points
# ---------------------------------------------------------------------------------------------

class _JSONObject(list):
    """A JSON object, as its (key, value) pairs in order, so duplicate keys can be found."""


class _JSONNumber(str):
    """A JSON number, as the text it was written with."""


def _no_constants(name):
    raise ValueError(f"{name} is not valid JSON")


def _json_rows(snippet, fields, reporter: Reporter):
    document = snippet.document
    where_snippet = f"snippet {document.short(snippet.node)}"
    try:
        data = json.loads(str(snippet.points_literal), object_pairs_hook=_JSONObject,
                          parse_float=_JSONNumber, parse_int=_JSONNumber, parse_constant=_no_constants)
    except ValueError as error:
        reporter.error(f"tss:points is not valid JSON: {error}", where_snippet)
        return
    if type(data) is not list:
        reporter.error("tss:points must be a JSON array of JSON objects", where_snippet)
        return
    by_key = {field.column_name: field for field in snippet.fields if field.column_name is not None}
    wanted = set(fields)
    time_field = snippet.time_field
    for index, item in enumerate(data, 1):
        where = f"JSON point {index}"
        if not isinstance(item, _JSONObject):
            reporter.error("is not a JSON object", where)
            continue
        texts: dict = {}
        problems: list[str] = []
        seen: set[str] = set()
        for key, value in item:
            if key.startswith("@"):
                problems.append(f"has the key {key!r}; keys must not start with @")
                continue
            if key in seen:
                problems.append(f"has the key {key!r} more than once")
                continue
            seen.add(key)
            field = by_key.get(key)
            if field is None:
                reporter.warning_once(("json key", key), f"the key {key!r} is not the tss:columnName of a field: its "
                                      "values are not values of the snippet", where)
                continue
            if value is None:
                text = None
            elif isinstance(value, _JSONNumber):
                reporter.warning_once(("json number", key), f"field {field.name!r} has JSON numbers; a value should be "
                                      "a JSON string in the lexical form of its datatype, such as \"2.50\"", where)
                text = str(value)
            elif isinstance(value, bool):
                reporter.warning_once(("json boolean", key), f"field {field.name!r} has JSON booleans; a value should be "
                                      "a JSON string, such as \"true\"", where)
                text = "true" if value else "false"
            elif isinstance(value, str):
                text = value
            else:
                problems.append(f"field {field.name!r}: a value must be a JSON string, not an object or an array")
                continue
            if field in wanted or field is time_field:
                texts[field] = text if text != "" else None
        time_text = texts.get(time_field)
        if time_text:
            where = f"JSON point {index} ({time_text})"
        for message in problems:
            reporter.error(message, where)
        yield RawRow(where, texts)


# ---------------------------------------------------------------------------------------------
# CSV files
# ---------------------------------------------------------------------------------------------

def _csv_rows(snippet, fields, reporter: Reporter):
    dialect = snippet.csv
    source = data_source(snippet)
    name = source_name(source)
    encoding = dialect.encoding
    if codecs.lookup(encoding).name == "utf-8":
        encoding = "utf-8-sig"  # a byte order mark at the start is not part of the first column name
    # In a growing snippet, a last record that no line break ends yet is still being written
    unfinished = snippet.growing and not _ends_with_line_break(source)
    if isinstance(source, Path):
        stream = open(source, "r", encoding=encoding, newline="")
    else:
        stream = io.TextIOWrapper(source, encoding=encoding, newline="")
    columns = [field for field in fields if not (snippet.time_grid is not None and field is snippet.time_field)]
    try:
        reader = csv.reader(stream, delimiter=dialect.delimiter, strict=True)
        positions: dict = {}
        try:
            if dialect.header:
                header = next(reader, None)
                if header is None:
                    reporter.error("the file is empty: it has no header line", name)
                    return
                for field in columns:
                    count = header.count(field.column_name)
                    if count == 0:
                        shown = ", ".join(repr(column) for column in header[:20])
                        raise DataError(f"the header line has no column {field.column_name!r} (the tss:columnName of "
                                        f"field {field.name!r}); its columns are: {shown}", name)
                    if count > 1:
                        raise DataError(f"the header line has the column {field.column_name!r} more than once", name)
                    positions[field] = header.index(field.column_name)
            else:
                for field in columns:
                    positions[field] = field.column_index
            needed = max(positions.values(), default=-1) + 1
            pending = None
            for cells in reader:
                if not cells:
                    continue  # an empty line
                where = f"CSV line {reader.line_num}"
                if len(cells) < needed:
                    reporter.warning_once("short row", f"has {len(cells)} cells, fewer than the {needed} the fields "
                                          "need; the missing cells are read as empty values (only the first such "
                                          "row is reported)", where)
                texts = {}
                for field, index in positions.items():
                    text = cells[index] if index < len(cells) else ""
                    texts[field] = text if text != "" else None
                if pending is not None:
                    yield pending
                pending = RawRow(where, texts)
            if pending is not None and unfinished:
                reporter.warning("the last record has no line break yet: it is still being written, so it is not "
                                 "read (the snippet is growing)", pending.where)
            elif pending is not None:
                yield pending
        except csv.Error as error:
            raise DataError(f"is not valid CSV: {error}", f"{name}, CSV line {reader.line_num}") from None
        except UnicodeDecodeError as error:
            raise DataError(f"is not valid {dialect.encoding} text (tss:encoding): {error}", name) from None
    finally:
        stream.close()


def _ends_with_line_break(source) -> bool:
    """Whether a data file ends with a line break (in UTF-8 and other ASCII-based encodings, or
    UTF-16); ``True`` when that can't be known (a file object that can't seek), and for an empty file."""
    try:
        if isinstance(source, Path):
            with open(source, "rb") as file:
                file.seek(0, io.SEEK_END)
                size = file.tell()
                file.seek(max(0, size - 2))
                tail = file.read()
        else:
            position = source.tell()
            source.seek(0, io.SEEK_END)
            size = source.tell()
            source.seek(max(0, size - 2))
            tail = source.read()
            source.seek(position)
    except (OSError, AttributeError, ValueError):
        return True
    return size == 0 or tail.endswith((b"\n", b"\r", b"\n\x00", b"\r\x00"))


# ---------------------------------------------------------------------------------------------
# Parquet and Arrow files
# ---------------------------------------------------------------------------------------------

def _arrow_types():
    pa = require("pyarrow", "Reading Parquet and Arrow files", "files")
    return pa, pa.types


def allowed_datatypes(arrow_type, *, time_field: bool):
    """The ``tss:datatype`` values the specification allows for a Parquet or Arrow column type, or
    ``None`` when any datatype that reads a text is allowed (a string column).

    Raises :class:`UnsupportedError` for a type that is not in the specification's tables."""
    pa, types = _arrow_types()
    if types.is_dictionary(arrow_type):
        arrow_type = arrow_type.value_type
    if types.is_boolean(arrow_type):
        return {XSD.boolean}
    if types.is_signed_integer(arrow_type):
        return set(xsd.INTEGER_RANGES)
    if types.is_unsigned_integer(arrow_type):
        return {XSD.nonNegativeInteger} | set(xsd.UNSIGNED_TYPES)
    if types.is_float16(arrow_type) or types.is_float32(arrow_type):
        return {XSD.float}
    if types.is_float64(arrow_type):
        return {XSD.double}
    if types.is_decimal(arrow_type):
        return {XSD.decimal}
    if _is_text(arrow_type):
        return None
    if types.is_date(arrow_type):
        return {XSD.date}
    if types.is_timestamp(arrow_type):
        if arrow_type.tz is not None:
            return {XSD.dateTimeStamp} if time_field else {XSD.dateTimeStamp, XSD.dateTime}
        return set() if time_field else {XSD.dateTime}
    if types.is_time(arrow_type):
        return {XSD.time}
    raise UnsupportedError(f"the column type {arrow_type} is not in the specification's table of types")


def _is_text(arrow_type) -> bool:
    _, types = _arrow_types()
    return (types.is_string(arrow_type) or types.is_large_string(arrow_type)
            or (hasattr(types, "is_string_view") and types.is_string_view(arrow_type)))


_PER_SECOND = {"s": 1, "ms": 1000, "us": 1_000_000, "ns": 1_000_000_000}


def column_texts(array) -> list:
    """The values of a Parquet or Arrow column in the lexical forms of the specification's
    tables, with ``None`` for a null."""
    pa, types = _arrow_types()
    if isinstance(array, pa.ChunkedArray):
        array = array.combine_chunks()
    if types.is_dictionary(array.type):
        array = array.dictionary_decode()
    kind = array.type
    if types.is_boolean(kind):
        return [None if value is None else ("true" if value else "false") for value in array.to_pylist()]
    if types.is_integer(kind):
        return [None if value is None else str(value) for value in array.to_pylist()]
    if types.is_float16(kind) or types.is_float32(kind):
        import numpy

        width = numpy.float16 if types.is_float16(kind) else numpy.float32
        return [None if value is None else _special(value) or str(width(value)) for value in array.to_pylist()]
    if types.is_float64(kind):
        return [None if value is None else xsd.float_text(value) for value in array.to_pylist()]
    if types.is_decimal(kind):
        scale = kind.scale
        if scale < 0:
            raise UnsupportedError(f"a DECIMAL with a negative scale ({kind}) is not supported")
        return [None if value is None else (f"{value:.{scale}f}" if scale else f"{value:.0f}.")
                for value in array.to_pylist()]
    if _is_text(kind):
        return array.to_pylist()
    if types.is_date(kind):
        return [None if value is None else value.isoformat() for value in array.to_pylist()]
    if types.is_timestamp(kind):
        per_second = _PER_SECOND[kind.unit]
        utc = kind.tz is not None
        counts = array.cast(pa.int64()).to_pylist()
        return [None if value is None else time_from_epoch(value, per_second, utc=utc) for value in counts]
    if types.is_time(kind):
        per_second = _PER_SECOND[kind.unit]
        counts = array.cast(pa.int64()).to_pylist()
        return [None if value is None else time_of_day_from_count(value, per_second) for value in counts]
    raise UnsupportedError(f"the column type {kind} is not in the specification's table of types")


def _special(value: float) -> str | None:
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "INF" if value > 0 else "-INF"
    return None


def _match_columns(snippet, fields, schema, reporter: Reporter, label: str, name: str) -> dict:
    """The column name of each field, after checking the column types against the datatypes."""
    names = list(schema.names)
    columns = {}
    for field in fields:
        if snippet.time_grid is not None and field is snippet.time_field:
            continue
        count = names.count(field.column_name)
        if count == 0:
            shown = ", ".join(repr(column) for column in names[:20])
            raise DataError(f"has no column {field.column_name!r} (the tss:columnName of field {field.name!r}); "
                            f"its columns are: {shown}", name)
        if count > 1:
            raise DataError(f"has the column {field.column_name!r} more than once", name)
        arrow_type = schema.field(field.column_name).type
        try:
            allowed = allowed_datatypes(arrow_type, time_field=field.is_time)
        except UnsupportedError as error:
            if reporter.strict:
                raise UnsupportedError(f"{name}, column {field.column_name!r}: {error}") from None
            reporter.error(str(error), f"{name}, column {field.column_name!r}")
            continue
        if allowed is not None and field.datatype not in allowed:
            if field.is_time and not allowed:
                message = (f"the {label} type {arrow_type} has no timezone, so it can't be the column of the "
                           "time field, whose times need one")
            else:
                choices = ", ".join(sorted(xsd.short_name(datatype) for datatype in allowed))
                message = (f"field {field.name!r} has tss:datatype {xsd.short_name(field.datatype)}, which the "
                           f"specification does not allow for the {label} type {arrow_type} (allowed: {choices})")
            reporter.error(message, f"{name}, column {field.column_name!r}")
        columns[field] = field.column_name
    return columns


def _batch_rows(batches, columns: dict, label: str, reporter: Reporter):
    number = 0
    for batch in batches:
        texts_by_field = {}
        for field, column in columns.items():
            try:
                texts_by_field[field] = column_texts(batch.column(batch.schema.get_field_index(column)))
            except UnsupportedError as error:
                if reporter.strict:
                    raise
                reporter.error(str(error), f"column {column!r}")
                texts_by_field[field] = [None] * batch.num_rows
        for row in range(batch.num_rows):
            number += 1
            texts = {}
            for field, values in texts_by_field.items():
                text = values[row]
                texts[field] = text if text != "" else None
            yield RawRow(f"{label} row {number}", texts)


def _parquet_rows(snippet, fields, reporter: Reporter):
    pa, _ = _arrow_types()
    import pyarrow.parquet as pq

    source = data_source(snippet)
    name = source_name(source)
    try:
        parquet = pq.ParquetFile(str(source) if isinstance(source, Path) else source)
    except Exception as error:
        raise DataFileError(f"{name} can't be read as a Parquet file: {error}") from None
    columns = _match_columns(snippet, fields, parquet.schema_arrow, reporter, "Parquet", name)
    selected = sorted(set(columns.values()))
    if not selected:  # (lenient mode, when no column could be used) every row, without values
        yield from (RawRow(f"Parquet row {n}", {}) for n in range(1, parquet.metadata.num_rows + 1))
        return
    yield from _batch_rows(parquet.iter_batches(batch_size=BATCH_ROWS, columns=selected), columns, "Parquet", reporter)


#: The first bytes of a file in the Arrow IPC file format; a stream starts with 0xFFFFFFFF.
ARROW_FILE_MAGIC = b"ARROW1"


def _first_bytes(source, count: int) -> bytes:
    if isinstance(source, Path):
        with open(source, "rb") as file:
            return file.read(count)
    start = source.read(count)
    source.seek(0)
    return start


def _stream_batches(reader, snippet, name: str, reporter: Reporter):
    """The record batches of an Arrow IPC stream. In a growing snippet's stream, a last message
    that is not complete yet is still being written: it is left out."""
    pa, _ = _arrow_types()
    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            return
        except (pa.ArrowInvalid, OSError) as error:
            if snippet.growing:
                reporter.warning("the last message of the Arrow stream is not complete (it is still being written), "
                                 "so it is not read", name)
                return
            raise DataFileError(f"{name} is not a complete Arrow IPC stream: {error}") from None
        yield batch


def _arrow_rows(snippet, fields, reporter: Reporter):
    """An Arrow data file is in the IPC file format or the IPC streaming format; the first bytes
    tell them apart (``ARROW1`` starts a file), not the file name."""
    pa, _ = _arrow_types()
    import pyarrow.ipc

    source = data_source(snippet)
    name = source_name(source)
    is_file_format = _first_bytes(source, len(ARROW_FILE_MAGIC)) == ARROW_FILE_MAGIC
    handle = pa.memory_map(str(source)) if isinstance(source, Path) else source
    try:
        try:
            if is_file_format:
                reader = pyarrow.ipc.open_file(handle)
                batches = (reader.get_batch(index) for index in range(reader.num_record_batches))
            else:
                reader = pyarrow.ipc.open_stream(handle)
                batches = _stream_batches(reader, snippet, name, reporter)
        except pa.ArrowInvalid as error:
            what = "an Arrow IPC file" if is_file_format else "an Arrow IPC stream"
            raise DataFileError(f"{name} can't be read as {what}: {error}") from None
        if is_file_format and snippet.growing:
            reporter.warning("is in the Arrow IPC file format, which can't be appended to: the snippet must state "
                             "tss:immutable true (a growing Arrow snippet uses the IPC streaming format)", name)
        columns = _match_columns(snippet, fields, reader.schema, reporter, "Arrow", name)
        yield from _batch_rows(batches, columns, "Arrow", reporter)
    finally:
        if isinstance(source, Path):
            handle.close()
