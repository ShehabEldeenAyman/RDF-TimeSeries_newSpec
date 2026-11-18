"""SPARQL on snippets, with rdflib.

>>> result = snippet.sparql("SELECT (AVG(?value) AS ?mean) WHERE { ?point ex:csv-value-field ?value }")  # doctest: +SKIP
>>> print(result)                                                                   # doctest: +SKIP
>>> with rdftss.TripleStore(document.snippets) as store:                            # doctest: +SKIP
...     print(store.describe())
...     store.query(store.examples()[0]).to_pandas()

A :class:`TripleStore` is an in-memory RDF graph that holds:

- the metadata documents of the snippets, as written: snippets, fields, windows, units,
  summaries, sensors, features of interest, ...;
- the points of each snippet, in the shape of embedded RDF points, whatever the snippet's
  storage class::

      <snippet> tss:hasPoint _:point .
      _:point <time field>    "2024-05-12T00:00:00Z"^^xsd:dateTime ;
              <value field>   "4747.00"^^xsd:double ;
              <quality field> "suspect"^^xsd:string .

  Only the points inside the window are there, with their decoded values in their exact text.
  An empty value has no triple. The documents' own embedded points (``tss:hasPoint`` and
  ``tss:points``) are left out, so that no point is there twice.

Every query knows the prefixes of the documents, and ``tss:``, ``xsd:``, ``rdf:``, ``rdfs:``,
``sosa:``, ``qudt:``, ``unit:`` and ``quantitykind:``.

Two things differ from the documents, so that rdflib can work with the data:

- rdflib cannot compare or sort ``xsd:dateTimeStamp`` values, so every ``xsd:dateTimeStamp``
  literal is an ``xsd:dateTime`` literal with the same text. Times then compare as moments:
  ``…T10:00:00+02:00`` equals ``…T08:00:00Z``. ``HOURS()``, ``DAY()`` and the like give the
  parts in the time's own offset.
- A field that is a blank node has no IRI to use as a predicate. Its values use an IRI
  ``urn:rdftss:field:…``, which :meth:`TripleStore.describe` shows.

Queries are read only and locked down. SPARQL Update is not accepted. ``SERVICE`` (which would
query other servers) and ``FROM``/``FROM NAMED`` (which would download graphs) are refused. An
answer is limited in rows, size and time.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field as dataclass_field

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parserutils import CompValue

from .errors import RDFTSSError, require
from .vocab import QUANTITYKIND, QUDT, SOSA, TSS, UNIT

#: The prefixes every query knows, besides those of the documents.
PREFIXES = {"tss": TSS, "xsd": XSD, "rdf": RDF, "rdfs": RDFS, "sosa": SOSA, "qudt": QUDT, "unit": UNIT,
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
    """An in-memory RDF graph with the metadata and the points of snippets, to query with SPARQL.

    - ``snippets``: the snippets to load (a list, or a :class:`~rdftss.document.Document`).
    - ``max_rows``, ``max_bytes``, ``timeout``: the limits of one answer.
    - ``strict``: read the snippets strictly (a snippet with a data error is then left out, and
      listed in ``skipped``); with ``strict=False`` the bad values are left out instead.
    - ``between``: ``(start, end)`` loads only the points from ``start``, before ``end``.
    - ``fields``: loads only these fields (names, IRIs or labels) of every snippet that has them;
      the time field is always loaded.

    rdflib keeps the graph in memory, at about 6 KB and a few dozen microseconds per point and
    field, so a store of 200,000 points with three fields takes some 10 seconds and over 1 GB.
    For large snippets, load the period and the fields you need with ``between`` and ``fields``.

    ``graph`` is the rdflib :class:`~rdflib.Graph` itself, to serialize it or use it with other
    tools. A store can be used as a context manager; :meth:`close` frees the graph.
    """

    def __init__(self, snippets, *, max_rows: int = 10_000, max_bytes: int = 50_000_000,
                 timeout: float = 30.0, strict: bool = True, between=None, fields=None):
        self.max_rows, self.max_bytes, self.timeout = max_rows, max_bytes, timeout
        snippets = list(getattr(snippets, "snippets", snippets))
        self.graph = Graph(bind_namespaces="none")
        self.loaded: list[tuple] = []    # (snippet, number of points)
        self.skipped: list[tuple] = []   # (snippet, why)
        self.predicates: dict = {}       # Field -> the IRI its values use
        documents = {id(snippet.document): snippet.document for snippet in snippets}
        for document in documents.values():
            self._add_metadata(document)
        for prefix, namespace in PREFIXES.items():
            self.graph.bind(prefix, namespace, replace=True)
        for snippet in snippets:
            try:
                points = snippet.read(strict=strict, fields=self._fields_of(snippet, fields))
                if between is not None:
                    points = points.between(*between)
            except RDFTSSError as error:
                self.skipped.append((snippet, str(error)))
                continue
            self._add_points(snippet, points)
            self.loaded.append((snippet, len(points)))
        self.namespaces = {prefix: str(namespace) for prefix, namespace in self.graph.namespaces() if prefix}

    # -- building it -----------------------------------------------------------------------------

    def _add_metadata(self, document) -> None:
        """Copy the document, without its embedded points."""
        graph = document.graph
        point_nodes = set()
        for snippet in document.snippets:
            point_nodes.update(graph.objects(snippet.node, TSS.hasPoint))
        for subject, predicate, value in graph:
            if subject in point_nodes or predicate in (TSS.hasPoint, TSS.points):
                continue
            self.graph.add((subject, predicate, _query_literal(value)))
        for prefix, namespace in graph.namespaces():
            if prefix:
                self.graph.bind(prefix, namespace, override=False)

    @staticmethod
    def _fields_of(snippet, keys):
        if keys is None:
            return None
        found = []
        for key in keys:
            try:
                found.append(snippet.field(key))
            except KeyError:
                pass
        return found

    def _predicate(self, field) -> URIRef:
        predicate = self.predicates.get(field)
        if predicate is None:
            predicate = field.node if isinstance(field.node, URIRef) else URIRef(f"urn:rdftss:field:{uuid.uuid4()}")
            self.predicates[field] = predicate
        return predicate

    @staticmethod
    def _literal(text: str, field) -> Literal:
        if field.language:
            return Literal(text, lang=field.language)
        datatype = XSD.dateTime if field.datatype == XSD.dateTimeStamp else field.datatype
        return Literal(text, datatype=datatype, normalize=False)

    def _add_points(self, snippet, points) -> None:
        add = self.graph.add
        time_field = snippet.time_field
        others = [field for field in snippet.fields if field is not time_field]
        time_predicate = self._predicate(time_field)
        predicates = [(field, self._predicate(field)) for field in others]
        for point in points:
            node = BNode()
            add((snippet.node, TSS.hasPoint, node))
            add((node, time_predicate, Literal(point.time_text, datatype=XSD.dateTime, normalize=False)))
            for field, predicate in predicates:
                text = point.texts.get(field)
                if text is None:
                    continue
                if field.value_encoding is not None:
                    text = field.value_encoding.decode(text)
                add((node, predicate, self._literal(text, field)))

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
        """What the store holds, in text: per snippet, the predicate of each field."""
        lines = []
        for snippet, count in self.loaded:
            kind = snippet.kind.iri.split("#")[-1] if snippet.kind else "no class"
            lines.append(f"Snippet {self.short(snippet.node)} ({kind}): {count} points, "
                         f"each linked with tss:hasPoint")
            fields = [snippet.time_field] + [field for field in snippet.fields if field is not snippet.time_field]
            names = {field: self.short(self._predicate(field)) for field in fields}
            width = max(len(name) for name in names.values())
            for field in fields:
                if field.language:
                    kind_of_value = f"@{field.language}"
                elif field is snippet.time_field or field.datatype == XSD.dateTimeStamp:
                    kind_of_value = "xsd:dateTime"
                else:
                    kind_of_value = self.short(field.datatype) if field.datatype else "xsd:string"
                lines.append(f"    ?point {names[field].ljust(width)}  {field.role:<10}  {kind_of_value}")
        for snippet, reason in self.skipped:
            lines.append(f"Not loaded: {snippet.name}: {reason}")
        lines.append("The metadata of the documents, as written (xsd:dateTimeStamp literals as xsd:dateTime).")
        lines.append("Prefixes: " + ", ".join(f"{prefix}:" for prefix in sorted(self.namespaces)))
        return "\n".join(lines)

    def examples(self) -> list[str]:
        """Example queries for the loaded snippets, with their real IRIs."""
        found = []
        for snippet, _ in self.loaded:
            subject = self.short(snippet.node) if isinstance(snippet.node, URIRef) else "?snippet"
            time_name = self.short(self._predicate(snippet.time_field))
            used = {"point", "time"}
            others = [(field, _variable(field.name, used)) for field in snippet.fields
                      if field is not snippet.time_field]
            optional = "".join(f"\n    OPTIONAL {{ ?point {self.short(self._predicate(field))} ?{name} }}"
                               for field, name in others)
            found.append(f"SELECT ?time {' '.join('?' + name for _, name in others)}\nWHERE {{\n"
                         f"    {subject} tss:hasPoint ?point .\n    ?point {time_name} ?time .{optional}\n"
                         f"}}\nORDER BY ?time\nLIMIT 10")
            numeric = [field for field in snippet.value_fields if field.is_numeric]
            if numeric:
                value_name = self.short(self._predicate(numeric[0]))
                found.append("SELECT ?day ?hour (COUNT(?value) AS ?n) (AVG(?value) AS ?mean) "
                             "(MIN(?value) AS ?minimum) (MAX(?value) AS ?maximum)\nWHERE {\n"
                             f"    {subject} tss:hasPoint ?point .\n"
                             f"    ?point {time_name} ?time ; {value_name} ?value .\n"
                             "    BIND(SUBSTR(STR(?time), 1, 10) AS ?day)\n    BIND(HOURS(?time) AS ?hour)\n"
                             "}\nGROUP BY ?day ?hour\nORDER BY ?day ?hour")
        found.append("SELECT ?snippet ?field ?datatype ?unit ?property\nWHERE {\n"
                     "    ?snippet tss:hasField ?field .\n    OPTIONAL { ?field tss:datatype ?datatype }\n"
                     "    OPTIONAL { ?field tss:unit ?unit }\n    OPTIONAL { ?field tss:observedProperty ?property }\n}")
        found.append("SELECT ?field ?count ?mean ?generated\nWHERE {\n    ?field tss:summary ?summary .\n"
                     "    ?summary tss:count ?count .\n    OPTIONAL { ?summary tss:mean ?mean }\n"
                     "    OPTIONAL { ?summary tss:generatedAtTime ?generated }\n}")
        return found

    def close(self) -> None:
        """Free the graph (it lives in memory only, so nothing is left behind)."""
        self.graph = Graph()
        self.loaded, self.predicates = [], {}

    def __enter__(self) -> "TripleStore":
        return self

    def __exit__(self, *exception) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<TripleStore: {len(self.loaded)} snippet(s), {len(self.graph)} triples>"
