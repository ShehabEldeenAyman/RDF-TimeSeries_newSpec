"""Writing metadata documents as Turtle.

:func:`document_turtle` writes a document so that:

- every literal keeps its text (numbers and booleans in full: ``"4810.5"^^xsd:double``);
- the RDF points of an ``EmbeddedRDFSource`` come last, one statement per row (the points of
  one time), in time order, as in the specification's examples, so that a new row can be
  appended at the end (the points of a source that is a blank node stay nested in it)::

      ex:source tss:hasPoint [
          tss:field ex:temperature-field ;
          tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
          tss:value "18.2"^^xsd:double
      ], [
          tss:field ex:humidity-field ;
          tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
          tss:value "61.0"^^xsd:double
      ] .

- ``tss:dataFile`` is written relative to the document (``<data/readings.csv>``) when both are
  local files, so a folder with a document and its data can be moved as a whole.
- a document with a growing ``tss:EmbeddedRDFSource`` (one that does not state
  ``tss:immutable true``) is written as an RDF Message Log, as the specification requires: the
  version directive ``VERSION "1.2-messages"``, the description of the sources as the first
  message, then one message per row, each ended by the delimiter ``MESSAGE``. A writer then
  adds a row by appending one message. The log states no ``tss:immutable false`` (it is the
  default, and the merge of the log would state both values once the source ends)::

      VERSION "1.2-messages"
      @prefix ex: <https://example.org/> .
      ex:source a tss:EmbeddedRDFSource ; ...
      MESSAGE
      ex:source tss:hasPoint [ ... ] .
      MESSAGE
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF

from .rdf import exact_turtle
from .times import parse_time
from .vocab import TSS

#: The order of the statements of a point: its value field, its time, its reading, then the others.
POINT_ORDER = {TSS.field: 0, TSS.time: 1, TSS.value: 2}


def _row_points(graph: Graph) -> dict:
    """{source: [point, ...]} for the RDF points that are written with their row: points that
    only one ``tss:hasPoint`` statement links to, nothing else refers to, and whose statements
    have a literal or an IRI as value. The source must be an IRI: the points of a blank-node
    source stay nested in it."""
    found: dict = {}
    for source, point in graph.subject_objects(TSS.hasPoint):
        if not isinstance(source, URIRef) or isinstance(point, Literal):
            continue
        if len(list(graph.subject_predicates(point))) != 1:
            continue
        if not all(isinstance(value, (Literal, URIRef)) for value in graph.objects(point, None)):
            continue
        found.setdefault(source, []).append(point)
    return found


def _instant(graph: Graph, point):
    """Sort key of a point: the instant of its tss:time, when it can be read."""
    value = graph.value(point, TSS.time)
    if value is not None:
        try:
            return (0, parse_time(str(value)).instant, str(value))
        except ValueError:
            pass
    return (1, 0, str(value))


def _relative(target: str, document_path: Path | None) -> str:
    """``target`` relative to the folder of ``document_path`` when both are local files."""
    if document_path is None:
        return target
    parsed = urlparse(target)
    if parsed.scheme != "file":
        return target
    target_path = Path(unquote(parsed.path))
    try:
        relative = os.path.relpath(target_path, Path(document_path).resolve().parent)
    except ValueError:  # another drive (Windows)
        return target
    return Path(relative).as_posix().replace(" ", "%20")


def growing_rdf_sources(graph: Graph) -> list:
    """The ``tss:EmbeddedRDFSource`` nodes of a graph that do not state ``tss:immutable true``."""
    return sorted((node for node in graph.subjects(RDF.type, TSS.EmbeddedRDFSource)
                   if not any(str(value).strip() in ("true", "1") for value in graph.objects(node, TSS.immutable))),
                  key=str)


def document_turtle(graph: Graph, document_path=None) -> str:
    """A metadata document as Turtle, or as an RDF Message Log when it has a growing
    ``tss:EmbeddedRDFSource`` (see the module documentation).

    ``document_path``: where the document will be written, to write ``tss:dataFile`` relative to it."""
    message_log = bool(growing_rdf_sources(graph))
    points = _row_points(graph)
    main = Graph()
    for prefix, namespace in graph.namespaces():
        main.bind(prefix, namespace, override=True, replace=True)
    skipped = {point for group in points.values() for point in group}
    data_files = []
    for triple in graph:
        subject, predicate, value = triple
        if subject in skipped:
            continue
        if message_log and predicate == TSS.immutable and str(value).strip() in ("false", "0"):
            continue
        if predicate == TSS.hasPoint and value in skipped:
            continue
        if predicate == TSS.dataFile and isinstance(subject, URIRef) and isinstance(value, URIRef) \
                and document_path is not None:
            data_files.append(triple)
            continue
        main.add(triple)
    text = exact_turtle(main).rstrip() + "\n"
    declared = set(re.findall(r"^@prefix ([^:\s]*):", text, flags=re.MULTILINE))
    manager = main.namespace_manager

    def term(node) -> str:
        if isinstance(node, Literal):
            if node.datatype is not None and not _usable(node.datatype):
                return node.n3()
            return node.n3(manager)
        if isinstance(node, URIRef):
            return manager.normalizeUri(node) if _usable(node) else f"<{node}>"
        return node.n3()

    def _usable(iri) -> bool:
        short = manager.normalizeUri(iri)
        return not short.startswith("<") and short.split(":", 1)[0] in declared

    def statements_of(point) -> list:
        return sorted(graph.predicate_objects(point), key=lambda pair: (POINT_ORDER.get(pair[0], 3), str(pair[0])))

    def row_statement(source, row: list) -> str:
        """One row: the blank points nested in one statement, then each point that has an IRI."""
        blank = [point for point in row if isinstance(point, BNode)]
        named = [point for point in row if not isinstance(point, BNode)]
        parts = []
        if blank:
            nested = []
            for point in blank:
                values = statements_of(point)
                lines = ["["] + [f"    {term(p)} {term(v)}{' ;' if i < len(values) - 1 else ''}"
                                 for i, (p, v) in enumerate(values)] + ["]"]
                nested.append("\n".join(lines))
            parts.append(f"{term(source)} {term(TSS.hasPoint)} " + ", ".join(nested) + " .")
        for point in named:
            values = statements_of(point)
            lines = [f"{term(source)} {term(TSS.hasPoint)} {term(point)} .", f"{term(point)}"]
            lines += [f"    {term(p)} {term(v)}{' ;' if i < len(values) - 1 else ' .'}" for i, (p, v) in enumerate(values)]
            parts.append("\n".join(lines))
        return "\n".join(parts)

    extra = []
    for subject, _, value in sorted(data_files):
        extra.append(f"{term(subject)} {term(TSS.dataFile)} <{_relative(str(value), document_path)}> .")
    statements = []
    for source in sorted(points, key=str):
        rows: dict = {}
        for point in points[source]:
            rows.setdefault(_instant(graph, point)[:2], []).append(point)
        for key in sorted(rows):
            row = sorted(rows[key], key=lambda point: (str(graph.value(point, TSS.field)), str(point)))
            statements.append(row_statement(source, row))
    if message_log:
        messages = [text + "".join("\n" + line + "\n" for line in extra)] + [statement + "\n" for statement in statements]
        return 'VERSION "1.2-messages"\n' + "".join(message + "MESSAGE\n" for message in messages)
    extra += statements
    if extra:
        text += "\n" + "\n\n".join(extra) + "\n"
    return text
