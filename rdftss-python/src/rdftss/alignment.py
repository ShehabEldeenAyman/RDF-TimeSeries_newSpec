"""What the alignment of the TSS vocabulary with SOSA, SAREF and QUDT derives from points.

The specification does not write its points with another vocabulary. The TSS vocabulary is
aligned with SOSA, SAREF and QUDT instead (the specification's #alignment), so that a reasoner
derives their observations and measurements from the points when they are needed::

    tss:Point   rdfs:subClassOf     sosa:Observation, saref:Measurement
    tss:time    rdfs:subPropertyOf  sosa:resultTime, saref:hasTimestamp
    tss:value   rdfs:subPropertyOf  sosa:hasSimpleResult, saref:hasValue
    sosa:observedProperty owl:propertyChainAxiom ( tss:field tss:observedProperty ) .
    ...

:func:`derive` does what a reasoner does with these axioms, without one. It reads them from the
bundled vocabulary (or from one you give), so it follows the vocabulary, and applies:

- ``rdfs:domain`` and ``rdfs:range``: a point is a ``tss:Point`` because it is the object of
  ``tss:hasPoint``;
- ``rdfs:subClassOf`` and ``rdfs:subPropertyOf``, transitively;
- ``owl:propertyChainAxiom`` with two properties: ``tss:field`` followed by
  ``tss:observedProperty`` gives ``sosa:observedProperty``.

>>> from rdftss.alignment import derive                      # doctest: +SKIP
>>> observations = derive(source.expand().graph)            # doctest: +SKIP
"""
from __future__ import annotations

from functools import lru_cache

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS


class Alignment:
    """The axioms of a vocabulary that :func:`derive` applies."""

    def __init__(self, vocabulary: Graph):
        self.domains: dict = {}
        self.ranges: dict = {}
        self.chains: list = []  # (derived property, first property, second property)
        super_classes: dict = {}
        super_properties: dict = {}
        for sub, sup in vocabulary.subject_objects(RDFS.subClassOf):
            if isinstance(sub, URIRef) and isinstance(sup, URIRef):
                super_classes.setdefault(sub, set()).add(sup)
        for sub, sup in vocabulary.subject_objects(RDFS.subPropertyOf):
            if isinstance(sub, URIRef) and isinstance(sup, URIRef):
                super_properties.setdefault(sub, set()).add(sup)
        self.super_classes = _closure(super_classes)
        self.super_properties = _closure(super_properties)
        for prop, cls in vocabulary.subject_objects(RDFS.domain):
            if isinstance(cls, URIRef):
                self.domains.setdefault(prop, set()).add(cls)
        for prop, cls in vocabulary.subject_objects(RDFS.range):
            if isinstance(cls, URIRef):
                self.ranges.setdefault(prop, set()).add(cls)
        for derived, chain in vocabulary.subject_objects(OWL.propertyChainAxiom):
            steps = list(Collection(vocabulary, chain)) if isinstance(chain, BNode) else []
            if len(steps) == 2:
                self.chains.append((derived, steps[0], steps[1]))

    @staticmethod
    @lru_cache(maxsize=1)
    def bundled() -> "Alignment":
        """The axioms of the bundled TSS vocabulary."""
        from .vocab import vocabulary_graph

        return Alignment(vocabulary_graph())


def _closure(direct: dict) -> dict:
    """Every super term of each term, following the hierarchy (the term itself excluded)."""
    result = {}
    for term in direct:
        seen, todo = set(), list(direct[term])
        while todo:
            current = todo.pop()
            if current in seen or current == term:
                continue
            seen.add(current)
            todo.extend(direct.get(current, ()))
        result[term] = seen
    return result


def derive(graph: Graph, *, context: Graph | None = None, vocabulary: Graph | None = None) -> Graph:
    """The triples that the alignment of the TSS vocabulary derives from ``graph``, and that
    ``graph`` (and ``context``) do not hold yet, as a new graph.

    - ``context``: statements to use, but derive nothing about, such as the metadata document
      while ``graph`` holds a batch of points.
    - ``vocabulary``: the axioms to apply; the bundled TSS vocabulary by default.
    """
    alignment = Alignment(vocabulary) if vocabulary is not None else Alignment.bundled()
    derived = Graph()
    for prefix, namespace in graph.namespaces():
        derived.bind(prefix, namespace, override=False)

    def known(triple) -> bool:
        return triple in graph or triple in derived or (context is not None and triple in context)

    def objects(subject, prop):
        yield from graph.objects(subject, prop)
        yield from derived.objects(subject, prop)
        if context is not None:
            yield from context.objects(subject, prop)

    subjects = set(graph.subjects())
    pending = list(graph)
    while pending:
        new = []
        for subject, prop, value in pending:
            if subject not in subjects:
                continue
            candidates = [(subject, sup, value) for sup in alignment.super_properties.get(prop, ())]
            for cls in alignment.domains.get(prop, ()):
                candidates.append((subject, RDF.type, cls))
            if not isinstance(value, Literal):
                for cls in alignment.ranges.get(prop, ()):
                    if value in subjects:
                        candidates.append((value, RDF.type, cls))
            if prop == RDF.type:
                candidates.extend((subject, RDF.type, sup) for sup in alignment.super_classes.get(value, ()))
            for result, first, second in alignment.chains:
                if prop == first:
                    candidates.extend((subject, result, end) for end in objects(value, second))
            for triple in candidates:
                if not known(triple):
                    derived.add(triple)
                    new.append(triple)
        pending = new
    return derived
