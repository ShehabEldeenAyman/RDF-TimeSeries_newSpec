"""Expanding snippets into observations.

Every value of a measured field (a field with a ``tss:observedProperty``, other than the time
field) inside the time window becomes one observation. An observation holds a link back to its
snippet, the field's observed property, sensor, feature of interest and unit, the time of its
point (on a time grid: the time of its step) and the decoded value as result, with the field's
datatype; a value without a value encoding keeps its text as written.

>>> result = snippet.expand("observations.nt")       # streamed to a file   # doctest: +SKIP
>>> print(result)                                    # counts, size, per field   # doctest: +SKIP
>>> graph = snippet.expand().graph                   # in memory, for small snippets   # doctest: +SKIP
>>> for observation in snippet.observations():      # no RDF at all   # doctest: +SKIP
...     observation.time, observation.result

The observations are written with SOSA, QUDT and Dublin Core by default, as the specification's
informative mapping says (:class:`SOSAMapping`). To use another vocabulary, subclass
:class:`ObservationMapping`.

The output holds no TSS vocabulary and no description of the snippet or its fields. It does hold
what the metadata says about the IRIs the observations use (observed properties, sensors,
features of interest, units): the concise bounded description of each, without the statements
that use the TSS vocabulary. Blank nodes copied from the metadata get new labels, so they never
merge with an observation.
"""
from __future__ import annotations

import io
import os
import tempfile
import warnings
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, RDF

from .rdf import exact_turtle
from .times import Time
from .vocab import QUDT, SOSA, TSS, XSD

#: Observations are written to the output in batches of this many.
BATCH_SIZE = 10_000


@dataclass(frozen=True)
class Observation:
    """One observation: a value of a measured field, at the time of its point.

    - ``snippet``, ``field``, ``point``: where it comes from.
    - ``time``: the time of the point (a :class:`~rdftss.times.Time`: ``.text``, ``.datetime``).
    - ``result``: the decoded value as an RDF literal with the field's datatype (or language).
    - ``observed_property``, ``sensor``, ``feature_of_interest``, ``unit``: from the field.
    """

    snippet: object
    field: object
    point: object
    time: Time
    result: Literal

    @property
    def observed_property(self):
        return self.field.observed_property

    @property
    def sensor(self):
        return self.field.sensor

    @property
    def feature_of_interest(self):
        return self.field.feature_of_interest

    @property
    def unit(self):
        return self.field.unit


def observations(snippet, *, strict: bool = True):
    """The observations of a snippet, one by one, in the order of its points (a generator).

    Reading follows the specification: only points in the time window, a point without a time
    and a value not in its lexical form are errors (with ``strict=True``)."""
    measured = snippet.measured_fields
    if not measured:
        return
    for point in snippet.iter_points(strict=strict, fields=measured):
        for field in measured:
            if field not in point.values:
                continue
            text = point.text(field)
            if field.datatype == RDF.langString:
                result = Literal(text, lang=field.language) if field.language else Literal(text)
            elif field.datatype is None:
                result = Literal(text)
            else:
                result = Literal(text, datatype=field.datatype, normalize=False)
            yield Observation(snippet, field, point, point.moment, result)


class ObservationMapping:
    """How an :class:`Observation` is written in RDF. Subclass it to use another vocabulary:

    - :meth:`triples` gives the triples of one observation, whose node is ``node``;
    - :meth:`used_iris` gives the IRIs whose description the output should carry;
    - ``prefixes`` are bound in the Turtle preview.
    """

    prefixes: dict = {}

    def triples(self, observation: Observation, node):
        raise NotImplementedError

    def used_iris(self, observation: Observation):
        """The IRIs whose concise bounded description is copied into the output: by default the
        observed property, the sensor, the feature of interest and the unit."""
        return [iri for iri in (observation.observed_property, observation.sensor,
                                observation.feature_of_interest, observation.unit) if isinstance(iri, URIRef)]


class SOSAMapping(ObservationMapping):
    """The specification's informative mapping to SOSA, QUDT and Dublin Core:

    ============================  ================================================
    the observation               a blank node of type ``sosa:Observation``
    the snippet                   ``dcterms:source`` (only when the snippet has an IRI)
    the observed property         ``sosa:observedProperty``
    the sensor                    ``sosa:madeBySensor``
    the feature of interest       ``sosa:hasFeatureOfInterest``
    the unit                      ``qudt:hasUnit``
    the time of the point         ``sosa:resultTime`` (an ``xsd:dateTime``)
    the result                    ``sosa:hasSimpleResult``
    ============================  ================================================
    """

    prefixes = {"sosa": SOSA, "qudt": QUDT, "dcterms": DCTERMS, "xsd": XSD}

    def triples(self, observation: Observation, node):
        yield node, RDF.type, SOSA.Observation
        if isinstance(observation.snippet.node, URIRef):
            yield node, DCTERMS.source, observation.snippet.node
        yield node, SOSA.observedProperty, observation.observed_property
        if observation.sensor is not None:
            yield node, SOSA.madeBySensor, observation.sensor
        if observation.feature_of_interest is not None:
            yield node, SOSA.hasFeatureOfInterest, observation.feature_of_interest
        if observation.unit is not None:
            yield node, QUDT.hasUnit, observation.unit
        yield node, SOSA.resultTime, Literal(observation.time.text, datatype=XSD.dateTime, normalize=False)
        yield node, SOSA.hasSimpleResult, observation.result


@dataclass
class ExpansionResult:
    """What an expansion made.

    - ``observations``, ``triples``: how many; ``per_field``: observations per field IRI (or name).
    - ``path`` and ``size`` (bytes) of the output file, or ``graph`` for an expansion in memory.
    - ``preview``: the first observations and the descriptions, as Turtle.
    - ``notes``: snippets that gave no observations, and why.
    """

    observations: int = 0
    triples: int = 0
    per_field: dict = dataclass_field(default_factory=dict)
    path: Path | None = None
    size: int | None = None
    graph: Graph | None = None
    preview: str = ""
    notes: list = dataclass_field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"Observations: {self.observations:,}", f"Triples:      {self.triples:,}"]
        if self.path is not None:
            lines.append(f"Output:       {self.path} ({self.size:,} bytes)")
        if self.per_field:
            lines.append("Per field:")
            lines.extend(f"  {name}: {count:,}" for name, count in self.per_field.items())
        lines.extend(f"Note: {note}" for note in self.notes)
        return "\n".join(lines)


def describe_iris(graph: Graph, iris) -> Graph:
    """What ``graph`` says about ``iris``: the concise bounded description of each, without the
    statements that use the TSS vocabulary, and with new blank node labels."""
    tss = str(TSS)
    without_tss = Graph()
    for subject, predicate, value in graph:
        if str(predicate).startswith(tss) or (isinstance(value, URIRef) and str(value).startswith(tss)):
            continue
        without_tss.add((subject, predicate, value))
    description = Graph()
    for iri in iris:
        for triple in without_tss.cbd(iri):
            description.add(triple)
    fresh: dict = {}
    copy = Graph()
    for triple in description:
        copy.add(tuple(fresh.setdefault(term, BNode()) if isinstance(term, BNode) else term for term in triple))
    return copy


def _snippets_of(target) -> list:
    if hasattr(target, "snippets"):
        return list(target.snippets)
    if isinstance(target, (list, tuple)):
        return list(target)
    return [target]


def expand(target, destination=None, *, format: str | None = None, mapping: ObservationMapping | None = None,
           strict: bool = True, preview: int = 200, batch_size: int = BATCH_SIZE) -> ExpansionResult:
    """Expand snippets into observations.

    - ``target``: a snippet, a document (every snippet) or a list of snippets.
    - ``destination``: a path or a binary file to write to; ``None`` builds an rdflib graph in
      memory (``result.graph``), which suits small snippets only.
    - ``format``: ``"nt"`` (N-Triples, the default) or ``"nquads"`` (N-Quads, one named graph per
      snippet IRI); by default from the file name (``.nq`` is N-Quads).
    - ``mapping``: the vocabulary (:class:`SOSAMapping` by default).
    - ``strict``: stop at the first data error (the default), or leave bad values out.
    - ``preview``: how many observations to put in ``result.preview``.

    The output is written in batches, so memory stays bounded. A file is first written next to
    its destination and only replaces it when the whole expansion worked.
    """
    mapping = mapping or SOSAMapping()
    snippets = _snippets_of(target)
    if format is None:
        name = str(destination) if isinstance(destination, (str, os.PathLike)) else ""
        format = "nquads" if name.endswith((".nq", ".nquads")) else "nt"
    if format not in ("nt", "nquads"):
        raise ValueError("format must be 'nt' (N-Triples) or 'nquads' (N-Quads)")
    result = ExpansionResult()
    used: dict = {}  # document -> the IRIs its observations use
    preview_graph = Graph()
    for prefix, namespace in mapping.prefixes.items():
        preview_graph.bind(prefix, namespace)

    def batches():
        """(snippet, graph) batches of observations."""
        number = 0
        for snippet in snippets:
            if not snippet.measured_fields:
                result.notes.append(f"{snippet.name} has no measured field (no tss:observedProperty), "
                                    "so it gives no observations")
                continue
            for prefix, namespace in snippet.graph.namespaces():
                preview_graph.bind(prefix, namespace, override=False)
            iris = used.setdefault(id(snippet.document), (snippet.document, set()))[1]
            batch = Graph(store="SimpleMemory")
            in_batch = 0
            for observation in observations(snippet, strict=strict):
                number += 1
                in_batch += 1
                node = BNode(f"o{number}")
                for triple in mapping.triples(observation, node):
                    batch.add(triple)
                    if result.observations < preview:
                        preview_graph.add(triple)
                iris.update(mapping.used_iris(observation))
                result.observations += 1
                key = observation.field.iri or observation.field.name
                result.per_field[key] = result.per_field.get(key, 0) + 1
                if in_batch >= batch_size:
                    yield snippet, batch
                    batch = Graph(store="SimpleMemory")
                    in_batch = 0
            if len(batch):
                yield snippet, batch

    def descriptions() -> Graph:
        graph = Graph()
        for document, iris in used.values():
            for triple in describe_iris(document.graph, iris):
                graph.add(triple)
        return graph

    if destination is None:
        graph = Graph()
        for prefix, namespace in preview_graph.namespaces():
            graph.bind(prefix, namespace, override=False)
        for _, batch in batches():
            for triple in batch:
                graph.add(triple)
        for triple in descriptions():
            graph.add(triple)
        result.graph = graph
        result.triples = len(graph)
    else:
        def write(output) -> None:
            for snippet, batch in batches():
                result.triples += len(batch)
                _write_batch(batch, output, format, snippet)
            extra = descriptions()
            result.triples += len(extra)
            if len(extra):
                _write_batch(extra, output, format, None)

        if hasattr(destination, "write"):
            write(destination)
        else:
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".part")
            try:
                with os.fdopen(handle, "wb") as output:
                    write(output)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
            result.path = path
            result.size = path.stat().st_size
    for triple in descriptions():
        preview_graph.add(triple)
    result.preview = exact_turtle(preview_graph) if len(preview_graph) else ""
    return result


def _write_batch(batch: Graph, output, format: str, snippet) -> None:
    """Write one batch with rdflib's line-based writers (N-Triples or N-Quads). Nothing carries
    over from one line to the next, so batches can follow each other in one file."""
    if format == "nt":
        batch.serialize(destination=output, format="nt", encoding="utf-8")
        return
    dataset = Dataset()
    name = snippet.node if snippet is not None and isinstance(snippet.node, URIRef) else None
    graph = dataset.graph(name) if name is not None else dataset.default_graph
    for triple in batch:
        graph.add(triple)
    stream = io.BytesIO()
    with warnings.catch_warnings():  # rdflib 7.6's N-Quads writer uses its own deprecated Dataset.contexts
        warnings.filterwarnings("ignore", message="Dataset.contexts is deprecated", category=DeprecationWarning)
        dataset.serialize(destination=stream, format="nquads", encoding="utf-8")
    output.write(stream.getvalue())
