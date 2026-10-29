"""RDF documents: which metadata endpoints there are (the bundled .ttl files in "metadata endpoint/"),
and reading them with rdflib, with the same base IRI as the browser (load_graph, load_source).
Used by every *_routes.py module: each route starts by reading its endpoint's metadata here.
(A user endpoint, an uploaded or online metadata file, is read by the browser only: this backend
never sees it.)"""
from fastapi import HTTPException
from fastapi.responses import Response
from rdflib import Graph
from pathlib import PurePosixPath
from urllib.parse import quote, urljoin

import paths
import source_reading
from rdf_generation import exact_turtle


# The RDF syntaxes the backend reads in an uploaded shapes file (the SHACL tab), by media type
# (the Content-Type the browser sends), with the name of the rdflib parser for each
RDF_FORMATS = {
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "text/n3": "n3",
    "application/n-triples": "nt",
    "application/rdf+xml": "xml",
}
# Not JSON-LD, although it is an RDF syntax too. A JSON-LD document can name its "@context" by a
# web address, or by a file on this computer (file:///...), and rdflib's JSON-LD parser downloads
# or reads it while it parses. So an uploaded shapes file could make the backend fetch any
# address, or read a local file. (The embedded points are read as JSON-LD, but with a context
# made here from the fields, and a point can't bring its own: see lift_points.)
JSON_LD = "application/ld+json"
JSON_LD_REFUSED = ("JSON-LD is not read here: its @context can make the parser download other addresses "
                   "or read files on this computer. Use Turtle, N-Triples, N3 or RDF/XML instead.")
# A shapes file often comes without a type (the browser gives a .ttl file the type ""), or with
# one of these vague types. It is read as Turtle then, like the browser does (frontend/src/rdf.js).
UNTYPED_MEDIA_TYPES = ["", "text/plain", "application/octet-stream"]


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
    """The RDF syntax of a document, from the Content-Type it was sent with:
    'text/turtle; charset=utf-8' -> 'text/turtle'. No type, or a vague one, is Turtle.
    None when it is not an RDF syntax we read (e.g. an HTML page, or JSON-LD: see RDF_FORMATS)."""
    media_type = bare_media_type(content_type)
    if media_type in UNTYPED_MEDIA_TYPES:
        return "text/turtle"
    if media_type in RDF_FORMATS:
        return media_type
    return None


def document_path(location):
    """Where a bundled document is on disk: location is "metadata endpoint/<id>.ttl" (see
    endpoint_file), and the file is <id>.ttl in paths.METADATA_DIR. Every reader goes through
    paths.METADATA_DIR (not DEMO_DIR / location), and so does the "Add data" tab, which writes
    the file (points_routes.py): a test or a script that points METADATA_DIR at a copy of the
    folder reads and writes that copy only."""
    return paths.METADATA_DIR / PurePosixPath(location).name


def read_document(location):
    """An endpoint's RDF document, a Turtle file in "metadata endpoint/" (see endpoint_file and
    document_path): (its text, its media type)."""
    path = document_path(location)
    # read_bytes + decode keeps the text exactly as it is in the file
    # (read_text would change the line endings)
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=500, detail=f"Could not read {location}: {error}")
    return text, "text/turtle"


def parse_document(text, media_type, base, name):
    """Parse an RDF document into an rdflib graph, with the parser for its media type.
    base: the document's IRI (its base IRI, see local_document_address). name: how error messages call the document"""
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
# Metadata endpoints: the bundled .ttl files
# ---------------------------------------------------------------------------

def local_endpoint_ids():
    """The local metadata endpoints are the .ttl files in the 'metadata endpoint' folder.
    The id of an endpoint is its file name without .ttl"""
    ids = []
    for ttl_file in sorted(paths.METADATA_DIR.glob("*.ttl")):
        ids.append(ttl_file.stem)
    return ids


def endpoint_file(endpoint_id):
    """Where an endpoint's RDF document is: a path inside demo 7, e.g. "metadata endpoint/x.ttl".
    An id that is not one of the bundled files is unknown (404)."""
    # Only accept ids we know, so nobody can ask for some other file on disk
    if endpoint_id in local_endpoint_ids():
        return f"metadata endpoint/{endpoint_id}.ttl"
    raise HTTPException(status_code=404, detail=f"Unknown metadata endpoint: {endpoint_id}")


# ---------------------------------------------------------------------------
# Base IRIs: the backend reads every document with the same base IRI as the browser
# ---------------------------------------------------------------------------
# Relative IRIs in a document, like <../data/x.csv> or <#value>, are read against its base IRI.
# The browser reads each document with the IRI the catalogue gives it (frontend/src/App.jsx): its
# address on this backend (see local_document_address). The backend reads it with that same IRI.
# So a field <#value> is the same IRI in the browser's store and here, and the browser can ask
# /query?field=<that IRI>.

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
    endpoint's IRI, so it is the document's base IRI too, in the browser and here."""
    return urljoin(base, quote(f"{paths.METADATA_FILES_PATH.lstrip('/')}/{endpoint_id}.ttl"))


def load_graph(endpoint_id, base=paths.DEFAULT_BASE):
    """An endpoint's metadata as an RDF graph, read with the base IRI the browser uses
    (see local_document_address). Every route parses its endpoint once, with this function,
    and passes the graph on to the functions it uses.
    base: the address of this backend as the browser sees it (see browser_base)"""
    location = endpoint_file(endpoint_id)
    text, media_type = read_document(location)
    return parse_document(text, media_type, local_document_address(endpoint_id, base), location)


def local_data_address(base):
    """Where demo 7's data folder is for the documents: {base}files/data/ (a document's
    <../data/x.csv> resolves there, see paths.DATA_FILES_PATH).
    base: the address of this backend as the browser sees it (see browser_base)"""
    return urljoin(base, paths.DATA_FILES_PATH.lstrip("/"))


def load_source(endpoint_id, request=None):
    """What the data routes start with: the endpoint's metadata as an RDF graph (see load_graph),
    and its tss:Source and fields (see source_reading.read_source), both read as the browser reads them.
    request: the route's request, for the address the browser uses (see browser_base)"""
    base = browser_base(request)
    graph = load_graph(endpoint_id, base)
    source = source_reading.read_source(graph, local_data_address(base))
    return graph, source
