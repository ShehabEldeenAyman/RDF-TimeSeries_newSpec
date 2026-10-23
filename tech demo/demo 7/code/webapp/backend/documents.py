"""RDF documents: which metadata endpoints there are, where their documents are, and reading them
with rdflib, with the same base IRI as the browser (load_graph, load_source).
Used by every *_routes.py module: each route starts by reading its endpoint's metadata here."""
from fastapi import HTTPException
from fastapi.responses import Response
from rdflib import Graph
from urllib.parse import quote, urljoin

import paths
import files
import online_registry
import source_reading
from rdf_generation import exact_turtle


# The RDF syntaxes the backend reads, by media type (the Content-Type a server sends),
# with the name of the rdflib parser for each
RDF_FORMATS = {
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "text/n3": "n3",
    "application/n-triples": "nt",
    "application/rdf+xml": "xml",
}
# Not JSON-LD, although it is an RDF syntax too. A JSON-LD document can name its "@context" by a
# web address, or by a file on this computer (file:///...), and rdflib's JSON-LD parser downloads
# or reads it while it parses. So an online document or an uploaded shapes file could make the
# backend fetch any address, or read a local file. (The embedded points are read as JSON-LD, but
# with a context made here from the fields, and a point can't bring its own: see lift_points.)
JSON_LD = "application/ld+json"
JSON_LD_REFUSED = ("JSON-LD is not read here: its @context can make the parser download other addresses "
                   "or read files on this computer. Use Turtle, N-Triples, N3 or RDF/XML instead.")
# Many simple servers (raw files on GitHub, ...) send Turtle without saying so, as one of these.
# They are read as Turtle, like the browser does (frontend/src/rdf.js).
UNTYPED_MEDIA_TYPES = ["", "text/plain", "application/octet-stream"]
# What we ask an online server for: Turtle first, but the other RDF syntaxes we read are fine too
RDF_ACCEPT = "text/turtle, application/rdf+xml;q=0.8, application/n-triples;q=0.8, text/n3;q=0.5, */*;q=0.1"


# ---------------------------------------------------------------------------
# RDF documents: read and parsed with rdflib
# ---------------------------------------------------------------------------

def bare_media_type(content_type):
    """'text/turtle; charset=utf-8' -> 'text/turtle' (the part after ";" are parameters).
    "" when there is no Content-Type."""
    if content_type is None:
        return ""
    return content_type.split(";")[0].strip().lower()


def rdf_media_type(content_type):
    """The RDF syntax of a document, from the Content-Type its server sent:
    'text/turtle; charset=utf-8' -> 'text/turtle'. No type, or a vague one, is Turtle.
    None when it is not an RDF syntax we read (e.g. an HTML page, or JSON-LD: see RDF_FORMATS)."""
    media_type = bare_media_type(content_type)
    if media_type in UNTYPED_MEDIA_TYPES:
        return "text/turtle"
    if media_type in RDF_FORMATS:
        return media_type
    return None


def fetch_document(url):
    """Download an online RDF document. Gives (its text, its media type)."""
    response = files.download(url, accept=RDF_ACCEPT)
    content_type = response.headers.get("Content-Type")
    if bare_media_type(content_type) == JSON_LD:
        raise HTTPException(status_code=502, detail=f"{url} sent JSON-LD. {JSON_LD_REFUSED}")
    media_type = rdf_media_type(content_type)
    if media_type is None:
        raise HTTPException(status_code=502, detail=f"{url} sent {content_type}, which is not an RDF syntax "
                                                    "this dashboard reads (Turtle, RDF/XML, N-Triples or N3)")
    return response.text, media_type


def read_document(location):
    """An endpoint's RDF document (see endpoint_file): (its text, its media type)."""
    if files.is_url(location):
        return fetch_document(location)
    path = paths.DEMO_DIR / location
    # read_bytes + decode keeps the text exactly as it is in the file
    # (read_text would change the line endings)
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=500, detail=f"Could not read {location}: {error}")
    return text, "text/turtle"


def parse_document(text, media_type, base, name):
    """Parse an RDF document into an rdflib graph, with the parser for its media type.
    base: the document's IRI (its base IRI, see document_iri). name: how error messages call the document"""
    graph = Graph()
    try:
        graph.parse(data=text, format=RDF_FORMATS[media_type], publicID=base)
    except Exception as error:
        # For example a typo in the Turtle
        raise HTTPException(status_code=500, detail=f"Could not read {name}: {error}")
    return graph


def turtle_response(graph):
    """An answer with an RDF graph, as Turtle written by rdflib. Numbers and booleans are written
    in full ("12"^^xsd:double, not 1.2e+01; see exact_turtle), so a SHACL report shows the wrong
    values of embedded points (like " 12 " or "yes") with their text exactly as it is."""
    return Response(exact_turtle(graph), media_type="text/turtle; charset=utf-8")


# ---------------------------------------------------------------------------
# Metadata endpoints: the local .ttl files, and the online ones (online_registry.py)
# ---------------------------------------------------------------------------

def local_endpoint_ids():
    """The local metadata endpoints are the .ttl files in the 'metadata endpoint' folder.
    The id of an endpoint is its file name without .ttl"""
    ids = []
    for ttl_file in sorted(paths.METADATA_DIR.glob("*.ttl")):
        ids.append(ttl_file.stem)
    return ids


def endpoint_file(endpoint_id):
    """Where an endpoint's RDF document is: a web address, or a path inside demo 7"""
    online = online_registry.load_online_endpoints()
    if endpoint_id in online:
        return online[endpoint_id]
    # Only accept ids we know, so nobody can ask for some other file on disk
    if endpoint_id in local_endpoint_ids():
        return f"metadata endpoint/{endpoint_id}.ttl"
    raise HTTPException(status_code=404, detail=f"Unknown metadata endpoint: {endpoint_id}")


# ---------------------------------------------------------------------------
# Base IRIs: the backend reads every document with the same base IRI as the browser
# ---------------------------------------------------------------------------
# Relative IRIs in a document, like <../data/x.csv> or <#value>, are read against its base IRI.
# The browser reads each document with the IRI the catalogue gives it (frontend/src/App.jsx),
# and the backend reads it with that same IRI. So a field <#value> is the same IRI in the
# browser's store and here, and the browser can ask /query?field=<that IRI>.

def browser_base(request):
    """The address of this backend as the browser sees it, e.g. http://localhost:5174/ (Vite passes
    requests on without changing their Host). Without a request (a route called from a test or a
    script): paths.DEFAULT_BASE."""
    if request is None:
        return paths.DEFAULT_BASE
    return str(request.base_url)


def local_document_address(endpoint_id, base):
    """The address of a local endpoint's file on this backend, e.g.
    http://localhost:5174/files/metadata%20endpoint/waterinfo_289423042_tss2.ttl
    (quote writes the space in "metadata endpoint" as %20). The catalogue gives it as the
    endpoint's IRI, so it is the document's base IRI too."""
    return urljoin(base, quote(f"{paths.METADATA_FILES_PATH.lstrip('/')}/{endpoint_id}.ttl"))


def document_iri(endpoint_id, location, base):
    """The IRI an endpoint's document is read with (its base IRI), the same IRI as the browser uses:
    - an online endpoint: the address it was registered with (location), also when its server
      redirects to another address;
    - a local endpoint: its address on this backend (see local_document_address).
    base: the address of this backend as the browser sees it (see browser_base)"""
    if files.is_url(location):
        return location
    return local_document_address(endpoint_id, base)


def load_graph(endpoint_id, base=paths.DEFAULT_BASE):
    """An endpoint's metadata as an RDF graph, read with the base IRI the browser uses
    (see document_iri). Every route parses its endpoint once, with this function, and
    passes the graph on to the functions it uses.
    base: the address of this backend as the browser sees it (see browser_base)"""
    location = endpoint_file(endpoint_id)
    text, media_type = read_document(location)
    return parse_document(text, media_type, document_iri(endpoint_id, location, base), location)


def local_data_address(endpoint_id, base):
    """Where demo 7's data folder is for this endpoint's document: {base}files/data/ for a local
    endpoint (its <../data/x.csv> resolves there, see paths.DATA_FILES_PATH). None for an online
    endpoint: its data files can only be web addresses, never files on this computer."""
    if endpoint_id in online_registry.load_online_endpoints():
        return None
    return urljoin(base, paths.DATA_FILES_PATH.lstrip("/"))


def load_source(endpoint_id, request=None):
    """What the data routes start with: the endpoint's metadata as an RDF graph (see load_graph),
    and its tss:Source and fields (see source_reading.read_source), both read as the browser reads them.
    request: the route's request, for the address the browser uses (see browser_base)"""
    base = browser_base(request)
    graph = load_graph(endpoint_id, base)
    source = source_reading.read_source(graph, local_data_address(endpoint_id, base))
    return graph, source
