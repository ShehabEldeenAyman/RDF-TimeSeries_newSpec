"""Writing metadata documents as Turtle.

:func:`document_turtle` writes a document so that:

- every literal keeps its text (numbers and booleans in full: ``"4810.5"^^xsd:double``);
- the RDF points of an ``EmbeddedRDFSnippet`` come last, one statement each, in time order,
  as in the specification's examples, so that a new point can be appended at the end (the
  points of a snippet that is a blank node stay nested in it)::

      ex:snippet tss:hasPoint [
          ex:time-field "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;
          ex:value-field "4810.5"^^xsd:double
      ] .

- ``tss:dataFile`` is written relative to the document (``<data/readings.csv>``) when both are
  local files, so a folder with a document and its data can be moved as a whole.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from rdflib import BNode, Graph, Literal, URIRef

from .rdf import exact_turtle
from .times import parse_time
from .vocab import TSS


def _inline_points(graph: Graph) -> dict:
    """{snippet: [point, ...]} for the RDF points that can be written inline: blank nodes that
    only one tss:hasPoint statement links to, and whose values are literals."""
    found: dict = {}
    for snippet, point in graph.subject_objects(TSS.hasPoint):
        # A separate statement must name its snippet: a blank-node snippet keeps its points nested
        if not isinstance(snippet, URIRef) or not isinstance(point, BNode):
            continue
        if len(list(graph.subjects(None, point))) != 1:
            continue
        if not all(isinstance(value, Literal) for value in graph.objects(point, None)):
            continue
        found.setdefault(snippet, []).append(point)
    return found


def _point_order(graph: Graph, snippet, point):
    """Sort key of a point: the instant of its time field value, when it can be read."""
    time_field = graph.value(snippet, TSS.timeField)
    value = graph.value(point, time_field) if time_field is not None else None
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


def document_turtle(graph: Graph, document_path=None) -> str:
    """A metadata document as Turtle (see the module documentation).

    ``document_path``: where the document will be written, to write ``tss:dataFile`` relative to it."""
    points = _inline_points(graph)
    main = Graph()
    for prefix, namespace in graph.namespaces():
        main.bind(prefix, namespace, override=True, replace=True)
    skipped = {point for group in points.values() for point in group}
    data_files = []
    for triple in graph:
        subject, predicate, value = triple
        if subject in skipped:
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

    extra = []
    for subject, _, value in sorted(data_files):
        extra.append(f"{term(subject)} {term(TSS.dataFile)} <{_relative(str(value), document_path)}> .")
    for snippet in sorted(points, key=str):
        time_field = graph.value(snippet, TSS.timeField)
        for point in sorted(points[snippet], key=lambda node: _point_order(graph, snippet, node)):
            values = sorted(graph.predicate_objects(point), key=lambda pair: (pair[0] != time_field, str(pair[0])))
            lines = [f"{term(snippet)} {term(TSS.hasPoint)} ["]
            for index, (predicate, value) in enumerate(values):
                end = " ;" if index < len(values) - 1 else ""
                lines.append(f"    {term(predicate)} {term(value)}{end}")
            lines.append("] .")
            extra.append("\n".join(lines))
    if extra:
        text += "\n" + "\n\n".join(extra) + "\n"
    return text
