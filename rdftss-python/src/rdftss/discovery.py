"""Finding snippets by what they observe, where, with which sensor, and when.

A :class:`~rdftss.document.Document` holds one metadata document. To search several, load each
one and give them all to :func:`find`::

    import rdftss
    from pathlib import Path

    documents = [rdftss.load(path) for path in Path("metadata").glob("*.ttl")]
    for match in rdftss.find(documents,
                             observed_property="quantitykind:ElectricConductivity",
                             overlapping=("2021-03-04", "2021-03-05")):
        print(match)                      # the snippet, its window and the matching fields
        match.snippet.how_to_read()       # where its readings are, and how to read them

The competency questions of the evaluation (demo 11, ``evaluation/competency_questions``) map
onto the library like this:

====  ======================================================================================
CQ1   ``find(documents, observed_property=..., feature_of_interest=...)``
CQ2   ``find(..., overlapping=(start, end))``: windows that only touch do not overlap
CQ3   ``snippet.kind``, ``snippet.data_file``, ``snippet.how_to_read()["embedded_predicate"]``
CQ4   ``snippet.time_field``, ``field.column_name``, ``field.column_index``, ``how_to_read()``
CQ5   ``field.datatype``, ``field.unit``, ``field.observed_property``, ``field.describe()``
CQ6   ``snippet.time_grid``, ``field.value_encoding``, ``snippet.csv``
CQ7   ``snippet.immutable`` (final) or ``snippet.growing``
CQ8   ``field.summary``, ``snippet.window``
====  ======================================================================================

Like the competency questions, :func:`find` matches IRIs exactly: it does no inference.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from rdflib import URIRef

from .model import Snippet, SnippetKind


@dataclass
class Match:
    """A snippet found by :func:`find`, with the fields that matched (all its measured fields
    when no field criterion was given)."""

    snippet: Snippet
    fields: list = dataclass_field(default_factory=list)

    def __str__(self) -> str:
        snippet = self.snippet
        names = ", ".join(field.name for field in self.fields) or "(no measured field)"
        kind = snippet.kind.value if snippet.kind else "?"
        return (f"{snippet.document.short(snippet.node)} ({kind}, window {snippet.window}) "
                f"in {snippet.document.source}: {names}")


def _snippets(objects) -> list[Snippet]:
    """The snippets of a Document, a Snippet, or a list of them."""
    if isinstance(objects, Snippet):
        return [objects]
    if hasattr(objects, "snippets"):
        return list(objects.snippets)
    found = []
    for item in objects:
        found.extend(_snippets(item))
    return found


def _iris(value, snippet: Snippet) -> set | None:
    """The IRIs a criterion stands for, reading prefixed names with the snippet's document."""
    if value is None:
        return None
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    found = set()
    for item in values:
        if isinstance(item, URIRef):
            found.add(item)
            continue
        text = str(item)
        found.add(URIRef(snippet.document.curie_to_iri(text) or text))
    return found


def find(objects, *, observed_property=None, feature_of_interest=None, sensor=None, unit=None,
         overlapping=None, kind=None, immutable: bool | None = None, label: str | None = None) -> list[Match]:
    """The snippets of ``objects`` (a document, a snippet, or a list of them) that meet every
    criterion given.

    - ``observed_property``, ``feature_of_interest``, ``sensor``, ``unit``: an IRI, a prefixed
      name (read with each document's prefixes, or well-known ones such as ``unit:``), or a list
      of them (any matches). A snippet matches when one of its fields matches all of these.
    - ``overlapping``: ``(start, end)``: the snippet's window overlaps ``[start, end)``. An open
      side of a window, or ``None`` here, is unbounded. Windows that only touch do not overlap.
    - ``kind``: a snippet class: ``"csv"``, ``"parquet"``, ``"arrow"``, ``"json"``, ``"rdf"``.
    - ``immutable``: ``True`` for final snippets, ``False`` for growing ones.
    - ``label``: a text that the snippet's label contains (any case).
    """
    wanted_kind = SnippetKind.parse(kind) if kind is not None else None
    criteria = {"observed_property": observed_property, "feature_of_interest": feature_of_interest,
                "sensor": sensor, "unit": unit}
    matches = []
    for snippet in _snippets(objects):
        if wanted_kind is not None and snippet.kind != wanted_kind:
            continue
        if immutable is not None and snippet.immutable != immutable:
            continue
        if label is not None and not any(label.lower() in str(text).lower() for text in snippet.labels):
            continue
        if overlapping is not None and not snippet.window.overlaps(*overlapping):
            continue
        wanted = {name: _iris(value, snippet) for name, value in criteria.items() if value is not None}
        if wanted:
            fields = [field for field in snippet.fields
                      if all(getattr(field, name) in iris for name, iris in wanted.items())]
            if not fields:
                continue
        else:
            fields = snippet.measured_fields
        matches.append(Match(snippet, fields))
    return matches
