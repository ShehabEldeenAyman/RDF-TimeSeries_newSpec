"""The five readers: they turn the rows of a source into rows of texts.

Each reader yields :class:`RawRow` objects: where the row is (for messages) and, per field, the
value as a text (``None`` for an empty value). :mod:`rdftss.rows` then applies the rules that
hold for every class: the time, the window, the key, the lexical forms and the value encodings.

=========================  =================================================================
Class                      How a value becomes a text
=========================  =================================================================
``EmbeddedRDFSource``     the lexical form of the literal on the points of the row: ``tss:time``,
                          ``tss:value`` of the point of a value field, or the field property of a
                          describing field
``EmbeddedJSONSource``    the JSON string under the field's ``tss:columnName``
``CSVSource``             the cell in the field's column (by header name, or by index)
``ParquetSource``         the value in the lexical form of its Parquet type
``ArrowSource``           the value in the lexical form of its Arrow type
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
from .datafiles import data_handle, data_name
from .errors import DataError, DataFileError, Issues, UnsupportedError, require
from .model import SourceKind
from .times import parse_time, time_from_epoch, time_of_day_from_count
from .vocab import TSS, XSD

#: Rows are read from Parquet and Arrow files in batches of this many rows.
BATCH_ROWS = 65536


class RawRow:
    """One row as texts: ``where`` it is, and ``texts`` by field (``None`` for an empty value).
    ``nodes``: for RDF points, the node of the point of each value field in the row."""

    __slots__ = ("where", "texts", "nodes")

    def __init__(self, where: str, texts: dict, nodes: dict | None = None):
        self.where = where
        self.texts = texts
        self.nodes = nodes


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


def raw_rows(source, fields, reporter: Reporter):
    """The rows of a source as :class:`RawRow` objects, with texts for ``fields``."""
    readers = {
        SourceKind.EMBEDDED_RDF: _rdf_rows,
        SourceKind.EMBEDDED_JSON: _json_rows,
        SourceKind.CSV: _csv_rows,
        SourceKind.PARQUET: _parquet_rows,
        SourceKind.ARROW: _arrow_rows,
    }
    return readers[source.kind](source, fields, reporter)


# ---------------------------------------------------------------------------------------------
# RDF points
# ---------------------------------------------------------------------------------------------

def _datatype_of(value: Literal):
    """The datatype of a literal: a literal without a datatype is an xsd:string (RDF 1.1), or an
    rdf:langString when it has a language."""
    if value.datatype is not None:
        return value.datatype
    return RDF.langString if value.language else XSD.string


def _rdf_rows(source, fields, reporter: Reporter):
    """The rows of a tss:EmbeddedRDFSource. Each point states its value field (``tss:field``,
    which it may leave out when the source has one value field), its time (``tss:time``), its
    reading (``tss:value``) and the values of the describing fields of its value field, with
    their field property. The points with the same time form one row."""
    graph = source.graph
    document = source.document
    where_source = f"source {document.short(source.node)}"
    time_field = source.time_field
    value_fields = source.value_fields
    single = value_fields[0] if len(value_fields) == 1 else None
    by_node = {field.node: field for field in source.fields}
    describing = {field.field_property: field for field in source.describing_fields if field.field_property is not None}
    wanted = set(fields)
    rows: dict = {}  # instant -> [where, texts, nodes]
    for point in sorted(set(graph.objects(source.node, TSS.hasPoint)), key=str):
        if isinstance(point, Literal):
            reporter.error(f"tss:hasPoint must link to a point node, not the literal {str(point)!r}", where_source)
            continue
        if len(set(graph.subjects(TSS.hasPoint, point))) > 1:
            reporter.warning_once(("shared point", point), "a point belongs to several sources; each point belongs "
                                  "to exactly one", f"point {document.short(point)}")
        problems: list[str] = []

        def one_literal(prop, datatype, what):
            values = list(graph.objects(point, prop))
            if len(values) != 1:
                problems.append(f"states {len(values)} {document.short(prop)}; a point states exactly one, {what}")
                return None
            value = values[0]
            if not isinstance(value, Literal):
                problems.append(f"its {document.short(prop)} is the node {document.short(value)}; it must be a literal")
                return None
            if _datatype_of(value) != datatype:
                problems.append(f"its {document.short(prop)} {document.short(value)} must have the datatype "
                                f"{xsd.short_name(datatype)}")
            return str(value)

        # Its value field
        stated = list(graph.objects(point, TSS.field))
        field = None
        if len(stated) > 1:
            problems.append(f"states {len(stated)} tss:field; a point is a point of exactly one value field")
        elif stated:
            field = by_node.get(stated[0])
            if field is None or not field.is_value:
                problems.append(f"its tss:field {document.short(stated[0])} is not a value field of the source")
                field = None
        elif single is not None:
            field = single
        else:
            problems.append("states no tss:field, and the source does not have exactly one value field, "
                            "so the point does not say which field its reading is a value of")
        # Its time and its reading
        time_text = one_literal(TSS.time, time_field.datatype, "the time of its row")
        value_text = one_literal(TSS.value, field.datatype, "its reading") if field is not None else None
        if value_text == "":
            problems.append("its tss:value is the empty text, which is no value: a point is one reading, so it "
                            "states one")
        # The values of describing fields, and other triples
        described: dict = {}
        for predicate, value in sorted(graph.predicate_objects(point), key=str):
            if predicate in (TSS.field, TSS.time, TSS.value):
                continue
            describer = describing.get(predicate)
            if describer is None:
                reporter.warning_once(("other triple", predicate), f"points state {document.short(predicate)}, which "
                                      "is not the field property of a describing field: its values are not values "
                                      "of the source", where_source)
                continue
            if field is not None and field not in describer.describes:
                problems.append(f"states {document.short(predicate)}, the field property of describing field "
                                f"{describer.name!r}, which does not describe field {field.name!r}")
                continue
            if describer in described:
                problems.append(f"states more than one value for describing field {describer.name!r}")
                described[describer] = None
                continue
            if not isinstance(value, Literal):
                problems.append(f"the value of describing field {describer.name!r} is the node "
                                f"{document.short(value)}; it must be a literal")
                described[describer] = None
                continue
            if _datatype_of(value) != describer.datatype:
                problems.append(f"describing field {describer.name!r}: the literal {document.short(value)} must have "
                                f"the field's datatype {xsd.short_name(describer.datatype)}")
            described[describer] = str(value)
        name = field.name if field is not None else "?"
        where = f"the point of {name!r} at {time_text}" if time_text else \
            f"a point without a time ({document.short(point)})"
        for message in problems:
            reporter.error(message, where)
        if time_text is None or field is None:
            continue
        try:
            moment = parse_time(time_text, require_timezone=True)
        except ValueError as error:
            reporter.error(f"its time is not a valid time with a timezone: {error}", where)
            continue
        # The row of its time
        if moment.instant not in rows:
            rows[moment.instant] = [f"the row at {time_text}", {time_field: time_text}, {}]
        _, texts, nodes = rows[moment.instant]
        if field in nodes:
            reporter.error(f"is the second point of field {field.name!r} at {time_text}; no two points of a value "
                           "field have the same time", where)
            continue
        nodes[field] = point
        if field in wanted and value_text is not None:
            texts[field] = value_text if value_text != "" else None
        for describer, text in described.items():
            if describer not in wanted or text is None:
                continue
            text = text if text != "" else None
            if texts.get(describer, text) != text:
                reporter.error(f"describing field {describer.name!r} has two values in the row at {time_text}, "
                               f"{texts[describer]!r} and {text!r}; a row has at most one value for each field", where)
                continue
            if text is not None:
                texts[describer] = text
    for where, texts, nodes in rows.values():
        yield RawRow(where, texts, nodes)


# ---------------------------------------------------------------------------------------------
# JSON rows
# ---------------------------------------------------------------------------------------------

class _JSONObject(list):
    """A JSON object, as its (key, value) pairs in order, so duplicate keys can be found."""


class _JSONNumber(str):
    """A JSON number, as the text it was written with."""


def _no_constants(name):
    raise ValueError(f"{name} is not valid JSON")


def _json_rows(source, fields, reporter: Reporter):
    document = source.document
    where_source = f"source {document.short(source.node)}"
    try:
        data = json.loads(str(source.points_literal), object_pairs_hook=_JSONObject,
                          parse_float=_JSONNumber, parse_int=_JSONNumber, parse_constant=_no_constants)
    except ValueError as error:
        reporter.error(f"tss:points is not valid JSON: {error}", where_source)
        return
    if type(data) is not list:
        reporter.error("tss:points must be a JSON array of JSON objects", where_source)
        return
    by_key = {field.column_name: field for field in source.fields if field.column_name is not None}
    wanted = set(fields)
    time_field = source.time_field
    for index, item in enumerate(data, 1):
        where = f"JSON row {index}"
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
                                      "values are not values of the source", where)
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
            where = f"JSON row {index} ({time_text})"
        for message in problems:
            reporter.error(message, where)
        yield RawRow(where, texts)


# ---------------------------------------------------------------------------------------------
# CSV files
# ---------------------------------------------------------------------------------------------

def _csv_rows(source, fields, reporter: Reporter):
    dialect = source.csv
    data = data_handle(source)
    name = data_name(data)
    encoding = dialect.encoding
    if codecs.lookup(encoding).name == "utf-8":
        encoding = "utf-8-sig"  # a byte order mark at the start is not part of the first column name
    # In a growing source, a last record that no line break ends yet is still being written
    unfinished = source.growing and not _ends_with_line_break(data)
    if isinstance(data, Path):
        stream = open(data, "r", encoding=encoding, newline="")
    else:
        stream = io.TextIOWrapper(data, encoding=encoding, newline="")
    columns = [field for field in fields if not (source.time_grid is not None and field is source.time_field)]
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
            positional = source.time_grid is not None and source.time_grid.by_position
            for cells in reader:
                if not cells and not positional:
                    continue  # an empty line (on a time grid by position, a step without readings)
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
                                 "read (the source is growing)", pending.where)
            elif pending is not None:
                yield pending
        except csv.Error as error:
            raise DataError(f"is not valid CSV: {error}", f"{name}, CSV line {reader.line_num}") from None
        except UnicodeDecodeError as error:
            raise DataError(f"is not valid {dialect.encoding} text (tss:encoding): {error}", name) from None
    finally:
        stream.close()


def _ends_with_line_break(data) -> bool:
    """Whether a data file ends with a line break (in UTF-8 and other ASCII-based encodings, or
    UTF-16); ``True`` when that can't be known (a file object that can't seek), and for an empty file."""
    try:
        if isinstance(data, Path):
            with open(data, "rb") as file:
                file.seek(0, io.SEEK_END)
                size = file.tell()
                file.seek(max(0, size - 2))
                tail = file.read()
        else:
            position = data.tell()
            data.seek(0, io.SEEK_END)
            size = data.tell()
            data.seek(max(0, size - 2))
            tail = data.read()
            data.seek(position)
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
            return {XSD.dateTime, XSD.dateTimeStamp}
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


def _match_columns(source, fields, schema, reporter: Reporter, label: str, name: str) -> dict:
    """The column name of each field, after checking the column types against the datatypes."""
    names = list(schema.names)
    columns = {}
    for field in fields:
        if source.time_grid is not None and field is source.time_field:
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


def _parquet_rows(source, fields, reporter: Reporter):
    pa, _ = _arrow_types()
    import pyarrow.parquet as pq

    data = data_handle(source)
    name = data_name(data)
    try:
        parquet = pq.ParquetFile(str(data) if isinstance(data, Path) else data)
    except Exception as error:
        raise DataFileError(f"{name} can't be read as a Parquet file: {error}") from None
    columns = _match_columns(source, fields, parquet.schema_arrow, reporter, "Parquet", name)
    selected = sorted(set(columns.values()))
    if not selected:  # (lenient mode, when no column could be used) every row, without values
        yield from (RawRow(f"Parquet row {n}", {}) for n in range(1, parquet.metadata.num_rows + 1))
        return
    yield from _batch_rows(parquet.iter_batches(batch_size=BATCH_ROWS, columns=selected), columns, "Parquet", reporter)


#: The first bytes of a file in the Arrow IPC file format; a stream starts with 0xFFFFFFFF.
ARROW_FILE_MAGIC = b"ARROW1"


def _first_bytes(data, count: int) -> bytes:
    if isinstance(data, Path):
        with open(data, "rb") as file:
            return file.read(count)
    start = data.read(count)
    data.seek(0)
    return start


def _stream_batches(reader, source, name: str, reporter: Reporter):
    """The record batches of an Arrow IPC stream. In a growing source's stream, a last message
    that is not complete yet is still being written: it is left out."""
    pa, _ = _arrow_types()
    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            return
        except (pa.ArrowInvalid, OSError) as error:
            if source.growing:
                reporter.warning("the last message of the Arrow stream is not complete (it is still being written), "
                                 "so it is not read", name)
                return
            raise DataFileError(f"{name} is not a complete Arrow IPC stream: {error}") from None
        yield batch


def _arrow_rows(source, fields, reporter: Reporter):
    """An Arrow data file is in the IPC file format or the IPC streaming format; the first bytes
    tell them apart (``ARROW1`` starts a file), not the file name."""
    pa, _ = _arrow_types()
    import pyarrow.ipc

    data = data_handle(source)
    name = data_name(data)
    is_file_format = _first_bytes(data, len(ARROW_FILE_MAGIC)) == ARROW_FILE_MAGIC
    handle = pa.memory_map(str(data)) if isinstance(data, Path) else data
    try:
        try:
            if is_file_format:
                reader = pyarrow.ipc.open_file(handle)
                batches = (reader.get_batch(index) for index in range(reader.num_record_batches))
            else:
                reader = pyarrow.ipc.open_stream(handle)
                batches = _stream_batches(reader, source, name, reporter)
        except pa.ArrowInvalid as error:
            what = "an Arrow IPC file" if is_file_format else "an Arrow IPC stream"
            raise DataFileError(f"{name} can't be read as {what}: {error}") from None
        if is_file_format and source.range_requests:
            reporter.error("is in the Arrow IPC file format, but the source states tss:rangeRequests true: its "
                           "bytes only grow, so its data file must be in the IPC streaming format", name)
        columns = _match_columns(source, fields, reader.schema, reporter, "Arrow", name)
        yield from _batch_rows(batches, columns, "Arrow", reporter)
    finally:
        if isinstance(data, Path):
            handle.close()
