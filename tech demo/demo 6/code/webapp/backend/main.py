from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from rdflib import Graph, URIRef, Literal, RDF, RDFS
from rdflib.namespace import DCTERMS, XSD
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import url2pathname
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import requests
import tempfile
import csv
import io
import duckdb
import json
import time
import math
from collections import Counter
from uuid import uuid4
from pyshacl import validate as shacl_validate

# The TSS2 inheritance rule (snippet <- group template) lives in tss2.py
from tss2 import TSS, SOSA, QUDT
from tss2 import value_of, find_group, resolve, resolve_with_source

# The SQL box (DuckDB) lives in sql_box.py
import sql_box
import rdf_generation
import csv_rdf_generation
import parquet_rdf_generation


# This file lives in demo 3/code/webapp/backend/, so demo 3/ is three folders up
DEMO_DIR = Path(__file__).resolve().parents[3]
METADATA_DIR = DEMO_DIR / "metadata endpoint"
CSV_FILE_TYPE = URIRef("http://publications.europa.eu/resource/authority/file-type/CSV")
PARQUET_FILE_TYPE = URIRef("http://publications.europa.eu/resource/authority/file-type/PARQUET")

# The online endpoints the user adds are remembered in this file
ONLINE_ENDPOINTS_FILE = Path(__file__).parent / "online_endpoints.json"

# Full external RDF exports are streamed to temporary files; previews stay small.
RDF_EXPORT_DIRECTORY = tempfile.TemporaryDirectory(prefix="rdf-observations-")
RDF_EXPORTS = {}

# Readable names for IRIs that have no rdfs:label in our files
READABLE_NAMES = {
    "http://qudt.org/vocab/unit/MicroS-PER-CentiM": "µS/cm",
    "http://qudt.org/vocab/quantitykind/ElectricConductivity": "Electrical conductivity",
}

# IRIs that start like this open a real web page, so the series inspector links to them
LINKABLE_VOCABULARIES = {
    "http://qudt.org/": "QUDT",
    "http://www.w3.org/ns/sosa/": "SOSA",
}

# The tss:datatype values that make a field numeric: only those fields have
# value ranges and mean/min/max in the query form
INTEGER_DATATYPES = [
    XSD.integer, XSD.int, XSD.long, XSD.short, XSD.byte,
    XSD.nonNegativeInteger, XSD.positiveInteger, XSD.nonPositiveInteger, XSD.negativeInteger,
    XSD.unsignedLong, XSD.unsignedInt, XSD.unsignedShort, XSD.unsignedByte,
]
DECIMAL_DATATYPES = [XSD.decimal, XSD.double, XSD.float]

# The series inspector groups the lines about a field by meaning. Each line is
# (the name the inspector shows, the property, where it is stated):
# "field" = on the field itself, "source" = on its source, so true for every field of that source.
# Data binding gets more lines in series_metadata (data file or points, time column, key).
FIELD_SECTIONS = [
    {
        "title": "Data binding",
        "explanation": "Where the values are: the data, and the column that holds this field.",
        "properties": [
            ("format", TSS["format"], "source"),
            ("column", TSS.columnName, "field"),
            ("column index", TSS.columnIndex, "field"),
        ],
    },
    {
        "title": "Time window",
        "explanation": "The stretch of time the source covers: from is included, until is not.",
        "properties": [("from", TSS["from"], "source"), ("until", TSS["until"], "source")],
    },
    {
        "title": "Values",
        "explanation": "How to read each value of this field, and which values are allowed.",
        "properties": [
            ("datatype", TSS.datatype, "field"),
            ("language", TSS.language, "field"),
            ("required", TSS.required, "field"),
            ("minimum", TSS.minimum, "field"),
            ("maximum", TSS.maximum, "field"),
        ],
    },
    {
        "title": "What is measured",
        "explanation": "What each value of this field is an observation of.",
        "properties": [
            ("observes", TSS.observedProperty, "field"),
            ("unit", TSS.unit, "field"),
            ("made by", TSS.sensor, "field"),
            ("feature", TSS.featureOfInterest, "field"),
        ],
    },
    {
        "title": "Provenance",
        "explanation": "Who produced the data, and whether it can still change.",
        "properties": [("produced by", TSS.producedBy, "source"), ("mutable", TSS.mutable, "source")],
    },
]


# ---------------------------------------------------------------------------
# SPARQL queries
# ---------------------------------------------------------------------------

# The source: one data set (a data file, or points embedded in the Turtle).
# Its columns are tss:Field nodes, linked with tss:hasField; get_endpoint_info reads those.
# In this demo every metadata endpoint holds exactly one source.
SOURCE_QUERY = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX tss:  <https://w3id.org/tss#>

SELECT ?source ?label ?embedded ?format ?dataFile ?mutable ?producedBy
       ?fromTime ?untilTime ?timeField ?pointLayout ?delimiter ?header ?encoding
WHERE {
  ?source a tss:Source .
  OPTIONAL { ?source rdfs:label ?label }
  OPTIONAL { ?source tss:embedded ?embedded }
  OPTIONAL { ?source tss:format ?format }
  OPTIONAL { ?source tss:dataFile ?dataFile }
  OPTIONAL { ?source tss:mutable ?mutable }
  OPTIONAL { ?source tss:producedBy ?producedBy }
  OPTIONAL { ?source tss:from ?fromTime }
  OPTIONAL { ?source tss:until ?untilTime }
  OPTIONAL { ?source tss:timeField ?timeField }
  OPTIONAL { ?source tss:pointLayout ?pointLayout }
  OPTIONAL { ?source tss:delimiter ?delimiter }
  OPTIONAL { ?source tss:header ?header }
  OPTIONAL { ?source tss:encoding ?encoding }
}
"""

# The source properties the overview shows in their own place.
# Every other property of the source is listed under "Other statements".
# (tss:points is left out on purpose: it can be very long.)
# TSS["format"], not TSS.format: TSS is a text, and text already has a .format
SOURCE_PROPERTIES = [
    RDF.type, RDFS.label, TSS.embedded, TSS["format"], TSS.dataFile, TSS.mutable, TSS.producedBy,
    TSS["from"], TSS.until, TSS.timeField, TSS.key, TSS.hasField,
    TSS.pointLayout, TSS.points, TSS.delimiter, TSS.header, TSS.encoding,
]


app = FastAPI()


# If a route crashes, answer with the error message instead of a bare
# "500 Internal Server Error". A bare 500 has no CORS header, so the browser
# would only say "Failed to fetch" and the page would claim the backend is not running.
# (This must come before add_middleware(CORSMiddleware...) so CORS also covers these answers.)
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
# A file "location" is either a path inside the demo 3 folder (e.g. "data/x.csv")
# or a web address (e.g. "https://example.org/data/x.csv").
# The functions below work for both, so the routes don't have to care which one it is.

def is_url(location):
    return location.startswith("http://") or location.startswith("https://")


def download(url, stream=False):
    """GET an online file. A network problem becomes a clear error message.
    stream=True: don't download everything at once (for big CSV files)."""
    try:
        response = requests.get(url, stream=stream, timeout=30)
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


def read_text(location):
    """The whole text of a (small) file"""
    if is_url(location):
        return download(location).text
    return (DEMO_DIR / location).read_text(encoding="utf-8")


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
        target = Path(tempfile.mkdtemp(prefix="tss2-")) / "data.csv"
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
# Metadata endpoints: the local .ttl files, and the online ones the user added
# ---------------------------------------------------------------------------

def local_endpoint_ids():
    """The local metadata endpoints are the .ttl files in the 'metadata endpoint' folder.
    The id of an endpoint is its file name without .ttl"""
    ids = []
    for ttl_file in sorted(METADATA_DIR.glob("*.ttl")):
        ids.append(ttl_file.stem)
    return ids


def load_online_endpoints():
    """The online endpoints the user added, e.g. {"online-1": "https://.../x_tss2.ttl"}.
    They are kept in a small JSON file, so they are still there after a restart."""
    if not ONLINE_ENDPOINTS_FILE.exists():
        return {}
    return json.loads(ONLINE_ENDPOINTS_FILE.read_text(encoding="utf-8"))


def save_online_endpoints(online):
    ONLINE_ENDPOINTS_FILE.write_text(json.dumps(online, indent=2), encoding="utf-8")


def endpoint_file(endpoint_id):
    """Where an endpoint's Turtle file is: a web address, or a path inside demo 3"""
    online = load_online_endpoints()
    if endpoint_id in online:
        return online[endpoint_id]
    # Only accept ids we know, so nobody can ask for some other file on disk
    if endpoint_id in local_endpoint_ids():
        return f"metadata endpoint/{endpoint_id}.ttl"
    raise HTTPException(status_code=404, detail=f"Unknown metadata endpoint: {endpoint_id}")


def endpoint_base(endpoint_id):
    """The address that relative IRIs in the endpoint (like <../data/x.csv>) are read
    against: the web address of an online endpoint, or the file:/// address of a local one"""
    location = endpoint_file(endpoint_id)
    if is_url(location):
        return location
    return (DEMO_DIR / location).as_uri()


def load_graph(endpoint_id):
    location = endpoint_file(endpoint_id)
    text = read_text(location)
    graph = Graph()
    try:
        graph.parse(data=text, format="turtle", publicID=endpoint_base(endpoint_id))
    except Exception as error:
        # For example a typo in the Turtle
        raise HTTPException(status_code=500, detail=f"Could not read {location}: {error}")
    return graph


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def show(graph, value):
    """Make an RDF value readable: IRIs become prefix:name, e.g. sosa:Observation"""
    if value is None:
        return None
    if isinstance(value, URIRef):
        return graph.namespace_manager.normalizeUri(value)
    return str(value)


def iri_to_location(iri):
    """Where a file is. rdflib read <../data/x.csv> against the endpoint's own address, so:
    - a local endpoint gives  file:///C:/.../demo%203/data/x.csv  -> we keep "data/x.csv"
    - an online endpoint gives https://.../data/x.csv             -> we keep the web address"""
    if iri is None:
        return None
    iri = str(iri)
    if is_url(iri):
        return iri
    if iri.startswith("file:"):
        path = Path(url2pathname(urlparse(iri).path))
        try:
            return path.relative_to(DEMO_DIR).as_posix()
        except ValueError:
            pass  # a file outside the demo 3 folder: not allowed, see below
    raise HTTPException(status_code=500, detail=f"{iri} is not a web address or a file inside the demo 3 folder")


def parse_time(text):
    """Read an ISO date-time such as 2021-03-03T23:45:00Z.
    A time without a timezone (like the ones the query form sends) is taken as UTC."""
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Not a valid date-time: '{text}' (expected something like 2021-03-04T00:00)")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def load_csvw_json(described_by):
    """The CSVW file as a Python dict. Raises ValueError when it can't be used."""
    try:
        metadata = json.loads(read_text(described_by))
    except json.JSONDecodeError as error:
        raise ValueError(f"The CSVW file {described_by} is not valid JSON: {error}")
    schema = metadata.get("tableSchema") if isinstance(metadata, dict) else None
    if not isinstance(schema, dict) or not isinstance(schema.get("columns"), list):
        raise ValueError(f"The CSVW file {described_by} has no tableSchema with a list of columns in it")
    for column in schema["columns"]:
        if not isinstance(column, dict) or "name" not in column:
            raise ValueError(f"The CSVW file {described_by} has a column without a name")
    csvw_delimiter(metadata, described_by)
    return metadata


def csvw_delimiter(metadata, described_by):
    """Use the declared delimiter, with the CSVW comma default when omitted."""
    return csv_rdf_generation.csvw_delimiter(metadata, described_by)


def column_name(graph, field):
    """The column a field reads, e.g. "Timestamp". A field without tss:columnName shows its IRI."""
    name = graph.value(field, TSS.columnName)
    if name is not None:
        return str(name)
    return show(graph, field)


def field_info(graph, field, time_fields, key_fields):
    """What the dashboard shows about one field (one column of the source)"""
    index = graph.value(field, TSS.columnIndex)
    unit = graph.value(field, TSS.unit)
    describes = graph.value(field, TSS.describesField)

    # A field can have more than one rdfs:comment: keep them all
    comments = []
    for comment in graph.objects(field, RDFS.comment):
        comments.append(str(comment))

    return {
        "id": str(field),
        "iri": show(graph, field),
        "columnName": show(graph, graph.value(field, TSS.columnName)),
        "columnIndex": int(index) if index is not None and str(index).isdigit() else None,
        "label": show(graph, graph.value(field, RDFS.label)),
        "comments": comments,
        "datatype": show(graph, graph.value(field, TSS.datatype)),
        "language": show(graph, graph.value(field, TSS.language)),
        "required": show(graph, graph.value(field, TSS.required)),
        "minimum": show(graph, graph.value(field, TSS.minimum)),
        "maximum": show(graph, graph.value(field, TSS.maximum)),
        "observedProperty": show(graph, graph.value(field, TSS.observedProperty)),
        "unit": READABLE_NAMES.get(str(unit), show(graph, unit)),
        "sensor": show(graph, graph.value(field, TSS.sensor)),
        "featureOfInterest": show(graph, graph.value(field, TSS.featureOfInterest)),
        # The column of the field this one says something about, e.g. "Value"
        "describesField": column_name(graph, describes) if describes is not None else None,
        "isTime": field in time_fields,
        "isKey": field in key_fields,
    }


def field_order(field):
    """How the fields are sorted: by tss:columnIndex when they have one,
    otherwise the time field first and then the rest by column name"""
    if field["columnIndex"] is not None:
        return (0, field["columnIndex"], "")
    if field["isTime"]:
        return (1, 0, "")
    return (2, 0, field["columnName"] or "")


def get_endpoint_info(endpoint_id):
    """Everything the dashboard needs to know about one metadata endpoint"""
    graph = load_graph(endpoint_id)

    rows = list(graph.query(SOURCE_QUERY))
    if len(rows) == 0:
        raise HTTPException(status_code=404, detail=f"No tss:Source found in {endpoint_file(endpoint_id)}")
    row = rows[0]
    source = row.source

    time_fields = list(graph.objects(source, TSS.timeField))
    key_fields = list(graph.objects(source, TSS.key))
    key_names = []
    for field in key_fields:
        key_names.append(column_name(graph, field))

    info = {
        "id": endpoint_id,
        "file": endpoint_file(endpoint_id),
        "online": is_url(endpoint_file(endpoint_id)),
        "label": str(row.label) if row.label is not None else endpoint_id,
        # the source: where the data is and how it changes
        "source": show(graph, source),
        "embedded": embedded_mode(graph, source),
        "format": show(graph, row.format),
        "dataFile": iri_to_location(row.dataFile),
        "mutable": show(graph, row.mutable),
        "producedBy": show(graph, row.producedBy),
        "from": show(graph, row.fromTime),
        "until": show(graph, row.untilTime),
        "timeField": column_name(graph, row.timeField) if row.timeField is not None else None,
        "key": key_names,
        # how to read the data: CSV settings, or the layout of embedded points
        "delimiter": show(graph, row.delimiter),
        "header": show(graph, row.header),
        "encoding": show(graph, row.encoding),
        "pointLayout": show(graph, row.pointLayout),
        # filled in below
        "fields": [],
        "otherProperties": [],
    }

    for field in graph.objects(source, TSS.hasField):
        info["fields"].append(field_info(graph, field, time_fields, key_fields))
    info["fields"].sort(key=field_order)

    # Everything else that is said about the source, e.g. its licence or provenance
    for prop, value in graph.predicate_objects(source):
        if prop in SOURCE_PROPERTIES:
            continue
        info["otherProperties"].append({"property": show(graph, prop), "value": describe(graph, value)})
    info["otherProperties"].sort(key=lambda line: line["property"])

    return info


def embedded_mode(graph, group):
    """Require an explicit boolean, rather than guessing a storage profile."""
    values = list(graph.objects(group, TSS.embedded)) if group is not None else []
    if len(values) != 1 or not isinstance(values[0], Literal) or not isinstance(values[0].toPython(), bool):
        return None
    return values[0].toPython()


@api.post("/endpoints/{endpoint_id}/rdf")
def generate_rdf(endpoint_id):
    """Generate standalone Turtle readings from embedded points, CSV or Parquet."""
    graph = load_graph(endpoint_id)
    try:
        groups = list(graph.subjects(RDF.type, TSS.SnippetGroup))
        modes = {embedded_mode(graph, group) for group in groups}
        if not groups or None in modes or len(modes) != 1:
            raise ValueError("Every group must state the same boolean tss:embedded storage profile.")
        if modes == {True}:
            result = rdf_generation.generate(graph, endpoint_base(endpoint_id))
        else:
            formats = {parquet_rdf_generation.data_format(graph, group) for group in groups}
            if len(formats) != 1 or None in formats:
                raise ValueError("RDF generation requires all external groups to use CSV or all to use Parquet, with a supported dcterms:format.")
            parquet = formats == {"parquet"}
            token = uuid4().hex
            target = Path(RDF_EXPORT_DIRECTORY.name) / f"{token}.ttl"

            def load_files(group):
                data_iri = rdf_generation.single(graph, group, TSS.dataFile, required=True)
                if not isinstance(data_iri, URIRef):
                    raise ValueError("tss:dataFile must be a file IRI.")
                data_location = iri_to_location(data_iri)
                if not file_exists(data_location):
                    raise ValueError(f"Data file not found: {data_location}")
                if parquet:
                    return local_copy(data_location), data_iri
                csvw_iri = rdf_generation.single(graph, group, TSS.describedby, required=True)
                if not isinstance(csvw_iri, URIRef):
                    raise ValueError("tss:describedby must be a file IRI.")
                csvw_location = iri_to_location(csvw_iri)
                metadata = load_csvw_json(csvw_location)
                return metadata, local_copy(data_location), data_iri

            try:
                generator = parquet_rdf_generation.generate if parquet else csv_rdf_generation.generate
                result = generator(graph, load_files, target)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            RDF_EXPORTS[token] = (endpoint_id, target)
            result["downloadUrl"] = f"/api/endpoints/{endpoint_id}/rdf/download/{token}"
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    result["filename"] = "observations.ttl"
    return result


@api.get("/endpoints/{endpoint_id}/rdf/download/{token}")
def download_generated_rdf(endpoint_id, token):
    artifact = RDF_EXPORTS.get(token)
    if artifact is None or artifact[0] != endpoint_id or not artifact[1].is_file():
        raise HTTPException(status_code=404, detail="This generated RDF download is unavailable. Generate it again.")
    return FileResponse(artifact[1], media_type="text/turtle", filename="observations.ttl")


@api.get("/endpoints/{endpoint_id}/shacl")
def check_shacl(endpoint_id):
    """Select embedded, external CSV, or external non-CSV shapes from the TSS."""
    graph = load_graph(endpoint_id)
    groups = list(graph.subjects(RDF.type, TSS.SnippetGroup))
    modes = {embedded_mode(graph, group) for group in groups}
    if not groups or None in modes or len(modes) != 1:
        raise HTTPException(status_code=400, detail="Cannot select SHACL shapes: every SnippetGroup must state one boolean tss:embedded, and all groups must use the same profile.")
    embedded = modes.pop()
    if embedded:
        name = "embedded_shapes.ttl"
    else:
        csv_modes = {list(graph.objects(group, DCTERMS.format)) == [CSV_FILE_TYPE] for group in groups}
        if len(csv_modes) != 1:
            raise HTTPException(status_code=400, detail="Cannot select SHACL shapes: external groups must all use the CSV profile or all use the non-CSV profile. Upload custom shapes for mixed profiles.")
        name = "external_csv_shapes.ttl" if csv_modes.pop() else "external_non_csv_shapes.ttl"
    shapes = Graph().parse(DEMO_DIR / "SHACL" / name, format="turtle")
    return shacl_report(graph, shapes, name, embedded)


def shacl_report(graph, shapes, name, embedded, custom=False):
    """Use the same report format for bundled and user-provided shapes."""
    started = time.monotonic()
    try:
        conforms, report, report_text = shacl_validate(graph, shacl_graph=shapes, allow_warnings=True)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not validate with {name}: {error}")
    if not isinstance(report, Graph):
        raise HTTPException(status_code=400, detail=f"Could not validate with {name}: {report}")
    sh = URIRef("http://www.w3.org/ns/shacl#ValidationResult")
    severity = URIRef("http://www.w3.org/ns/shacl#resultSeverity")
    counts = Counter(str(report.value(result, severity)).rsplit("#", 1)[-1]
                     for result in report.subjects(RDF.type, sh))
    return {"conforms": bool(conforms), "embedded": embedded, "shapes": name, "custom": custom,
            "seconds": round(time.monotonic() - started, 2), "report": report_text,
            "violations": counts["Violation"], "warnings": counts["Warning"], "infos": counts["Info"]}


class CustomShacl(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    turtle: str = Field(min_length=1, max_length=5_000_000)


@api.post("/endpoints/{endpoint_id}/shacl")
def check_custom_shacl(endpoint_id, upload: CustomShacl):
    """Validate with uploaded Turtle shapes, without replacing bundled files."""
    graph = load_graph(endpoint_id)
    name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
    if len(upload.turtle.encode("utf-8")) > 5_000_000:
        raise HTTPException(status_code=413, detail="The shapes file must be at most 5 MB.")
    shapes = Graph()
    try:
        shapes.parse(data=upload.turtle.lstrip("\ufeff"), format="turtle", publicID="https://example.org/uploaded-shapes/")
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not read {name} as Turtle: {error}")
    sh = "http://www.w3.org/ns/shacl#"
    has_shapes = any((None, RDF.type, URIRef(sh + kind)) in shapes for kind in ("NodeShape", "PropertyShape")) or any(
        (None, URIRef(sh + prop), None) in shapes
        for prop in ("targetClass", "targetNode", "targetSubjectsOf", "targetObjectsOf", "property", "path")
    )
    if not has_shapes:
        raise HTTPException(status_code=400, detail=f"{name} contains no SHACL shapes or targets.")
    groups = list(graph.subjects(RDF.type, TSS.SnippetGroup))
    modes = {embedded_mode(graph, group) for group in groups}
    embedded = modes.pop() if len(modes) == 1 else None
    return shacl_report(graph, shapes, name, embedded, custom=True)


# ---------------------------------------------------------------------------
# Helper functions for the series inspector
# ---------------------------------------------------------------------------

def describe(graph, value):
    """How the series inspector shows one RDF value: readable text first,
    the exact RDF when you hover over it, and a link if the IRI has a web page"""
    if value is None:
        return None
    result = {"text": str(value), "hover": str(value), "link": None, "linkName": None}

    if isinstance(value, URIRef):
        label = graph.value(value, RDFS.label)
        if label is not None:
            result["text"] = str(label)
        elif str(value) in READABLE_NAMES:
            result["text"] = READABLE_NAMES[str(value)]
        else:
            result["text"] = show(graph, value)  # e.g. ex:sensor-grootdok-ec20
        for start, name in LINKABLE_VOCABULARIES.items():
            if str(value).startswith(start):
                result["link"] = str(value)
                result["linkName"] = name

    elif isinstance(value, Literal):
        result["hover"] = value.n3(graph.namespace_manager)  # e.g. "datetime" or "2025-01-01T00:00:00Z"^^xsd:dateTime
        moment = value.toPython()
        if isinstance(moment, datetime):
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            result["text"] = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    else:
        # A blank node: show its rdfs:label when it has one, e.g. a prov:Activity
        label = graph.value(value, RDFS.label)
        result["text"] = str(label) if label is not None else "[ ] (a blank node)"

    return result


def make_line(graph, name, property_name, value, where):
    """One line of the series inspector"""
    return {
        "name": name,               # what the inspector calls it, e.g. "time column"
        "property": property_name,  # the RDF property, e.g. tss:timeField
        "where": where if value is not None else "missing",  # "field", "source" or "missing"
        "value": describe(graph, value),
    }


def mark_turtle(text, marks):
    """Split a Turtle file into lines and mark the statements we care about.
    marks maps the first word of a statement (e.g. "ex:factory-snippet") to a
    mark (e.g. "group"). A statement runs until the line that ends with "."."""
    lines = []
    mark = None
    at_statement_start = True
    for line in text.splitlines():
        code = line.strip()
        if code.startswith("#"):
            code = ""                        # a comment line
        code = code.split(" #")[0].strip()   # drop a comment at the end of a line

        if at_statement_start and code != "":
            mark = marks.get(code.split()[0])
            at_statement_start = False

        lines.append({"text": line, "mark": mark})

        if code.endswith("."):
            mark = None
            at_statement_start = True
    return lines


def read_points(graph, source):
    """The JSON points embedded in a source (tss:points), as a list of objects.
    Each object is one row; its keys are the column names of the fields (tss:pointLayout tss:Object).
    Raises ValueError with the reason when they can't be used."""
    try:
        points = json.loads(str(graph.value(source, TSS.points)))
    except (ValueError, TypeError):
        raise ValueError("tss:points does not hold valid JSON")
    if not isinstance(points, list):
        raise ValueError("tss:points must be a JSON array")
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("Every point in tss:points must be a JSON object (tss:pointLayout tss:Object)")
    return points


def cell_text(value):
    """One cell of a Parquet file as text, like it would be in a CSV file ("" when empty)"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return sql_box.to_json_value(value)  # e.g. 2021-03-03T23:15:00Z
    return str(value)


def data_rows(graph, info, source):
    """Every row of a source's data, one at a time, as a dict: column name -> the cell as text.
    Works for a CSV file, a Parquet file and JSON points embedded in the Turtle.
    Raises ValueError with the reason when the data can't be read.
    (A function with "yield" hands out its rows one by one, so a big file never has to fit in memory.)"""
    if info["embedded"]:
        for point in read_points(graph, source):
            row = {}
            for key, value in point.items():
                row[key] = "" if value is None else str(value)
            yield row
        return

    location = info["dataFile"]
    if location is None:
        raise ValueError("The source has no tss:dataFile")
    if not file_exists(location):
        raise ValueError(f"Data file not found: {location}")

    if info["format"] == "tss:Parquet":
        try:
            with duckdb.connect() as connection:
                result = connection.execute(
                    "SELECT * FROM read_parquet(?, hive_partitioning = false)", [str(local_copy(location))]
                )
                names = [column[0] for column in result.description]
                while True:
                    batch = result.fetchmany(10000)  # 10000 rows at a time
                    if len(batch) == 0:
                        break
                    for found in batch:
                        row = {}
                        for index in range(len(names)):
                            row[names[index]] = cell_text(found[index])
                        yield row
        except duckdb.Error as error:
            raise ValueError(f"Could not read the Parquet file {location}: {error}")
        return

    if info["format"] == "tss:CSV":
        delimiter = info["delimiter"] if info["delimiter"] is not None else ","
        if len(delimiter) != 1:
            raise ValueError(f"tss:delimiter must be one character, not '{delimiter}'")
        if info["encoding"] is not None and info["encoding"].lower() not in ("utf-8", "utf8"):
            raise ValueError(f"This dashboard only reads UTF-8 files, not tss:encoding '{info['encoding']}'")

        # The name of each column: from the header line, or, without one (tss:header false),
        # the column name of the field with that tss:columnIndex
        reader = csv.reader(read_lines(location), delimiter=delimiter)
        names = {}
        if info["header"] != "false":
            header = next(reader, [])
            for index in range(len(header)):
                names[index] = header[index]
        else:
            for field in info["fields"]:
                if field["columnIndex"] is not None and field["columnName"] is not None:
                    names[field["columnIndex"]] = field["columnName"]

        for cells in reader:
            row = {}
            for index in range(len(cells)):
                row[names.get(index, f"column {index}")] = cells[index]
            yield row
        return

    raise ValueError(f"The dashboard can't read data in format {info['format']}")


def first_rows(graph, info, source, count=10):
    """The first rows of a source's data, as (headers, rows): the column names,
    and each row as a list of texts in that order.
    Raises ValueError with the reason when the data can't be read."""
    found = []
    for row in data_rows(graph, info, source):
        found.append(row)
        if len(found) == count:
            break

    # The order of the keys in a JSON object means nothing, so embedded points
    # show the fields' column names first
    headers = []
    if info["embedded"]:
        for field in info["fields"]:
            if field["columnName"] is not None:
                headers.append(field["columnName"])
    for row in found:
        for name in row:
            if name not in headers:
                headers.append(name)

    rows = []
    for row in found:
        cells = []
        for header in headers:
            cells.append(row.get(header, ""))
        rows.append(cells)
    return headers, rows


def column_positions(fields, headers):
    """Where each field is in the rows: field id -> column position, found by its column name"""
    positions = {}
    for field in fields:
        if field["columnName"] in headers:
            positions[field["id"]] = headers.index(field["columnName"])
    return positions


def find_field(info, field_id):
    """The field (one column of the source) with this IRI, from get_endpoint_info"""
    for field in info["fields"]:
        if field["id"] == field_id:
            return field
    raise HTTPException(status_code=404, detail=f"Unknown field: {field_id}")


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
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    """Count, mean, min and max of the readings (empty cells are counted apart)"""
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
            lowest = {"value": value, "time": reading["time"]}
        if highest is None or value > highest["value"]:
            highest = {"value": value, "time": reading["time"]}

    return {
        "count": count,
        "empty": empty,
        "mean": round(total / count, 3) if count > 0 else None,
        "min": lowest,
        "max": highest,
    }


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
# API routes
# ---------------------------------------------------------------------------

@api.get("/")
def hello():
    return {"message": "Hello from the demo 3 backend"}


@api.get("/endpoints")
def list_endpoints():
    """The list for the drop down: the local endpoints first, then the online ones"""
    online = load_online_endpoints()
    endpoints = []
    for endpoint_id in local_endpoint_ids() + list(online):
        # One broken endpoint should not hide the others
        try:
            label = get_endpoint_info(endpoint_id)["label"]
        except HTTPException as error:
            label = f"{endpoint_id} (can't be read: {error.detail})"
        endpoints.append({
            "id": endpoint_id,
            "label": label,
            "online": endpoint_id in online,
            "file": endpoint_file(endpoint_id),
        })
    return endpoints


@api.post("/endpoints/online")
def add_online_endpoint(url):
    """Add an online TSS2 metadata endpoint: the web address of a Turtle file.
    From then on it works like the local ones."""
    url = url.strip()
    if not is_url(url):
        raise HTTPException(status_code=400, detail="The address must start with http:// or https://")

    online = load_online_endpoints()
    for endpoint_id, known_url in online.items():
        if known_url == url:
            raise HTTPException(status_code=400, detail=f"This endpoint is already in the list ({endpoint_id})")

    # Give it the first free id: online-1, online-2, ...
    number = 1
    while f"online-{number}" in online:
        number += 1
    endpoint_id = f"online-{number}"

    # Save it, then check that it really is a TSS2 endpoint. If not, take it out again.
    online[endpoint_id] = url
    save_online_endpoints(online)
    try:
        info = get_endpoint_info(endpoint_id)
    except HTTPException:
        del online[endpoint_id]
        save_online_endpoints(online)
        raise

    return {"id": endpoint_id, "label": info["label"], "online": True, "file": url}


@api.delete("/endpoints/{endpoint_id}")
def remove_online_endpoint(endpoint_id):
    """Take an online endpoint out of the list (the local .ttl files can't be removed here)"""
    online = load_online_endpoints()
    if endpoint_id not in online:
        raise HTTPException(status_code=404, detail=f"{endpoint_id} is not an online endpoint")
    del online[endpoint_id]
    save_online_endpoints(online)
    return {"removed": endpoint_id}


@api.get("/endpoints/{endpoint_id}")
def get_endpoint(endpoint_id):
    """What one metadata endpoint holds, plus its raw Turtle text"""
    info = get_endpoint_info(endpoint_id)
    info["turtle"] = read_text(endpoint_file(endpoint_id))
    return info


@api.get("/endpoints/{endpoint_id}/data")
def get_data_file(endpoint_id, lines: int = 20):
    """Preview the source's data file (tss:dataFile): the first lines of a CSV file,
    or the first rows of a Parquet file converted to CSV text."""
    info = get_endpoint_info(endpoint_id)
    if info["embedded"] is True:
        raise HTTPException(status_code=400, detail="This source has no data file: its points are embedded in the Turtle file (tss:points).")
    lines = max(1, min(lines, 1000))
    location = info["dataFile"]
    if location is None:
        raise HTTPException(status_code=400, detail="The source has no tss:dataFile.")
    if not file_exists(location):
        return {"path": location, "found": False}

    if info["format"] == "tss:Parquet":
        try:
            with duckdb.connect() as connection:
                result = connection.execute(
                    "SELECT * FROM read_parquet(?, hive_partitioning = false) LIMIT ?",
                    [str(local_copy(location)), lines - 1],
                )
                columns = [column[0] for column in result.description]
                rows = result.fetchall()
        except duckdb.Error as error:
            raise HTTPException(status_code=400, detail=f"Could not preview Parquet file {location}: {error}") from error
        text = io.StringIO(newline="")
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(
            [sql_box.to_json_value(value) if isinstance(value, datetime) else value for value in row]
            for row in rows
        )
        return {
            "path": location,
            "found": True,
            "note": f"CSV preview of the first {len(rows)} Parquet rows; download contains the original Parquet file",
            "text": text.getvalue(),
        }
    if info["format"] != "tss:CSV":
        raise HTTPException(status_code=400, detail=f"A data preview is available for tss:CSV and tss:Parquet sources only, not {info['format']}.")

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
        "path": location,
        "found": True,
        "note": note,
        "text": "\n".join(first_lines),
    }


@api.get("/endpoints/{endpoint_id}/data/download")
def download_data_file(endpoint_id):
    """Download the original data file (tss:dataFile), CSV or Parquet"""
    info = get_endpoint_info(endpoint_id)
    if info["embedded"] is True or info["dataFile"] is None:
        raise HTTPException(status_code=400, detail="This source has no data file to download.")
    media_type = "application/vnd.apache.parquet" if info["format"] == "tss:Parquet" else "text/csv"
    return send_as_download(info["dataFile"], media_type)


@api.get("/endpoints/{endpoint_id}/points")
def get_points_file(endpoint_id):
    """The JSON points embedded in the source (tss:points), one point per line"""
    info = get_endpoint_info(endpoint_id)
    if info["embedded"] is not True:
        raise HTTPException(status_code=400, detail="This source has no embedded points: its data is in an external file (tss:dataFile).")
    graph = load_graph(endpoint_id)
    source = graph.value(predicate=RDF.type, object=TSS.Source)
    try:
        points = read_points(graph, source)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    lines = []
    for point in points:
        lines.append("  " + json.dumps(point, ensure_ascii=False))
    return {
        "path": "tss:points",
        "found": True,
        "note": f"{len(points)} points, embedded in {info['file']}",
        "text": "[\n" + ",\n".join(lines) + "\n]",
    }


def query_field(endpoint_id, field):
    """What the query routes need: the endpoint info, its graph, the chosen field and its source"""
    info = get_endpoint_info(endpoint_id)
    graph = load_graph(endpoint_id)
    chosen = find_field(info, field)
    source = graph.value(predicate=TSS.hasField, object=URIRef(field))
    return info, graph, chosen, source


@api.get("/endpoints/{endpoint_id}/filter-columns")
def filter_columns(endpoint_id, field):
    """The other columns the query form can filter on (like a quality code),
    with the values that occur in them. Only columns with a few different values
    (2 to 30) are useful for that, so only those are sent."""
    info, graph, chosen, source = query_field(endpoint_id, field)

    # Start with every column except the time and this field, and drop a column
    # as soon as it has too many different values
    candidates = {}  # column name -> the different values seen so far
    for other in info["fields"]:
        if other["columnName"] is None or other["isTime"] or other["id"] == chosen["id"]:
            continue
        candidates[other["columnName"]] = set()

    try:
        for row in data_rows(graph, info, source):
            for name in list(candidates):  # list(...): we may delete from candidates inside the loop
                candidates[name].add(row.get(name, ""))
                if len(candidates[name]) > 30:
                    del candidates[name]
            if len(candidates) == 0:
                break
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    columns = []
    for name, values in candidates.items():
        if len(values) >= 2:  # a column with one value is no use as a filter
            columns.append({"name": name, "values": sorted(values)})
    return columns


def parse_value(text, datatype):
    """A cell of a numeric field as a number (None when the cell is empty)"""
    if text == "":
        return None
    try:
        value = int(text) if datatype in INTEGER_DATATYPES else float(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"The field declares numeric values ({datatype}), but contains {text!r}.")
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail="The field contains a non-finite numeric value.")
    return value


@api.get("/endpoints/{endpoint_id}/query")
def query_series(endpoint_id, field, start=None, end=None, min_value=None, max_value=None,
                 filter_column=None, filter_value=None, resample="", aggregate="mean", limit: int = 100):
    """Get the readings of one field (one time series), with the filters of the query form,
    plus summary numbers and the gaps in the data.

    The source tells us where the data is (tss:dataFile or the embedded tss:points),
    how to read it (tss:format, and for CSV tss:delimiter, tss:header, tss:encoding),
    which column holds the time (tss:timeField) and which time window it covers
    (tss:from, tss:until). The field tells us its own column (tss:columnName) and
    its datatype (tss:datatype): only numeric fields have value ranges and mean/min/max."""
    info, graph, chosen, source = query_field(endpoint_id, field)
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

    # 2. The columns to read
    time_column = info["timeField"]
    value_column = chosen["columnName"]
    if time_column is None:
        raise HTTPException(status_code=400, detail="The source has no tss:timeField, so the readings have no time.")
    if chosen["isTime"]:
        raise HTTPException(status_code=400, detail="This is the time field itself. Pick a field that holds values.")
    if filter_column:
        names = [other["columnName"] for other in info["fields"]]
        if filter_column not in names:
            raise HTTPException(status_code=400, detail=f"'{filter_column}' is not the column of a field of this source")

    datatype = graph.value(URIRef(field), TSS.datatype)
    numeric = datatype in INTEGER_DATATYPES or datatype in DECIMAL_DATATYPES
    if not numeric and (low is not None or high is not None or resample):
        holds = show(graph, datatype) if datatype is not None else "values without a tss:datatype"
        raise HTTPException(status_code=400, detail=f"Value ranges and mean/min/max aggregation need a numeric field. This field holds {holds}.")

    # 3. The source only covers its own time window: from is included, until is not
    window_start = parse_time(info["from"]) if info["from"] else None
    window_end = parse_time(info["until"]) if info["until"] else None

    # 4. Go through the data row by row
    moments = []  # the time of every row inside the time range: to find the gaps
    kept = []     # the readings that also pass the column filter and the value range
    first = True
    try:
        for row in data_rows(graph, info, source):
            # The columns must be in the data (an embedded point may leave out an empty value)
            if first and not info["embedded"]:
                for name in [time_column, value_column, filter_column]:
                    if name and name not in row:
                        raise HTTPException(status_code=400, detail=f"Column '{name}' is not in {info['dataFile']}")
            first = False
            if row.get(time_column, "") == "":
                raise HTTPException(status_code=400, detail=f"A row has no time in column '{time_column}'")

            row_time = parse_time(row[time_column])
            if window_start and row_time < window_start:
                continue
            if window_end and row_time >= window_end:
                continue
            if start_time and row_time < start_time:
                continue
            if end_time and row_time > end_time:
                continue
            moments.append(row_time)

            if filter_column and row.get(filter_column, "") != filter_value:
                continue
            text = row.get(value_column, "")
            if numeric:
                value = parse_value(text, datatype)
            else:
                value = text if text != "" else None
            if value is None and (low is not None or high is not None):
                continue  # with a value range, an empty cell can never match
            if low is not None and value < low:
                continue
            if high is not None and value > high:
                continue
            kept.append({"time": row[time_column], "moment": row_time, "value": value})
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    # Files are usually in time order, but nothing guarantees it (JSON points certainly not)
    moments.sort()
    kept.sort(key=lambda reading: reading["moment"])

    # 5. The rows to show: every reading, or one value per hour or per day
    if resample:
        rows = resample_readings(kept, resample, aggregate)
    else:
        rows = []
        for reading in kept:
            rows.append({"time": reading["time"], "value": reading["value"]})

    if numeric:
        summary = summarize(kept)
    else:
        summary = {"count": 0, "empty": 0, "mean": None, "min": None, "max": None}
        for reading in kept:
            if reading["value"] is None:
                summary["empty"] += 1
            else:
                summary["count"] += 1

    return {
        "field": chosen["label"] if chosen["label"] else value_column,
        "embedded": info["embedded"] is True,
        "format": info["format"],
        "numeric": numeric,
        "datatype": show(graph, datatype),
        "unit": chosen["unit"],
        "dataFile": info["dataFile"],
        "delimiter": info["delimiter"] if info["delimiter"] is not None else ",",
        "timeColumn": time_column,
        "valueColumn": value_column,
        "filterColumn": filter_column if filter_column else None,
        "filterValue": filter_value if filter_column else None,
        "resample": resample,
        "aggregate": aggregate,
        "summary": summary,
        "gaps": find_gaps(moments),   # over every reading in the time range
        "totalRows": len(rows),
        "limitReached": len(rows) > limit,
        "readings": rows[:limit],     # [:limit] = only the first "limit" rows
    }


@api.get("/endpoints/{endpoint_id}/series/metadata")
def series_metadata(endpoint_id, field):
    """The series inspector: everything known about one field (one column of the source),
    and for every value where it is stated: on the field itself ("field"), or on its
    source ("source"), which makes it true for every field of that source."""
    graph = load_graph(endpoint_id)
    info = get_endpoint_info(endpoint_id)

    chosen = find_field(info, field)
    field = URIRef(field)
    source = graph.value(predicate=TSS.hasField, object=field)

    # 1. The lines of each section
    sections = []
    shown = {RDF.type, RDFS.label, RDFS.comment, TSS.describesField}  # shown elsewhere in the inspector
    for section in FIELD_SECTIONS:
        lines = []
        for name, prop, where in section["properties"]:
            node = field if where == "field" else source
            lines.append(make_line(graph, name, show(graph, prop), graph.value(node, prop), where))
            if where == "field":
                shown.add(prop)
        sections.append({"title": section["title"], "explanation": section["explanation"], "lines": lines})

    #    Data binding: first where the data is, last the time column and the key of the source
    binding = sections[0]
    if info["embedded"]:
        binding["explanation"] = "The values are embedded in the Turtle file as JSON points; this field is one key in each point."
        try:
            count = len(read_points(graph, source))
        except ValueError:
            count = None
        line = make_line(graph, "readings", "tss:points", graph.value(source, TSS.points), "source")
        if line["value"] is not None:
            line["value"]["text"] = f"{count} points" if count is not None else "not valid JSON"
            line["value"]["hover"] = "tss:points (shown in the Turtle tab below)"
        binding["lines"].insert(0, line)
    else:
        line = make_line(graph, "data file", "tss:dataFile", graph.value(source, TSS.dataFile), "source")
        if line["value"] is not None:
            line["value"]["text"] = info["dataFile"]  # e.g. data/waterinfo_289423042.csv
        binding["lines"].insert(0, line)

    time_field = graph.value(source, TSS.timeField)
    line = make_line(graph, "time column", "tss:timeField", time_field, "source")
    if time_field is not None:
        line["value"]["text"] = column_name(graph, time_field)
    binding["lines"].append(line)

    keys = list(graph.objects(source, TSS.key))
    line = make_line(graph, "key", "tss:key", keys[0] if len(keys) > 0 else None, "source")
    if len(keys) > 0:
        line["value"]["text"] = ", ".join(info["key"])
    binding["lines"].append(line)

    # 2. Anything else stated on the field, so nothing is hidden
    other_lines = []
    for prop in sorted(set(graph.predicates(field)) - shown):
        for value in graph.objects(field, prop):
            other_lines.append(make_line(graph, show(graph, prop), show(graph, prop), value, "field"))
    if len(other_lines) > 0:
        sections.append({"title": "Also stated", "explanation": "Everything else the metadata says about this field.", "lines": other_lines})

    # 3. Count where the values are stated
    counts = {"field": 0, "source": 0, "missing": 0}
    for section in sections:
        for line in section["lines"]:
            counts[line["where"]] += 1

    # 4. What the source states, for the source's own panel
    source_lines = []
    for prop, value in graph.predicate_objects(source):
        if prop in (RDF.type, RDFS.label, TSS.hasField, TSS.points):
            continue
        shown_value = describe(graph, value)
        if prop in (TSS.timeField, TSS.key):
            shown_value["text"] = column_name(graph, value)
        source_lines.append({"property": show(graph, prop), "value": shown_value})
    source_lines.sort(key=lambda line: line["property"])

    # 5. The source files, with the parts this field uses marked
    turtle_marks = {}
    for node, mark in [(time_field, "time"), (source, "source"), (field, "field")]:
        if node is not None:
            turtle_marks[show(graph, node)] = mark  # how our files write it, e.g. ex:grootdok-ec20-field
            turtle_marks[f"<{node}>"] = mark        # in case a file writes the full IRI
    turtle_text = read_text(endpoint_file(endpoint_id))

    rows = {
        "file": "tss:points in the Turtle file" if info["embedded"] else info["dataFile"],
        "error": None,
        "columns": [],
        "rows": [],
        "notFound": [],
    }
    try:
        headers, table_rows = first_rows(graph, info, source)
    except ValueError as error:
        rows["error"] = str(error)
    else:
        positions = column_positions(info["fields"], headers)

        # Mark this field, the time field, and the fields that describe this one
        bound = {}
        for other in info["fields"]:
            if other["id"] not in positions:
                rows["notFound"].append(other["columnName"] or other["iri"])
                continue
            if other["id"] == chosen["id"]:
                bound[positions[other["id"]]] = "field"
            elif other["isTime"]:
                bound[positions[other["id"]]] = "time"
            elif graph.value(URIRef(other["id"]), TSS.describesField) == field:
                bound[positions[other["id"]]] = "related"
        for index in range(len(headers)):
            rows["columns"].append({"header": headers[index], "bound": bound.get(index)})
        rows["rows"] = table_rows

    return {
        "label": chosen["label"] if chosen["label"] else chosen["columnName"],
        "comments": chosen["comments"],
        "field": {"short": show(graph, field), "iri": str(field)},
        "counts": counts,
        "sections": sections,
        "source": {"lines": source_lines},
        "files": {
            "turtleFile": endpoint_file(endpoint_id),
            "turtle": mark_turtle(turtle_text, turtle_marks),
            "rows": rows,
        },
    }


# ---------------------------------------------------------------------------
# The SQL box (DuckDB): read-only SQL on the CSV files of an endpoint
# ---------------------------------------------------------------------------

def column_role(field):
    """What a column of the data table is, in words, for the SQL box"""
    if field is None:
        return "not described by a field"
    if field["isTime"]:
        return "time (tss:timeField)"
    if field["describesField"]:
        return f"describes {field['describesField']}"
    if field["observedProperty"]:
        label = field["label"] if field["label"] else field["columnName"]
        unit = f" ({field['unit']})" if field["unit"] else ""
        return f"value of: {label}{unit}"
    return None


def sql_table(info, graph):
    """The SQL table of the source: its name, where its data is, and its columns
    (one per data column, typed by the field's tss:datatype; Parquet keeps its own types).
    "error" says why it can't be loaded."""
    by_column = {}  # column name -> its field
    for field in info["fields"]:
        if field["columnName"] is not None:
            by_column[field["columnName"]] = field

    table = {"name": "data", "file": None, "kind": None, "columns": [], "error": None}
    names = []          # the columns, in the order of the data
    native_types = {}   # Parquet: column name -> its type in the file
    try:
        if info["embedded"]:
            # The table is named after the source, e.g. ex:zevenaarshaven-source -> zevenaarshaven_source
            source = graph.value(predicate=RDF.type, object=TSS.Source)
            table["name"] = sql_box.table_name(str(source).split("#")[-1].split("/")[-1])
            table["file"] = "tss:points"
            table["kind"] = "points"
            points = list(data_rows(graph, info, source))
            # The fields' column names first, then any other key the points use
            for field in info["fields"]:
                if field["columnName"] is not None:
                    names.append(field["columnName"])
            for point in points:
                for key in point:
                    if key not in names:
                        names.append(key)
            table["rows"] = []
            for point in points:
                row = []
                for name in names:
                    value = point.get(name, "")
                    row.append(value if value != "" else None)  # an empty or missing value is NULL
                table["rows"].append(row)
        else:
            location = info["dataFile"]
            if location is None:
                raise ValueError("The source has no tss:dataFile")
            table["name"] = sql_box.table_name(location)
            table["file"] = location
            if not file_exists(location):
                raise ValueError(f"Data file not found: {location}")
            table["path"] = local_copy(location).as_posix()

            if info["format"] == "tss:Parquet":
                table["kind"] = "parquet"
                with duckdb.connect() as connection:
                    described = connection.execute(
                        "DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning = false)", [table["path"]]
                    ).fetchall()
                for column in described:
                    names.append(column[0])            # e.g. Timestamp
                    native_types[column[0]] = column[1]  # e.g. TIMESTAMP WITH TIME ZONE
            elif info["format"] == "tss:CSV":
                table["kind"] = "csv"
                table["delimiter"] = info["delimiter"] if info["delimiter"] is not None else ","
                if len(table["delimiter"]) != 1:
                    raise ValueError(f"tss:delimiter must be one character, not '{table['delimiter']}'")
                table["header"] = info["header"] != "false"
                first_line = next(csv.reader(read_lines(location), delimiter=table["delimiter"]), [])
                if table["header"]:
                    names = first_line
                else:
                    # No header line: a column gets the column name of the field with its tss:columnIndex
                    for index in range(len(first_line)):
                        name = f"column {index}"
                        for field in info["fields"]:
                            if field["columnIndex"] == index and field["columnName"] is not None:
                                name = field["columnName"]
                        names.append(name)
            else:
                raise ValueError(f"The SQL box can't read data in format {info['format']}")
    except (ValueError, duckdb.Error) as error:
        table["error"] = str(error)
        return table

    for name in names:
        if names.count(name) > 1:
            table["error"] = f"Two columns are called '{name}'; a SQL table needs different column names."
            return table
        field = by_column.get(name)
        if table["kind"] == "parquet":
            sql_type = native_types[name]
        else:
            sql_type = sql_box.duckdb_type(field["datatype"] if field is not None else None)
        table["columns"].append({"name": name, "type": sql_type, "role": column_role(field)})
    return table


@api.get("/endpoints/{endpoint_id}/sql/tables")
def sql_schema(endpoint_id):
    """What the SQL box can query for this endpoint: the data table, the metadata tables, and example queries"""
    info = get_endpoint_info(endpoint_id)
    table = sql_table(info, load_graph(endpoint_id))

    metadata_tables = []
    for name, metadata in sql_box.METADATA_TABLES.items():
        columns = []
        for column_name, sql_type in metadata["columns"]:
            columns.append({"name": column_name, "type": sql_type})
        metadata_tables.append({"name": name, "explanation": metadata["explanation"], "columns": columns})

    return {
        "tables": [{
            "name": table["name"],
            "file": table["file"],
            "kind": table["kind"],
            "error": table["error"],
            "columns": table["columns"],
        }],
        "metadataTables": metadata_tables,
        "examples": sql_box.example_queries(table if table["error"] is None else None, info["fields"]),
        "maxRows": sql_box.MAX_ROWS,
        "maxSeconds": sql_box.MAX_SECONDS,
    }


class SqlQuery(BaseModel):
    """What the browser sends: {"sql": "SELECT ..."} (in the body, because a query can be long)"""
    sql: str


@api.post("/endpoints/{endpoint_id}/sql")
def run_sql(endpoint_id, query: SqlQuery):
    """Run one read-only SQL query with DuckDB on the data of this endpoint"""
    info = get_endpoint_info(endpoint_id)

    # 1. The data table. If it can't be made (e.g. the data file is missing), it is
    #    skipped: the metadata tables still work.
    table = sql_table(info, load_graph(endpoint_id))
    tables = []
    skipped = []
    if table["error"] is None:
        tables.append(table)
    else:
        skipped.append({"name": table["name"], "error": table["error"]})
    loaded_name = table["name"] if table["error"] is None else None

    # 2. The TSS metadata, for the "source" and "fields" tables
    source_row = {
        "source": info["source"],
        "label": info["label"],
        "sql_table": loaded_name,
        "format": info["format"],
        "data_file": info["dataFile"],
        "time_column": info["timeField"],
        "from_time": info["from"],
        "until_time": info["until"],
        "produced_by": info["producedBy"],
        "mutable": info["mutable"] == "true" if info["mutable"] is not None else None,
    }
    field_rows = []
    for field in info["fields"]:
        field_rows.append({
            "column_name": field["columnName"],
            "sql_table": loaded_name,
            "field": field["iri"],
            "label": field["label"],
            "role": column_role(field),
            "datatype": field["datatype"],
            "unit": field["unit"],
            "observed_property": field["observedProperty"],
            "sensor": field["sensor"],
            "feature_of_interest": field["featureOfInterest"],
            "describes": field["describesField"],
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
# The built frontend (npm run build) is in frontend/dist. Every address that is not
# an /api route is looked up as a file in there, and "/" gives index.html.
# Not built yet? Then we skip this, and you use the dashboard through npm run dev.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
