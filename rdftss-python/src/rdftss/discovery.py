"""Finding sources by what they observe, where, with which sensor, and when.

A :class:`~rdftss.document.Document` holds one metadata document. To search several, load each
one and give them all to :func:`find`::

    import rdftss
    from pathlib import Path

    documents = [rdftss.load(path) for path in Path("metadata").glob("*.ttl")]
    for match in rdftss.find(documents,
                             observed_property="quantitykind:ElectricConductivity",
                             overlapping=("2021-03-04", "2021-03-05")):
        print(match)                      # the source, its window and the matching fields
        match.source.how_to_read()       # where its readings are, and how to read them

Typical discovery questions map onto the library like this:

=======================================================  ===========================================
Which sources observe a property of a feature?           ``find(documents, observed_property=..., feature_of_interest=...)``
Which windows overlap an interval?                       ``find(..., overlapping=(start, end))``: windows that only touch do not overlap
Where are the readings?                                  ``source.kind``, ``source.data_file``, ``source.how_to_read()``
How is each field bound to its values?                   ``source.time_field``, ``field.column_name``, ``field.field_property``
What does a field observe?                               ``field.datatype``, ``field.unit``, ``field.observed_property``
What is the time grid or the value encoding?             ``source.time_grid``, ``field.value_encoding``, ``source.csv``
Is it final, or growing, and does it allow Range?        ``source.immutable``, ``source.growing``, ``source.range_requests``
What summaries are there?                                ``field.summary``, ``source.window``
=======================================================  ===========================================

:func:`find` matches IRIs exactly: it does no inference.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from rdflib import URIRef

from .model import Source, SourceKind


@dataclass
class Match:
    """A source found by :func:`find`, with the fields that matched (all its value fields
    when no field criterion was given)."""

    source: Source
    fields: list = dataclass_field(default_factory=list)

    def __str__(self) -> str:
        source = self.source
        names = ", ".join(field.name for field in self.fields) or "(no value field)"
        kind = source.kind.value if source.kind else "?"
        return (f"{source.document.short(source.node)} ({kind}, window {source.window}) "
                f"in {source.document.location}: {names}")


def _sources(objects) -> list[Source]:
    """The sources of a Document, a Source, or a list of them."""
    if isinstance(objects, Source):
        return [objects]
    if hasattr(objects, "sources"):
        return list(objects.sources)
    found = []
    for item in objects:
        found.extend(_sources(item))
    return found


def _iris(value, source: Source) -> set | None:
    """The IRIs a criterion stands for, reading prefixed names with the source's document."""
    if value is None:
        return None
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    found = set()
    for item in values:
        if isinstance(item, URIRef):
            found.add(item)
            continue
        text = str(item)
        found.add(URIRef(source.document.curie_to_iri(text) or text))
    return found


def find(objects, *, observed_property=None, feature_of_interest=None, sensor=None, unit=None,
         overlapping=None, kind=None, immutable: bool | None = None, label: str | None = None) -> list[Match]:
    """The sources of ``objects`` (a document, a source, or a list of them) that meet every
    criterion given.

    - ``observed_property``, ``feature_of_interest``, ``sensor``, ``unit``: an IRI, a prefixed
      name (read with each document's prefixes, or well-known ones such as ``unit:``), or a list
      of them (any matches). A source matches when one of its fields matches all of these.
    - ``overlapping``: ``(start, end)``: the source's window overlaps ``[start, end)``. An open
      side of a window, or ``None`` here, is unbounded. Windows that only touch do not overlap.
    - ``kind``: a source class: ``"csv"``, ``"parquet"``, ``"arrow"``, ``"json"``, ``"rdf"``.
    - ``immutable``: ``True`` for final sources, ``False`` for growing ones.
    - ``label``: a text that the source's label contains (any case).
    """
    wanted_kind = SourceKind.parse(kind) if kind is not None else None
    criteria = {"observed_property": observed_property, "feature_of_interest": feature_of_interest,
                "sensor": sensor, "unit": unit}
    matches = []
    for source in _sources(objects):
        if wanted_kind is not None and source.kind != wanted_kind:
            continue
        if immutable is not None and source.immutable != immutable:
            continue
        if label is not None and not any(label.lower() in str(text).lower() for text in source.labels):
            continue
        if overlapping is not None and not source.window.overlaps(*overlapping):
            continue
        wanted = {name: _iris(value, source) for name, value in criteria.items() if value is not None}
        if wanted:
            fields = [field for field in source.fields
                      if all(getattr(field, name) in iris for name, iris in wanted.items())]
            if not fields:
                continue
        else:
            fields = source.value_fields
        matches.append(Match(source, fields))
    return matches
