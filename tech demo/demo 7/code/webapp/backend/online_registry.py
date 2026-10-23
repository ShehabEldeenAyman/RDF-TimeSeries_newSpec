"""The online metadata endpoints the user added (online-1, online-2, ...), kept as RDF in the file
online_endpoints.ttl (paths.ONLINE_ENDPOINTS_FILE), read and written with rdflib.
Used by documents.py (which endpoints there are) and catalog_routes.py (the catalogue, adding and removing)."""
from fastapi import HTTPException
from rdflib import BNode, Graph, Literal, URIRef, RDF, RDFS
from rdflib.namespace import DCAT, DCTERMS
import math

import paths


# The catalogue says each document's media type as an IRI of the IANA list, e.g.
# https://www.iana.org/assignments/media-types/text/turtle
IANA_MEDIA_TYPES = "https://www.iana.org/assignments/media-types/"


def online_number(endpoint_id):
    """'online-12' -> 12, to keep online-2 before online-10"""
    number = endpoint_id.split("-")[-1]
    if number.isdigit():
        return int(number)
    return math.inf


def read_registry():
    """The online endpoints the user added, as an RDF graph: the file online_endpoints.ttl
    (an empty graph before the first one is added). They are kept as RDF in a file, so they
    are still there after a restart. Each one is a dcat:Dataset whose IRI is its web address,
    with its id, and the label and media type its document had when it was added:
        <https://.../x_tss2.ttl> a dcat:Dataset ;
            dcterms:identifier "online-1" ;
            rdfs:label "Waterinfo EC20, Kanaal Gent-Terneuzen Grootdok (BE)"@en ;
            dcat:distribution [ a dcat:Distribution ;
                dcat:mediaType <https://www.iana.org/assignments/media-types/text/turtle> ] .
    So the catalogue does not have to download every online document each time it is asked for."""
    registry = Graph()
    registry.bind("dcat", DCAT)
    registry.bind("dcterms", DCTERMS)
    registry.bind("rdfs", RDFS)
    if paths.ONLINE_ENDPOINTS_FILE.exists():
        try:
            registry.parse(paths.ONLINE_ENDPOINTS_FILE, format="turtle")
        except Exception as error:
            raise HTTPException(status_code=500, detail=f"Could not read {paths.ONLINE_ENDPOINTS_FILE.name}: {error}")
    return registry


def online_endpoints(registry):
    """The online endpoints of the registry (see read_registry), in order:
    {"online-1": "https://.../x_tss2.ttl", ...}"""
    found = []
    for dataset in registry.subjects(RDF.type, DCAT.Dataset):
        identifier = registry.value(dataset, DCTERMS.identifier)
        if isinstance(dataset, URIRef) and identifier is not None:
            found.append((online_number(str(identifier)), str(identifier), str(dataset)))
    found.sort()

    online = {}
    for number, endpoint_id, url in found:
        online[endpoint_id] = url
    return online


def load_online_endpoints():
    """The online endpoints the user added, in order: {"online-1": "https://.../x_tss2.ttl", ...}"""
    return online_endpoints(read_registry())


def register_online_endpoint(registry, endpoint_id, url, labels, media_type):
    """Add one online endpoint to the registry graph: its id, the labels of its tss:Source
    (rdflib literals, with their language tags) and the media type of its document"""
    dataset = URIRef(url)
    registry.add((dataset, RDF.type, DCAT.Dataset))
    registry.add((dataset, DCTERMS.identifier, Literal(endpoint_id)))
    for label in labels:
        registry.add((dataset, RDFS.label, label))
    distribution = BNode()
    registry.add((dataset, DCAT.distribution, distribution))
    registry.add((distribution, RDF.type, DCAT.Distribution))
    registry.add((distribution, DCAT.mediaType, URIRef(IANA_MEDIA_TYPES + media_type)))


def unregister_online_endpoint(registry, url):
    """Take one online endpoint out of the registry graph, with its distribution"""
    dataset = URIRef(url)
    for distribution in list(registry.objects(dataset, DCAT.distribution)):
        registry.remove((distribution, None, None))
    registry.remove((dataset, None, None))


def save_registry(registry):
    """Write the registry graph to online_endpoints.ttl (as Turtle, written by rdflib)"""
    paths.ONLINE_ENDPOINTS_FILE.write_text(registry.serialize(format="turtle"), encoding="utf-8")
