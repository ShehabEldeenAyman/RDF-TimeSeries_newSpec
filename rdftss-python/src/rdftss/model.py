"""The model of a metadata document: snippets, fields, summaries, time windows and time grids.

You get these objects from :func:`rdftss.load`; you do not make them yourself::

    import rdftss

    document = rdftss.load("metadata.ttl")
    snippet = document.snippet()            # the only snippet of the document
    print(snippet.describe())               # what it is, in words
    snippet.kind                            # SnippetKind.CSV
    snippet.time_field.name                 # "time"
    snippet.field("value").unit             # URIRef('http://qudt.org/vocab/unit/...')

Everything the specification defines has an attribute, with the defaults filled in
(delimiter ``,``, encoding ``utf-8``, header ``true``, key = the time field). Anything else is
still in the RDF: ``snippet.node`` and ``field.node`` are the nodes in ``document.graph``.

While reading the metadata, rdftss reports what breaks the specification as issues
(``snippet.issues``) instead of failing. What makes a snippet unreadable is listed in
``snippet.reading_problems``; reading such a snippet raises a :class:`~rdftss.errors.DocumentError`
that lists them.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import TYPE_CHECKING, Iterator

from rdflib import BNode, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from . import xsd
from .encodings import ENCODINGS, ValueEncoding
from .errors import DocumentError, Issues
from .times import Duration, Time, TimeGridMath, parse_time, to_instant
from .vocab import TSS, TSS_FROM, XSD

if TYPE_CHECKING:
    from .document import Document
    from .points import Points


class SnippetKind(Enum):
    """The five snippet classes: where the points of a snippet are."""

    EMBEDDED_RDF = "EmbeddedRDFSnippet"
    EMBEDDED_JSON = "EmbeddedJSONSnippet"
    CSV = "CSVSnippet"
    PARQUET = "ParquetSnippet"
    ARROW = "ArrowSnippet"

    @property
    def iri(self) -> URIRef:
        """The IRI of the class, such as ``tss:CSVSnippet``."""
        return TSS[self.value]

    @property
    def is_embedded(self) -> bool:
        """Whether the points are in the metadata itself."""
        return self in (SnippetKind.EMBEDDED_RDF, SnippetKind.EMBEDDED_JSON)

    @property
    def is_external(self) -> bool:
        """Whether the points are in a data file (CSV, Parquet or Arrow)."""
        return not self.is_embedded

    @property
    def description(self) -> str:
        """Where the points are, in words."""
        return {
            SnippetKind.EMBEDDED_RDF: "RDF points in the metadata (tss:hasPoint)",
            SnippetKind.EMBEDDED_JSON: "JSON points in the metadata (tss:points)",
            SnippetKind.CSV: "a CSV data file",
            SnippetKind.PARQUET: "a Parquet data file",
            SnippetKind.ARROW: "an Arrow (IPC file) data file",
        }[self]

    @property
    def suffix(self) -> str | None:
        """The usual file name suffix of the data file: ``.csv``, ``.parquet`` or ``.arrow``."""
        return {SnippetKind.CSV: ".csv", SnippetKind.PARQUET: ".parquet", SnippetKind.ARROW: ".arrow"}.get(self)

    @classmethod
    def parse(cls, value) -> "SnippetKind":
        """A snippet kind from a :class:`SnippetKind`, a class IRI, a class name such as
        ``"CSVSnippet"``, or a short name: ``"rdf"``, ``"json"``, ``"csv"``, ``"parquet"`` or ``"arrow"``."""
        if isinstance(value, SnippetKind):
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
        raise ValueError(f"Unknown snippet kind {value!r}; use one of: rdf, json, csv, parquet, arrow")


@dataclass(frozen=True)
class TimeWindow:
    """The time window ``[start, end)`` of a snippet: ``tss:from`` (included) until ``tss:until``
    (not included). A side that the snippet does not state is open (``None``)."""

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
    """The time grid of an external snippet: step n is at ``base`` + n × ``step``.

    ``step_field`` is the field whose column holds the step of each row."""

    base: Time
    step: Duration
    step_field: "Field"

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
    """One column of a snippet: a ``tss:Field``.

    The main attributes:

    - ``name``: a short name, unique in the snippet: the column name, else the last part of the
      field's IRI. Use it with ``snippet.field(name)``, ``point[name]`` and in DataFrames.
    - ``node`` and ``iri``: the field's node in the graph, and its IRI as text (``None`` for a
      blank node).
    - ``column_name``, ``column_index``: where its values are in a data file or JSON point.
    - ``datatype``: its ``tss:datatype``, such as ``XSD.double``; ``language`` for ``rdf:langString``.
    - ``minimum``, ``maximum``: the expected range (Python numbers, or ``None``).
    - ``value_encoding``: a :class:`~rdftss.encodings.ValueEncoding`, or ``None``.
    - ``observed_property``, ``unit``, ``sensor``, ``feature_of_interest``: what it observes.
    - ``describes``: the fields this field describes (``tss:describesField``).
    - ``summary``: its stored :class:`Summary`, or ``None``.
    - ``role``: ``"time"``, ``"step"``, ``"measured"``, ``"describing"`` or ``"other"``.
    """

    def __init__(self, snippet: "Snippet", node):
        self.snippet = snippet
        self.node = node
        self.name: str = ""
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
        self.describes: list[Field] = []
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
        return self.snippet.time_field is self

    @property
    def is_step(self) -> bool:
        """Whether this is the step field of a time grid (``tss:stepField``)."""
        return self.snippet.step_field is self

    @property
    def is_measured(self) -> bool:
        """Whether this is a measured field: it has a ``tss:observedProperty`` and is not the time field.
        Each of its values becomes an observation when the snippet is expanded."""
        return self.observed_property is not None and not self.is_time

    @property
    def is_describing(self) -> bool:
        """Whether this field describes other fields (``tss:describesField``), like a quality code.
        A describing field with its own observed property is also a measured field."""
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
        """``"time"``, ``"step"``, ``"measured"``, ``"describing"`` or ``"other"``."""
        if self.is_time:
            return "time"
        if self.is_step:
            return "step"
        if self.is_measured:
            return "measured"
        if self.is_describing:
            return "describing"
        return "other"

    def short(self, term) -> str | None:
        """A term in short form with the document's prefixes, such as ``unit:DEG_C``."""
        return self.snippet.document.short(term) if term is not None else None

    def as_dict(self) -> dict:
        """What the metadata says about this field, as a plain dict with IRIs as texts."""
        def text(term):
            return str(term) if term is not None else None

        return {
            "name": self.name,
            "field": text(self.node),
            "label": self.label,
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
        if self.snippet.kind == SnippetKind.EMBEDDED_RDF:
            where.append(f"predicate {self.short(self.node)}")
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
            lines.append(f"  describes:  {', '.join(other.name for other in self.describes)}")
        if self.summary is not None:
            lines.append(f"  summary:    {self.summary}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<Field {self.name!r} {xsd.short_name(self.datatype)} ({self.role})>"


class Snippet:
    """A snippet: a table of readings that share one time field.

    The main attributes:

    - ``kind``: the :class:`SnippetKind` (where the points are); ``None`` when the snippet states
      no snippet class, or several.
    - ``node``, ``iri``, ``name``, ``label``: who it is.
    - ``fields``: its :class:`Field` objects; ``time_field``, ``key`` and ``step_field``.
    - ``window``: its :class:`TimeWindow`.
    - ``mutable``, ``committed_until``, ``produced_by``: changes and provenance.
    - ``data_file``: the IRI of the data file of an external snippet; ``csv``: the
      :class:`CSVDialect` of a CSV snippet; ``time_grid``: its :class:`TimeGrid`, or ``None``.
    - ``issues``: what breaks the specification; ``reading_problems``: what stops reading it.

    Reading and analysis:

    - :meth:`read` gives the points, in time order; :meth:`iter_points` streams them.
    - :meth:`statistics`, :meth:`gaps`, :meth:`resample`: analysis of the points.
    - :meth:`to_pandas`, :meth:`to_polars`, :meth:`to_arrow`: DataFrames and tables.
    - :meth:`sql`: SQL with DuckDB.
    - :meth:`expand`: observations (SOSA by default); :meth:`convert`: another storage class.
    """

    def __init__(self, document: "Document", node):
        self.document = document
        self.node = node
        self.kind: SnippetKind | None = None
        self.labels: list[Literal] = []
        self.comments: list[Literal] = []
        self.mutable: bool | None = None
        self.committed_until: Time | None = None
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
    def iri(self) -> str | None:
        """The snippet's IRI as text, or ``None`` for a blank node."""
        return str(self.node) if isinstance(self.node, URIRef) else None

    @property
    def label(self) -> str | None:
        """The snippet's ``rdfs:label`` (an English one when there are several), or ``None``."""
        return best_label(self.labels)

    @property
    def name(self) -> str:
        """A short name: the last part of the snippet's IRI, else its label, else ``"snippet"``."""
        return local_name(self.node) or self.label or "snippet"

    @property
    def graph(self):
        """The RDF graph of the document."""
        return self.document.graph

    @property
    def measured_fields(self) -> list[Field]:
        """The fields with a ``tss:observedProperty`` (other than the time field)."""
        return [field for field in self.fields if field.is_measured]

    @property
    def value_fields(self) -> list[Field]:
        """Every field except the time field and the step field."""
        return [field for field in self.fields if not field.is_time and not field.is_step]

    def values_of(self, predicate) -> list:
        """Every value the graph states for this snippet with ``predicate``, for statements the
        model does not cover."""
        return sorted(self.graph.objects(self.node, URIRef(predicate)), key=str)

    def field(self, key) -> Field:
        """One field of the snippet, by its name, column name, IRI (in full or with a prefix the
        document uses, such as ``"ex:value-field"``), the last part of its IRI, or its label.

        Raises :class:`KeyError`, listing the field names, when there is no such field."""
        if isinstance(key, Field):
            if key.snippet is self:
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
        """``self.field(key)``, or, when ``key`` is ``None``, the only measured field (or the only
        value field). Raises :class:`ValueError` when there is no single obvious field."""
        if key is not None:
            return self.field(key)
        candidates = self.measured_fields or self.value_fields
        if len(candidates) == 1:
            return candidates[0]
        names = ", ".join(repr(candidate.name) for candidate in candidates)
        raise ValueError(f"{self.name} has several fields ({names}); say which one, e.g. field={candidates[0].name!r}")

    # -- describing it ---------------------------------------------------------------------------

    def how_to_read(self) -> dict:
        """Where the readings are and how to read them, as a plain dict: the storage class, the
        data file or the embedded predicate, how each field is bound to a column, JSON key or
        predicate, the time grid and the value encodings. (Competency questions CQ3, CQ4, CQ6.)"""
        def text(term):
            return str(term) if term is not None else None

        result = {
            "snippet": text(self.node),
            "class": text(self.kind.iri) if self.kind else None,
            "where": self.kind.description if self.kind else None,
            "data_file": self.data_file,
            "embedded_predicate": text({SnippetKind.EMBEDDED_RDF: TSS.hasPoint,
                                        SnippetKind.EMBEDDED_JSON: TSS.points}.get(self.kind)),
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
            result["time_grid"] = {"base": self.time_grid.base.text, "step": self.time_grid.step.text,
                                   "step_field": text(self.time_grid.step_field.node),
                                   "step_column": self.time_grid.step_field.column_name}
        for field in self.fields:
            binding = {
                "field": text(field.node),
                "name": field.name,
                "role": field.role,
                "datatype": text(field.datatype),
                "column_name": field.column_name,
                "column_index": field.column_index,
            }
            if self.kind == SnippetKind.EMBEDDED_RDF:
                binding["predicate"] = text(field.node)
            if field.value_encoding is not None:
                binding["value_encoding"] = text(field.value_encoding.iri)
                binding["baseline"] = text(field.value_encoding.baseline)
            result["fields"].append(binding)
        return result

    def describe(self) -> str:
        """What this snippet is, in a few lines of text: class, window, fields, issues."""
        short = self.document.short
        lines = [f"Snippet {short(self.node)}" + (f" — {self.label}" if self.label else "")]
        lines.append(f"  class:      {short(self.kind.iri) if self.kind else '(no snippet class)'}"
                     + (f": {self.kind.description}" if self.kind else ""))
        if self.data_file:
            lines.append(f"  data file:  {self.data_file}")
        if self.csv is not None:
            lines.append(f"  CSV:        delimiter {self.csv.delimiter!r}, encoding {self.csv.encoding}, "
                         f"header {'yes' if self.csv.header else 'no'}")
        lines.append(f"  window:     {self.window}")
        if self.time_grid is not None:
            lines.append(f"  time grid:  {self.time_grid} (steps in column "
                         f"{self.time_grid.step_field.column_name!r})")
        lines.append(f"  mutable:    {self.mutable}" + (
            f", committed until {self.committed_until.text}" if self.committed_until else ""))
        if self.produced_by is not None:
            lines.append(f"  produced by {short(self.produced_by)}")
        lines.append(f"  fields ({len(self.fields)}):")
        for field in self.fields:
            extra = []
            if field.unit is not None:
                extra.append(short(field.unit))
            if field.observed_property is not None:
                extra.append(f"observes {short(field.observed_property)}")
            if field.value_encoding is not None:
                extra.append(repr(field.value_encoding))
            details = f" — {', '.join(extra)}" if extra else ""
            lines.append(f"    {field.name:<20} {field.role:<11} {xsd.short_name(field.datatype)}{details}")
        if self.issues:
            lines.append(f"  issues ({len(self.issues)}):")
            lines.extend(f"    {issue}" for issue in self.issues)
        return "\n".join(lines)

    def __repr__(self) -> str:
        kind = self.kind.value if self.kind else "no snippet class"
        return f"<Snippet {self.document.short(self.node)} ({kind}, {len(self.fields)} fields)>"

    # -- data access -----------------------------------------------------------------------------

    def set_data_file(self, source) -> "Snippet":
        """Read the points of this external snippet from ``source`` (a path, or a binary file
        object) instead of its ``tss:dataFile``. Returns the snippet."""
        if self.kind is not None and self.kind.is_embedded:
            raise DocumentError(f"{self.name} is an embedded snippet: its points are in the metadata")
        self._data_override = source
        return self

    def check_readable(self) -> None:
        """Raise a :class:`~rdftss.errors.DocumentError` when the metadata does not say what
        reading the points needs (see ``reading_problems``)."""
        if self.reading_problems:
            problems = "\n".join(f"  - {problem}" for problem in self.reading_problems)
            raise DocumentError(f"Can't read the points of {self.document.short(self.node)}:\n{problems}")

    # -- reading ---------------------------------------------------------------------------------

    def read(self, *, strict: bool = True, window: bool = True, fields=None) -> "Points":
        """The points of the snippet, in time order, as :class:`~rdftss.points.Points` (a list).

        - ``strict=True`` raises a :class:`~rdftss.errors.DataError` at the first error the
          specification names (a point without a time, two points with the same time, a value
          not in the lexical form of its datatype, ...). With ``strict=False`` the bad value or
          point is left out, and every problem is listed in ``points.issues``.
        - ``window=True`` keeps the points in the time window only (the specification's rule);
          ``window=False`` reads every row of the data file.
        - ``fields``: read only these fields (names or Field objects); the time field is always read.
        """
        from .points import read_points

        return read_points(self, strict=strict, window=window, fields=fields)

    def iter_points(self, *, strict: bool = True, window: bool = True, fields=None, issues=None):
        """The points one by one, without holding them all in memory. External data files are
        streamed in file order; embedded points come in time order. With ``strict=False``,
        problems are appended to ``issues`` (a list) when one is given."""
        from .points import iter_points

        return iter_points(self, strict=strict, window=window, fields=fields, issues=issues)

    def __iter__(self) -> Iterator:
        return iter(self.read())

    # -- analysis (shortcuts to Points) ----------------------------------------------------------

    def statistics(self, field=None):
        """Count, missing, first and last time, minimum, maximum, mean and median of a field's
        values in the window, as the stored summaries define them. See :meth:`Points.statistics`."""
        return self.read(fields=[field] if field is not None else None).statistics(field)

    def gaps(self):
        """The usual time between two points, and the longer intervals. See :meth:`Points.gaps`."""
        return self.read(fields=[]).gaps()

    def resample(self, every, field=None, how: str = "mean"):
        """Aggregate a field per period (``"1h"``, ``"1d"``, ``"P1M"``, ...). See :meth:`Points.resample`."""
        return self.read().resample(every, field=field, how=how)

    def to_arrow(self, **options):
        """The points in the window as a ``pyarrow.Table``. See :meth:`Points.to_arrow`."""
        return self.read().to_arrow(**options)

    def to_pandas(self, **options):
        """The points in the window as a pandas DataFrame. See :meth:`Points.to_pandas`."""
        return self.read().to_pandas(**options)

    def to_polars(self, **options):
        """The points in the window as a polars DataFrame. See :meth:`Points.to_polars`."""
        return self.read().to_polars(**options)

    def sql(self, query: str, **limits):
        """Run one read-only SQL query on this snippet with DuckDB. The table of the points is
        called ``points`` (and also by the snippet's name). See :class:`rdftss.sql.Database`."""
        from .sql import Database

        with Database([self], **limits) as database:
            return database.query(query)

    # -- materialisation -------------------------------------------------------------------------

    def observations(self, *, strict: bool = True):
        """The observations of this snippet, one per value of a measured field in the window.
        See :func:`rdftss.expansion.observations`."""
        from .expansion import observations

        return observations(self, strict=strict)

    def expand(self, destination=None, **options):
        """Expand this snippet into observations (SOSA by default). See :func:`rdftss.expansion.expand`."""
        from .expansion import expand

        return expand(self, destination, **options)

    def convert(self, to, **options):
        """This snippet in another storage class (``"csv"``, ``"parquet"``, ``"arrow"``,
        ``"json"`` or ``"rdf"``), as a new document. See :func:`rdftss.conversion.convert`."""
        from .conversion import convert

        return convert(self, to, **options)


# ---------------------------------------------------------------------------------------------
# Reading the model from the graph
# ---------------------------------------------------------------------------------------------

SNIPPET_CLASSES = {kind.iri: kind for kind in SnippetKind}
DATA_FILE_PROPERTIES = (TSS.dataFile, TSS.delimiter, TSS.encoding, TSS.header)
GRID_PROPERTIES = (TSS.timeBase, TSS.timeStep, TSS.stepField)


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
        if value.datatype != XSD.dateTimeStamp:
            self.issues.error(f"{self.name(prop)} must be an xsd:dateTimeStamp literal, "
                              f"not {self.document.short(value)}", self.where)
        try:
            return parse_time(str(value), require_timezone=True), True
        except ValueError as error:
            self.issues.error(f"{self.name(prop)} is not a valid time: {error}", self.where)
            return None, True


def build_snippets(document: "Document") -> list[Snippet]:
    """Find every snippet of a document and read its metadata (see :class:`Snippet`)."""
    graph = document.graph
    candidates = set()
    for snippet_class in list(SNIPPET_CLASSES) + [TSS.Snippet]:
        candidates.update(graph.subjects(RDF.type, snippet_class))
    candidates.update(graph.subjects(TSS.hasField, None))
    candidates.update(graph.subjects(TSS.hasPoint, None))
    candidates.update(graph.subjects(TSS.points, None))
    candidates.update(graph.subjects(TSS.dataFile, None))
    nodes = sorted(candidates, key=lambda node: (isinstance(node, BNode), str(node)))

    # A field (or a point) linked from more than one snippet
    owners: dict = {}
    for node in nodes:
        for field_node in graph.objects(node, TSS.hasField):
            owners.setdefault(field_node, []).append(node)

    snippets = [Snippet(document, node) for node in nodes]
    for snippet in snippets:
        _read_snippet(snippet, owners)
    return snippets


def _read_snippet(snippet: Snippet, owners: dict) -> None:
    document = snippet.document
    graph = document.graph
    where = f"snippet {document.short(snippet.node)}"
    node = _NodeReader(document, snippet.node, snippet.issues, where)
    problems = snippet.reading_problems

    # The snippet class
    kinds = [SNIPPET_CLASSES[cls] for cls in graph.objects(snippet.node, RDF.type) if cls in SNIPPET_CLASSES]
    if not kinds:
        snippet.issues.error("states no snippet class (tss:EmbeddedRDFSnippet, tss:EmbeddedJSONSnippet, "
                             "tss:CSVSnippet, tss:ParquetSnippet or tss:ArrowSnippet), so it does not say "
                             "where its readings are", where)
        problems.append("it states no snippet class, so it does not say where its readings are")
    elif len(kinds) > 1:
        names = ", ".join(sorted(document.short(kind.iri) for kind in kinds))
        snippet.issues.error(f"states several snippet classes ({names}); it must state exactly one", where)
        problems.append(f"it states several snippet classes ({names})")
    else:
        snippet.kind = kinds[0]
    kind = snippet.kind

    snippet.labels = node.values(RDFS.label)
    snippet.comments = node.values(RDFS.comment)
    snippet.mutable = node.boolean(TSS.mutable)
    if not node.values(TSS.mutable):
        snippet.issues.error("states no tss:mutable (true or false)", where)
    snippet.committed_until, stated = node.time(TSS.committedUntil)
    if not stated:
        snippet.issues.error("states no tss:committedUntil", where)
    snippet.produced_by = node.iri(TSS.producedBy)
    snippet.description = node.one(TSS.snippetDescription)

    # The time window
    start, start_stated = node.time(TSS_FROM)
    end, end_stated = node.time(TSS.until)
    if (start_stated and start is None) or (end_stated and end is None):
        problems.append("its time window (tss:from, tss:until) has a time that can't be read")
    if start is not None and end is not None and not end.instant > start.instant:
        snippet.issues.error(f"tss:until ({end.text}) must come after tss:from ({start.text})", where)
    snippet.window = TimeWindow(start, end)

    # The fields
    for field_node in sorted(graph.objects(snippet.node, TSS.hasField),
                             key=lambda term: (isinstance(term, BNode), str(term))):
        if isinstance(field_node, Literal):
            snippet.issues.error(f"tss:hasField must link to a field, not the literal {str(field_node)!r}", where)
            continue
        if len(owners.get(field_node, [])) > 1:
            snippet.issues.error(f"the field {document.short(field_node)} belongs to several snippets; "
                                 "each field belongs to exactly one", where)
        snippet.fields.append(_read_field(snippet, field_node))
    if not snippet.fields:
        snippet.issues.error("has no field (tss:hasField)", where)
    _name_fields(snippet)

    # The time field and the key
    time_node = node.one(TSS.timeField)
    if time_node is None:
        snippet.issues.error("states no tss:timeField", where)
        problems.append("it states no tss:timeField, so its points have no time")
    else:
        found = [field for field in snippet.fields if field.node == time_node]
        if not found:
            snippet.issues.error(f"its tss:timeField {document.short(time_node)} is not one of its fields "
                                 "(tss:hasField)", where)
            problems.append("its tss:timeField is not one of its fields")
        else:
            snippet.time_field = found[0]
            if snippet.time_field.datatype != XSD.dateTimeStamp:
                snippet.issues.error(f"the tss:datatype of the time field must be xsd:dateTimeStamp, "
                                     f"not {xsd.short_name(snippet.time_field.datatype)}", where)
    key_node = node.one(TSS.key)
    if key_node is not None and (snippet.time_field is None or key_node != snippet.time_field.node):
        snippet.issues.error(f"its tss:key ({document.short(key_node)}) must be its time field", where)
    snippet.key = snippet.time_field

    # Where the points are: the rules of the snippet class come from the tables below
    if kind is not None:
        for prop in NOT_ALLOWED[kind]:
            if node.values(prop):
                snippet.issues.error(f"a {document.short(kind.iri)} must not state {document.short(prop)}", where)
        for prop in NO_EFFECT.get(kind, ()):
            if node.values(prop):
                snippet.issues.warning(f"{document.short(prop)} has no effect on a {document.short(kind.iri)}", where)
        STORAGE_RULES[kind](snippet, node, where)

    # Columns of the fields
    _check_columns(snippet, where)

    # tss:describesField
    for field in snippet.fields:
        for described in sorted(graph.objects(field.node, TSS.describesField), key=str):
            match = [other for other in snippet.fields if other.node == described]
            if match:
                field.describes.append(match[0])
            else:
                snippet.issues.error(f"field {field.name!r}: tss:describesField {document.short(described)} "
                                     "is not a field of the same snippet", where)

    _sort_fields(snippet)


# ---------------------------------------------------------------------------------------------
# The rules of each snippet class
# ---------------------------------------------------------------------------------------------

def _read_rdf_points(snippet: Snippet, node: _NodeReader, where: str) -> None:
    """tss:EmbeddedRDFSnippet: the points are the nodes it links to with tss:hasPoint."""
    _no_value_encodings(snippet, where)


def _read_json_points(snippet: Snippet, node: _NodeReader, where: str) -> None:
    """tss:EmbeddedJSONSnippet: exactly one rdf:JSON literal in tss:points, and a column name
    (its JSON key) on every field."""
    _no_value_encodings(snippet, where)
    document = snippet.document
    literals = node.values(TSS.points)
    if len(literals) != 1:
        snippet.issues.error(f"a tss:EmbeddedJSONSnippet must state exactly one tss:points "
                             f"(it states {len(literals)})", where)
    if not literals:
        snippet.reading_problems.append("it states no tss:points")
    elif not isinstance(literals[0], Literal):
        snippet.reading_problems.append("its tss:points is not a literal")
    else:
        snippet.points_literal = literals[0]
        if literals[0].datatype != RDF.JSON:
            snippet.issues.error("tss:points must be a literal with datatype rdf:JSON", where)
    for field in snippet.fields:
        if field.column_name is None:
            snippet.issues.error(f"field {field.name!r} states no tss:columnName, which is its key in "
                                 "the JSON points", where)
            snippet.reading_problems.append(f"field {document.short(field.node)} has no tss:columnName (its JSON key)")


def _read_external(snippet: Snippet, node: _NodeReader, where: str) -> None:
    """tss:ParquetSnippet and tss:ArrowSnippet (and the common part of tss:CSVSnippet): a data
    file, and maybe a time grid."""
    data_file = node.iri(TSS.dataFile)
    if data_file is None:
        snippet.issues.error("states no tss:dataFile", where)
        snippet.reading_problems.append("it states no tss:dataFile")
    else:
        snippet.data_file = str(data_file)
    _read_time_grid(snippet, node, where)


def _read_csv(snippet: Snippet, node: _NodeReader, where: str) -> None:
    """tss:CSVSnippet: a data file, and the CSV settings with their defaults."""
    _read_external(snippet, node, where)
    problems = snippet.reading_problems
    delimiter = node.text(TSS.delimiter)
    if delimiter is None:
        delimiter = ","
    elif len(delimiter) != 1:
        snippet.issues.error(f"tss:delimiter must be one character, not {delimiter!r}", where)
        problems.append(f"its tss:delimiter {delimiter!r} is not one character")
    encoding = node.text(TSS.encoding) or "utf-8"
    try:
        codecs.lookup(encoding)
    except LookupError:
        snippet.issues.error(f"tss:encoding {encoding!r} is not a known text encoding", where)
        problems.append(f"its tss:encoding {encoding!r} is not a known text encoding")
    header = node.boolean(TSS.header)
    snippet.csv = CSVDialect(delimiter, encoding, True if header is None else header)


#: What each snippet class must not state.
NOT_ALLOWED = {
    SnippetKind.EMBEDDED_RDF: (TSS.points,) + DATA_FILE_PROPERTIES + GRID_PROPERTIES,
    SnippetKind.EMBEDDED_JSON: (TSS.hasPoint,) + DATA_FILE_PROPERTIES + GRID_PROPERTIES,
    SnippetKind.CSV: (TSS.hasPoint, TSS.points),
    SnippetKind.PARQUET: (TSS.hasPoint, TSS.points),
    SnippetKind.ARROW: (TSS.hasPoint, TSS.points),
}
#: What has no effect on a snippet class (a warning).
NO_EFFECT = {
    SnippetKind.PARQUET: (TSS.delimiter, TSS.encoding, TSS.header),
    SnippetKind.ARROW: (TSS.delimiter, TSS.encoding, TSS.header),
}
#: How the part of the metadata that is particular to each snippet class is read.
STORAGE_RULES = {
    SnippetKind.EMBEDDED_RDF: _read_rdf_points,
    SnippetKind.EMBEDDED_JSON: _read_json_points,
    SnippetKind.CSV: _read_csv,
    SnippetKind.PARQUET: _read_external,
    SnippetKind.ARROW: _read_external,
}


def _no_value_encodings(snippet: Snippet, where: str) -> None:
    """The fields of an embedded snippet state their readings: no value encoding, no baseline."""
    for field in snippet.fields:
        if field.value_encoding is not None or snippet.graph.value(field.node, TSS.baseline) is not None:
            snippet.issues.error(f"field {field.name!r}: the fields of an embedded snippet must not "
                                 "state tss:valueEncoding or tss:baseline", where)
            field.value_encoding = None


def _read_time_grid(snippet: Snippet, node: _NodeReader, where: str) -> None:
    """The time grid of an external snippet: tss:timeBase, tss:timeStep and tss:stepField."""
    problems = snippet.reading_problems
    base, base_stated = node.time(TSS.timeBase)
    step_literal = node.literal(TSS.timeStep)
    step_node = node.one(TSS.stepField)
    if not base_stated and step_literal is None:
        if step_node is not None:
            snippet.issues.error("a snippet without a time grid must not state tss:stepField", where)
        return
    if not base_stated or step_literal is None:
        snippet.issues.error("a time grid needs both tss:timeBase and tss:timeStep", where)
        problems.append("its time grid states only one of tss:timeBase and tss:timeStep")
    step = None
    if step_literal is not None:
        if step_literal.datatype != XSD.duration:
            snippet.issues.error("tss:timeStep must be an xsd:duration literal", where)
        try:
            step = Duration.parse(str(step_literal))
            if not step.is_positive:
                snippet.issues.error(f"tss:timeStep must be more than zero, not {step.text}", where)
                problems.append(f"its tss:timeStep {step.text} is not more than zero")
                step = None
        except ValueError as error:
            snippet.issues.error(f"tss:timeStep is not valid: {error}", where)
            problems.append("its tss:timeStep can't be read")
    step_field = None
    if step_node is None:
        snippet.issues.error("a snippet with a time grid must state tss:stepField", where)
        problems.append("its time grid has no tss:stepField")
    else:
        found = [field for field in snippet.fields if field.node == step_node]
        if not found:
            snippet.issues.error("its tss:stepField is not one of its fields", where)
            problems.append("its tss:stepField is not one of its fields")
        elif found[0] is snippet.time_field:
            snippet.issues.error("its tss:stepField must not be its time field", where)
            problems.append("its tss:stepField is its time field")
        else:
            step_field = found[0]
            if step_field.datatype != XSD.integer:
                snippet.issues.error(f"the tss:datatype of the step field must be xsd:integer, "
                                     f"not {xsd.short_name(step_field.datatype)}", where)
    time_field = snippet.time_field
    if time_field is not None and (time_field.column_name is not None or time_field.column_index is not None):
        snippet.issues.error("with a time grid, the time field must not state a tss:columnName or "
                             "tss:columnIndex: its time comes from the step", where)
    if base is not None and step is not None and step_field is not None:
        snippet.step_field = step_field
        snippet.time_grid = TimeGrid(base, step, step_field)


def _read_field(snippet: Snippet, field_node) -> Field:
    document = snippet.document
    graph = document.graph
    field = Field(snippet, field_node)
    where = f"field {document.short(field_node)}"
    node = _NodeReader(document, field_node, snippet.issues, where)
    if (field_node, RDF.type, TSS.Field) not in graph:
        snippet.issues.warning("is not typed tss:Field", where)
    column = node.text(TSS.columnName)
    if column is not None and column == "":
        snippet.issues.error("tss:columnName must not be empty", where)
        column = None
    field.column_name = column
    field.column_index = node.integer(TSS.columnIndex)
    field.datatype = node.iri(TSS.datatype)
    if field.datatype is None:
        snippet.issues.error("states no tss:datatype", where)
    language = node.literal(TSS.language)
    field.language = str(language) if language is not None else None
    if field.datatype == RDF.langString and field.language is None:
        snippet.issues.warning("an rdf:langString field should state its language with tss:language", where)
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
            snippet.issues.error(f"{document.short(prop)} must be a number, not {document.short(literal)}", where)
    if field.minimum is not None and field.maximum is not None and field.maximum < field.minimum:
        snippet.issues.error(f"tss:maximum ({field.maximum}) is below tss:minimum ({field.minimum})", where)
    field.observed_property = node.iri(TSS.observedProperty)
    field.unit = node.iri(TSS.unit)
    field.sensor = node.iri(TSS.sensor)
    field.feature_of_interest = node.iri(TSS.featureOfInterest)
    field.labels = node.values(RDFS.label)
    field.comments = node.values(RDFS.comment)
    field.description = node.one(TSS.fieldDescription)

    # The value encoding
    encoding = node.iri(TSS.valueEncoding)
    baseline = node.literal(TSS.baseline)
    if encoding is not None:
        cls = ENCODINGS.get(encoding)
        if cls is None:
            snippet.issues.error(f"tss:valueEncoding {document.short(encoding)} is not known to rdftss", where)
            snippet.reading_problems.append(f"field {document.short(field_node)} has an unknown value encoding "
                                            f"{document.short(encoding)}")
        else:
            if encoding == TSS.DeltaFromBaseline:
                if baseline is None:
                    snippet.issues.error("tss:DeltaFromBaseline needs exactly one tss:baseline", where)
                    snippet.reading_problems.append(f"field {document.short(field_node)} has "
                                                    "tss:DeltaFromBaseline but no tss:baseline")
                else:
                    if baseline.datatype != field.datatype:
                        snippet.issues.error(f"tss:baseline must have the field's datatype "
                                             f"({xsd.short_name(field.datatype)})", where)
                    try:
                        field.value_encoding = cls(field.datatype, baseline)
                    except Exception as error:  # an unreadable number
                        snippet.issues.error(f"tss:baseline {str(baseline)!r} is not a number: {error}", where)
                        snippet.reading_problems.append(f"field {document.short(field_node)} has an unreadable "
                                                        "tss:baseline")
            else:
                field.value_encoding = cls(field.datatype, baseline)
    elif baseline is not None:
        snippet.issues.error("states tss:baseline without tss:valueEncoding tss:DeltaFromBaseline", where)

    # The stored summary
    summary_node = node.one(TSS.summary)
    if summary_node is not None:
        field.summary = _read_summary(snippet, summary_node, where)
    return field


def _read_summary(snippet: Snippet, summary_node, where: str) -> Summary:
    document = snippet.document
    node = _NodeReader(document, summary_node, snippet.issues, where + " summary")
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


def _check_columns(snippet: Snippet, where: str) -> None:
    kind = snippet.kind
    if kind is None:
        return
    grid_time = snippet.time_grid is not None or snippet.graph.value(snippet.node, TSS.timeBase) is not None
    names: dict[str, Field] = {}
    indexes: dict[int, Field] = {}
    for field in snippet.fields:
        if field.column_name is not None:
            if field.column_name in names:
                snippet.issues.error(f"fields {names[field.column_name].name!r} and {field.name!r} "
                                     f"have the same tss:columnName {field.column_name!r}", where)
            names[field.column_name] = field
        if field.column_index is not None:
            if field.column_index in indexes:
                snippet.issues.error(f"two fields have the same tss:columnIndex {field.column_index}", where)
            indexes[field.column_index] = field
        if kind.is_external and field.column_name is None and not (grid_time and field is snippet.time_field):
            snippet.issues.error(f"field {snippet.document.short(field.node)} states no tss:columnName", where)
            needs_index = kind == SnippetKind.CSV and snippet.csv is not None and not snippet.csv.header
            if not needs_index or field.column_index is None:
                snippet.reading_problems.append(f"field {snippet.document.short(field.node)} has no tss:columnName, "
                                                "so its column can't be found")
    if kind == SnippetKind.CSV and snippet.csv is not None and not snippet.csv.header:
        for field in snippet.fields:
            if field.column_index is None and not (snippet.time_grid is not None and field is snippet.time_field):
                snippet.issues.error(f"field {snippet.document.short(field.node)} needs a tss:columnIndex: the "
                                     "CSV file has no header line (tss:header false)", where)
                snippet.reading_problems.append(f"field {snippet.document.short(field.node)} has no tss:columnIndex, "
                                                "and the CSV file has no header line")


def _name_fields(snippet: Snippet) -> None:
    """Give every field a short name, unique in the snippet: its column name, else the last
    part of its IRI, else its label."""
    used: set[str] = set()
    for field in snippet.fields:
        name = field.column_name or local_name(field.node) or field.label or "field"
        candidate, number = name, 2
        while candidate in used:
            candidate = f"{name}_{number}"
            number += 1
        field.name = candidate
        used.add(candidate)


def _sort_fields(snippet: Snippet) -> None:
    """Sort the fields: the time field, then the step field, then by column index, column name and IRI."""

    def order(field: Field):
        rank = 0 if field is snippet.time_field else 1 if field is snippet.step_field else 2
        index = field.column_index if field.column_index is not None else 10**9
        role = 0 if field.observed_property is not None else 1 if field.describes else 2
        return (rank, index, role, field.column_name or "", str(field.node))

    snippet.fields.sort(key=order)
