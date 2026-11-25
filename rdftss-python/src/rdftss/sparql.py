"""SPARQL on sources, with rdflib.

>>> result = source.sparql("SELECT (AVG(?value) AS ?mean) WHERE { ?point tss:field ex:value-field ; tss:value ?value }")  # doctest: +SKIP
>>> print(result)                                                                   # doctest: +SKIP
>>> with rdftss.TripleStore(document.sources) as store:                             # doctest: +SKIP
...     print(store.describe())
...     store.query(store.examples()[0]).to_pandas()

A :class:`TripleStore` is an in-memory RDF graph that holds:

- the metadata documents of the sources, as written: sources, fields, windows, units,
  summaries, sensors, features of interest, ..., with the default ``tss:property`` of each
  describing field that states none;
- the points of each source, as the specification's expansion makes them, whatever the
  source's storage class::

      <source> tss:hasPoint _:point .
      _:point tss:field <value field> ;
          tss:time  "2024-05-12T00:00:00Z"^^xsd:dateTime ;
          tss:value "4747.00"^^xsd:double ;
          <field property of a describing field> "suspect"^^xsd:string .

  Only the points inside the window are there, with their decoded values in their exact text.
  An empty value has no point (or, for a describing field, no triple). The documents' own
  embedded points are replaced by these, so that no point is there twice.
- with ``derive=True``, what the alignment of the TSS vocabulary derives from the points:
  ``sosa:Observation`` and ``saref:Measurement``, with ``sosa:resultTime``,
  ``sosa:hasSimpleResult``, ``sosa:observedProperty``, ``qudt:hasUnit``, ... (see :mod:`rdftss.alignment`).

Every query knows the prefixes of the documents, and ``tss:``, ``xsd:``, ``rdf:``, ``rdfs:``,
``sosa:``, ``saref:``, ``qudt:``, ``unit:`` and ``quantitykind:``.

rdflib cannot compare or sort ``xsd:dateTimeStamp`` values, so in the store every
``xsd:dateTimeStamp`` literal is an ``xsd:dateTime`` literal with the same text. Times then compare
as moments: ``…T10:00:00+02:00`` equals ``…T08:00:00Z``. ``HOURS()``, ``DAY()`` and the like give
the parts in the time's own offset.

Queries are read only and locked down. SPARQL Update is not accepted. ``SERVICE`` (which would
query other servers) and ``FROM``/``FROM NAMED`` (which would download graphs) are refused. An
answer is limited in rows, size and time.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field as dataclass_field

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parserutils import CompValue

from .errors import RDFTSSError, require
from .times import to_instant
from .vocab import QUANTITYKIND, QUDT, SAREF, SOSA, TSS, UNIT

#: The prefixes every query knows, besides those of the documents.
PREFIXES = {"tss": TSS, "xsd": XSD, "rdf": RDF, "rdfs": RDFS, "sosa": SOSA, "saref": SAREF, "qudt": QUDT, "unit": UNIT,
            "quantitykind": QUANTITYKIND}


class QueryError(RDFTSSError):
    """A query was refused, failed, took too long or gave too big an answer."""


class _Stopped(BaseException):
    """Raised inside a query that ran out of time. A BaseException, so rdflib does not catch it."""


@dataclass
class QueryResult:
    """The answer to a ``SELECT`` query.

    - ``columns``: the names of the selected variables, without ``?``.
    - ``terms``: the rows as rdflib terms, exactly as in the graph (``None`` when unbound).
    - ``rows``: the same rows as Python values: numbers, ``datetime``, ``bool``, ``str``, and
      IRIs as :class:`rdflib.URIRef`.
    - ``more_rows``: whether there were more rows than the row limit; ``seconds``: how long it took.
    """

    columns: list
    terms: list
    more_rows: bool
    seconds: float
    shorten: object = dataclass_field(default=str, repr=False, compare=False)

    @property
    def rows(self) -> list[tuple]:
        return [tuple(_python(term) for term in row) for row in self.terms]

    def __len__(self) -> int:
        return len(self.terms)

    def __iter__(self):
        return iter(self.rows)

    def as_dicts(self) -> list[dict]:
        """The rows as dicts of Python values, by variable name."""
        return [dict(zip(self.columns, row)) for row in self.rows]

    def to_pandas(self):
        """The answer as a pandas DataFrame of Python values."""
        pandas = require("pandas", "QueryResult.to_pandas", "pandas")
        return pandas.DataFrame(self.rows, columns=self.columns)

    def __str__(self) -> str:
        def text(term):
            if term is None:
                return ""
            return self.shorten(term) if isinstance(term, URIRef) else str(term)

        shown = [[text(term) for term in row] for row in self.terms[:20]]
        widths = [len(column) for column in self.columns]
        for row in shown:
            widths = [max(width, min(len(value), 40)) for width, value in zip(widths, row)]
        lines = [" | ".join(column.ljust(width) for column, width in zip(self.columns, widths)).rstrip(),
                 "-+-".join("-" * width for width in widths)]
        lines += [" | ".join(value[:40].ljust(width) for value, width in zip(row, widths)).rstrip() for row in shown]
        more = len(self.terms) - len(shown)
        if more > 0 or self.more_rows:
            lines.append(f"... ({more} more rows{' and more beyond the row limit' if self.more_rows else ''})")
        return "\n".join(lines)


def _python(term):
    if isinstance(term, Literal):
        return term.toPython()
    return term


def _query_literal(term):
    """The literal as the store holds it: xsd:dateTimeStamp becomes xsd:dateTime, same text."""
    if isinstance(term, Literal) and term.datatype == XSD.dateTimeStamp:
        return Literal(str(term), datatype=XSD.dateTime, normalize=False)
    return term


def _algebra_names(node, found: set) -> set:
    if isinstance(node, CompValue):
        found.add(node.name)
        for value in node.values():
            _algebra_names(value, found)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _algebra_names(value, found)
    return found


def _variable(name: str, used: set) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", name).strip("_") or "v"
    candidate, number = cleaned, 2
    while candidate in used:
        candidate = f"{cleaned}_{number}"
        number += 1
    used.add(candidate)
    return candidate


def _run(work, timeout: float):
    """Run ``work()`` in a thread, and stop it when it takes longer than ``timeout`` seconds."""
    outcome: dict = {}

    def target():
        try:
            outcome["value"] = work()
        except BaseException as error:  # handed to the caller below
            outcome["error"] = error

    worker = threading.Thread(target=target, name="rdftss-sparql", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        _interrupt(worker)
        worker.join(1.0)
        raise QueryError(f"The query was stopped after {timeout} seconds.")
    error = outcome.get("error")
    if isinstance(error, _Stopped):
        raise QueryError(f"The query was stopped after {timeout} seconds.")
    if error is not None:
        raise error
    return outcome["value"]


def _interrupt(thread: threading.Thread) -> None:
    """Raise :class:`_Stopped` inside a running thread, so that a slow query stops instead of
    running on in the background. rdflib evaluates queries in Python, so this stops it at its
    next step (CPython only; elsewhere the query runs to its end and its answer is dropped)."""
    try:
        import ctypes

        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(_Stopped))
    except (ImportError, AttributeError):
        pass


class TripleStore:
    """An in-memory RDF graph with the metadata and the points of sources, to query with SPARQL.

    - ``sources``: the sources to load (a list, or a :class:`~rdftss.document.Document`).
    - ``max_rows``, ``max_bytes``, ``timeout``: the limits of one answer.
    - ``strict``: read the sources strictly (a source with a data error is then left out, and
      listed in ``skipped``); with ``strict=False`` the bad values are left out instead.
    - ``between``: ``(start, end)`` loads only the points from ``start``, before ``end``.
    - ``fields``: loads only the points of these value fields (names, IRIs or labels) of every
      source that has them, with the values of their describing fields.
    - ``derive``: also load what the alignment with SOSA, SAREF and QUDT derives.

    rdflib keeps the graph in memory, at a few KB and a few dozen microseconds per point, so a
    store of 200,000 points takes some 10 seconds and over 1 GB. For large sources, load the
    period and the fields you need with ``between`` and ``fields``.

    ``graph`` is the rdflib :class:`~rdflib.Graph` itself, to serialize it or use it with other
    tools. A store can be used as a context manager; :meth:`close` frees the graph.
    """

    def __init__(self, sources, *, max_rows: int = 10_000, max_bytes: int = 50_000_000,
                 timeout: float = 30.0, strict: bool = True, between=None, fields=None, derive: bool = False):
        from .alignment import derive as derive_from

        self.max_rows, self.max_bytes, self.timeout = max_rows, max_bytes, timeout
        sources = list(getattr(sources, "sources", sources))
        self.graph = Graph(bind_namespaces="none")
        self.loaded: list[tuple] = []    # (source, number of points)
        self.skipped: list[tuple] = []   # (source, why)
        documents = {id(source.document): source.document for source in sources}
        for document in documents.values():
            self._add_metadata(document)
        for prefix, namespace in PREFIXES.items():
            self.graph.bind(prefix, namespace, replace=True)
        low, high = (None, None) if between is None else (
            to_instant(between[0]) if between[0] is not None else None,
            to_instant(between[1]) if between[1] is not None else None)
        for source in sources:
            try:
                points = [point for point in source.points(strict=strict, fields=self._fields_of(source, fields))
                          if (low is None or point.time.instant >= low) and (high is None or point.time.instant < high)]
            except RDFTSSError as error:
                self.skipped.append((source, str(error)))
                continue
            for point in points:
                for subject, predicate, value in point.triples():
                    self.graph.add((subject, predicate, _query_literal(value)))
            self.loaded.append((source, len(points)))
        if derive:
            for triple in derive_from(self.graph):
                self.graph.add(triple)
        self.namespaces = {prefix: str(namespace) for prefix, namespace in self.graph.namespaces() if prefix}

    # -- building it -----------------------------------------------------------------------------

    def _add_metadata(self, document) -> None:
        """Copy the document without its embedded points, with the default field properties."""
        from .expansion import metadata_graph

        point_nodes = set()
        for source in document.sources:
            point_nodes.update(document.graph.objects(source.node, TSS.hasPoint))
        for subject, predicate, value in metadata_graph([document]):
            if subject in point_nodes or predicate == TSS.hasPoint:
                continue
            self.graph.add((subject, predicate, _query_literal(value)))
        for prefix, namespace in document.graph.namespaces():
            if prefix:
                self.graph.bind(prefix, namespace, override=False)

    @staticmethod
    def _fields_of(source, keys):
        if keys is None:
            return None
        found = []
        for key in keys:
            try:
                field = source.field(key)
            except KeyError:
                continue
            if field.is_value:
                found.append(field)
        return found

    # -- using it --------------------------------------------------------------------------------

    def short(self, iri) -> str:
        """An IRI as a prefixed name when one of the store's prefixes fits, else as ``<iri>``."""
        if not isinstance(iri, URIRef):
            return str(iri)
        return self.graph.namespace_manager.normalizeUri(iri)

    def query(self, sparql: str):
        """Run one SPARQL query.

        Gives a :class:`QueryResult` for ``SELECT``, ``True`` or ``False`` for ``ASK``, and an
        rdflib :class:`~rdflib.Graph` for ``CONSTRUCT`` and ``DESCRIBE``.

        Raises :class:`QueryError` for a query that can't be parsed (SPARQL Update included),
        that uses ``SERVICE``, ``FROM`` or ``FROM NAMED``, that fails, that takes longer than
        the time limit, or whose answer is larger than the size limit."""
        try:
            prepared = prepareQuery(sparql, initNs=self.namespaces)
        except Exception as error:
            raise QueryError(f"The query can't be read: {error}") from None
        algebra = prepared.algebra
        if algebra.get("datasetClause"):
            raise QueryError("FROM and FROM NAMED are not allowed: the query runs on the store's own graph.")
        if "ServiceGraphPattern" in _algebra_names(algebra, set()):
            raise QueryError("SERVICE is not allowed: queries can't reach other servers.")
        started = time.time()

        def work():
            try:
                result = self.graph.query(prepared)
                if result.type == "ASK":
                    return bool(result.askAnswer)
                if result.type in ("CONSTRUCT", "DESCRIBE"):
                    graph = result.graph
                    if len(graph) > self.max_rows:
                        raise QueryError(f"The answer has {len(graph)} triples, more than {self.max_rows}; "
                                         "ask for fewer (with LIMIT, for example).")
                    return graph
                columns = [str(variable) for variable in result.vars]
                rows, size, more_rows = [], 0, False
                for row in result:
                    if len(rows) == self.max_rows:
                        more_rows = True
                        break
                    terms = tuple(row)
                    size += sum(len(term) for term in terms if term is not None)
                    if size > self.max_bytes:
                        raise QueryError(f"The answer is larger than {self.max_bytes} bytes; ask for fewer rows "
                                         "or variables (with LIMIT, for example).")
                    rows.append(terms)
                return QueryResult(columns, rows, more_rows, 0.0, self.short)
            except (QueryError, _Stopped):
                raise
            except Exception as error:
                raise QueryError(f"The query failed: {error}") from None

        answer = _run(work, self.timeout)
        if isinstance(answer, QueryResult):
            answer.seconds = round(time.time() - started, 3)
        return answer

    def describe(self) -> str:
        """What the store holds, in text: per source, how its points state their values."""
        lines = []
        for source, count in self.loaded:
            kind = source.kind.iri.split("#")[-1] if source.kind else "no class"
            lines.append(f"Source {self.short(source.node)} ({kind}): {count} points, "
                         "each linked with tss:hasPoint")
            time_type = "xsd:dateTime"
            for field in source.value_fields:
                value_type = f"@{field.language}" if field.language else \
                    (self.short(field.datatype) if field.datatype else "xsd:string")
                lines.append(f"    ?point tss:field {self.short(field.node)} ; tss:time ?time ({time_type}) ; "
                             f"tss:value ?value ({value_type})")
                for describer in field.described_by:
                    if describer.field_property is not None:
                        lines.append(f"        ?point {self.short(describer.field_property)} ?{describer.name} "
                                     f"(describing field {describer.name!r})")
        for source, reason in self.skipped:
            lines.append(f"Not loaded: {source.name}: {reason}")
        lines.append("The metadata of the documents, as written (xsd:dateTimeStamp literals as xsd:dateTime).")
        lines.append("Prefixes: " + ", ".join(f"{prefix}:" for prefix in sorted(self.namespaces)))
        return "\n".join(lines)

    def examples(self) -> list[str]:
        """Example queries for the loaded sources, with their real IRIs."""
        found = []
        for source, _ in self.loaded:
            subject = self.short(source.node) if isinstance(source.node, URIRef) else "?source"
            for field in source.value_fields:
                used = {"point", "time", "value"}
                describing = [(describer, _variable(describer.name, used)) for describer in field.described_by
                              if describer.field_property is not None]
                optional = "".join(f"\n    OPTIONAL {{ ?point {self.short(describer.field_property)} ?{name} }}"
                                   for describer, name in describing)
                found.append(f"SELECT ?time ?value {' '.join('?' + name for _, name in describing)}".rstrip()
                             + f"\nWHERE {{\n    {subject} tss:hasPoint ?point .\n"
                             f"    ?point tss:field {self.short(field.node)} ; tss:time ?time ; tss:value ?value ."
                             f"{optional}\n}}\nORDER BY ?time\nLIMIT 10")
            numeric = [field for field in source.value_fields if field.is_numeric]
            if numeric:
                found.append("SELECT ?day ?hour (COUNT(?value) AS ?n) (AVG(?value) AS ?mean) "
                             "(MIN(?value) AS ?minimum) (MAX(?value) AS ?maximum)\nWHERE {\n"
                             f"    {subject} tss:hasPoint ?point .\n"
                             f"    ?point tss:field {self.short(numeric[0].node)} ; tss:time ?time ; tss:value ?value .\n"
                             "    BIND(SUBSTR(STR(?time), 1, 10) AS ?day)\n    BIND(HOURS(?time) AS ?hour)\n"
                             "}\nGROUP BY ?day ?hour\nORDER BY ?day ?hour")
        found.append("SELECT ?source ?field ?datatype ?unit ?property\nWHERE {\n"
                     "    ?source tss:hasField ?field .\n    ?field a tss:ValueField .\n"
                     "    OPTIONAL { ?field tss:datatype ?datatype }\n"
                     "    OPTIONAL { ?field tss:unit ?unit }\n    OPTIONAL { ?field tss:observedProperty ?property }\n}")
        found.append("SELECT ?field ?count ?mean ?generated\nWHERE {\n    ?field tss:summary ?summary .\n"
                     "    ?summary tss:count ?count .\n    OPTIONAL { ?summary tss:mean ?mean }\n"
                     "    OPTIONAL { ?summary tss:generatedAtTime ?generated }\n}")
        return found

    def close(self) -> None:
        """Free the graph (it lives in memory only, so nothing is left behind)."""
        self.graph = Graph()
        self.loaded = []

    def __enter__(self) -> "TripleStore":
        return self

    def __exit__(self, *exception) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<TripleStore: {len(self.loaded)} source(s), {len(self.graph)} triples>"
