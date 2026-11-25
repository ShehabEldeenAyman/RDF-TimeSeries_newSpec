"""Expanding sources into points, as the specification says (#expanding).

A source of any class can be expanded into its points as RDF, in the shape of the points of a
``tss:EmbeddedRDFSource``. For each row in the time window, and for each value field with a value
in it, there is one point::

    <source> tss:hasPoint _:point .
    _:point tss:field <value field> ;
        tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;    # the time of the row
        tss:value "4810.5"^^xsd:double ;                   # the reading: the decoded value
        ex:status "ok" .                                   # the value of each describing field

The point is the RDF point itself for a ``tss:EmbeddedRDFSource``, the IRI that ``tss:aboutUrl``
makes when the value field states one, and a new blank node otherwise. The expansion of a source
is its metadata document without ``tss:points``, with the points added, and with the default
``tss:property`` of each describing field that states none, so that it can be read without the
specification. A value without a value encoding keeps its text as written.

>>> result = source.expand("points.nt")      # streamed to a file   # doctest: +SKIP
>>> graph = source.expand().graph            # in memory, for small sources   # doctest: +SKIP
>>> for point in source.points():            # no RDF at all   # doctest: +SKIP
...     point.field.name, point.time.text, point.value

With ``derive=True``, the output also holds what the alignment of the TSS vocabulary with SOSA,
SAREF and QUDT derives (see :mod:`rdftss.alignment`): each point is then also a
``sosa:Observation`` and a ``saref:Measurement``, with its result time, its result, and the
observed property, sensor, feature of interest and unit of its field.
"""
from __future__ import annotations

import io
import os
import tempfile
import warnings
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from urllib.parse import urljoin

from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.namespace import RDF

from .errors import DataError, Issue
from .rdf import exact_turtle
from .templates import expand_template, template_variables
from .times import Time
from .vocab import TSS

#: Points are written to the output in batches of this many.
BATCH_SIZE = 10_000


def literal(text: str, field) -> Literal:
    """A value as an RDF literal with its field's datatype (or language), keeping its text."""
    if field.datatype == RDF.langString:
        return Literal(text, lang=field.language) if field.language else Literal(text)
    if field.datatype is None:
        return Literal(text)
    return Literal(text, datatype=field.datatype, normalize=False)


@dataclass(frozen=True)
class Point:
    """One point: one reading, the value of a value field in a row.

    - ``source``, ``field`` (its value field), ``row``: where it comes from.
    - ``node``: the IRI or blank node of the point.
    - ``time``: the time of its row (a :class:`~rdftss.times.Time`: ``.text``, ``.datetime``).
    - ``value``: its reading: the decoded value as an RDF literal with the field's datatype (or language).
    - ``describing``: the values of the describing fields that describe it, as literals, by field.
    - ``observed_property``, ``sensor``, ``feature_of_interest``, ``unit``: from its field.
    """

    source: object
    field: object
    row: object
    node: object
    time: Time
    value: Literal
    describing: dict = dataclass_field(default_factory=dict)

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

    def triples(self):
        """The triples of the point, as the expansion states them."""
        node = self.node
        yield self.source.node, TSS.hasPoint, node
        yield node, TSS.field, self.field.node
        yield node, TSS.time, Literal(self.time.text, datatype=self.source.time_field.datatype, normalize=False)
        yield node, TSS.value, self.value
        for describer, value in self.describing.items():
            yield node, describer.field_property, value


def _base_iri(source) -> str | None:
    """The IRI that point IRIs and default properties are resolved against: the data file of an
    external source, the metadata document of an embedded one."""
    return source.data_file if source.kind is not None and source.kind.is_external else source.document.base


def points(source, *, strict: bool = True, window: bool = True, fields=None, issues=None):
    """The points of a source, one by one (a generator), in the order of its rows.

    - ``strict``: stop at the first data error (the default), or leave bad values out.
    - ``window``: only the rows in the time window (the default, as the expansion does).
    - ``fields``: only the points of these value fields.
    - ``issues``: a list that gets the problems found, with ``strict=False``.
    """
    value_fields = source.value_fields if fields is None else \
        [field for field in (source.field(key) for key in fields) if field.is_value]
    if not value_fields:
        return
    needed = set(value_fields)
    for field in value_fields:
        needed.update(field.described_by)
        if field.about_url is not None:
            names = set(template_variables(field.about_url))
            needed.update(other for other in source.fields if other.column_name in names)
    by_column = {field.column_name: field for field in source.fields if field.column_name is not None}
    base = _base_iri(source)
    made: set = set()
    for row in source.iter_rows(strict=strict, window=window, fields=list(needed), issues=issues):
        for field in value_fields:
            if field not in row.values:
                continue
            node = row.nodes.get(field) if row.nodes else None
            if node is None and field.about_url is not None:
                texts = {name: row.raw(other) for name, other in by_column.items()}
                iri = URIRef(urljoin(base, expand_template(field.about_url, texts)) if base
                             else expand_template(field.about_url, texts))
                if iri in made:
                    message = (f"field {field.name!r}: tss:aboutUrl gives the IRI {iri} to two points; "
                               "no two points of a source have the same IRI")
                    if strict:
                        raise DataError(message, row.where)
                    if issues is not None:
                        issues.append(Issue("error", message, row.where))
                    continue
                made.add(iri)
                node = iri
            if node is None:
                node = BNode()
            describing = {describer: literal(row.text(describer), describer)
                          for describer in field.described_by
                          if describer in row.values and describer.field_property is not None}
            yield Point(source, field, row, node, row.moment, literal(row.text(field), field), describing)


@dataclass
class ExpansionResult:
    """What an expansion made.

    - ``points``, ``triples``: how many; ``per_field``: points per field IRI (or name).
    - ``derived``: how many triples the alignment derived (with ``derive=True``).
    - ``path`` and ``size`` (bytes) of the output file, or ``graph`` for an expansion in memory.
    - ``preview``: the first points, as Turtle.
    - ``notes``: sources that gave no points, and why.
    """

    points: int = 0
    triples: int = 0
    derived: int = 0
    per_field: dict = dataclass_field(default_factory=dict)
    path: Path | None = None
    size: int | None = None
    graph: Graph | None = None
    preview: str = ""
    notes: list = dataclass_field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"Points:       {self.points:,}", f"Triples:      {self.triples:,}"]
        if self.derived:
            lines.append(f"Derived:      {self.derived:,} (through the alignment with SOSA, SAREF and QUDT)")
        if self.path is not None:
            lines.append(f"Output:       {self.path} ({self.size:,} bytes)")
        if self.per_field:
            lines.append("Per field:")
            lines.extend(f"  {name}: {count:,}" for name, count in self.per_field.items())
        lines.extend(f"Note: {note}" for note in self.notes)
        return "\n".join(lines)


def _sources_of(target) -> list:
    if hasattr(target, "sources"):
        return list(target.sources)
    if isinstance(target, (list, tuple)):
        return list(target)
    return [target]


def metadata_graph(documents, *, default_properties: bool = True) -> Graph:
    """The metadata documents without ``tss:points``, with the default ``tss:property`` of each
    describing field that states none."""
    graph = Graph()
    for document in documents:
        for prefix, namespace in document.graph.namespaces():
            graph.bind(prefix, namespace, override=False)
        for triple in document.graph:
            if triple[1] != TSS.points:
                graph.add(triple)
        if default_properties:
            for source in document.sources:
                for field in source.describing_fields:
                    if field.property is None and field.default_property is not None:
                        graph.add((field.node, TSS.property, field.default_property))
    return graph


def expand(target, destination=None, *, format: str | None = None, strict: bool = True, derive: bool = False,
           metadata: bool = True, preview: int = 200, batch_size: int = BATCH_SIZE) -> ExpansionResult:
    """Expand sources into their points as RDF (the specification's #expanding).

    - ``target``: a source, a document (every source) or a list of sources.
    - ``destination``: a path or a binary file to write to; ``None`` builds an rdflib graph in
      memory (``result.graph``), which suits small sources only.
    - ``format``: ``"nt"`` (N-Triples, the default) or ``"nquads"`` (N-Quads: the metadata in the
      default graph, the points of each source in a graph named by the source); by default from
      the file name (``.nq`` is N-Quads).
    - ``strict``: stop at the first data error (the default), or leave bad values out.
    - ``derive``: also write what the alignment with SOSA, SAREF and QUDT derives.
    - ``metadata``: also write the metadata documents (the default), as the specification's
      expansion does; ``False`` writes only the points.
    - ``preview``: how many points to put in ``result.preview``.

    The output is written in batches, so memory stays bounded. A file is first written next to
    its destination and only replaces it when the whole expansion worked.
    """
    from .alignment import derive as derive_from

    sources = _sources_of(target)
    if format is None:
        name = str(destination) if isinstance(destination, (str, os.PathLike)) else ""
        format = "nquads" if name.endswith((".nq", ".nquads")) else "nt"
    if format not in ("nt", "nquads"):
        raise ValueError("format must be 'nt' (N-Triples) or 'nquads' (N-Quads)")
    for source in sources:
        source.check_readable()
    documents = list({id(source.document): source.document for source in sources}.values())
    described = metadata_graph(documents)
    result = ExpansionResult()
    preview_graph = Graph()
    preview_nodes: set = set()
    for prefix, namespace in described.namespaces():
        preview_graph.bind(prefix, namespace, override=False)

    def with_derived(batch: Graph, nodes: set) -> Graph:
        if not derive:
            return batch
        for triple in derive_from(batch, context=described):
            if triple[0] in nodes:
                batch.add(triple)
                result.derived += 1
                if triple[0] in preview_nodes:
                    preview_graph.add(triple)
        return batch

    def batches():
        """(source or None, graph) batches: the metadata first, then the points."""
        if metadata:
            head = Graph(store="SimpleMemory")
            for triple in described:
                head.add(triple)
            if derive:
                extra = derive_from(described)
                result.derived += len(extra)
                for triple in extra:
                    head.add(triple)
            yield None, head
        for source in sources:
            if not source.value_fields:
                result.notes.append(f"{source.name} has no value field (tss:ValueField), so it has no points")
                continue
            batch = Graph(store="SimpleMemory")
            nodes: set = set()
            for point in points(source, strict=strict):
                for triple in point.triples():
                    batch.add(triple)
                    if result.points < preview:
                        preview_graph.add(triple)
                if result.points < preview:
                    preview_nodes.add(point.node)
                nodes.add(point.node)
                result.points += 1
                key = point.field.iri or point.field.name
                result.per_field[key] = result.per_field.get(key, 0) + 1
                if len(nodes) >= batch_size:
                    yield source, with_derived(batch, nodes)
                    batch, nodes = Graph(store="SimpleMemory"), set()
            if len(batch):
                yield source, with_derived(batch, nodes)

    if destination is None:
        graph = Graph()
        for prefix, namespace in described.namespaces():
            graph.bind(prefix, namespace, override=False)
        for _, batch in batches():
            for triple in batch:
                graph.add(triple)
        result.graph = graph
        result.triples = len(graph)
    else:
        def write(output) -> None:
            for source, batch in batches():
                result.triples += len(batch)
                _write_batch(batch, output, format, source)

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
    result.preview = exact_turtle(preview_graph) if len(preview_graph) else ""
    return result


def _write_batch(batch: Graph, output, format: str, source) -> None:
    """Write one batch with rdflib's line-based writers (N-Triples or N-Quads). Nothing carries
    over from one line to the next, so batches can follow each other in one file."""
    if format == "nt":
        batch.serialize(destination=output, format="nt", encoding="utf-8")
        return
    dataset = Dataset()
    name = source.node if source is not None and isinstance(source.node, URIRef) else None
    graph = dataset.graph(name) if name is not None else dataset.default_graph
    for triple in batch:
        graph.add(triple)
    stream = io.BytesIO()
    with warnings.catch_warnings():  # rdflib 7.6's N-Quads writer uses its own deprecated Dataset.contexts
        warnings.filterwarnings("ignore", message="Dataset.contexts is deprecated", category=DeprecationWarning)
        dataset.serialize(destination=stream, format="nquads", encoding="utf-8")
    output.write(stream.getvalue())
