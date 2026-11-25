"""The model of a metadata document: sources, fields, summaries, time windows and time grids.

You get these objects from :func:`rdftss.load`; you do not make them yourself::

    import rdftss

    document = rdftss.load("metadata.ttl")
    source = document.source()             # the only source of the document
    print(source.describe())               # what it is, in words
    source.kind                            # SourceKind.CSV
    source.time_field.name                 # "time"
    source.field("value").unit             # URIRef('http://qudt.org/vocab/unit/...')
    source.value_fields                    # the fields whose values are readings (tss:ValueField)

Everything the specification defines has an attribute, with the defaults filled in
(delimiter ``,``, encoding ``utf-8``, header ``true``, key = the time field, the default field
property of a describing field). Anything else is still in the RDF: ``source.node`` and
``field.node`` are the nodes in ``document.graph``.

While reading the metadata, rdftss reports what breaks the specification as issues
(``source.issues``) instead of failing. What makes a source unreadable is listed in
``source.reading_problems``; reading such a source raises a :class:`~rdftss.errors.DocumentError`
that lists them.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import TYPE_CHECKING, Iterator
from urllib.parse import quote

from rdflib import BNode, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from . import xsd
from .encodings import ENCODINGS, ValueEncoding
from .errors import DocumentError, Issues
from .templates import TemplateError, template_variables
from .times import Duration, Time, TimeGridMath, parse_time, to_instant
from .vocab import TSS, TSS_FROM, XSD

#: The characters a fragment may hold as they are (RFC 3986); the others are percent-encoded.
FRAGMENT_SAFE = "!$&'()*+,;=:@/?-._~"

if TYPE_CHECKING:
    from .document import Document
    from .rows import Rows


class SourceKind(Enum):
    """The five source classes: where the points of a source are."""

    EMBEDDED_RDF = "EmbeddedRDFSource"
    EMBEDDED_JSON = "EmbeddedJSONSource"
    CSV = "CSVSource"
    PARQUET = "ParquetSource"
    ARROW = "ArrowSource"

    @property
    def iri(self) -> URIRef:
        """The IRI of the class, such as ``tss:CSVSource``."""
        return TSS[self.value]

    @property
    def is_embedded(self) -> bool:
        """Whether the points are in the metadata itself."""
        return self in (SourceKind.EMBEDDED_RDF, SourceKind.EMBEDDED_JSON)

    @property
    def is_external(self) -> bool:
        """Whether the points are in a data file (CSV, Parquet or Arrow)."""
        return not self.is_embedded

    @property
    def description(self) -> str:
        """Where the rows are, in words."""
        return {
            SourceKind.EMBEDDED_RDF: "RDF points in the metadata (tss:hasPoint)",
            SourceKind.EMBEDDED_JSON: "JSON rows in the metadata (tss:points)",
            SourceKind.CSV: "a CSV data file",
            SourceKind.PARQUET: "a Parquet data file",
            SourceKind.ARROW: "an Arrow IPC data file (file or streaming format)",
        }[self]

    @property
    def allows_range_requests(self) -> bool:
        """Whether a source of this class may state ``tss:rangeRequests true``: its bytes can only
        grow (an RDF Message Log, a CSV file, an Arrow IPC stream)."""
        return self in (SourceKind.EMBEDDED_RDF, SourceKind.CSV, SourceKind.ARROW)

    @property
    def suffix(self) -> str | None:
        """The usual file name suffix of the data file: ``.csv``, ``.parquet`` or ``.arrow``."""
        return {SourceKind.CSV: ".csv", SourceKind.PARQUET: ".parquet", SourceKind.ARROW: ".arrow"}.get(self)

    @classmethod
    def parse(cls, value) -> "SourceKind":
        """A source kind from a :class:`SourceKind`, a class IRI, a class name such as
        ``"CSVSource"``, or a short name: ``"rdf"``, ``"json"``, ``"csv"``, ``"parquet"`` or ``"arrow"``."""
        if isinstance(value, SourceKind):
            return value
        text = str(value)
        if text.startswith(str(TSS)):
            text = text[len(str(TSS)):]
        short = {"rdf": cls.EMBEDDED_RDF, "json": cls.EMBEDDED_JSON, "csv": cls.CSV,
                 "parquet": cls.PARQUET, "arrow": cls.ARROW, "feather": cls.ARROW}
        if text.lower() in short:
            return short[text.lower()]
        for kind in cls:
            if kind.value.lower() == text.lower():
                return kind
        raise ValueError(f"Unknown source kind {value!r}; use one of: rdf, json, csv, parquet, arrow")


@dataclass(frozen=True)
class TimeWindow:
    """The time window ``[start, end)`` of a source: ``tss:from`` (included) until ``tss:until``
    (not included). A side that the source does not state is open (``None``)."""

    start: Time | None = None
    end: Time | None = None

    def contains(self, time) -> bool:
        """Whether a time lies in the window. ``time`` may be a :class:`~rdftss.times.Time`, a
        ``datetime`` or a text."""
        instant = to_instant(time)
        if self.start is not None and instant < self.start.instant:
            return False
        if self.end is not None and instant >= self.end.instant:
            return False
        return True

    def overlaps(self, start=None, end=None) -> bool:
        """Whether the window overlaps ``[start, end)``. Windows that only touch do not overlap;
        a ``None`` side is open."""
        if end is not None and self.start is not None and self.start.instant >= to_instant(end):
            return False
        if start is not None and self.end is not None and self.end.instant <= to_instant(start):
            return False
        return True

    @property
    def is_open(self) -> bool:
        """Whether the window states neither ``tss:from`` nor ``tss:until``."""
        return self.start is None and self.end is None

    def __str__(self) -> str:
        start = self.start.text if self.start else "open"
        end = self.end.text if self.end else "open"
        return f"[{start}, {end})"


@dataclass(frozen=True)
class CSVDialect:
    """How a CSV data file is written: ``tss:delimiter``, ``tss:encoding`` and ``tss:header``,
    with the defaults filled in (``","``, ``"utf-8"``, ``True``)."""

    delimiter: str = ","
    encoding: str = "utf-8"
    header: bool = True


@dataclass(frozen=True)
class TimeGrid:
    """The time grid of an external source: step n is at ``base`` + n × ``step``.

    ``step_field`` is the field whose column holds the step of each row. Without one (``None``),
    the step of a row is its position in the data file: the first row is step 0."""

    base: Time
    step: Duration
    step_field: "Field | None" = None

    @property
    def by_position(self) -> bool:
        """Whether the step of a row is its position in the data file (no step field)."""
        return self.step_field is None

    def time_of(self, n: int) -> Time:
        """The time of step ``n``. ``P1M`` steps are calendar months: from 2021-01-31, step 1 is
        2021-02-28."""
        return TimeGridMath(self.base, self.step).time_of(n)

    def step_of(self, time) -> int | None:
        """The step at ``time`` (a :class:`~rdftss.times.Time` or a text), or ``None`` when that
        time is not on the grid."""
        if not isinstance(time, Time):
            time = parse_time(str(time))
        return TimeGridMath(self.base, self.step).step_of(time)

    def __str__(self) -> str:
        return f"{self.base.text} + n × {self.step.text}"

    def where_steps_are(self) -> str:
        """Where the step of a row comes from, in words."""
        if self.step_field is None:
            return "the position of the row"
        return f"column {self.step_field.column_name!r}"


@dataclass
class Summary:
    """A stored summary of a field (``tss:summary``), as the metadata states it.

    The numbers are Python values; ``literals`` holds the literals exactly as written, by
    property IRI. Every attribute is optional (``None`` when not stated)."""

    node: object
    missing_value_policy: URIRef | None = None
    count: int | None = None
    missing: int | None = None
    first_time: Time | None = None
    last_time: Time | None = None
    observed_minimum: object = None
    observed_maximum: object = None
    mean: object = None
    median: object = None
    generated_at: Time | None = None
    literals: dict = dataclass_field(default_factory=dict, repr=False)

    def as_dict(self) -> dict:
        """The summary as a plain dict, with times as texts."""
        return {
            "missing_value_policy": str(self.missing_value_policy) if self.missing_value_policy else None,
            "count": self.count,
            "missing": self.missing,
            "first_time": self.first_time.text if self.first_time else None,
            "last_time": self.last_time.text if self.last_time else None,
            "observed_minimum": self.observed_minimum,
            "observed_maximum": self.observed_maximum,
            "mean": self.mean,
            "median": self.median,
            "generated_at": self.generated_at.text if self.generated_at else None,
        }

    def __str__(self) -> str:
        parts = [f"{name}={value}" for name, value in self.as_dict().items() if value is not None]
        return "Summary(" + ", ".join(parts) + ")"


#: The summary attributes: (property, attribute name, kind).
SUMMARY_ATTRIBUTES = [
    (TSS["count"], "count", "count"),
    (TSS.missing, "missing", "count"),
    (TSS.firstTime, "first_time", "time"),
    (TSS.lastTime, "last_time", "time"),
    (TSS.observedMinimum, "observed_minimum", "number"),
    (TSS.observedMaximum, "observed_maximum", "number"),
    (TSS.mean, "mean", "number"),
    (TSS.median, "median", "number"),
    (TSS.generatedAtTime, "generated_at", "time"),
]


def local_name(iri) -> str | None:
    """The last part of an IRI, after its last ``#`` or ``/``: ``ex:value-field`` gives ``value-field``."""
    if not isinstance(iri, URIRef):
        return None
    text = str(iri)
    for separator in ("#", "/", ":"):
        if separator in text:
            last = text.rsplit(separator, 1)[1]
            if last:
                return last
    return text


def best_label(labels) -> str | None:
    """The label to show: an English one, else one without a language, else the first."""
    if not labels:
        return None
    for literal in labels:
        if getattr(literal, "language", None) and literal.language.lower().startswith("en"):
            return str(literal)
    for literal in labels:
        if not getattr(literal, "language", None):
            return str(literal)
    return str(sorted(labels, key=str)[0])


class Field:
    """One column of a source: a ``tss:Field``, which is a value field or a context field.

    The main attributes:

    - ``name``: a short name, unique in the source: the column name, else the last part of the
      field's IRI. Use it with ``source.field(name)``, ``row[name]`` and in DataFrames.
    - ``node`` and ``iri``: the field's node in the graph, and its IRI as text (``None`` for a
      blank node).
    - ``kind``: ``"value"`` for a ``tss:ValueField`` (its values are readings: each is a point),
      ``"context"`` for a ``tss:ContextField`` (the time, a step, a describing field, an
      identifier), ``None`` when the field states neither, or both.
    - ``column_name``, ``column_index``: where its values are in a data file or JSON row.
    - ``datatype``: its ``tss:datatype``, such as ``XSD.double``; ``language`` for ``rdf:langString``.
    - ``minimum``, ``maximum``: the expected range (Python numbers, or ``None``).
    - ``value_encoding``: a :class:`~rdftss.encodings.ValueEncoding`, or ``None``.
    - ``observed_property``, ``unit``, ``sensor``, ``feature_of_interest``: what a value field observes.
    - ``describes``: the value fields this field describes (``tss:describesField``);
      ``described_by``: the describing fields that describe this one.
    - ``property``: the ``tss:property`` it states; ``field_property``: the property that states
      the values of a describing field on the points (stated, or the default one).
    - ``about_url``: the URI template that makes the IRI of each point of a value field (``tss:aboutUrl``).
    - ``summary``: its stored :class:`Summary`, or ``None``.
    - ``role``: ``"time"``, ``"step"``, ``"value"``, ``"describing"`` or ``"context"``.
    """

    def __init__(self, source: "Source", node):
        self.source = source
        self.node = node
        self.name: str = ""
        self.kind: str | None = None
        self.column_name: str | None = None
        self.column_index: int | None = None
        self.datatype: URIRef | None = None
        self.language: str | None = None
        self.minimum = None
        self.maximum = None
        self.minimum_literal: Literal | None = None
        self.maximum_literal: Literal | None = None
        self.value_encoding: ValueEncoding | None = None
        self.observed_property: URIRef | None = None
        self.unit: URIRef | None = None
        self.sensor: URIRef | None = None
        self.feature_of_interest: URIRef | None = None
        self.property: URIRef | None = None
        self.about_url: str | None = None
        self.describes: list[Field] = []
        self.described_by: list[Field] = []
        self.summary: Summary | None = None
        self.labels: list[Literal] = []
        self.comments: list[Literal] = []
        self.description = None

    @property
    def iri(self) -> str | None:
        """The field's IRI as text, or ``None`` for a blank node."""
        return str(self.node) if isinstance(self.node, URIRef) else None

    @property
    def label(self) -> str | None:
        """The field's ``rdfs:label`` (an English one when there are several), or ``None``."""
        return best_label(self.labels)

    @property
    def is_time(self) -> bool:
        """Whether this is the time field (``tss:timeField``)."""
        return self.source.time_field is self

    @property
    def is_step(self) -> bool:
        """Whether this is the step field of a time grid (``tss:stepField``)."""
        return self.source.step_field is self

    @property
    def is_value(self) -> bool:
        """Whether this is a value field (``tss:ValueField``): each of its values is a reading, a point."""
        return self.kind == "value"

    @property
    def is_context(self) -> bool:
        """Whether this is a context field (``tss:ContextField``): its values are not readings."""
        return self.kind == "context"

    @property
    def is_describing(self) -> bool:
        """Whether this field describes value fields (``tss:describesField``), like a quality code."""
        return bool(self.describes)

    @property
    def is_numeric(self) -> bool:
        """Whether the datatype is numeric (``xsd:double``, ``xsd:decimal``, an integer type, ...)."""
        return xsd.is_numeric(self.datatype)

    @property
    def is_encoded(self) -> bool:
        """Whether the data file states this field's values with a value encoding."""
        return self.value_encoding is not None

    @property
    def role(self) -> str:
        """``"time"``, ``"step"``, ``"value"``, ``"describing"`` or ``"context"``."""
        if self.is_time:
            return "time"
        if self.is_step:
            return "step"
        if self.is_value:
            return "value"
        if self.is_describing:
            return "describing"
        return "context"

    @property
    def default_property(self) -> URIRef | None:
        """The default field property of a describing field: a base IRI, ``#``, and the column name
        as a fragment, as the default ``{#_name}`` of ``csvw:propertyUrl``. The base is the data
        file of an external source, and the metadata document of an embedded source. ``None``
        without a column name, or when that IRI is not known."""
        if self.column_name is None:
            return None
        base = self.source.data_file if self.source.kind is not None and self.source.kind.is_external \
            else self.source.document.base
        if not base:
            return None
        return URIRef(base.split("#", 1)[0] + "#" + quote(self.column_name, safe=FRAGMENT_SAFE))

    @property
    def field_property(self) -> URIRef | None:
        """The property that states the values of this describing field on the points it describes:
        its ``tss:property``, or else its :attr:`default_property`. ``None`` for any other field."""
        if not self.describes:
            return None
        return self.property if self.property is not None else self.default_property

    def short(self, term) -> str | None:
        """A term in short form with the document's prefixes, such as ``unit:DEG_C``."""
        return self.source.document.short(term) if term is not None else None

    def as_dict(self) -> dict:
        """What the metadata says about this field, as a plain dict with IRIs as texts."""
        def text(term):
            return str(term) if term is not None else None

        return {
            "name": self.name,
            "field": text(self.node),
            "label": self.label,
            "kind": self.kind,
            "role": self.role,
            "column_name": self.column_name,
            "column_index": self.column_index,
            "datatype": text(self.datatype),
            "language": self.language,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "value_encoding": text(self.value_encoding.iri) if self.value_encoding else None,
            "baseline": text(self.value_encoding.baseline) if self.value_encoding else None,
            "observed_property": text(self.observed_property),
            "unit": text(self.unit),
            "sensor": text(self.sensor),
            "feature_of_interest": text(self.feature_of_interest),
            "describes": [text(other.node) for other in self.describes],
            "field_property": text(self.field_property),
            "about_url": self.about_url,
        }

    def describe(self) -> str:
        """What this field is, in a few lines of text."""
        lines = [f"Field {self.name!r} ({self.role}): {self.short(self.node)}"]
        if self.label:
            lines.append(f"  label:      {self.label}")
        where = []
        if self.column_name is not None:
            where.append(f"column {self.column_name!r}")
        if self.column_index is not None:
            where.append(f"index {self.column_index}")
        if self.source.kind == SourceKind.EMBEDDED_RDF:
            where.append("on its points: " + ("tss:time" if self.is_time else "tss:value" if self.is_value
                                              else self.short(self.field_property) if self.describes else "-"))
        if where:
            lines.append(f"  values in:  {', '.join(where)}")
        datatype = xsd.short_name(self.datatype)
        if self.language:
            datatype += f" (language {self.language})"
        lines.append(f"  datatype:   {datatype}")
        if self.value_encoding is not None:
            lines.append(f"  encoding:   {self.short(self.value_encoding.iri)} {self.value_encoding!r}")
        for name, term in (("observes", self.observed_property), ("unit", self.unit),
                           ("sensor", self.sensor), ("feature", self.feature_of_interest)):
            if term is not None:
                lines.append(f"  {name + ':':<11} {self.short(term)}")
        if self.minimum is not None or self.maximum is not None:
            lines.append(f"  expected:   {self.minimum} to {self.maximum}")
        if self.describes:
            lines.append(f"  describes:  {', '.join(other.name for other in self.describes)}, "
                         f"with the property {self.short(self.field_property)}")
        if self.about_url:
            lines.append(f"  point IRIs: {self.about_url}")
        if self.summary is not None:
            lines.append(f"  summary:    {self.summary}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<Field {self.name!r} {xsd.short_name(self.datatype)} ({self.role})>"


class Source:
    """A source: a table of readings that share one time field.

    Each row has one time and at most one value for each field. Each value of a value field is
    one reading: a point. The main attributes:

    - ``kind``: the :class:`SourceKind` (where the rows are); ``None`` when the source states
      no source class, or several.
    - ``node``, ``iri``, ``name``, ``label``: who it is.
    - ``fields``: its :class:`Field` objects; ``time_field``, ``key`` and ``step_field``;
      ``value_fields``, ``context_fields`` and ``describing_fields``.
    - ``window``: its :class:`TimeWindow`.
    - ``immutable``: ``True`` when the source is final; ``False`` (also when not stated) when it
      is growing: rows may still be added. ``growing`` is its opposite. ``range_requests``: whether
      its publisher promises that its bytes only grow (``tss:rangeRequests true``).
      ``produced_by``: provenance.
    - ``data_file``: the IRI of the data file of an external source; ``csv``: the
      :class:`CSVDialect` of a CSV source; ``time_grid``: its :class:`TimeGrid`, or ``None``.
    - ``issues``: what breaks the specification; ``reading_problems``: what stops reading it.

    Reading and analysis:

    - :meth:`read` gives the rows, in time order; :meth:`iter_rows` streams them;
      :meth:`points` gives the points, one per reading.
    - :meth:`statistics`, :meth:`gaps`, :meth:`resample`: analysis of the rows.
    - :meth:`to_pandas`, :meth:`to_polars`, :meth:`to_arrow`: DataFrames and tables.
    - :meth:`sparql`: SPARQL queries over the points and the metadata.
    - :meth:`expand`: the points as RDF; :meth:`convert`: another storage class.
    """

    def __init__(self, document: "Document", node):
        self.document = document
        self.node = node
        self.kind: SourceKind | None = None
        self.labels: list[Literal] = []
        self.comments: list[Literal] = []
        self.immutable: bool = False
        self.range_requests: bool = False
        self.produced_by: URIRef | None = None
        self.description = None
        self.window = TimeWindow()
        self.fields: list[Field] = []
        self.time_field: Field | None = None
        self.key: Field | None = None
        self.step_field: Field | None = None
        self.time_grid: TimeGrid | None = None
        self.data_file: str | None = None
        self.csv: CSVDialect | None = None
        self.points_literal: Literal | None = None
        self.issues = Issues()
        self.reading_problems: list[str] = []
        self._data_override = None

    # -- who it is -------------------------------------------------------------------------------

    @property
    def growing(self) -> bool:
        """Whether rows may still be added: the source does not state ``tss:immutable true``."""
        return not self.immutable

    @property
    def iri(self) -> str | None:
        """The source's IRI as text, or ``None`` for a blank node."""
        return str(self.node) if isinstance(self.node, URIRef) else None

    @property
    def label(self) -> str | None:
        """The source's ``rdfs:label`` (an English one when there are several), or ``None``."""
        return best_label(self.labels)

    @property
    def name(self) -> str:
        """A short name: the last part of the source's IRI, else its label, else ``"source"``."""
        return local_name(self.node) or self.label or "source"

    @property
    def graph(self):
        """The RDF graph of the document."""
        return self.document.graph

    @property
    def value_fields(self) -> list[Field]:
        """The value fields (``tss:ValueField``): the fields whose values are readings."""
        return [field for field in self.fields if field.is_value]

    @property
    def context_fields(self) -> list[Field]:
        """The fields that are not value fields: the time field, the step field, describing fields, ..."""
        return [field for field in self.fields if not field.is_value]

    @property
    def describing_fields(self) -> list[Field]:
        """The context fields that describe a value field (``tss:describesField``)."""
        return [field for field in self.fields if field.describes]

    def values_of(self, predicate) -> list:
        """Every value the graph states for this source with ``predicate``, for statements the
        model does not cover."""
        return sorted(self.graph.objects(self.node, URIRef(predicate)), key=str)

    def field(self, key) -> Field:
        """One field of the source, by its name, column name, IRI (in full or with a prefix the
        document uses, such as ``"ex:value-field"``), the last part of its IRI, or its label.

        Raises :class:`KeyError`, listing the field names, when there is no such field."""
        if isinstance(key, Field):
            if key.source is self:
                return key
            key = key.node
        if isinstance(key, (URIRef, BNode)):
            for candidate in self.fields:
                if candidate.node == key:
                    return candidate
        elif isinstance(key, str):
            for test in (
                lambda f: f.name == key,
                lambda f: f.column_name == key,
                lambda f: f.iri == key,
                lambda f: f.iri is not None and f.iri == self.document.curie_to_iri(key),
                lambda f: local_name(f.node) == key,
                lambda f: f.label is not None and f.label.lower() == key.lower(),
            ):
                found = [candidate for candidate in self.fields if test(candidate)]
                if len(found) == 1:
                    return found[0]
        names = ", ".join(repr(candidate.name) for candidate in self.fields)
        raise KeyError(f"{self.name} has no field {key!r}. Its fields are: {names}")

    def default_field(self, key=None) -> Field:
        """``self.field(key)``, or, when ``key`` is ``None``, the only value field. Raises
        :class:`ValueError` when there is no single obvious field."""
        if key is not None:
            return self.field(key)
        candidates = self.value_fields
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ValueError(f"{self.name} has no value field (tss:ValueField); say which field, "
                             f"e.g. field={self.fields[-1].name!r}" if self.fields else f"{self.name} has no field")
        names = ", ".join(repr(candidate.name) for candidate in candidates)
        raise ValueError(f"{self.name} has several value fields ({names}); say which one, "
                         f"e.g. field={candidates[0].name!r}")

    # -- describing it ---------------------------------------------------------------------------

    def how_to_read(self) -> dict:
        """Where the readings are and how to read them, as a plain dict: the storage class, the
        data file or the embedded predicate, how each field is bound to a column, a JSON key or a
        property of the points, the time grid and the value encodings."""
        def text(term):
            return str(term) if term is not None else None

        result = {
            "source": text(self.node),
            "class": text(self.kind.iri) if self.kind else None,
            "where": self.kind.description if self.kind else None,
            "data_file": self.data_file,
            "embedded_predicate": text({SourceKind.EMBEDDED_RDF: TSS.hasPoint,
                                        SourceKind.EMBEDDED_JSON: TSS.points}.get(self.kind)),
            "range_requests": self.range_requests,
            "csv": None,
            "time_field": text(self.time_field.node) if self.time_field else None,
            "time_grid": None,
            "window": [self.window.start.text if self.window.start else None,
                       self.window.end.text if self.window.end else None],
            "fields": [],
        }
        if self.csv is not None:
            result["csv"] = {"delimiter": self.csv.delimiter, "encoding": self.csv.encoding,
                             "header": self.csv.header}
        if self.time_grid is not None:
            step_field = self.time_grid.step_field
            result["time_grid"] = {"base": self.time_grid.base.text, "step": self.time_grid.step.text,
                                   "step_field": text(step_field.node) if step_field else None,
                                   "step_column": step_field.column_name if step_field else None,
                                   "by_position": self.time_grid.by_position}
        for field in self.fields:
            binding = {
                "field": text(field.node),
                "name": field.name,
                "kind": field.kind,
                "role": field.role,
                "datatype": text(field.datatype),
                "column_name": field.column_name,
                "column_index": field.column_index,
            }
            if self.kind == SourceKind.EMBEDDED_RDF or field.describes:
                binding["property"] = text(TSS.time if field.is_time else TSS.value if field.is_value
                                           else field.field_property)
            if field.value_encoding is not None:
                binding["value_encoding"] = text(field.value_encoding.iri)
                binding["baseline"] = text(field.value_encoding.baseline)
            if field.about_url is not None:
                binding["about_url"] = field.about_url
            result["fields"].append(binding)
        return result

    def describe(self) -> str:
        """What this source is, in a few lines of text: class, window, fields, issues."""
        short = self.document.short
        lines = [f"Source {short(self.node)}" + (f" — {self.label}" if self.label else "")]
        lines.append(f"  class:      {short(self.kind.iri) if self.kind else '(no source class)'}"
                     + (f": {self.kind.description}" if self.kind else ""))
        if self.data_file:
            lines.append(f"  data file:  {self.data_file}")
        if self.csv is not None:
            lines.append(f"  CSV:        delimiter {self.csv.delimiter!r}, encoding {self.csv.encoding}, "
                         f"header {'yes' if self.csv.header else 'no'}")
        lines.append(f"  window:     {self.window}")
        if self.time_grid is not None:
            lines.append(f"  time grid:  {self.time_grid} (the step of a row: {self.time_grid.where_steps_are()})")
        lines.append("  changes:    " + ("immutable: final, no row is added any more" if self.immutable
                                          else "growing: rows may still be added")
                     + ("; the bytes only grow (tss:rangeRequests true)" if self.range_requests else ""))
        if self.produced_by is not None:
            lines.append(f"  produced by {short(self.produced_by)}")
        lines.append(f"  fields ({len(self.fields)}):")
        for field in self.fields:
            extra = []
            if field.unit is not None:
                extra.append(short(field.unit))
            if field.observed_property is not None:
                extra.append(f"observes {short(field.observed_property)}")
            if field.describes:
                extra.append(f"describes {', '.join(other.name for other in field.describes)}")
            if field.value_encoding is not None:
                extra.append(repr(field.value_encoding))
            details = f" — {', '.join(extra)}" if extra else ""
            lines.append(f"    {field.name:<20} {field.role:<11} {xsd.short_name(field.datatype)}{details}")
        if self.issues:
            lines.append(f"  issues ({len(self.issues)}):")
            lines.extend(f"    {issue}" for issue in self.issues)
        return "\n".join(lines)

    def __repr__(self) -> str:
        kind = self.kind.value if self.kind else "no source class"
        return f"<Source {self.document.short(self.node)} ({kind}, {len(self.fields)} fields)>"

    # -- data access -----------------------------------------------------------------------------

    def set_data_file(self, data) -> "Source":
        """Read the rows of this external source from ``data`` (a path, or a binary file
        object) instead of its ``tss:dataFile``. Returns the source."""
        if self.kind is not None and self.kind.is_embedded:
            raise DocumentError(f"{self.name} is an embedded source: its rows are in the metadata")
        self._data_override = data
        return self

    def check_readable(self) -> None:
        """Raise a :class:`~rdftss.errors.DocumentError` when the metadata does not say what
        reading the rows needs (see ``reading_problems``)."""
        if self.reading_problems:
            problems = "\n".join(f"  - {problem}" for problem in self.reading_problems)
            raise DocumentError(f"Can't read the rows of {self.document.short(self.node)}:\n{problems}")

    # -- reading ---------------------------------------------------------------------------------

    def read(self, *, strict: bool = True, window: bool = True, fields=None) -> "Rows":
        """The rows of the source, in time order, as :class:`~rdftss.rows.Rows` (a list).

        - ``strict=True`` raises a :class:`~rdftss.errors.DataError` at the first error the
          specification names (a row without a time, two rows with the same time, a value not
          in the lexical form of its datatype, ...). With ``strict=False`` the bad value or
          row is left out, and every problem is listed in ``rows.issues``.
        - ``window=True`` keeps the rows in the time window only (the specification's rule);
          ``window=False`` reads every row of the data file.
        - ``fields``: read only these fields (names or Field objects); the time field is always read.
        """
        from .rows import read_rows

        return read_rows(self, strict=strict, window=window, fields=fields)

    def iter_rows(self, *, strict: bool = True, window: bool = True, fields=None, issues=None):
        """The rows one by one, without holding them all in memory. External data files are
        streamed in file order; embedded rows come in time order. With ``strict=False``,
        problems are appended to ``issues`` (a list) when one is given."""
        from .rows import iter_rows

        return iter_rows(self, strict=strict, window=window, fields=fields, issues=issues)

    def points(self, *, strict: bool = True, window: bool = True, fields=None):
        """The points of the source, one per reading: a value of a value field in a row, with the
        time of the row and the values of the describing fields that describe it. A generator of
        :class:`~rdftss.expansion.Point`, in the order of :meth:`iter_rows`."""
        from .expansion import points

        return points(self, strict=strict, window=window, fields=fields)

    def __iter__(self) -> Iterator:
        return iter(self.read())

    # -- analysis (shortcuts to Rows) ------------------------------------------------------------

    def statistics(self, field=None):
        """Count, missing, first and last time, minimum, maximum, mean and median of a field's
        values in the window, as the stored summaries define them. See :meth:`Rows.statistics`."""
        return self.read(fields=[field] if field is not None else None).statistics(field)

    def gaps(self):
        """The usual time between two rows, and the longer intervals. See :meth:`Rows.gaps`."""
        return self.read(fields=[]).gaps()

    def resample(self, every, field=None, how: str = "mean"):
        """Aggregate a field per period (``"1h"``, ``"1d"``, ``"P1M"``, ...). See :meth:`Rows.resample`."""
        return self.read().resample(every, field=field, how=how)

    def to_arrow(self, **options):
        """The rows in the window as a ``pyarrow.Table``. See :meth:`Rows.to_arrow`."""
        return self.read().to_arrow(**options)

    def to_pandas(self, **options):
        """The rows in the window as a pandas DataFrame. See :meth:`Rows.to_pandas`."""
        return self.read().to_pandas(**options)

    def to_polars(self, **options):
        """The rows in the window as a polars DataFrame. See :meth:`Rows.to_polars`."""
        return self.read().to_polars(**options)

    def verify_summaries(self, **options):
        """Recompute this source's stored summaries from the data, and compare. See
        :func:`rdftss.summaries.verify_summaries`."""
        from .summaries import verify_summaries

        return verify_summaries(self, **options)

    def sparql(self, query: str, **options):
        """Run one read-only SPARQL query on this source: its points (``tss:hasPoint``, with
        ``tss:field``, ``tss:time`` and ``tss:value``) and its document's metadata. ``options`` are
        those of :class:`rdftss.sparql.TripleStore`; for several queries, make a store once."""
        from .sparql import TripleStore

        with TripleStore([self], **options) as store:
            return store.query(query)

    # -- materialisation -------------------------------------------------------------------------

    def expand(self, destination=None, **options):
        """Expand this source into its points as RDF, as the specification says. See
        :func:`rdftss.expansion.expand`."""
        from .expansion import expand

        return expand(self, destination, **options)

    def convert(self, to, **options):
        """This source in another storage class (``"csv"``, ``"parquet"``, ``"arrow"``,
        ``"json"`` or ``"rdf"``), as a new document. See :func:`rdftss.conversion.convert`."""
        from .conversion import convert

        return convert(self, to, **options)

# ---------------------------------------------------------------------------------------------
# Reading the model from the graph
# ---------------------------------------------------------------------------------------------

SOURCE_CLASSES = {kind.iri: kind for kind in SourceKind}
DATA_FILE_PROPERTIES = (TSS.dataFile, TSS.delimiter, TSS.encoding, TSS.header)
GRID_PROPERTIES = (TSS.timeBase, TSS.timeStep, TSS.stepField)
#: The datatypes of a time: an ``xsd:dateTime`` with a timezone, or an ``xsd:dateTimeStamp``.
TIME_DATATYPES = (XSD.dateTime, XSD.dateTimeStamp)
#: The two kinds of field: its ``rdf:type`` -> ``Field.kind``.
FIELD_KINDS = {TSS.ValueField: "value", TSS.ContextField: "context"}
#: What only a value field states: a context field says nothing about what it observes.
VALUE_FIELD_ONLY = (TSS.observedProperty, TSS.sensor, TSS.featureOfInterest, TSS.aboutUrl)

#: Classes of earlier drafts of the specification, and the classes that replace them.
EARLIER_DRAFT_CLASSES = {
    TSS.EmbeddedRDFSnippet: TSS.EmbeddedRDFSource,
    TSS.EmbeddedJSONSnippet: TSS.EmbeddedJSONSource,
    TSS.CSVSnippet: TSS.CSVSource,
    TSS.ParquetSnippet: TSS.ParquetSource,
    TSS.ArrowSnippet: TSS.ArrowSource,
    TSS.Snippet: TSS.Source,
    TSS.MetaField: TSS.ContextField,
}
#: Properties of earlier drafts of the specification, and what replaces them.
EARLIER_DRAFT_PROPERTIES = {
    TSS.mutable: "tss:mutable false is now tss:immutable true, and tss:mutable true is the default (leave it out)",
    TSS.committedUntil: "it is no longer used; a consumer finds out whether a growing source changed with "
                        "HTTP validators (ETag, Last-Modified)",
    TSS.snippetDescription: "it is now tss:sourceDescription",
}
#: The source classes that can't state tss:rangeRequests true, and why.
NO_RANGE_REQUESTS = {
    SourceKind.EMBEDDED_JSON: "its tss:points literal is written anew on every Write",
    SourceKind.PARQUET: "a Parquet file is written anew when rows are added (its footer indexes its row groups)",
}


class _NodeReader:
    """Reads the statements of one node, reporting problems as issues."""

    def __init__(self, document: "Document", node, issues: Issues, where: str):
        self.document = document
        self.graph = document.graph
        self.node = node
        self.issues = issues
        self.where = where

    def name(self, prop) -> str:
        return self.document.short(prop)

    def values(self, prop) -> list:
        return sorted(self.graph.objects(self.node, prop), key=lambda term: (type(term).__name__, str(term)))

    def one(self, prop):
        values = self.values(prop)
        if len(values) > 1:
            shown = ", ".join(self.document.short(value) for value in values[:5])
            self.issues.error(f"states {len(values)} values for {self.name(prop)} ({shown}); only one is allowed",
                              self.where)
        return values[0] if values else None

    def iri(self, prop):
        value = self.one(prop)
        if value is not None and not isinstance(value, URIRef):
            self.issues.error(f"{self.name(prop)} must be an IRI, not {self.document.short(value)}", self.where)
            return None
        return value

    def literal(self, prop) -> Literal | None:
        value = self.one(prop)
        if value is not None and not isinstance(value, Literal):
            self.issues.error(f"{self.name(prop)} must be a literal, not {self.document.short(value)}", self.where)
            return None
        return value

    def text(self, prop) -> str | None:
        value = self.literal(prop)
        return str(value) if value is not None else None

    def boolean(self, prop) -> bool | None:
        value = self.literal(prop)
        if value is None:
            return None
        try:
            result = xsd.read_value(str(value), XSD.boolean)
        except ValueError:
            self.issues.error(f"{self.name(prop)} must be true or false, not {str(value)!r}", self.where)
            return None
        if value.datatype not in (None, XSD.boolean):
            self.issues.warning(f"{self.name(prop)} should be an xsd:boolean", self.where)
        return result

    def integer(self, prop, minimum: int | None = 0) -> int | None:
        value = self.literal(prop)
        if value is None:
            return None
        try:
            number = xsd.read_value(str(value), XSD.integer)
        except ValueError:
            self.issues.error(f"{self.name(prop)} must be a whole number, not {str(value)!r}", self.where)
            return None
        if minimum is not None and number < minimum:
            self.issues.error(f"{self.name(prop)} must be {minimum} or more, not {number}", self.where)
            return None
        return number

    def time(self, prop) -> tuple[Time | None, bool]:
        """(the time, whether it was stated). A time that can't be read is reported and gives
        (None, True)."""
        value = self.literal(prop)
        if value is None:
            return None, bool(self.values(prop))
        if value.datatype not in TIME_DATATYPES:
            self.issues.error(f"{self.name(prop)} must be an xsd:dateTime literal (or an xsd:dateTimeStamp), "
                              f"not {self.document.short(value)}", self.where)
        try:
            return parse_time(str(value), require_timezone=True), True
        except ValueError as error:
            self.issues.error(f"{self.name(prop)} is not a valid time: {error}", self.where)
            return None, True


def build_sources(document: "Document") -> list[Source]:
    """Find every source of a document and read its metadata (see :class:`Source`)."""
    graph = document.graph
    candidates = set()
    for source_class in list(SOURCE_CLASSES) + [TSS.Source] + list(EARLIER_DRAFT_CLASSES):
        if source_class != TSS.MetaField:
            candidates.update(graph.subjects(RDF.type, source_class))
    candidates.update(graph.subjects(TSS.hasField, None))
    candidates.update(graph.subjects(TSS.hasPoint, None))
    candidates.update(graph.subjects(TSS.points, None))
    candidates.update(graph.subjects(TSS.dataFile, None))
    nodes = sorted(candidates, key=lambda node: (isinstance(node, BNode), str(node)))

    # A field linked from more than one source
    owners: dict = {}
    for node in nodes:
        for field_node in graph.objects(node, TSS.hasField):
            owners.setdefault(field_node, []).append(node)

    sources = [Source(document, node) for node in nodes]
    for source in sources:
        _read_source(source, owners)
    return sources


def _read_source(source: Source, owners: dict) -> None:
    document = source.document
    graph = document.graph
    where = f"source {document.short(source.node)}"
    node = _NodeReader(document, source.node, source.issues, where)
    problems = source.reading_problems

    # The source class
    types = set(graph.objects(source.node, RDF.type))
    kinds = [SOURCE_CLASSES[cls] for cls in types if cls in SOURCE_CLASSES]
    if not kinds:
        earlier = sorted(cls for cls in types if cls in EARLIER_DRAFT_CLASSES)
        hint = "".join(f"; {document.short(cls)} is from an earlier draft, it is now "
                       f"{document.short(EARLIER_DRAFT_CLASSES[cls])}" for cls in earlier)
        source.issues.error("states no source class (tss:EmbeddedRDFSource, tss:EmbeddedJSONSource, "
                            "tss:CSVSource, tss:ParquetSource or tss:ArrowSource), so it does not say "
                            "where its readings are" + hint, where)
        problems.append("it states no source class, so it does not say where its readings are")
    elif len(kinds) > 1:
        names = ", ".join(sorted(document.short(kind.iri) for kind in kinds))
        source.issues.error(f"states several source classes ({names}); it must state exactly one", where)
        problems.append(f"it states several source classes ({names})")
    else:
        source.kind = kinds[0]
    kind = source.kind

    source.labels = node.values(RDFS.label)
    source.comments = node.values(RDFS.comment)
    source.immutable = node.boolean(TSS.immutable) is True
    source.range_requests = node.boolean(TSS.rangeRequests) is True
    for old, advice in EARLIER_DRAFT_PROPERTIES.items():
        if node.values(old):
            source.issues.warning(f"{document.short(old)} is from an earlier draft of the specification: {advice}",
                                  where)
    source.produced_by = node.iri(TSS.producedBy)
    source.description = node.one(TSS.sourceDescription)

    # The time window
    start, start_stated = node.time(TSS_FROM)
    end, end_stated = node.time(TSS.until)
    if (start_stated and start is None) or (end_stated and end is None):
        problems.append("its time window (tss:from, tss:until) has a time that can't be read")
    if start is not None and end is not None and not end.instant > start.instant:
        source.issues.error(f"tss:until ({end.text}) must come after tss:from ({start.text})", where)
    source.window = TimeWindow(start, end)

    # The fields
    for field_node in sorted(graph.objects(source.node, TSS.hasField),
                             key=lambda term: (isinstance(term, BNode), str(term))):
        if isinstance(field_node, Literal):
            source.issues.error(f"tss:hasField must link to a field, not the literal {str(field_node)!r}", where)
            continue
        if len(owners.get(field_node, [])) > 1:
            source.issues.error(f"the field {document.short(field_node)} belongs to several sources; "
                                "each field belongs to exactly one", where)
        source.fields.append(_read_field(source, field_node))
    if not source.fields:
        source.issues.error("has no field (tss:hasField)", where)
    _name_fields(source)

    # The time field and the key
    time_node = node.one(TSS.timeField)
    if time_node is None:
        source.issues.error("states no tss:timeField", where)
        problems.append("it states no tss:timeField, so its rows have no time")
    else:
        found = [field for field in source.fields if field.node == time_node]
        if not found:
            source.issues.error(f"its tss:timeField {document.short(time_node)} is not one of its fields "
                                "(tss:hasField)", where)
            problems.append("its tss:timeField is not one of its fields")
        else:
            source.time_field = found[0]
            if source.time_field.datatype not in TIME_DATATYPES:
                source.issues.error(f"the tss:datatype of the time field must be xsd:dateTime or xsd:dateTimeStamp, "
                                    f"not {xsd.short_name(source.time_field.datatype)}", where)
    key_node = node.one(TSS.key)
    if key_node is not None and (source.time_field is None or key_node != source.time_field.node):
        source.issues.error(f"its tss:key ({document.short(key_node)}) must be its time field", where)
    source.key = source.time_field

    # Where the rows are: the rules of the source class come from the tables below
    if kind is not None:
        for prop in NOT_ALLOWED[kind]:
            if node.values(prop):
                source.issues.error(f"a {document.short(kind.iri)} must not state {document.short(prop)}", where)
        for prop in NO_EFFECT.get(kind, ()):
            if node.values(prop):
                source.issues.warning(f"{document.short(prop)} has no effect on a {document.short(kind.iri)}", where)
        if source.range_requests and kind in NO_RANGE_REQUESTS:
            source.issues.error(f"a {document.short(kind.iri)} must not state tss:rangeRequests true: "
                                f"{NO_RANGE_REQUESTS[kind]}", where)
        STORAGE_RULES[kind](source, node, where)

    # Columns of the fields
    _check_columns(source, where)

    # tss:describesField: a context field describes value fields of the same source
    for field in source.fields:
        for described in sorted(graph.objects(field.node, TSS.describesField), key=str):
            match = [other for other in source.fields if other.node == described]
            if not match:
                source.issues.error(f"field {field.name!r}: tss:describesField {document.short(described)} "
                                    "is not a field of the same source", where)
            elif not match[0].is_value:
                source.issues.error(f"field {field.name!r}: tss:describesField {document.short(described)} must be "
                                    "a value field (tss:ValueField) of the same source", where)
            else:
                field.describes.append(match[0])
                match[0].described_by.append(field)

    _check_field_kinds(source, where)
    _check_field_properties(source, where)
    _check_about_urls(source, where)
    _check_changes(source, where)
    _sort_fields(source)
    if source.fields and not source.value_fields:
        source.issues.warning("has no value field (tss:ValueField), so it holds no readings", where)


def _check_field_kinds(source: Source, where: str) -> None:
    """The time field, the step field and every describing field are context fields, and a
    context field says nothing about what it observes (the specification's #field-kinds)."""
    document = source.document
    for field in source.fields:
        if field.kind == "value":
            for role, is_role in (("the time field", field.is_time), ("the step field", field.is_step),
                                  ("a describing field", field.is_describing)):
                if is_role:
                    source.issues.error(f"field {field.name!r} is {role}, so it must be a context field "
                                        "(tss:ContextField), not a tss:ValueField", where)
        elif field.kind == "context":
            for prop in VALUE_FIELD_ONLY:
                if source.graph.value(field.node, prop) is not None:
                    source.issues.error(f"field {field.name!r} is a context field, so it must not state "
                                        f"{document.short(prop)}: only a value field does", where)
        if field.about_url is not None and source.kind == SourceKind.EMBEDDED_RDF:
            source.issues.error(f"field {field.name!r}: the fields of a tss:EmbeddedRDFSource do not state "
                                "tss:aboutUrl, as its points are RDF nodes already", where)


def _check_field_properties(source: Source, where: str) -> None:
    """``tss:property`` and the field property of each describing field (the specification's
    #point-values)."""
    document = source.document
    used: dict = {}
    for field in source.fields:
        if field.property is not None and not field.describes:
            source.issues.error(f"field {field.name!r} states tss:property, but only a describing field "
                                "(tss:describesField) has one", where)
        if not field.describes:
            continue
        prop = field.field_property
        if prop is None:
            if field.column_name is None:
                if source.kind == SourceKind.EMBEDDED_RDF:
                    source.issues.error(f"field {field.name!r} is a describing field of a tss:EmbeddedRDFSource, "
                                        "so it must state tss:property or tss:columnName (which makes its default "
                                        "property)", where)
                    source.reading_problems.append(f"describing field {document.short(field.node)} has no field "
                                                   "property, so its values can't be found on the points")
            elif source.kind == SourceKind.EMBEDDED_RDF:
                source.reading_problems.append(f"the default property of describing field "
                                               f"{document.short(field.node)} is made from the IRI of the metadata "
                                               "document, which is not known: load the document from a file or "
                                               "URL, or give base=")
            continue
        if prop == RDF.type or str(prop).startswith(str(TSS)):
            source.issues.error(f"field {field.name!r}: its field property {document.short(prop)} must not be "
                                "rdf:type or a term of the TSS vocabulary", where)
        if prop in used:
            source.issues.error(f"fields {used[prop].name!r} and {field.name!r} have the same field property "
                                f"{document.short(prop)}", where)
        used[prop] = field


def _check_about_urls(source: Source, where: str) -> None:
    """The URI template of ``tss:aboutUrl``: valid, and every variable is the column name of a
    field of the source (the specification's #point-iris)."""
    names = {field.column_name for field in source.fields if field.column_name is not None}
    for field in source.fields:
        if field.about_url is None:
            continue
        try:
            variables = template_variables(field.about_url)
        except TemplateError as error:
            source.issues.error(f"field {field.name!r}: tss:aboutUrl {field.about_url!r} is not a valid URI "
                                f"template: {error}", where)
            field.about_url = None
            continue
        unknown = [name for name in variables if name not in names]
        if unknown:
            source.issues.error(f"field {field.name!r}: the variables {', '.join(map(repr, unknown))} of "
                                "tss:aboutUrl are not the tss:columnName of a field of the source", where)


def _check_changes(source: Source, where: str) -> None:
    """The rules on growing and immutable sources (the specification's #change)."""
    document = source.document
    if source.growing:
        for field in source.fields:
            if field.summary is not None:
                source.issues.error(f"field {field.name!r} has a tss:summary, but the source is growing (it does "
                                    "not state tss:immutable true); only an immutable source has stored summaries",
                                    where)
    if source.kind == SourceKind.EMBEDDED_RDF and source.growing and document.message_log is False:
        source.issues.error("a growing tss:EmbeddedRDFSource must be written as an RDF Message Log "
                            '(VERSION "1.2-messages", and a delimiter MESSAGE after each message), so that a new '
                            "row is appended as one more message", where)
    if document.message_log and any(str(value).strip() in ("false", "0")
                                    for value in document.graph.objects(source.node, TSS.immutable)):
        source.issues.error("an RDF Message Log must not state tss:immutable false: the merge of its messages "
                            "would state both false and true once the source ends (false is the default)", where)


# ---------------------------------------------------------------------------------------------
# The rules of each source class
# ---------------------------------------------------------------------------------------------

def _read_rdf_points(source: Source, node: _NodeReader, where: str) -> None:
    """tss:EmbeddedRDFSource: the points are the nodes it links to with tss:hasPoint."""
    _no_value_encodings(source, where)


def _read_json_points(source: Source, node: _NodeReader, where: str) -> None:
    """tss:EmbeddedJSONSource: exactly one rdf:JSON literal in tss:points, and a column name
    (its key in the JSON rows) on every field."""
    _no_value_encodings(source, where)
    document = source.document
    literals = node.values(TSS.points)
    if len(literals) != 1:
        source.issues.error(f"a tss:EmbeddedJSONSource must state exactly one tss:points "
                            f"(it states {len(literals)})", where)
    if not literals:
        source.reading_problems.append("it states no tss:points")
    elif not isinstance(literals[0], Literal):
        source.reading_problems.append("its tss:points is not a literal")
    else:
        source.points_literal = literals[0]
        if literals[0].datatype != RDF.JSON:
            source.issues.error("tss:points must be a literal with datatype rdf:JSON", where)
    for field in source.fields:
        if field.column_name is None:
            source.issues.error(f"field {field.name!r} states no tss:columnName, which is its key in "
                                "the JSON rows", where)
            source.reading_problems.append(f"field {document.short(field.node)} has no tss:columnName (its JSON key)")


def _read_external(source: Source, node: _NodeReader, where: str) -> None:
    """tss:ParquetSource and tss:ArrowSource (and the common part of tss:CSVSource): a data
    file, and maybe a time grid."""
    data_file = node.iri(TSS.dataFile)
    if data_file is None:
        source.issues.error("states no tss:dataFile", where)
        source.reading_problems.append("it states no tss:dataFile")
    else:
        source.data_file = str(data_file)
    _read_time_grid(source, node, where)


def _read_csv(source: Source, node: _NodeReader, where: str) -> None:
    """tss:CSVSource: a data file, and the CSV settings with their defaults."""
    _read_external(source, node, where)
    problems = source.reading_problems
    delimiter = node.text(TSS.delimiter)
    if delimiter is None:
        delimiter = ","
    elif len(delimiter) != 1:
        source.issues.error(f"tss:delimiter must be one character, not {delimiter!r}", where)
        problems.append(f"its tss:delimiter {delimiter!r} is not one character")
    encoding = node.text(TSS.encoding) or "utf-8"
    try:
        codecs.lookup(encoding)
    except LookupError:
        source.issues.error(f"tss:encoding {encoding!r} is not a known text encoding", where)
        problems.append(f"its tss:encoding {encoding!r} is not a known text encoding")
    header = node.boolean(TSS.header)
    source.csv = CSVDialect(delimiter, encoding, True if header is None else header)


#: What each source class must not state.
NOT_ALLOWED = {
    SourceKind.EMBEDDED_RDF: (TSS.points,) + DATA_FILE_PROPERTIES + GRID_PROPERTIES,
    SourceKind.EMBEDDED_JSON: (TSS.hasPoint,) + DATA_FILE_PROPERTIES + GRID_PROPERTIES,
    SourceKind.CSV: (TSS.hasPoint, TSS.points),
    SourceKind.PARQUET: (TSS.hasPoint, TSS.points),
    SourceKind.ARROW: (TSS.hasPoint, TSS.points),
}
#: What has no effect on a source class (a warning).
NO_EFFECT = {
    SourceKind.PARQUET: (TSS.delimiter, TSS.encoding, TSS.header),
    SourceKind.ARROW: (TSS.delimiter, TSS.encoding, TSS.header),
}
#: How the part of the metadata that is particular to each source class is read.
STORAGE_RULES = {
    SourceKind.EMBEDDED_RDF: _read_rdf_points,
    SourceKind.EMBEDDED_JSON: _read_json_points,
    SourceKind.CSV: _read_csv,
    SourceKind.PARQUET: _read_external,
    SourceKind.ARROW: _read_external,
}


def _no_value_encodings(source: Source, where: str) -> None:
    """The fields of an embedded source state their readings: no value encoding, no baseline."""
    for field in source.fields:
        if field.value_encoding is not None or source.graph.value(field.node, TSS.baseline) is not None:
            source.issues.error(f"field {field.name!r}: the fields of an embedded source must not "
                                "state tss:valueEncoding or tss:baseline", where)
            field.value_encoding = None


def _read_time_grid(source: Source, node: _NodeReader, where: str) -> None:
    """The time grid of an external source: tss:timeBase and tss:timeStep, and optionally
    tss:stepField; without a step field, the step of a row is its position in the data file."""
    problems = source.reading_problems
    base, base_stated = node.time(TSS.timeBase)
    step_literal = node.literal(TSS.timeStep)
    step_node = node.one(TSS.stepField)
    if not base_stated and step_literal is None:
        if step_node is not None:
            source.issues.error("a source without a time grid must not state tss:stepField", where)
        return
    if not base_stated or step_literal is None:
        source.issues.error("a time grid needs both tss:timeBase and tss:timeStep", where)
        problems.append("its time grid states only one of tss:timeBase and tss:timeStep")
    step = None
    if step_literal is not None:
        if step_literal.datatype != XSD.duration:
            source.issues.error("tss:timeStep must be an xsd:duration literal", where)
        try:
            step = Duration.parse(str(step_literal))
            if not step.is_positive:
                source.issues.error(f"tss:timeStep must be more than zero, not {step.text}", where)
                problems.append(f"its tss:timeStep {step.text} is not more than zero")
                step = None
        except ValueError as error:
            source.issues.error(f"tss:timeStep is not valid: {error}", where)
            problems.append("its tss:timeStep can't be read")
    step_field = None
    if step_node is not None:
        found = [field for field in source.fields if field.node == step_node]
        if not found:
            source.issues.error("its tss:stepField is not one of its fields", where)
            problems.append("its tss:stepField is not one of its fields")
        elif found[0] is source.time_field:
            source.issues.error("its tss:stepField must not be its time field", where)
            problems.append("its tss:stepField is its time field")
        else:
            step_field = found[0]
            if step_field.datatype != XSD.integer:
                source.issues.error(f"the tss:datatype of the step field must be xsd:integer, "
                                    f"not {xsd.short_name(step_field.datatype)}", where)
    time_field = source.time_field
    if time_field is not None and (time_field.column_name is not None or time_field.column_index is not None):
        source.issues.error("with a time grid, the time field must not state a tss:columnName or "
                            "tss:columnIndex: its time comes from the step", where)
    if base is not None and step is not None and (step_node is None or step_field is not None):
        source.step_field = step_field
        source.time_grid = TimeGrid(base, step, step_field)


def _read_field(source: Source, field_node) -> Field:
    document = source.document
    graph = document.graph
    field = Field(source, field_node)
    where = f"field {document.short(field_node)}"
    node = _NodeReader(document, field_node, source.issues, where)
    types = set(graph.objects(field_node, RDF.type))
    kinds = {FIELD_KINDS[cls] for cls in types if cls in FIELD_KINDS}
    if len(kinds) == 1:
        field.kind = kinds.pop()
    elif kinds:
        source.issues.error("states both tss:ValueField and tss:ContextField; a field is exactly one of the two",
                            where)
    else:
        hint = ("; tss:MetaField is from an earlier draft, it is now tss:ContextField" if TSS.MetaField in types
                else "")
        source.issues.error("states neither tss:ValueField nor tss:ContextField: a field whose values are "
                            "readings is a tss:ValueField, and any other field (the time, a step, a describing "
                            "field, an identifier) a tss:ContextField" + hint, where)
    column = node.text(TSS.columnName)
    if column is not None and column == "":
        source.issues.error("tss:columnName must not be empty", where)
        column = None
    field.column_name = column
    field.column_index = node.integer(TSS.columnIndex)
    field.datatype = node.iri(TSS.datatype)
    if field.datatype is None:
        source.issues.error("states no tss:datatype", where)
    language = node.literal(TSS.language)
    field.language = str(language) if language is not None else None
    if field.datatype == RDF.langString and field.language is None:
        source.issues.warning("an rdf:langString field should state its language with tss:language", where)
    for prop, attribute in ((TSS.minimum, "minimum"), (TSS.maximum, "maximum")):
        literal = node.literal(prop)
        if literal is None:
            continue
        setattr(field, attribute + "_literal", literal)
        try:
            value = xsd.read_value(str(literal), literal.datatype if literal.datatype else XSD.decimal)
            if not isinstance(value, (int, float)) and not hasattr(value, "is_nan"):
                raise ValueError("not a number")
            setattr(field, attribute, value)
        except ValueError:
            source.issues.error(f"{document.short(prop)} must be a number, not {document.short(literal)}", where)
    if field.minimum is not None and field.maximum is not None and field.maximum < field.minimum:
        source.issues.error(f"tss:maximum ({field.maximum}) is below tss:minimum ({field.minimum})", where)
    field.observed_property = node.iri(TSS.observedProperty)
    field.unit = node.iri(TSS.unit)
    field.sensor = node.iri(TSS.sensor)
    field.feature_of_interest = node.iri(TSS.featureOfInterest)
    field.property = node.iri(TSS.property)
    about = node.literal(TSS.aboutUrl)
    field.about_url = str(about) if about is not None else None
    field.labels = node.values(RDFS.label)
    field.comments = node.values(RDFS.comment)
    field.description = node.one(TSS.fieldDescription)

    # The value encoding
    encoding = node.iri(TSS.valueEncoding)
    baseline = node.literal(TSS.baseline)
    if encoding is not None:
        cls = ENCODINGS.get(encoding)
        if cls is None:
            source.issues.error(f"tss:valueEncoding {document.short(encoding)} is not known to rdftss", where)
            source.reading_problems.append(f"field {document.short(field_node)} has an unknown value encoding "
                                           f"{document.short(encoding)}")
        else:
            if encoding == TSS.DeltaFromBaseline:
                if baseline is None:
                    source.issues.error("tss:DeltaFromBaseline needs exactly one tss:baseline", where)
                    source.reading_problems.append(f"field {document.short(field_node)} has "
                                                   "tss:DeltaFromBaseline but no tss:baseline")
                else:
                    if baseline.datatype != field.datatype:
                        source.issues.error(f"tss:baseline must have the field's datatype "
                                            f"({xsd.short_name(field.datatype)})", where)
                    try:
                        field.value_encoding = cls(field.datatype, baseline)
                    except Exception as error:  # an unreadable number
                        source.issues.error(f"tss:baseline {str(baseline)!r} is not a number: {error}", where)
                        source.reading_problems.append(f"field {document.short(field_node)} has an unreadable "
                                                       "tss:baseline")
            else:
                field.value_encoding = cls(field.datatype, baseline)
    elif baseline is not None:
        source.issues.error("states tss:baseline without tss:valueEncoding tss:DeltaFromBaseline", where)

    # The stored summary
    summary_node = node.one(TSS.summary)
    if summary_node is not None:
        field.summary = _read_summary(source, summary_node, where)
    return field


def _read_summary(source: Source, summary_node, where: str) -> Summary:
    document = source.document
    node = _NodeReader(document, summary_node, source.issues, where + " summary")
    summary = Summary(summary_node)
    summary.missing_value_policy = node.iri(TSS.missingValuePolicy)
    for prop, attribute, kind in SUMMARY_ATTRIBUTES:
        if kind == "time":
            value, _ = node.time(prop)
            setattr(summary, attribute, value)
            literal = node.literal(prop) if value is not None else None
        elif kind == "count":
            value = node.integer(prop)
            setattr(summary, attribute, value)
            literal = node.literal(prop) if value is not None else None
        else:
            literal = node.literal(prop)
            if literal is not None:
                try:
                    datatype = literal.datatype if literal.datatype else XSD.decimal
                    if not xsd.is_numeric(datatype):
                        raise ValueError("not a number")
                    setattr(summary, attribute, xsd.read_value(str(literal), datatype))
                except ValueError:
                    node.issues.error(f"{document.short(prop)} must be a number, not {document.short(literal)}",
                                      node.where)
        if literal is not None:
            summary.literals[prop] = literal
    return summary


def _check_columns(source: Source, where: str) -> None:
    kind = source.kind
    if kind is None:
        return
    grid_time = source.time_grid is not None or source.graph.value(source.node, TSS.timeBase) is not None
    names: dict[str, Field] = {}
    indexes: dict[int, Field] = {}
    for field in source.fields:
        if field.column_name is not None:
            if field.column_name in names:
                source.issues.error(f"fields {names[field.column_name].name!r} and {field.name!r} "
                                    f"have the same tss:columnName {field.column_name!r}", where)
            names[field.column_name] = field
        if field.column_index is not None:
            if field.column_index in indexes:
                source.issues.error(f"two fields have the same tss:columnIndex {field.column_index}", where)
            indexes[field.column_index] = field
        if kind.is_external and field.column_name is None and not (grid_time and field is source.time_field):
            source.issues.error(f"field {source.document.short(field.node)} states no tss:columnName", where)
            needs_index = kind == SourceKind.CSV and source.csv is not None and not source.csv.header
            if not needs_index or field.column_index is None:
                source.reading_problems.append(f"field {source.document.short(field.node)} has no tss:columnName, "
                                               "so its column can't be found")
    if kind == SourceKind.CSV and source.csv is not None and not source.csv.header:
        for field in source.fields:
            if field.column_index is None and not (source.time_grid is not None and field is source.time_field):
                source.issues.error(f"field {source.document.short(field.node)} needs a tss:columnIndex: the "
                                    "CSV file has no header line (tss:header false)", where)
                source.reading_problems.append(f"field {source.document.short(field.node)} has no tss:columnIndex, "
                                               "and the CSV file has no header line")


def _name_fields(source: Source) -> None:
    """Give every field a short name, unique in the source: its column name, else the last
    part of its IRI, else its label."""
    used: set[str] = set()
    for field in source.fields:
        name = field.column_name or local_name(field.node) or field.label or "field"
        candidate, number = name, 2
        while candidate in used:
            candidate = f"{name}_{number}"
            number += 1
        field.name = candidate
        used.add(candidate)


def _sort_fields(source: Source) -> None:
    """Sort the fields: the time field, then the step field, then by column index, kind, column
    name and IRI."""

    def order(field: Field):
        rank = 0 if field is source.time_field else 1 if field is source.step_field else 2
        index = field.column_index if field.column_index is not None else 10**9
        role = 0 if field.is_value else 1 if field.describes else 2
        return (rank, index, role, field.column_name or "", str(field.node))

    source.fields.sort(key=order)
