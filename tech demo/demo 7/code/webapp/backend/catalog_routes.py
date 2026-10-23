"""Routes for the catalogue: the list of metadata endpoints as RDF (DCAT), adding and removing online
endpoints, and an endpoint's RDF document as it is. main.py adds this router to the app."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from rdflib import BNode, Graph, Literal, URIRef, RDF, RDFS
from rdflib.namespace import DCAT, DCTERMS
from urllib.parse import quote, urljoin
import re

import files
import online_registry
import source_reading
import documents

# The routes of this file, all under /api (main.py adds them to the app with include_router)
router = APIRouter(prefix="/api")


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


def add_dataset(graph, catalog, endpoint_id, dataset, download_url, labels, media_type, problem):
    """Add one endpoint to the catalogue: a dcat:Dataset with its id, its labels (the rdfs:label
    literals of its tss:Source) and a dcat:Distribution that says where to download it, and in
    which syntax (media_type: an IANA IRI). An endpoint that can't be read (problem: why) gets
    an rdfs:comment that says why, instead of a label, so one broken endpoint does not break
    the catalogue."""
    graph.add((catalog, DCAT.dataset, dataset))
    graph.add((dataset, RDF.type, DCAT.Dataset))
    graph.add((dataset, DCTERMS.identifier, Literal(endpoint_id)))
    for label in labels:
        graph.add((dataset, RDFS.label, label))  # the same literal, so with the same language tag
    if problem is not None:
        graph.add((dataset, RDFS.comment, Literal(f"Can't be read: {problem}")))

    distribution = BNode()
    graph.add((dataset, DCAT.distribution, distribution))
    graph.add((distribution, RDF.type, DCAT.Distribution))
    graph.add((distribution, DCAT.downloadURL, download_url))
    graph.add((distribution, DCAT.mediaType, media_type))


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

    # A local endpoint is a file this backend serves: its IRI is also where to download it
    for endpoint_id in documents.local_endpoint_ids():
        address = URIRef(documents.local_document_address(endpoint_id, base))
        labels, problem = local_labels(endpoint_id, address)
        turtle = URIRef(online_registry.IANA_MEDIA_TYPES + "text/turtle")  # the local files are Turtle
        add_dataset(graph, catalog, endpoint_id, address, address, labels, turtle, problem)

    # An online endpoint keeps its own IRI (its web address), but the browser downloads it
    # through this backend, so the other server does not have to allow that (CORS).
    # Its label and media type come from the registry (noted when it was added), so the
    # catalogue never waits for another server.
    registry = online_registry.read_registry()
    for endpoint_id, url in online_registry.online_endpoints(registry).items():
        dataset = URIRef(url)
        document = URIRef(urljoin(base, f"api/endpoints/{quote(endpoint_id)}/document"))
        labels = list(registry.objects(dataset, RDFS.label))
        if len(labels) == 0:
            labels = [Literal(url)]  # no label was noted: its address is the best name it has
        media_type = None
        for distribution in registry.objects(dataset, DCAT.distribution):
            media_type = registry.value(distribution, DCAT.mediaType)
        if media_type is None:
            media_type = URIRef(online_registry.IANA_MEDIA_TYPES + "text/turtle")  # what a document without a type is read as
        add_dataset(graph, catalog, endpoint_id, dataset, document, labels, media_type, None)
    return graph


def catalog_response(request):
    """The answer with the catalogue (Turtle)"""
    return documents.turtle_response(catalog_graph(str(request.base_url)))


@router.get("/catalog")
def get_catalog(request: Request):
    """The catalogue of metadata endpoints (Turtle, DCAT): the local ones, then the online ones"""
    return catalog_response(request)


@router.post("/endpoints/online")
def add_online_endpoint(url, request: Request):
    """Add an online TSS metadata endpoint: the web address of an RDF document (e.g. a Turtle file)
    with one tss:Source. From then on it works like the local ones.
    Answers with the updated catalogue (Turtle)."""
    url = url.strip()
    if not files.is_url(url):
        raise HTTPException(status_code=400, detail="The address must start with http:// or https://")
    # It becomes an IRI in the catalogue, and an IRI can't hold these
    if re.search(r'[\s<>"{}|\\^`]', url):
        raise HTTPException(status_code=400, detail='The address can\'t contain spaces or any of < > " { } | \\ ^ `')

    registry = online_registry.read_registry()
    online = online_registry.online_endpoints(registry)
    for endpoint_id, known_url in online.items():
        if known_url == url:
            raise HTTPException(status_code=400, detail=f"This endpoint is already in the list ({endpoint_id})")
    # A local endpoint's own address (as the browser sees it) is in the list already, with that
    # same IRI: as an online endpoint too, the catalogue would have one dataset with two ids
    for endpoint_id in documents.local_endpoint_ids():
        if documents.local_document_address(endpoint_id, documents.browser_base(request)) == url:
            raise HTTPException(status_code=400, detail=f"This is a local endpoint ({endpoint_id}): "
                                                        "it is already in the list.")

    # Check that it really is a TSS metadata endpoint before it goes in the list.
    # It is read with its own address as base IRI, as it will be from now on (see documents.document_iri).
    try:
        text, media_type = documents.fetch_document(url)
        document = documents.parse_document(text, media_type, url, url)
        source = source_reading.find_source(document)
    except HTTPException as error:
        raise HTTPException(status_code=400, detail=f"Can't add {url}: {error.detail}")
    # Its labels (with their language tags) and its media type are noted in the registry
    # now, so the catalogue does not have to download it again
    labels = []
    for label in document.objects(source, RDFS.label):
        labels.append(label)

    # Give it the first free id: online-1, online-2, ...
    number = 1
    while f"online-{number}" in online:
        number += 1
    online_registry.register_online_endpoint(registry, f"online-{number}", url, labels, media_type)
    online_registry.save_registry(registry)
    return catalog_response(request)


@router.delete("/endpoints/{endpoint_id}")
def remove_online_endpoint(endpoint_id, request: Request):
    """Take an online endpoint out of the list (the local .ttl files can't be removed here).
    Answers with the updated catalogue (Turtle)."""
    registry = online_registry.read_registry()
    online = online_registry.online_endpoints(registry)
    if endpoint_id not in online:
        raise HTTPException(status_code=404, detail=f"{endpoint_id} is not an online endpoint")
    online_registry.unregister_online_endpoint(registry, online[endpoint_id])
    online_registry.save_registry(registry)
    return catalog_response(request)


@router.get("/endpoints/{endpoint_id}/document")
def get_document(endpoint_id):
    """An endpoint's RDF document, exactly as it is: the local file, or the online document,
    downloaded here so the browser does not need the other server to allow it (CORS)"""
    location = documents.endpoint_file(endpoint_id)
    text, media_type = documents.read_document(location)
    headers = {}
    if files.is_url(location):
        # The online document's own IRI: the address it was registered with (also when its
        # server redirects elsewhere). Relative IRIs in it are read against this address,
        # by the browser (the catalogue's dataset IRI) and by the backend (see documents.document_iri).
        headers["Content-Location"] = location
    return Response(text, media_type=media_type, headers=headers)
