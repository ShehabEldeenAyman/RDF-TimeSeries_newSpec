"""The vocabularies rdftss uses, and the version of the specification it implements.

Every term is an :class:`rdflib.URIRef`, so it does not matter which prefixes a document
uses: ``tss:CSVSnippet``, ``t:CSVSnippet`` and ``<https://w3id.org/tss#CSVSnippet>`` are
the same term.

>>> from rdftss.vocab import TSS
>>> TSS.CSVSnippet
rdflib.term.URIRef('https://w3id.org/tss#CSVSnippet')

``from`` is a Python keyword, so ``tss:from`` is written ``TSS["from"]`` (or :data:`TSS_FROM`).
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import DCAT, DCTERMS, OWL, PROV, RDF, RDFS, SKOS

#: The version of the RDF Time Series Snippets vocabulary that rdftss implements
#: (``owl:versionInfo`` of the bundled ``tss.ttl``, demo 11).
SPEC_VERSION = "0.2.0"

#: Where the specification is published.
SPEC_URL = "https://knowledgeonwebscale.github.io/rdf-timeseriessnippets/"

TSS = Namespace("https://w3id.org/tss#")
#: XML Schema datatypes. An open namespace, so terms such as ``XSD.dateTimeStamp`` always work.
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
SOSA = Namespace("http://www.w3.org/ns/sosa/")
QUDT = Namespace("http://qudt.org/schema/qudt/")
UNIT = Namespace("http://qudt.org/vocab/unit/")
QUANTITYKIND = Namespace("http://qudt.org/vocab/quantitykind/")
CSVW = Namespace("http://www.w3.org/ns/csvw#")

#: ``tss:from`` (``from`` is a Python keyword).
TSS_FROM = TSS["from"]

#: The prefixes rdftss knows without a document, for short names such as ``"unit:DEG_C"``.
WELL_KNOWN_PREFIXES: dict[str, Namespace] = {
    "tss": TSS,
    "xsd": XSD,
    "rdf": Namespace(str(RDF)),
    "rdfs": Namespace(str(RDFS)),
    "owl": Namespace(str(OWL)),
    "sosa": SOSA,
    "qudt": QUDT,
    "unit": UNIT,
    "quantitykind": QUANTITYKIND,
    "dcterms": Namespace(str(DCTERMS)),
    "dcat": Namespace(str(DCAT)),
    "prov": Namespace(str(PROV)),
    "skos": Namespace(str(SKOS)),
    "csvw": CSVW,
}


def vocabulary_path() -> Path:
    """The path of the bundled ``tss.ttl``: the vocabulary of demo 11, version :data:`SPEC_VERSION`."""
    return Path(str(resources.files("rdftss") / "vocabulary" / "tss.ttl"))


def vocabulary_graph() -> Graph:
    """The bundled TSS vocabulary (the ontology) as an rdflib graph. Works offline."""
    graph = Graph()
    graph.parse(vocabulary_path(), format="turtle")
    return graph
