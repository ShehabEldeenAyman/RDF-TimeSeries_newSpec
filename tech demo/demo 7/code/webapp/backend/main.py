from fastapi import FastAPI, HTTPException, APIRouter, Depends, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from rdflib import BNode, Graph, URIRef, Literal, Namespace, RDF, RDFS
from rdflib.namespace import DCAT, DCTERMS, SH, XSD
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urljoin, urlparse, unquote
from urllib.request import url2pathname
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import BaseModel
import requests
import tempfile
import csv
import io
import logging
import mimetypes
import re
import duckdb
import math
from uuid import uuid4
from pyshacl import validate as shacl_validate

# The TSS vocabulary: tss:Source, tss:Field, tss:hasField, ...
TSS = Namespace("https://w3id.org/tss#")

# The SQL box (DuckDB) lives in sql_box.py
import sql_box
# The RDF converter (code/TSS2RDF/tss2rdf.py): generate() for the RDF generation tab, and
# lift_points(), which reads the points embedded in a source (tss:points) as RDF
import rdf_generation
# From the converter too (importing rdf_generation above made the code folder importable):
# LEXICAL_FORMS, how the text of each XSD datatype looks (xsd:double: "4797.72", "3.0E1", "INF"),
# TIME_DATATYPES, the datatypes a time may have (xsd:dateTime, xsd:dateTimeStamp),
# exact_turtle, which writes a graph as Turtle with every literal's text exactly as it is,
# and NORMALIZE_LOCK, which pyshacl's validate() must hold (see validation_report)
from TSS2RDF.tss2rdf import LEXICAL_FORMS, NORMALIZE_LOCK, TIME_DATATYPES, exact_turtle

# rdflib logs a long traceback for every value that does not fit its datatype; bad values are reported by SHACL instead
logging.getLogger("rdflib.term").setLevel(logging.ERROR)


# This file lives in demo 7/code/webapp/backend/, so demo 7/ is three folders up
DEMO_DIR = Path(__file__).resolve().parents[3]
METADATA_DIR = DEMO_DIR / "metadata endpoint"
SHACL_DIR = DEMO_DIR / "SHACL"

# The RDF documents are also served as plain files (see the end of this file), at these addresses:
#   /files/metadata%20endpoint/<id>.ttl   the local metadata endpoints
#   /files/SHACL/<name>.ttl               the SHACL shapes
# (%20 is how a space is written in a web address)
METADATA_FILES_PATH = "/files/metadata endpoint"
SHACL_FILES_PATH = "/files/SHACL"
# The data folder is NOT served (some of its data may not be shared). But a local metadata
# endpoint's <../data/x.csv> is read against its address, so it becomes
# /files/data/x.csv, in the browser and here. iri_to_location maps such an IRI back to
# the file demo 7/data/x.csv.
DATA_FILES_PATH = "/files/data/"

# The address of this backend as the browser sees it, for code that has no request to ask
# (a route called from a test or a script): the Vite dev server, which passes requests on
# without changing their Host. See browser_base.
DEFAULT_BASE = "http://localhost:5174/"

# Python does not know the .ttl extension, so without this the files above would be
# sent as text/plain instead of text/turtle
mimetypes.add_type("text/turtle", ".ttl")

# The online endpoints the user adds are remembered in this file, as RDF (see read_registry):
#   <https://.../x_tss2.ttl> a dcat:Dataset ; dcterms:identifier "online-1" ; rdfs:label "..."@en ; ...
ONLINE_ENDPOINTS_FILE = Path(__file__).parent / "online_endpoints.ttl"

# The catalogue says each document's media type as an IRI of the IANA list, e.g.
# https://www.iana.org/assignments/media-types/text/turtle
IANA_MEDIA_TYPES = "https://www.iana.org/assignments/media-types/"

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

# The generated RDF (the RDF generation tab) is written to temporary files: the whole
# graph to download (N-Triples), and a preview with its first observations (Turtle).
# token -> those files.
RDF_EXPORT_DIRECTORY = tempfile.TemporaryDirectory(prefix="rdf-observations-")
RDF_EXPORTS = {}
# The name the downloaded file gets (N-Triples: one triple per line, see tss2rdf.write_batch)
GENERATED_RDF_FILENAME = "observations.nt"

# The default SHACL shapes (in the SHACL folder) for each storage profile: tss:embedded true or false
DEFAULT_SHAPES = {True: "embedded_shapes.ttl", False: "external_shapes.ttl"}
# An uploaded shapes file may be at most this big (5 MB)
MAX_SHAPES_BYTES = 5_000_000


app = FastAPI()


# If a route crashes, answer with the error message as JSON ({"detail": ...}), like the
# errors the routes raise themselves, instead of a bare "500 Internal Server Error"
# that tells the browser nothing.
@app.middleware("http")
async def crash_to_message(request, call_next):
    try:
        return await call_next(request)
    except Exception as error:
        return JSONResponse(status_code=500, content={"detail": f"The backend crashed: {type(error).__name__}: {error}"})


# Start this backend with:  uvicorn main:app --reload --port 8001

# All data routes go on this router, so they all start with /api
api = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Files: local or online
# ---------------------------------------------------------------------------
# A file "location" is either a path inside the demo 7 folder (e.g. "data/x.csv")
# or a web address (e.g. "https://example.org/data/x.csv").
# The functions below work for both, so the routes don't have to care which one it is.
#
# Security: this backend downloads every http(s) address an online endpoint names (its
# document, and its tss:dataFile), also addresses on this computer (localhost) and on the
# private network, and passes what it gets on to the browser. Anyone who can reach port 8001
# can register such an endpoint. So never make this backend reachable from other computers
# (and never run tunnel.py with it). See "Security" in code/webapp/README.md.

def is_url(location):
    return location.startswith("http://") or location.startswith("https://")


def download(url, stream=False, accept=None):
    """GET an online file. A network problem becomes a clear error message.
    stream=True: don't download everything at once (for big CSV files).
    accept: the media types we would like (the Accept header), e.g. RDF_ACCEPT."""
    headers = {}
    if accept is not None:
        headers["Accept"] = accept
    try:
        response = requests.get(url, stream=stream, timeout=30, headers=headers)
        response.raise_for_status()  # turns "404 Not Found" and the like into an error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Could not download {url}: {error}")
    response.encoding = "utf-8"  # our files are UTF-8, even when the server doesn't say so
    return response


def file_exists(location):
    if location is None:
        return False
    if is_url(location):
        try:
            response = requests.get(location, stream=True, timeout=30)  # stream: only look, don't download
            response.close()
            return response.status_code < 400
        except requests.RequestException:
            return False
    return (DEMO_DIR / location).exists()


def read_lines(location):
    """The lines of a file, one at a time, so a big CSV never has to fit in memory.
    (A function with "yield" hands out its values one by one, whenever the loop
    that uses it asks for the next one.)"""
    if is_url(location):
        with download(location, stream=True) as response:
            for line in response.iter_lines(decode_unicode=True):
                yield line
    else:
        with open(DEMO_DIR / location, encoding="utf-8", newline="") as f:
            for line in f:
                yield line.rstrip("\r\n")


def file_name(location):
    """The last part of a location, e.g. waterinfo_289423042.csv"""
    return unquote(urlparse(location).path.split("/")[-1])


# Online files that are needed as a file on this computer (DuckDB reads files,
# not web addresses): web address -> downloaded copy. Kept while the backend
# runs; restart the backend to download fresh copies.
DOWNLOADED = {}


def local_copy(location):
    """A file on this computer with the content of a location"""
    if not is_url(location):
        return DEMO_DIR / location
    if location not in DOWNLOADED or not DOWNLOADED[location].exists():
        # The copy gets the name of the online file (e.g. waterinfo_289429042.parquet), so messages
        # show a name people know. Odd characters become "_": DuckDB reads * ? [ ] as patterns.
        name = re.sub(r"[^A-Za-z0-9._-]", "_", file_name(location))
        if name.strip("._") == "":
            name = "data"
        target = Path(tempfile.mkdtemp(prefix="tss2-")) / name
        with download(location, stream=True) as response:
            with open(target, "wb") as f:
                for piece in response.iter_content(chunk_size=65536):
                    f.write(piece)
        DOWNLOADED[location] = target
    return DOWNLOADED[location]


def send_as_download(location, media_type):
    """Answer with a whole file (local or online) and tell the browser to save it"""
    if not file_exists(location):
        raise HTTPException(status_code=404, detail=f"File not found: {location}")

    if is_url(location):
        # Pass the online file on to the browser piece by piece
        response = download(location, stream=True)
        return StreamingResponse(
            response.iter_content(chunk_size=65536),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{file_name(location)}"'},
        )
    # filename=... tells the browser to save the file instead of showing it
    return FileResponse(DEMO_DIR / location, media_type=media_type, filename=file_name(location))


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
    response = download(url, accept=RDF_ACCEPT)
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
    if is_url(location):
        return fetch_document(location)
    path = DEMO_DIR / location
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
    script): DEFAULT_BASE."""
    if request is None:
        return DEFAULT_BASE
    return str(request.base_url)


def local_document_address(endpoint_id, base):
    """The address of a local endpoint's file on this backend, e.g.
    http://localhost:5174/files/metadata%20endpoint/waterinfo_289423042_tss2.ttl
    (quote writes the space in "metadata endpoint" as %20). The catalogue gives it as the
    endpoint's IRI, so it is the document's base IRI too."""
    return urljoin(base, quote(f"{METADATA_FILES_PATH.lstrip('/')}/{endpoint_id}.ttl"))


def document_iri(endpoint_id, location, base):
    """The IRI an endpoint's document is read with (its base IRI), the same IRI as the browser uses:
    - an online endpoint: the address it was registered with (location), also when its server
      redirects to another address;
    - a local endpoint: its address on this backend (see local_document_address).
    base: the address of this backend as the browser sees it (see browser_base)"""
    if is_url(location):
        return location
    return local_document_address(endpoint_id, base)


def load_graph(endpoint_id, base=DEFAULT_BASE):
    """An endpoint's metadata as an RDF graph, read with the base IRI the browser uses
    (see document_iri). Every route parses its endpoint once, with this function, and
    passes the graph on to the functions it uses.
    base: the address of this backend as the browser sees it (see browser_base)"""
    location = endpoint_file(endpoint_id)
    text, media_type = read_document(location)
    return parse_document(text, media_type, document_iri(endpoint_id, location, base), location)


def local_data_address(endpoint_id, base):
    """Where demo 7's data folder is for this endpoint's document: {base}files/data/ for a local
    endpoint (its <../data/x.csv> resolves there, see DATA_FILES_PATH). None for an online
    endpoint: its data files can only be web addresses, never files on this computer."""
    if endpoint_id in load_online_endpoints():
        return None
    return urljoin(base, DATA_FILES_PATH.lstrip("/"))


def load_source(endpoint_id, request=None):
    """What the data routes start with: the endpoint's metadata as an RDF graph (see load_graph),
    and its tss:Source and fields (see read_source), both read as the browser reads them.
    request: the route's request, for the address the browser uses (see browser_base)"""
    base = browser_base(request)
    graph = load_graph(endpoint_id, base)
    source = read_source(graph, local_data_address(endpoint_id, base))
    return graph, source


# ---------------------------------------------------------------------------
# Metadata endpoints: the local .ttl files, and the online ones the user added
# ---------------------------------------------------------------------------

def local_endpoint_ids():
    """The local metadata endpoints are the .ttl files in the 'metadata endpoint' folder.
    The id of an endpoint is its file name without .ttl"""
    ids = []
    for ttl_file in sorted(METADATA_DIR.glob("*.ttl")):
        ids.append(ttl_file.stem)
    return ids


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
    if ONLINE_ENDPOINTS_FILE.exists():
        try:
            registry.parse(ONLINE_ENDPOINTS_FILE, format="turtle")
        except Exception as error:
            raise HTTPException(status_code=500, detail=f"Could not read {ONLINE_ENDPOINTS_FILE.name}: {error}")
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
    ONLINE_ENDPOINTS_FILE.write_text(registry.serialize(format="turtle"), encoding="utf-8")


def endpoint_file(endpoint_id):
    """Where an endpoint's RDF document is: a web address, or a path inside demo 7"""
    online = load_online_endpoints()
    if endpoint_id in online:
        return online[endpoint_id]
    # Only accept ids we know, so nobody can ask for some other file on disk
    if endpoint_id in local_endpoint_ids():
        return f"metadata endpoint/{endpoint_id}.ttl"
    raise HTTPException(status_code=404, detail=f"Unknown metadata endpoint: {endpoint_id}")


# ---------------------------------------------------------------------------
# The catalogue: the list of metadata endpoints, as RDF (DCAT)
# ---------------------------------------------------------------------------

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
    for endpoint_id in local_endpoint_ids():
        address = URIRef(local_document_address(endpoint_id, base))
        labels, problem = local_labels(endpoint_id, address)
        turtle = URIRef(IANA_MEDIA_TYPES + "text/turtle")  # the local files are Turtle
        add_dataset(graph, catalog, endpoint_id, address, address, labels, turtle, problem)

    # An online endpoint keeps its own IRI (its web address), but the browser downloads it
    # through this backend, so the other server does not have to allow that (CORS).
    # Its label and media type come from the registry (noted when it was added), so the
    # catalogue never waits for another server.
    registry = read_registry()
    for endpoint_id, url in online_endpoints(registry).items():
        dataset = URIRef(url)
        document = URIRef(urljoin(base, f"api/endpoints/{quote(endpoint_id)}/document"))
        labels = list(registry.objects(dataset, RDFS.label))
        if len(labels) == 0:
            labels = [Literal(url)]  # no label was noted: its address is the best name it has
        media_type = None
        for distribution in registry.objects(dataset, DCAT.distribution):
            media_type = registry.value(distribution, DCAT.mediaType)
        if media_type is None:
            media_type = URIRef(IANA_MEDIA_TYPES + "text/turtle")  # what a document without a type is read as
        add_dataset(graph, catalog, endpoint_id, dataset, document, labels, media_type, None)
    return graph


def local_labels(endpoint_id, address):
    """The rdfs:label literals of a local endpoint's tss:Source, read from its file: (labels, None).
    When the file can't be read: ([], the reason). address: its IRI (see local_document_address)."""
    location = f"metadata endpoint/{endpoint_id}.ttl"
    try:
        text, media_type = read_document(location)
        document = parse_document(text, media_type, str(address), location)
        source = find_source(document)
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


def turtle_response(graph):
    """An answer with an RDF graph, as Turtle written by rdflib. Numbers and booleans are written
    in full ("12"^^xsd:double, not 1.2e+01; see exact_turtle), so a SHACL report shows the wrong
    values of embedded points (like " 12 " or "yes") with their text exactly as it is."""
    return Response(exact_turtle(graph), media_type="text/turtle; charset=utf-8")


def catalog_response(request):
    """The answer with the catalogue (Turtle)"""
    return turtle_response(catalog_graph(str(request.base_url)))


@api.get("/catalog")
def get_catalog(request: Request):
    """The catalogue of metadata endpoints (Turtle, DCAT): the local ones, then the online ones"""
    return catalog_response(request)


@api.post("/endpoints/online")
def add_online_endpoint(url, request: Request):
    """Add an online TSS metadata endpoint: the web address of an RDF document (e.g. a Turtle file)
    with one tss:Source. From then on it works like the local ones.
    Answers with the updated catalogue (Turtle)."""
    url = url.strip()
    if not is_url(url):
        raise HTTPException(status_code=400, detail="The address must start with http:// or https://")
    # It becomes an IRI in the catalogue, and an IRI can't hold these
    if re.search(r'[\s<>"{}|\\^`]', url):
        raise HTTPException(status_code=400, detail='The address can\'t contain spaces or any of < > " { } | \\ ^ `')

    registry = read_registry()
    online = online_endpoints(registry)
    for endpoint_id, known_url in online.items():
        if known_url == url:
            raise HTTPException(status_code=400, detail=f"This endpoint is already in the list ({endpoint_id})")
    # A local endpoint's own address (as the browser sees it) is in the list already, with that
    # same IRI: as an online endpoint too, the catalogue would have one dataset with two ids
    for endpoint_id in local_endpoint_ids():
        if local_document_address(endpoint_id, browser_base(request)) == url:
            raise HTTPException(status_code=400, detail=f"This is a local endpoint ({endpoint_id}): "
                                                        "it is already in the list.")

    # Check that it really is a TSS metadata endpoint before it goes in the list.
    # It is read with its own address as base IRI, as it will be from now on (see document_iri).
    try:
        text, media_type = fetch_document(url)
        document = parse_document(text, media_type, url, url)
        source = find_source(document)
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
    register_online_endpoint(registry, f"online-{number}", url, labels, media_type)
    save_registry(registry)
    return catalog_response(request)


@api.delete("/endpoints/{endpoint_id}")
def remove_online_endpoint(endpoint_id, request: Request):
    """Take an online endpoint out of the list (the local .ttl files can't be removed here).
    Answers with the updated catalogue (Turtle)."""
    registry = read_registry()
    online = online_endpoints(registry)
    if endpoint_id not in online:
        raise HTTPException(status_code=404, detail=f"{endpoint_id} is not an online endpoint")
    unregister_online_endpoint(registry, online[endpoint_id])
    save_registry(registry)
    return catalog_response(request)


@api.get("/endpoints/{endpoint_id}/document")
def get_document(endpoint_id):
    """An endpoint's RDF document, exactly as it is: the local file, or the online document,
    downloaded here so the browser does not need the other server to allow it (CORS)"""
    location = endpoint_file(endpoint_id)
    text, media_type = read_document(location)
    headers = {}
    if is_url(location):
        # The online document's own IRI: the address it was registered with (also when its
        # server redirects elsewhere). Relative IRIs in it are read against this address,
        # by the browser (the catalogue's dataset IRI) and by the backend (see document_iri).
        headers["Content-Location"] = location
    return Response(text, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Reading a source as RDF terms
# ---------------------------------------------------------------------------
# The routes below never compare prefixed names like "tss:CSV": a document may use any
# prefix it likes, or none at all. They compare the RDF terms rdflib gives
# (graph.value(source, TSS["format"]) == TSS.CSV), and read typed values with
# Literal.toPython() (a boolean, a whole number, a datetime, ...).

def find_source(graph):
    """The one tss:Source of an endpoint's metadata (this dashboard reads one source per endpoint)"""
    sources = list(graph.subjects(RDF.type, TSS.Source))
    if len(sources) == 0:
        raise HTTPException(status_code=404, detail="The metadata has no tss:Source")
    if len(sources) > 1:
        raise HTTPException(status_code=400, detail=f"The metadata has {len(sources)} tss:Source nodes; "
                                                    "this dashboard reads one source per endpoint")
    return sources[0]


def boolean_value(graph, node, prop):
    """A boolean property, like tss:header or tss:mutable, as True or False, read by rdflib.
    None when it is not stated, or not a boolean."""
    value = graph.value(node, prop)
    if isinstance(value, Literal) and isinstance(value.toPython(), bool):
        return value.toPython()
    return None


def moment_of(value):
    """An xsd:dateTime literal as a datetime in UTC, read by rdflib (a time without a timezone
    is taken as UTC). None when it is not a date-time (or not a literal at all)."""
    if not isinstance(value, Literal):
        return None
    moment = value.toPython()  # rdflib reads an xsd:dateTime literal as a datetime
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def window_moment(graph, source, name):
    """tss:from or tss:until of the source as a datetime in UTC, or None when it is not stated"""
    value = graph.value(source, TSS[name])
    if value is None:
        return None
    moment = moment_of(value)
    if moment is None:
        raise HTTPException(status_code=400, detail=f'tss:{name} must be an xsd:dateTime like '
                                                    f'"2021-03-03T23:45:00Z"^^xsd:dateTime, not {value.n3(graph.namespace_manager)}.')
    return moment


def column_index(value):
    """A tss:columnIndex as a number (zero-based), or None when it is not a whole number of 0 or more"""
    if not isinstance(value, Literal):
        return None
    number = value.toPython()  # rdflib reads an xsd:integer literal as an int
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        return None
    return number


def read_field(graph, field, time_field):
    """One field of the source (one column of its data), as RDF terms and plain values"""
    column = graph.value(field, TSS.columnName)
    return {
        "node": field,                                      # the field's IRI: routes and rows use it
        "columnName": str(column) if column is not None else None,   # e.g. "Value"
        "columnIndex": column_index(graph.value(field, TSS.columnIndex)),
        "datatype": graph.value(field, TSS.datatype),       # an IRI such as XSD.double, or None
        "isTime": field == time_field,
        "label": graph.value(field, RDFS.label),
        "observedProperty": graph.value(field, TSS.observedProperty),
        "unit": graph.value(field, TSS.unit),
        "describes": graph.value(field, TSS.describesField),  # the field this one says something about
    }


def field_order(field):
    """How the fields are sorted: by tss:columnIndex when they have one,
    otherwise the time field first and then the rest by column name"""
    if field["columnIndex"] is not None:
        return (0, field["columnIndex"], "")
    if field["isTime"]:
        return (1, 0, "")
    return (2, 0, field["columnName"] or "")


def read_source(graph, data_address):
    """What the data routes need to know about the endpoint's tss:Source and its fields,
    read from the graph as RDF terms and Python values.
    data_address: where demo 7's data folder is for this document (see local_data_address),
    or None for an online endpoint (see iri_to_location)"""
    source = find_source(graph)
    time_field = graph.value(source, TSS.timeField)
    fields = []
    for field in graph.objects(source, TSS.hasField):
        fields.append(read_field(graph, field, time_field))
    fields.sort(key=field_order)

    delimiter = graph.value(source, TSS.delimiter)
    encoding = graph.value(source, TSS.encoding)
    return {
        "node": source,
        "embedded": embedded_mode(graph, source),       # True, False, or None (not stated)
        "format": graph.value(source, TSS["format"]),    # an IRI: compare it with TSS.CSV or TSS.Parquet
        "dataFile": iri_to_location(graph.value(source, TSS.dataFile), data_address),  # e.g. "data/x.csv", or None
        "delimiter": str(delimiter) if delimiter is not None else ",",
        "header": boolean_value(graph, source, TSS.header) is not False,  # a header line, unless it says false
        "encoding": str(encoding) if encoding is not None else "utf-8",
        "timeField": time_field,
        "fields": fields,
    }


def find_field(source, field_iri):
    """The field (one column of the source) with this IRI, from read_source"""
    for field in source["fields"]:
        if field["node"] == URIRef(field_iri):
            return field
    raise HTTPException(status_code=404, detail=f"Unknown field: {field_iri}")


def is_numeric(datatype):
    """Whether a field's tss:datatype is a number type: only numeric fields have value
    ranges and mean/min/max. (The same map of XSD types as the SQL box.)"""
    return sql_box.duckdb_type(datatype) in sql_box.NUMERIC_TYPES


# The cells of a data file (the text of a CSV cell, or a Parquet value written as text) are
# read by Python itself, after a check that the text has the XSD form of the field's datatype
# (LEXICAL_FORMS): see data_time and read_number. A data file can have hundreds of thousands
# of cells, and an rdflib Literal for each of them would make the query several times slower.
# The embedded points are RDF already: their values are rdflib Literals (see point_rows), so
# rdflib reads them (Literal.toPython()), after the same check of their text: see
# literal_time and literal_number. So does the metadata.
# The XSD forms, each made into a pattern once (re.compile), to check many cells quickly
DATA_FORMS = {}
for datatype_iri, form_text in LEXICAL_FORMS.items():
    DATA_FORMS[datatype_iri] = re.compile(form_text)


def time_form(datatype):
    """The XSD form a time of this datatype must have: an xsd:dateTimeStamp must have a timezone,
    an xsd:dateTime may leave it out. Any other datatype is read as xsd:dateTime (see TIME_DATATYPES)."""
    if datatype in TIME_DATATYPES:
        return DATA_FORMS[datatype]
    return DATA_FORMS[XSD.dateTime]


def data_time(text, datatype=XSD.dateTime):
    """The text of a time cell as a datetime, or None when it is not an xsd:dateTime like
    2021-03-03T23:45:00Z. A time without a timezone is taken as UTC.
    datatype: the time field's tss:datatype (an xsd:dateTimeStamp time needs a timezone, see time_form)"""
    if time_form(datatype).fullmatch(text) is None:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:  # e.g. 30 February, or a year Python can't hold
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def read_number(text, datatype, graph):
    """The text of a numeric field's value as a number (None when it is empty):
    "4797.72" of an xsd:double field -> 4797.72. The text must have the XSD form of the
    datatype, so " 12 ", "1_000" or "4,5" are refused, as SHACL refuses them."""
    if text == "":
        return None
    form = DATA_FORMS.get(datatype)  # None: a datatype without a known form
    if form is not None and form.fullmatch(text) is None:
        raise HTTPException(status_code=400, detail=f"The field declares numeric values "
                                                    f"({message_text(graph, datatype)}), but contains {text!r}.")
    try:
        if sql_box.duckdb_type(datatype) == "BIGINT":
            value = int(text)
        else:
            value = float(text)  # also for xsd:decimal ("1.50" -> 1.5)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"The field declares numeric values "
                                                    f"({message_text(graph, datatype)}), but contains {text!r}.")
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail="The field contains a non-finite numeric value.")
    return value


def literal_time(literal):
    """The time of an embedded point (an rdflib Literal) as a datetime, read by rdflib, or None
    when it is not an xsd:dateTime (or xsd:dateTimeStamp) literal with the XSD form, like
    "2021-03-03T23:45:00Z". A time without a timezone is taken as UTC (see moment_of);
    an xsd:dateTimeStamp must have one."""
    if literal.datatype not in TIME_DATATYPES or time_form(literal.datatype).fullmatch(str(literal)) is None:
        return None
    # rdflib reads an xsd:dateTime literal (Literal.toPython()), but gives an xsd:dateTimeStamp
    # literal back unread. It is an xsd:dateTime with a timezone, so its text is read as one.
    return moment_of(Literal(str(literal), datatype=XSD.dateTime))


def literal_number(literal, datatype, graph):
    """The value of an embedded point (an rdflib Literal) for a numeric field, read by rdflib:
    "4797.72"^^xsd:double -> 4797.72. As with read_number, the literal must have the field's
    datatype, and its text the XSD form of that datatype: " 12 " or "1_000" are refused, as SHACL
    refuses them (rdflib alone would read them as 12.0 and 1000.0)."""
    value = literal.toPython()  # an int, a float or a Decimal; the literal itself when rdflib can't read it
    form = DATA_FORMS.get(datatype)  # None: a datatype without a known form
    wrong_form = form is not None and form.fullmatch(str(literal)) is None
    if (literal.datatype != datatype or wrong_form or isinstance(value, bool)
            or not isinstance(value, (int, float, Decimal))):
        raise HTTPException(status_code=400, detail=f"The field declares numeric values "
                                                    f"({message_text(graph, datatype)}), but contains {str(literal)!r}.")
    if isinstance(value, Decimal):
        value = float(value)  # xsd:decimal, like read_number: "1.50" -> 1.5
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail="The field contains a non-finite numeric value.")
    return value


def value_text(row, field):
    """The text of a field's value in a row of data_rows: a cell's text as it is, or the text
    (lexical form) of an embedded point's literal. "" when the row has no value for the field."""
    return str(row.get(field, ""))


# ---------------------------------------------------------------------------
# Reading the data: a CSV file, a Parquet file, or the points embedded in the Turtle
# ---------------------------------------------------------------------------

def point_time(row, time_field):
    """How points are put in time order: by their time, and a point without a valid time last.
    A valid time is an xsd:dateTime (or xsd:dateTimeStamp) literal in the XSD form (see
    literal_time), the same rule as /query and SHACL: "2021-03-03" alone is no valid time."""
    moment = None
    value = row.get(time_field)
    if isinstance(value, Literal):
        moment = literal_time(value)
    if moment is None:
        return (1, datetime.min.replace(tzinfo=timezone.utc))
    return (0, moment)


def point_rows(graph, source):
    """The points embedded in a source (tss:points), read as RDF, as rows in time order.
    lift_points (code/TSS2RDF/tss2rdf.py, via rdf_generation.py) parses the rdf:JSON literal
    as JSON-LD, with a context made from the source's fields, so each point becomes a blank
    node, each key a field IRI, and each value a literal typed by the field's tss:datatype:
        _:p1  ex:zevenaarshaven-ec20-value-field  "4797.72"^^xsd:double .
    Each row is a dict: field (its IRI) -> its literal. Keys that are no field's tss:columnName
    (predicates under urn:tss:unmapped:) are left out; the SHACL check warns about them.
    Raises ValueError with the reason when the points can't be read."""
    points = rdf_generation.lift_points(graph, source["node"])
    fields = set()
    for field in source["fields"]:
        fields.add(field["node"])

    rows = []
    for point in points.subjects(unique=True):
        # A value that is itself a JSON object becomes a node inside a point: that is no point
        if (None, None, point) in points:
            continue
        row = {}
        for predicate, value in points.predicate_objects(point):
            if predicate not in fields:
                continue  # a key that is no field's column name (urn:tss:unmapped:...)
            if not isinstance(value, Literal):
                raise ValueError(f"A point in tss:points has a JSON object as its "
                                 f"{column_name(graph, predicate)!r} value, not a value.")
            if predicate in row:
                raise ValueError(f"A point in tss:points has more than one {column_name(graph, predicate)!r} value.")
            row[predicate] = value
        # A point with no value for any field (only keys that are no column name, or only empty
        # values) has nothing to show: it is skipped, as the browser does (SHACL's P6 still warns
        # about its keys)
        if len(row) == 0:
            continue
        rows.append(row)

    # Points have no order in RDF, so put them in time order
    time_field = source["timeField"]
    rows.sort(key=lambda row: point_time(row, time_field))
    return rows


def cell_text(value):
    """One cell of a Parquet file as text, like it would be in a CSV file ("" when empty)"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return sql_box.to_json_value(value)  # e.g. 2021-03-03T23:15:00Z
    return str(value)


def column_positions(source, header, only=None):
    """Where each field's values are in the rows of a data file: field (its IRI) -> column position.
    header: the column names (a CSV header line, or a Parquet file's columns): a field's column
    is the one named by its tss:columnName. header None: a CSV file without a header line
    (tss:header false), where a field's column is its tss:columnIndex.
    only: the fields (their IRIs) to find; None means every field."""
    positions = {}
    for field in source["fields"]:
        if only is not None and field["node"] not in only:
            continue
        if header is None:
            if field["columnIndex"] is not None:
                positions[field["node"]] = field["columnIndex"]
        elif field["columnName"] in header:
            positions[field["node"]] = header.index(field["columnName"])
    return positions


def csv_header(source, first_cells):
    """Column names for a CSV file without a header line (tss:header false): each column is
    named after the field with that tss:columnIndex, the others "column 0", "column 1", ..."""
    names = []
    for index in range(len(first_cells)):
        name = f"column {index}"
        for field in source["fields"]:
            if field["columnIndex"] == index and field["columnName"] is not None:
                name = field["columnName"]
        names.append(name)
    return names


def check_csv_settings(source):
    """Stop with the reason when this dashboard can't read the source's CSV file"""
    if len(source["delimiter"]) != 1:
        raise ValueError(f"tss:delimiter must be one character, not '{source['delimiter']}'")
    if source["encoding"].lower() not in ("utf-8", "utf8"):
        raise ValueError(f"This dashboard only reads UTF-8 files, not tss:encoding '{source['encoding']}'")


def data_location(source):
    """Where the source's data file is; raises ValueError when it has none, or when it is not there"""
    location = source["dataFile"]
    if location is None:
        raise ValueError("The source has no tss:dataFile")
    if not file_exists(location):
        raise ValueError(f"Data file not found: {location}")
    return location


def parquet_head(location, count):
    """The column names and the first rows of a Parquet file, each value as text (see cell_text)"""
    with duckdb.connect() as connection:
        result = connection.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning = false) LIMIT ?", [str(local_copy(location)), count]
        )
        names = [column[0] for column in result.description]
        found = result.fetchall()
    rows = []
    for values in found:
        cells = []
        for value in values:
            cells.append(cell_text(value))
        rows.append(cells)
    return names, rows


def data_rows(graph, source, only=None):
    """Every row of a source's data, one at a time, as a dict: field (its IRI) -> its value.
    A field that is missing, or "", has no value in that row.
    only: the fields (their IRIs) a route needs; None means every field. Reading fewer
    columns of a big file is faster.
    - CSV file: the text of the cell in the field's column, exactly as it is in the file (see column_positions)
    - Parquet file: the value in the field's column, as text (see cell_text)
    - points embedded in the Turtle: the field's literal itself, an rdflib Literal (see point_rows)
    Read a typed value from a text with data_time or read_number, and from a literal with
    literal_time or literal_number (all of them check the XSD form first). value_text gives
    the text of either.
    Raises ValueError with the reason when the data can't be read.
    (A function with "yield" hands out its rows one by one, so a big file never has to fit in memory.)"""
    if source["embedded"]:
        # The points are RDF already: each row holds their literals (field -> Literal)
        for point in point_rows(graph, source):
            yield point
        return

    location = data_location(source)

    if source["format"] == TSS.Parquet:
        try:
            with duckdb.connect() as connection:
                result = connection.execute(
                    "SELECT * FROM read_parquet(?, hive_partitioning = false)", [str(local_copy(location))]
                )
                names = [column[0] for column in result.description]
                positions = column_positions(source, names, only)
                while True:
                    batch = result.fetchmany(10000)  # 10000 rows at a time
                    if len(batch) == 0:
                        break
                    for found in batch:
                        row = {}
                        for field, index in positions.items():
                            row[field] = cell_text(found[index])
                        yield row
        except duckdb.Error as error:
            raise ValueError(f"Could not read the Parquet file {location}: {error}")
        return

    if source["format"] == TSS.CSV:
        check_csv_settings(source)
        reader = csv.reader(read_lines(location), delimiter=source["delimiter"])
        header = None
        if source["header"]:
            header = next(reader, [])
        positions = column_positions(source, header, only)
        for cells in reader:
            row = {}
            for field, index in positions.items():
                if index < len(cells):
                    row[field] = cells[index]
            yield row
        return

    raise ValueError(f"The dashboard can't read data in tss:format {message_text(graph, source['format'])}")


def data_columns(graph, source):
    """The fields that have a column in the source's data (their IRIs), in the order of the
    data's columns. Embedded points have no column order: they keep the order of the fields
    (see field_order). Raises ValueError with the reason when the data can't be read."""
    if source["embedded"]:
        columns = []
        for field in source["fields"]:
            if field["columnName"] is not None:
                columns.append(field["node"])
        return columns

    location = data_location(source)
    if source["format"] == TSS.Parquet:
        try:
            names, rows = parquet_head(location, 0)  # 0 rows: only the column names
        except duckdb.Error as error:
            raise ValueError(f"Could not read the Parquet file {location}: {error}")
        positions = column_positions(source, names)
    elif source["format"] == TSS.CSV:
        check_csv_settings(source)
        header = None  # no header line (tss:header false): the fields' tss:columnIndex says where they are
        if source["header"]:
            header = next(csv.reader(read_lines(location), delimiter=source["delimiter"]), [])
        positions = column_positions(source, header)
    else:
        raise ValueError(f"The dashboard can't read data in tss:format {message_text(graph, source['format'])}")

    # The fields, in the order of their columns
    order = []
    for field, position in positions.items():
        order.append((position, field))
    order.sort()
    columns = []
    for position, field in order:
        columns.append(field)
    return columns


def first_rows(graph, source, count):
    """The first rows of a source's data, for the inspector's table, as (columns, rows):
    the fields that have a column in the data (see data_columns), and each row as a list
    with the text of each field's value in that row, None when it has no value ("").
    Raises ValueError with the reason when the data can't be read."""
    columns = data_columns(graph, source)
    rows = []
    if count == 0:
        return columns, rows
    for row in data_rows(graph, source):
        cells = []
        for field in columns:
            text = value_text(row, field)
            if text == "":
                cells.append(None)
            else:
                cells.append(text)
        rows.append(cells)
        if len(rows) == count:
            break
    return columns, rows


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def message_text(graph, term):
    """How an RDF term is written in an error message: a prefixed name like tss:CSV,
    <a full IRI>, or a literal like "abc". Only for people to read: the code never
    compares these texts."""
    if term is None:
        return "(not stated)"
    return term.n3(graph.namespace_manager)


def iri_to_location(iri, data_address):
    """Where a data file (tss:dataFile) is: a file in demo 7's data folder ("data/x.csv"), or a web address.
    rdflib read <../data/x.csv> against the document's IRI (see document_iri), so:
    - a local endpoint gives   http://localhost:5174/files/data/x.csv     -> "data/x.csv"
      (data_address is http://localhost:5174/files/data/; that folder is not served, the backend reads the file)
    - a local endpoint may name file:///C:/.../demo%207/data/x.csv        -> "data/x.csv"
    - an online endpoint gives https://.../data/x.csv                     -> the web address
      (data_address is None: an online document can't name a file on this computer)
    Only files inside demo 7's data folder are read. The path is resolved first (resolve()
    follows "..", and links), so data/../../x can't get out of it."""
    if iri is None:
        return None
    iri = str(iri)
    parts = urlparse(iri)
    path = None
    if data_address is not None:
        # A local endpoint: {base}files/data/x.csv, or a file:/// IRI, is a file on this computer
        folder = urlparse(data_address)
        same_server = parts.scheme == folder.scheme and parts.netloc.lower() == folder.netloc.lower()
        if same_server and parts.path.startswith(folder.path):
            path = DEMO_DIR / "data" / unquote(parts.path[len(folder.path):])  # unquote: %20 -> a space, ...
        elif parts.scheme == "file":
            path = Path(url2pathname(parts.path))
    if path is None:
        if is_url(iri):
            return iri  # downloaded from the web (see download)
        if data_address is None and parts.scheme == "file":
            raise HTTPException(status_code=400, detail=f"tss:dataFile <{iri}> is a file on this computer, "
                                                        "but an online endpoint can only name web addresses.")
        raise HTTPException(status_code=400, detail=f"tss:dataFile <{iri}> is not a web address or a file in demo 7's data folder.")

    data_folder = (DEMO_DIR / "data").resolve()
    path = path.resolve()
    if path == data_folder or not path.is_relative_to(data_folder):
        raise HTTPException(status_code=400, detail=f"tss:dataFile <{iri}> is not a file in demo 7's data folder.")
    return "data/" + path.relative_to(data_folder).as_posix()


def parse_time(text):
    """Read a date-time typed in the query form, such as 2021-03-04T00:00.
    A time without a timezone is taken as UTC. (Times in the RDF are read by rdflib instead,
    see moment_of, and times in the data by data_time.)"""
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Not a valid date-time: '{text}' (expected something like 2021-03-04T00:00)")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def column_name(graph, field):
    """The column a field reads, e.g. "Timestamp", for error messages.
    A field without tss:columnName is written as its IRI."""
    name = graph.value(field, TSS.columnName)
    if name is not None:
        return str(name)
    return message_text(graph, field)


def embedded_mode(graph, group):
    """Require an explicit boolean, rather than guessing a storage profile."""
    values = list(graph.objects(group, TSS.embedded)) if group is not None else []
    if len(values) != 1 or not isinstance(values[0], Literal) or not isinstance(values[0].toPython(), bool):
        return None
    return values[0].toPython()


@api.post("/endpoints/{endpoint_id}/rdf")
def generate_rdf(endpoint_id, request: Request = None):
    """Turn the source's data into SOSA observations (see code/TSS2RDF/tss2rdf.py).
    The whole graph is written to a temporary file to download (N-Triples), and its first
    observations to a preview file (Turtle). The answer holds the counts and the addresses
    of those two files."""
    graph, source = load_source(endpoint_id, request)

    # The data file, as a file on this computer (embedded points are in the graph itself)
    data_path = None
    if source["embedded"] is not True:
        if source["dataFile"] is None:
            raise HTTPException(status_code=400, detail="The source has no tss:dataFile.")
        if not file_exists(source["dataFile"]):
            raise HTTPException(status_code=404, detail=f"Data file not found: {source['dataFile']}")
        data_path = local_copy(source["dataFile"])

    token = uuid4().hex
    target = Path(RDF_EXPORT_DIRECTORY.name) / f"{token}.nt"
    try:
        result = rdf_generation.generate(graph, data_path, target)
    except ValueError as error:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(error))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    # The preview (the first 200 observations, written as Turtle by rdflib) gets a file and
    # an address of its own: the browser parses it as RDF
    preview = Path(RDF_EXPORT_DIRECTORY.name) / f"{token}-preview.ttl"
    preview.write_text(result["preview"], encoding="utf-8", newline="\n")
    RDF_EXPORTS[token] = {"endpoint": endpoint_id, "file": target, "preview": preview}

    address = f"/api/endpoints/{quote(endpoint_id)}/rdf"
    return {
        "observations": result["observations"],
        "triples": result["triples"],
        "series": result["series"],                  # [{"field": <the field's IRI>, "observations": n}]
        "previewUrl": f"{address}/preview/{token}",
        "previewObservations": result["previewObservations"],
        "previewTruncated": result["previewTruncated"],
        "bytes": result["bytes"],                    # the size of the whole file
        "downloadUrl": f"{address}/download/{token}",
        "filename": GENERATED_RDF_FILENAME,
    }


def generated_rdf(endpoint_id, token):
    """The files of one RDF generation (see generate_rdf), or 404 when they are gone
    (they only live as long as the backend runs)"""
    export = RDF_EXPORTS.get(token)
    if export is None or export["endpoint"] != endpoint_id or not export["file"].is_file():
        raise HTTPException(status_code=404, detail="This generated RDF is no longer available. Generate it again.")
    return export


@api.get("/endpoints/{endpoint_id}/rdf/preview/{token}")
def preview_generated_rdf(endpoint_id, token):
    """The preview of generated RDF: the first 200 observations, as Turtle"""
    return FileResponse(generated_rdf(endpoint_id, token)["preview"], media_type="text/turtle")


@api.get("/endpoints/{endpoint_id}/rdf/download/{token}")
def download_generated_rdf(endpoint_id, token):
    """All the generated RDF, as an N-Triples file to save"""
    return FileResponse(generated_rdf(endpoint_id, token)["file"], media_type="application/n-triples",
                        filename=GENERATED_RDF_FILENAME)


# ---------------------------------------------------------------------------
# SHACL: validate the metadata (and its embedded points) with pyshacl
# ---------------------------------------------------------------------------

def storage_profile(graph):
    """True (embedded points) or False (an external data file), from the tss:embedded of
    the metadata's tss:Source nodes. None when that can't be told: no source, a source
    without one boolean tss:embedded, or sources that disagree."""
    modes = set()
    for source in graph.subjects(RDF.type, TSS.Source):
        modes.add(embedded_mode(graph, source))
    if len(modes) != 1:
        return None
    return modes.pop()


def shacl_data_graph(graph):
    """The graph SHACL validates: the metadata, plus the points of each embedded source
    read as RDF (lift_points, see point_rows), so the shapes can check every value:
        _:p1  ex:zevenaarshaven-ec20-value-field  "4797.72"^^xsd:double .
    Gives (the data graph, problems): problems lists (source, reason) for the sources
    whose tss:points can't be read."""
    data = Graph()
    for triple in graph:
        data.add(triple)

    problems = []
    for source in graph.subjects(RDF.type, TSS.Source):
        if embedded_mode(graph, source) is not True:
            continue  # an external data file is not read by SHACL
        if (source, TSS.points, None) not in graph:
            continue  # the shapes report a missing tss:points themselves (S6)
        try:
            points = rdf_generation.lift_points(graph, source)
        except ValueError as error:
            problems.append((source, str(error)))
            continue
        for triple in points:
            data.add(triple)
    return data, problems


def validation_report(graph, shapes, name):
    """Validate an endpoint's metadata (and its embedded points) with these shapes (pyshacl).
    Gives the report as an RDF graph, and its sh:ValidationReport node.
    name: how error messages call the shapes"""
    data, problems = shacl_data_graph(graph)
    try:
        # allow_warnings: warnings alone don't make the metadata fail; only violations do.
        # pyshacl switches rdflib.NORMALIZE_LITERALS off and on again while it reads the shapes,
        # and a lift of embedded points in another request switches it too: the lock makes them
        # take turns, so a lift never loses the exact texts of its values (see tss2rdf.NORMALIZE_LOCK).
        with NORMALIZE_LOCK:
            conforms, report, report_text = shacl_validate(data, shacl_graph=shapes, allow_warnings=True)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not validate with {name}: {error}")
    if not isinstance(report, Graph):
        raise HTTPException(status_code=400, detail=f"Could not validate with {name}: {report}")
    report_node = report.value(predicate=RDF.type, object=SH.ValidationReport)

    # Points that can't be read as JSON-LD at all: SHACL can't look inside the rdf:JSON
    # literal, so the report gets a result for them here, like the shapes' own SPARQL checks give
    for source, reason in problems:
        result = BNode()
        report.add((report_node, SH.result, result))
        report.add((result, RDF.type, SH.ValidationResult))
        report.add((result, SH.focusNode, source))
        report.add((result, SH.resultPath, TSS.points))
        report.add((result, SH.resultSeverity, SH.Violation))
        report.add((result, SH.resultMessage, Literal(f"S6 points: {reason}")))
        report.add((result, SH.sourceConstraintComponent, SH.SPARQLConstraintComponent))
        report.set((report_node, SH.conforms, Literal(False)))

    # The metadata's prefixes (tss:, ex:, ...), so the Turtle of the report is easier to read
    for prefix, namespace in graph.namespaces():
        report.bind(prefix, namespace, override=False)
    report.bind("dcterms", DCTERMS)
    return report, report_node


@api.get("/endpoints/{endpoint_id}/shacl")
def check_shacl(endpoint_id, request: Request):
    """Validate with the bundled shapes for the source's storage profile (tss:embedded).
    Answers with the SHACL validation report (an sh:ValidationReport, as Turtle)."""
    graph = load_graph(endpoint_id, browser_base(request))  # the report names the IRIs the browser knows
    embedded = storage_profile(graph)
    if embedded is None:
        raise HTTPException(status_code=400, detail="Cannot pick the default shapes: the metadata needs a tss:Source "
                                                    "with tss:embedded true or false. You can upload your own shapes instead.")
    name = DEFAULT_SHAPES[embedded]
    shapes = Graph().parse(SHACL_DIR / name, format="turtle")
    report, report_node = validation_report(graph, shapes, name)
    # Which shapes were used: their address on this backend (/files/SHACL/<name>)
    address = urljoin(str(request.base_url), quote(f"{SHACL_FILES_PATH.lstrip('/')}/{name}"))
    report.add((report_node, DCTERMS.source, URIRef(address)))
    return turtle_response(report)


async def request_body(request: Request):
    """The body of the request, as bytes. FastAPI runs this before the route that asks for it
    (Depends), because reading the body has to wait for it to arrive (await)."""
    return await request.body()


def has_shapes(shapes):
    """Whether a graph holds SHACL shapes: a shape (sh:NodeShape, sh:PropertyShape),
    or a SHACL target or property"""
    for kind in (SH.NodeShape, SH.PropertyShape):
        if (None, RDF.type, kind) in shapes:
            return True
    for prop in (SH.targetClass, SH.targetNode, SH.targetSubjectsOf, SH.targetObjectsOf, SH.property, SH.path):
        if (None, prop, None) in shapes:
            return True
    return False


@api.post("/endpoints/{endpoint_id}/shacl")
def check_custom_shacl(endpoint_id, request: Request, body: Annotated[bytes, Depends(request_body)]):
    """Validate with your own shapes instead of the bundled ones. The request body is the
    shapes file itself, in the RDF syntax its Content-Type says (text/turtle, application/rdf+xml, ...;
    not JSON-LD, see RDF_FORMATS). Answers with the SHACL validation report (Turtle), like the GET above."""
    graph = load_graph(endpoint_id, browser_base(request))
    if len(body) > MAX_SHAPES_BYTES:
        raise HTTPException(status_code=413, detail="The shapes file must be at most 5 MB.")
    content_type = request.headers.get("content-type")
    if bare_media_type(content_type) == JSON_LD:
        raise HTTPException(status_code=400, detail=f"The shapes were sent as JSON-LD. {JSON_LD_REFUSED}")
    media_type = rdf_media_type(content_type)
    if media_type is None:
        raise HTTPException(status_code=415, detail=f"The shapes were sent as {content_type}, which is not an RDF syntax "
                                                    "this dashboard reads (Turtle, RDF/XML, N-Triples or N3)")

    shapes = Graph()
    try:
        # utf-8-sig: a byte order mark at the start of the file (some editors write one) is skipped.
        # Relative IRIs in the shapes are read against the address they were sent to.
        text = body.decode("utf-8-sig")
        shapes.parse(data=text, format=RDF_FORMATS[media_type], publicID=str(request.url))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not read the shapes as {media_type}: {error}")
    if not has_shapes(shapes):
        raise HTTPException(status_code=400, detail="The uploaded file contains no SHACL shapes or targets.")

    report, report_node = validation_report(graph, shapes, "the uploaded shapes")
    return turtle_response(report)


# ---------------------------------------------------------------------------
# Helper functions for the query form (filters, summary, gaps, hourly/daily values)
# ---------------------------------------------------------------------------

def parse_number(text, what):
    """A number typed in the form, or None when the field was left empty"""
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{what} must be a number, not '{text}'")


def time_text(moment):
    """A date-time as text, always in UTC, e.g. 2021-03-04T01:00:00Z"""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def duration_text(seconds):
    """A number of seconds as text people can read, e.g. 2 d 3 h 15 min"""
    minutes = int(seconds // 60)
    days = minutes // (24 * 60)
    hours = (minutes // 60) % 24
    minutes = minutes % 60
    parts = []
    if days > 0:
        parts.append(f"{days} d")
    if hours > 0:
        parts.append(f"{hours} h")
    if minutes > 0 or len(parts) == 0:
        parts.append(f"{minutes} min")
    return " ".join(parts)


def summarize(readings):
    """Count, mean, min and max of the readings (empty cells are counted apart).
    Each reading is {"moment": its time (a datetime), "value": its value}."""
    count = 0
    empty = 0
    total = 0
    lowest = None
    highest = None
    for reading in readings:
        value = reading["value"]
        if value is None:
            empty += 1
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        count += 1
        total += value
        if lowest is None or value < lowest["value"]:
            lowest = reading
        if highest is None or value > highest["value"]:
            highest = reading

    summary = {"count": count, "empty": empty, "mean": None, "min": None, "max": None}
    if count > 0:
        summary["mean"] = round(total / count, 3)
        summary["min"] = {"value": lowest["value"], "time": time_text(lowest["moment"])}
        summary["max"] = {"value": highest["value"], "time": time_text(highest["moment"])}
    return summary


def find_gaps(moments):
    """Gaps: places where the time between two readings is longer than usual.
    "Usual" is the step that occurs most often (15 minutes in our files).
    moments must be in time order, like the rows of our CSV files."""
    if len(moments) < 3:
        return {"step": None, "count": 0, "missing": None, "longest": []}

    # 1. How often does each step occur? {900.0: 80000, 1800.0: 4000, ...} (in seconds)
    steps = {}
    for i in range(1, len(moments)):
        seconds = (moments[i] - moments[i - 1]).total_seconds()
        steps[seconds] = steps.get(seconds, 0) + 1
    usual = None
    for seconds, how_often in steps.items():
        if usual is None or how_often > steps[usual]:
            usual = seconds

    # 2. Every step longer than the usual one is a gap
    gaps = []
    missing = 0
    for i in range(1, len(moments)):
        seconds = (moments[i] - moments[i - 1]).total_seconds()
        if seconds > usual:
            gaps.append((seconds, moments[i - 1], moments[i]))
            missing += seconds - usual
    gaps.sort(reverse=True)  # longest first, because the length comes first in each gap

    longest = []
    for seconds, before, after in gaps[:5]:
        longest.append({"from": time_text(before), "until": time_text(after), "length": duration_text(seconds)})

    return {
        "step": duration_text(usual),
        "count": len(gaps),
        "missing": duration_text(missing),
        "longest": longest,
    }


def resample_readings(readings, period, aggregate):
    """Put the readings in groups per hour or per day, and turn each group into
    one value: the mean, the min or the max of the values in it"""
    groups = {}  # start of the hour (or day) -> the values in it. A dict keeps the time order.
    for reading in readings:
        moment = reading["moment"].astimezone(timezone.utc)
        if period == "hour":
            group_start = moment.replace(minute=0, second=0, microsecond=0)
        else:
            group_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        if group_start not in groups:
            groups[group_start] = []
        if reading["value"] is not None:
            groups[group_start].append(reading["value"])

    rows = []
    for group_start, values in groups.items():
        if len(values) == 0:
            value = None
        elif aggregate == "min":
            value = min(values)
        elif aggregate == "max":
            value = max(values)
        else:
            value = round(sum(values) / len(values), 3)
        rows.append({"time": time_text(group_start), "value": value, "count": len(values)})
    return rows


# ---------------------------------------------------------------------------
# Data routes: what the browser can't read from the RDF itself (the data, and numbers
# computed from it). The answers are JSON, and don't repeat what the metadata says
# (formats, datatypes, units, column names, ...): the browser reads that from the RDF.
# Fields are named by their full IRI: the IRIs the browser reads, because both read the
# metadata with the same base IRI (see load_source; that is what the routes' request is for.
# A route called without a request, from a test or a script, uses DEFAULT_BASE).
# ---------------------------------------------------------------------------

@api.get("/endpoints/{endpoint_id}/data")
def get_data_file(endpoint_id, lines: int = 20, request: Request = None):
    """Preview the source's data file (tss:dataFile): the first lines of a CSV file,
    or the first rows of a Parquet file converted to CSV text."""
    graph, source = load_source(endpoint_id, request)
    if source["embedded"] is True:
        raise HTTPException(status_code=400, detail="This source has no data file: its points are embedded in the Turtle file (tss:points).")
    lines = max(1, min(lines, 1000))
    location = source["dataFile"]
    if location is None:
        raise HTTPException(status_code=400, detail="The source has no tss:dataFile.")
    if not file_exists(location):
        return {"found": False, "note": "The data file (tss:dataFile) was not found.", "text": None}

    if source["format"] == TSS.Parquet:
        try:
            columns, rows = parquet_head(location, lines - 1)  # lines - 1: the header line is a line too
        except duckdb.Error as error:
            raise HTTPException(status_code=400, detail=f"Could not preview Parquet file {location}: {error}") from error
        text = io.StringIO(newline="")
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)
        return {
            "found": True,
            "note": f"CSV preview of the first {len(rows)} Parquet rows; download contains the original Parquet file",
            "text": text.getvalue(),
        }
    if source["format"] != TSS.CSV:
        raise HTTPException(status_code=400, detail=f"A data preview is available for tss:CSV and tss:Parquet sources only, "
                                                    f"not {message_text(graph, source['format'])}.")

    first_lines = []
    for line in read_lines(location):
        first_lines.append(line)
        if len(first_lines) == lines:
            break

    if is_url(location):
        note = f"first {len(first_lines)} lines of the online file"
    else:
        size_mb = round((DEMO_DIR / location).stat().st_size / 1_000_000, 1)
        note = f"first {len(first_lines)} lines of a {size_mb} MB file"
    return {
        "found": True,
        "note": note,
        "text": "\n".join(first_lines),
    }


@api.get("/endpoints/{endpoint_id}/data/download")
def download_data_file(endpoint_id, request: Request = None):
    """Download the original data file (tss:dataFile), CSV or Parquet"""
    graph, source = load_source(endpoint_id, request)
    if source["embedded"] is True or source["dataFile"] is None:
        raise HTTPException(status_code=400, detail="This source has no data file to download.")
    media_type = "application/vnd.apache.parquet" if source["format"] == TSS.Parquet else "text/csv"
    return send_as_download(source["dataFile"], media_type)


@api.get("/endpoints/{endpoint_id}/rows")
def get_first_rows(endpoint_id, limit: int = 10, request: Request = None):
    """The first rows of the source's data, for the series inspector: a CSV file, a Parquet
    file or the embedded points (read as RDF). One column per field that has a column in the
    data, named by the field's IRI; each cell is the text of the value, or None (no value)."""
    graph, source = load_source(endpoint_id, request)
    limit = max(1, min(limit, 1000))
    try:
        columns, rows = first_rows(graph, source, limit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    column_iris = []
    for field in columns:
        column_iris.append(str(field))
    return {"columns": column_iris, "rows": rows}


@api.get("/endpoints/{endpoint_id}/filter-columns")
def filter_columns(endpoint_id, field, request: Request = None):
    """The other columns the query form can filter on (like a quality code),
    with the values that occur in them. Only columns with a few different values
    (2 to 30) are useful for that, so only those are sent."""
    graph, source = load_source(endpoint_id, request)
    chosen = find_field(source, field)

    # Start with every field except the time and this field, and drop a field
    # as soon as it has too many different values
    candidates = {}  # field (its IRI) -> the different values seen so far
    for other in source["fields"]:
        if other["columnName"] is None or other["isTime"] or other["node"] == chosen["node"]:
            continue
        candidates[other["node"]] = set()

    try:
        for row in data_rows(graph, source, only=set(candidates)):
            for node in list(candidates):  # list(...): we may delete from candidates inside the loop
                candidates[node].add(value_text(row, node))
                if len(candidates[node]) > 30:
                    del candidates[node]
            if len(candidates) == 0:
                break
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    columns = []
    for other in source["fields"]:
        values = candidates.get(other["node"])
        if values is not None and len(values) >= 2:  # a column with one value is no use as a filter
            columns.append({"field": str(other["node"]), "values": sorted(values)})
    return columns


@api.get("/endpoints/{endpoint_id}/query")
def query_series(endpoint_id, field, start=None, end=None, min_value=None, max_value=None,
                 filter_field: Annotated[str | None, Query(alias="filterField")] = None,
                 filter_value=None, resample="", aggregate="mean", limit: int = 100, request: Request = None):
    """Get the readings of one field (one time series), with the filters of the query form,
    plus summary numbers and the gaps in the data.
    field and filter_field (called filterField in the address) are field IRIs.

    The source tells us where the data is (tss:dataFile or the embedded tss:points),
    how to read it (tss:format, and for CSV tss:delimiter, tss:header, tss:encoding),
    which field holds the time (tss:timeField) and which time window it covers
    (tss:from, tss:until). The field tells us its own column (tss:columnName) and
    its datatype (tss:datatype): only numeric fields have value ranges and mean/min/max."""
    graph, source = load_source(endpoint_id, request)
    chosen = find_field(source, field)
    limit = max(1, min(limit, 5000))  # don't send huge answers to the browser

    # 1. What the form asks for
    start_time = parse_time(start) if start else None
    end_time = parse_time(end) if end else None
    low = parse_number(min_value, "The lowest value")
    high = parse_number(max_value, "The highest value")
    if resample not in ["", "hour", "day"]:
        raise HTTPException(status_code=400, detail="resample must be empty, 'hour' or 'day'")
    if aggregate not in ["mean", "min", "max"]:
        raise HTTPException(status_code=400, detail="aggregate must be 'mean', 'min' or 'max'")

    # 2. The fields to read: the time field, this field, and the field to filter on
    time_field = None
    for other in source["fields"]:
        if other["isTime"]:
            time_field = other
    if time_field is None:
        raise HTTPException(status_code=400, detail="The source has no tss:timeField (one of its fields), so the readings have no time.")
    if chosen["isTime"]:
        raise HTTPException(status_code=400, detail="This is the time field itself. Pick a field that holds values.")
    filtered = None  # the field to filter on (filterField), by its IRI
    if filter_field:
        for other in source["fields"]:
            if other["node"] == URIRef(filter_field):
                filtered = other
        if filtered is None:
            raise HTTPException(status_code=400, detail=f"filterField <{filter_field}> is not a field of this source")
        if filter_value is None:
            # (filter_value= with nothing after it is fine: it keeps the rows without a value there)
            raise HTTPException(status_code=400, detail="filterField needs a filter_value too")

    datatype = chosen["datatype"]
    numeric = is_numeric(datatype)
    if not numeric and (low is not None or high is not None or resample):
        holds = message_text(graph, datatype) if datatype is not None else "values without a tss:datatype"
        raise HTTPException(status_code=400, detail=f"Value ranges and mean/min/max aggregation need a numeric field. This field holds {holds}.")

    # 3. The source only covers its own time window: from is included, until is not
    window_start = window_moment(graph, source["node"], "from")
    window_end = window_moment(graph, source["node"], "until")

    # 4. Go through the data row by row
    moments = []  # the time of every row inside the time range: to find the gaps
    kept = []     # the readings that also pass the column filter and the value range
    needed_fields = [time_field, chosen]
    if filtered is not None:
        needed_fields.append(filtered)
    only = set()
    for needed in needed_fields:
        only.add(needed["node"])
    first = True
    try:
        for row in data_rows(graph, source, only):
            # The columns must be in the data file (an embedded point may leave out an empty value)
            if first and not source["embedded"]:
                for needed in needed_fields:
                    if needed["node"] not in row:
                        name = needed["columnName"] or message_text(graph, needed["node"])
                        raise HTTPException(status_code=400, detail=f"Column '{name}' is not in {source['dataFile']}")
            first = False

            # The time: an embedded point's literal (see literal_time), or the text of a cell (see data_time)
            time_value = row.get(time_field["node"], "")
            if time_value == "":
                raise HTTPException(status_code=400, detail=f"A row has no time in column '{time_field['columnName']}'")
            if isinstance(time_value, Literal):
                row_time = literal_time(time_value)
            else:
                row_time = data_time(time_value, time_field["datatype"])
            if row_time is None:
                kind = "an xsd:dateTime"
                if time_field["datatype"] == XSD.dateTimeStamp:
                    kind = "an xsd:dateTimeStamp (a date-time with a timezone)"
                raise HTTPException(status_code=400, detail=f"A row has {str(time_value)!r} in column '{time_field['columnName']}' "
                                                            f"(the tss:timeField), which is not {kind} like 2021-03-03T23:45:00Z")
            if window_start and row_time < window_start:
                continue
            if window_end and row_time >= window_end:
                continue
            if start_time and row_time < start_time:
                continue
            if end_time and row_time > end_time:
                continue
            moments.append(row_time)

            if filtered is not None and value_text(row, filtered["node"]) != filter_value:
                continue
            cell = row.get(chosen["node"], "")  # the text of a cell, or an embedded point's literal
            if numeric and isinstance(cell, Literal):
                value = literal_number(cell, datatype, graph)
            elif numeric:
                value = read_number(cell, datatype, graph)
            else:
                text = value_text(row, chosen["node"])
                value = text if text != "" else None
            if value is None and (low is not None or high is not None):
                continue  # with a value range, an empty cell can never match
            if low is not None and value < low:
                continue
            if high is not None and value > high:
                continue
            kept.append({"moment": row_time, "value": value})
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    # Files are usually in time order, but nothing guarantees it
    moments.sort()
    kept.sort(key=lambda reading: reading["moment"])

    # 5. The rows to show: every reading, or one value per hour or per day.
    #    Only the rows that are sent get their time written as text (that saves time on big files).
    if resample:
        rows = resample_readings(kept, resample, aggregate)
        total_rows = len(rows)
    else:
        rows = []
        for reading in kept[:limit]:
            rows.append({"time": time_text(reading["moment"]), "value": reading["value"]})
        total_rows = len(kept)

    if numeric:
        summary = summarize(kept)
    else:
        summary = {"count": 0, "empty": 0, "mean": None, "min": None, "max": None}
        for reading in kept:
            if reading["value"] is None:
                summary["empty"] += 1
            else:
                summary["count"] += 1

    # Only what was computed from the data, and what the request asked for: the browser
    # reads everything about the field and the source (label, unit, datatype, ...) from the RDF
    return {
        "resample": resample,
        "aggregate": aggregate,
        "filterValue": filter_value if filtered is not None else None,
        "summary": summary,
        "gaps": find_gaps(moments),   # over every reading in the time range
        "totalRows": total_rows,
        "limitReached": total_rows > limit,
        "readings": rows[:limit],     # [:limit] = only the first "limit" rows
    }


# ---------------------------------------------------------------------------
# The SQL box (DuckDB): read-only SQL on the data of an endpoint
# ---------------------------------------------------------------------------

def column_role(field):
    """What a field is, in one word, for the role column of the SQL table "fields":
    'time' (the source's tss:timeField), 'describes' (it has a tss:describesField, like a
    quality code), 'value' (it has a tss:observedProperty), or None"""
    if field["isTime"]:
        return "time"
    if field["describes"] is not None:
        return "describes"
    if field["observedProperty"] is not None:
        return "value"
    return None


def text_or_none(term):
    """An RDF term as text for a SQL table: an IRI in full, a literal's text. None stays None (NULL)."""
    if term is None:
        return None
    return str(term)


def sql_value(literal, column):
    """A value of an embedded point, for its column of the SQL table: the Python value rdflib
    reads from the literal (a float for xsd:double, a datetime for xsd:dateTime, ...), or the
    literal's text for a text column. None (NULL) when the point has no value."""
    if literal is None:
        return None
    if column["type"] == "VARCHAR":
        return str(literal)
    if literal.datatype in TIME_DATATYPES:
        # A time is read like /query reads it (see literal_time), also an xsd:dateTimeStamp,
        # which rdflib's toPython() leaves unread
        value = literal_time(literal)
        if value is None:
            raise ValueError(f"Column {column['name']} should be {sql_box.TYPE_WORDS[column['type']]}, "
                             f"but the data has: {literal}")
        return value
    value = literal.toPython()
    # rdflib gives the literal itself back when its text does not fit the datatype. It also
    # reads some texts that are not in the XSD form ("yes" as xsd:boolean is false, " 12 " as
    # xsd:double is 12.0), so the text is checked against the XSD form too, as SHACL does.
    form = DATA_FORMS.get(literal.datatype)
    if isinstance(value, Literal) or (form is not None and form.fullmatch(str(literal)) is None):
        raise ValueError(f"Column {column['name']} should be {sql_box.TYPE_WORDS[column['type']]}, "
                         f"but the data has: {literal}")
    return value


def sql_table(graph, source):
    """The SQL table of the source: its name, where its data is, and its columns
    (one per data column, typed by the field's tss:datatype; Parquet keeps its own types).
    "error" says why it can't be loaded."""
    by_column = {}  # column name -> its field
    for field in source["fields"]:
        if field["columnName"] is not None:
            by_column[field["columnName"]] = field

    table = {"name": "data", "kind": None, "columns": [], "error": None}
    names = []          # the columns, in the order of the data
    native_types = {}   # Parquet: column name -> its type in the file
    points = []         # embedded: the points, read as RDF (see point_rows)
    try:
        if source["embedded"]:
            # The table is named after the source, e.g. ex:zevenaarshaven-source -> zevenaarshaven_source
            table["name"] = sql_box.table_name(str(source["node"]).split("#")[-1].split("/")[-1])
            table["kind"] = "points"
            # One column per field with a column name (a key that is no field's is left out)
            for field in source["fields"]:
                if field["columnName"] is not None:
                    names.append(field["columnName"])
            points = point_rows(graph, source)
        else:
            location = source["dataFile"]
            if location is None:
                raise ValueError("The source has no tss:dataFile")
            table["name"] = sql_box.table_name(location)
            if not file_exists(location):
                raise ValueError(f"Data file not found: {location}")
            table["path"] = local_copy(location).as_posix()

            if source["format"] == TSS.Parquet:
                table["kind"] = "parquet"
                with duckdb.connect() as connection:
                    described = connection.execute(
                        "DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning = false)", [table["path"]]
                    ).fetchall()
                for column in described:
                    names.append(column[0])            # e.g. Timestamp
                    native_types[column[0]] = column[1]  # e.g. TIMESTAMP WITH TIME ZONE
            elif source["format"] == TSS.CSV:
                table["kind"] = "csv"
                check_csv_settings(source)
                table["delimiter"] = source["delimiter"]
                table["header"] = source["header"]
                first_line = next(csv.reader(read_lines(location), delimiter=table["delimiter"]), [])
                if table["header"]:
                    names = first_line
                else:
                    # No header line: a column gets the column name of the field with its tss:columnIndex
                    names = csv_header(source, first_line)
            else:
                raise ValueError(f"The SQL box can't read data in tss:format {message_text(graph, source['format'])}")
    except (ValueError, duckdb.Error) as error:
        table["error"] = str(error)
        return table

    # CSV: the XSD form each column's cells must have, by column name (None: any text). These are
    # the same checks /query makes (LEXICAL_FORMS), so " 12 " is refused here too (sql_box.column_expression).
    table["forms"] = {}
    for name in names:
        if names.count(name) > 1:
            table["error"] = f"Two columns are called '{name}'; a SQL table needs different column names."
            return table
        field = by_column.get(name)
        datatype = field["datatype"] if field is not None else None
        if table["kind"] == "parquet":
            sql_type = native_types[name]
        else:
            sql_type = sql_box.duckdb_type(datatype)
        # Each column says which field it holds (the field's IRI), or None when no field describes it
        field_iri = str(field["node"]) if field is not None else None
        table["columns"].append({"name": name, "type": sql_type, "field": field_iri})
        table["forms"][name] = LEXICAL_FORMS.get(datatype)

    # Embedded points: one row per point, one value per column (see sql_value)
    if table["kind"] == "points":
        table["rows"] = []
        try:
            for point in points:
                row = []
                for column in table["columns"]:
                    row.append(sql_value(point.get(by_column[column["name"]]["node"]), column))
                table["rows"].append(row)
        except ValueError as error:
            table["error"] = f"Could not load the embedded points: {error}"
    return table


@api.get("/endpoints/{endpoint_id}/sql/tables")
def sql_schema(endpoint_id, request: Request = None):
    """What the SQL box can query for this endpoint: the data table, the metadata tables, and example queries"""
    graph, source = load_source(endpoint_id, request)
    table = sql_table(graph, source)

    metadata_tables = []
    for name, metadata in sql_box.METADATA_TABLES.items():
        columns = []
        for column_name, sql_type in metadata["columns"]:
            columns.append({"name": column_name, "type": sql_type})
        metadata_tables.append({"name": name, "explanation": metadata["explanation"], "columns": columns})

    # Where the data is (tss:dataFile or tss:points) and its format, the browser reads from the RDF
    return {
        "tables": [{
            "name": table["name"],
            "error": table["error"],
            "columns": table["columns"],   # [{"name", "type", "field": <the field's IRI, or None>}]
        }],
        "metadataTables": metadata_tables,
        "examples": sql_box.example_queries(table if table["error"] is None else None, source["fields"]),
        "maxRows": sql_box.MAX_ROWS,
        "maxSeconds": sql_box.MAX_SECONDS,
    }


class SqlQuery(BaseModel):
    """What the browser sends: {"sql": "SELECT ..."} (in the body, because a query can be long)"""
    sql: str


@api.post("/endpoints/{endpoint_id}/sql")
def run_sql(endpoint_id, query: SqlQuery, request: Request = None):
    """Run one read-only SQL query with DuckDB on the data of this endpoint"""
    graph, source = load_source(endpoint_id, request)

    # 1. The data table. If it can't be made (e.g. the data file is missing), it is
    #    skipped: the metadata tables still work.
    table = sql_table(graph, source)
    tables = []
    skipped = []
    if table["error"] is None:
        tables.append(table)
    else:
        skipped.append({"name": table["name"], "error": table["error"]})
    loaded_name = table["name"] if table["error"] is None else None

    # 2. The TSS metadata, for the "source" and "fields" tables (see sql_box.METADATA_TABLES).
    #    IRIs are written in full, so a query does not depend on the prefixes of the file.
    node = source["node"]
    label = graph.value(node, RDFS.label)
    source_row = {
        "source": str(node),
        "label": str(label) if label is not None else endpoint_id,
        "sql_table": loaded_name,
        "format": text_or_none(source["format"]),
        "time_field": text_or_none(source["timeField"]),
        "from_time": window_moment(graph, node, "from"),    # a datetime, read by rdflib
        "until_time": window_moment(graph, node, "until"),
        "produced_by": text_or_none(graph.value(node, TSS.producedBy)),
        "mutable": boolean_value(graph, node, TSS.mutable),
    }
    field_rows = []
    for field in source["fields"]:
        field_rows.append({
            "column_name": field["columnName"],
            "sql_table": loaded_name,
            "field": str(field["node"]),
            "label": text_or_none(field["label"]),
            "role": column_role(field),
            "datatype": text_or_none(field["datatype"]),
            "unit": text_or_none(field["unit"]),
            "observed_property": text_or_none(field["observedProperty"]),
            "sensor": text_or_none(graph.value(field["node"], TSS.sensor)),
            "feature_of_interest": text_or_none(graph.value(field["node"], TSS.featureOfInterest)),
            "describes": text_or_none(field["describes"]),
        })

    # 3. Load, lock, run, throw away
    try:
        con, not_loaded = sql_box.open_database(tables, {"source": [source_row], "fields": field_rows})
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    skipped = skipped + not_loaded  # a missing data file + data that could not be loaded
    try:
        result = sql_box.run_query(con, query.sql)
        result["skipped"] = skipped  # the tables that could not be loaded, and why
        return result
    except (ValueError, sql_box.duckdb.Error) as error:
        # a mistake in the query: show DuckDB's own message, it says what is wrong.
        # If the data table was not loaded, say so too: that may be why the query fails.
        message = str(error)
        for skipped_table in skipped:
            message += f"\n(Table {skipped_table['name']} was not loaded: {skipped_table['error']})"
        raise HTTPException(status_code=400, detail=message)
    finally:
        con.close()

# Add all the routes of the api router to the app (must come after all of them)
app.include_router(api)

# The RDF documents as plain files, for the browser to fetch and parse (frontend/src/rdf.js):
#   /files/metadata%20endpoint/<id>.ttl   the local metadata endpoints (the catalogue lists them)
#   /files/SHACL/<name>.ttl               the SHACL shapes
# The data/ folder is not served: some of its data may not be shared (the factory data);
# the /api data routes read it. These two must come before the "/" below, which would
# otherwise answer every address that is not an /api route.
app.mount(METADATA_FILES_PATH, StaticFiles(directory=METADATA_DIR), name="metadata-endpoints")
app.mount(SHACL_FILES_PATH, StaticFiles(directory=SHACL_DIR), name="shacl-shapes")

# The built frontend (npm run build) is in frontend/dist. Every address that is not
# an /api route or one of the files above is looked up as a file in there, and "/" gives index.html.
# Not built yet? Then we skip this, and you use the dashboard through npm run dev.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
