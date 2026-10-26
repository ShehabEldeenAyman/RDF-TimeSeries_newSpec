"""Routes for the catalogue: the list of the bundled metadata endpoints (the .ttl files in
"metadata endpoint/") as RDF (DCAT). main.py adds this router to the app.
(The user's own endpoints, uploaded or online, are not in it: the browser keeps those itself.)"""
from fastapi import APIRouter, HTTPException, Request
from rdflib import BNode, Graph, Literal, URIRef, RDF, RDFS
from rdflib.namespace import DCAT, DCTERMS
from urllib.parse import urljoin

import source_reading
import documents

# The routes of this file, all under /api (main.py adds them to the app with include_router)
router = APIRouter(prefix="/api")

# The catalogue says each document's media type as an IRI of the IANA list. The bundled
# documents are Turtle: https://www.iana.org/assignments/media-types/text/turtle
TURTLE = URIRef("https://www.iana.org/assignments/media-types/text/turtle")


def local_labels(endpoint_id, address):
    """The rdfs:label literals of a local endpoint's tss:Source, read from its file: (labels, None).
    When the file can't be read: ([], the reason). address: its IRI (see documents.local_document_address)."""
    location = f"metadata endpoint/{endpoint_id}.ttl"
    try:
        text, media_type = documents.read_document(location)
        document = documents.parse_document(text, media_type, str(address), location)
        source = source_reading.find_source(document)
    except HTTPException as error:
        return [], error.detail
    labels = []
    for label in document.objects(source, RDFS.label):
        labels.append(label)
    return labels, None


def add_dataset(graph, catalog, endpoint_id, address, labels, problem):
    """Add one endpoint to the catalogue: a dcat:Dataset with its id, its labels (the rdfs:label
    literals of its tss:Source) and a dcat:Distribution that says where to download it, and in
    which syntax (Turtle). A bundled endpoint is a file this backend serves, so its IRI (address)
    is also where to download it. An endpoint that can't be read (problem: why) gets an
    rdfs:comment that says why, instead of a label, so one broken endpoint does not break the
    catalogue."""
    graph.add((catalog, DCAT.dataset, address))
    graph.add((address, RDF.type, DCAT.Dataset))
    graph.add((address, DCTERMS.identifier, Literal(endpoint_id)))
    for label in labels:
        graph.add((address, RDFS.label, label))  # the same literal, so with the same language tag
    if problem is not None:
        graph.add((address, RDFS.comment, Literal(f"Can't be read: {problem}")))

    distribution = BNode()
    graph.add((address, DCAT.distribution, distribution))
    graph.add((distribution, RDF.type, DCAT.Distribution))
    graph.add((distribution, DCAT.downloadURL, address))
    graph.add((distribution, DCAT.mediaType, TURTLE))


def catalog_graph(base):
    """The catalogue of the metadata endpoints as an RDF graph, with the DCAT vocabulary:
        <{base}api/catalog> a dcat:Catalog ; dcat:dataset <one dataset per endpoint> .
    base: the address of this backend as the browser sees it, e.g. http://localhost:5174/"""
    graph = Graph()
    graph.bind("dcat", DCAT)
    graph.bind("dcterms", DCTERMS)
    graph.bind("rdfs", RDFS)
    catalog = URIRef(urljoin(base, "api/catalog"))
    graph.add((catalog, RDF.type, DCAT.Catalog))

    for endpoint_id in documents.local_endpoint_ids():
        address = URIRef(documents.local_document_address(endpoint_id, base))
        labels, problem = local_labels(endpoint_id, address)
        add_dataset(graph, catalog, endpoint_id, address, labels, problem)
    return graph


@router.get("/catalog")
def get_catalog(request: Request):
    """The catalogue of the bundled metadata endpoints (Turtle, DCAT)"""
    return documents.turtle_response(catalog_graph(str(request.base_url)))
