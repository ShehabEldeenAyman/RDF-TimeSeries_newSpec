from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from rdflib import Graph, URIRef, Literal, RDF, RDFS
from rdflib.namespace import DCTERMS
from csvw import CSVW
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

# The series inspector groups its lines by meaning, not by RDF structure
INSPECTOR_SECTIONS = [
    {
        "title": "Data binding",
        "explanation": "Where the values are: the data file, and the columns that hold the time and the value of each reading.",
        "properties": [("time column", TSS.timeColumn), ("value column", TSS.valueColumn)],
    },
    {
        "title": "Time window",
        "explanation": "The stretch of time this series covers.",
        "properties": [("from", TSS["from"]), ("until", TSS["until"])],
    },
    {
        "title": "Each reading is",
        "explanation": "The point template: every row of the CSV becomes one reading like this.",
        "properties": [("type", TSS.pointType), ("time goes in", TSS.timePath), ("value goes in", TSS.valuePath)],
    },
]

# What every reading shares, stated inside tss:about (the SOSA part of the point template).
# These lines are added to the "Each reading is" section.
ABOUT_PROPERTIES = [
    ("made by", SOSA.madeBySensor),
    ("observes", SOSA.observedProperty),
    ("feature", SOSA.hasFeatureOfInterest),
    ("unit", QUDT.hasUnit),
]


# ---------------------------------------------------------------------------
# SPARQL queries
# ---------------------------------------------------------------------------

# The snippet group and its template.
# One group = one data file: the group itself says which CSV file holds its series
# (tss:dataFile) and which CSVW file describes it (tss:describedby).
# In this demo every metadata endpoint holds exactly one group.
GROUP_QUERY = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX tss:  <https://w3id.org/tss#>

SELECT ?group ?label ?embedded ?dataFile ?describedBy ?mutable ?producedBy
       ?timeColumn ?fromTime ?untilTime ?pointType ?timePath ?valuePath
WHERE {
  ?group a tss:SnippetGroup .
  OPTIONAL { ?group rdfs:label ?label }
  OPTIONAL {
    ?group tss:snippetTemplate ?template .
    OPTIONAL { ?template tss:from ?fromTime }
    OPTIONAL { ?template tss:until ?untilTime }
    OPTIONAL { ?template tss:timeColumn ?timeColumn }
    OPTIONAL { ?template tss:pointType ?pointType }
    OPTIONAL { ?template tss:timePath ?timePath }
    OPTIONAL { ?template tss:valuePath ?valuePath }
  }

  OPTIONAL { ?group tss:dataFile ?dataFile }
  OPTIONAL { ?group tss:describedby ?describedBy }
  OPTIONAL { ?group tss:embedded ?embedded }
  OPTIONAL { ?group tss:mutable ?mutable }
  OPTIONAL { ?group tss:producedBy ?producedBy }
}
"""

# One row per snippet (= one time series = one value column in the CSV).
# Only what a snippet states itself. The values it can inherit from its group's
# template (columns, time window, tss:about, ...) are looked up with the TSS2
# rule in get_endpoint_info.
SNIPPETS_QUERY = """
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX tss:     <https://w3id.org/tss#>

SELECT ?snippet ?label ?identifier ?comment
WHERE {
  ?snippet a tss:Snippet .
  OPTIONAL { ?snippet rdfs:label ?label }

  OPTIONAL { ?snippet dcterms:identifier ?identifier }
  OPTIONAL { ?snippet rdfs:comment ?comment }
}
ORDER BY ?label
"""


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


def titles_of(column):
    """Every CSV header the CSVW file accepts for a column. "titles" can be a text,
    a list of texts, or one per language ({"en": "Value", "nl": "Waarde"}).
    Without titles, the header is the column's name."""
    titles = column.get("titles", column["name"])
    if isinstance(titles, str):
        return [titles]
    result = []
    if isinstance(titles, list):
        for title in titles:
            result.append(str(title))
    if isinstance(titles, dict):
        for value in titles.values():
            if isinstance(value, list):
                for title in value:
                    result.append(str(title))
            else:
                result.append(str(value))
    if len(result) == 0:
        result.append(column["name"])
    return result


def null_values(metadata, column):
    """The texts that mean "no value" in a column (CSVW "null"), e.g. ["", "NA"].
    The column's own setting wins, then the tableSchema's, then the table's."""
    for source in [column, metadata["tableSchema"], metadata]:
        if "null" in source:
            value = source["null"]
            return value if isinstance(value, list) else [value]
    return [""]


def read_csvw(described_by):
    """Read a CSVW file. Returns (delimiter, headers), where headers maps every
    column name (what TSS2 uses, e.g. "timestamp") to its CSV header (e.g. "Timestamp").
    Returns None when there is no such file."""
    if not file_exists(described_by):
        return None
    try:
        metadata = load_csvw_json(described_by)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    delimiter = csvw_delimiter(metadata, described_by)
    headers = {}
    for column in metadata["tableSchema"]["columns"]:
        headers[column["name"]] = titles_of(column)[0]
    return delimiter, headers


def csvw_columns(described_by):
    """What the SQL box needs from a CSVW file: the columns in CSV order
    ({"name", "header", "titles", "datatype", "nulls"}) and how to read the CSV
    (delimiter, quote, escape, skip, header, comment).
    Returns {"error": "..."} when the file is missing or can't be used."""
    if not file_exists(described_by):
        return {"error": f"CSVW file not found: {described_by}"}
    try:
        metadata = load_csvw_json(described_by)
    except ValueError as error:
        return {"error": str(error)}

    dialect = metadata.get("dialect", {})
    header_rows = dialect.get("headerRowCount", 1 if dialect.get("header", True) else 0)
    if header_rows > 1:
        return {"error": f"The CSVW file {described_by} says the CSV has {header_rows} header lines; "
                         "the SQL box can only read files with one."}
    quote = dialect.get("quoteChar", '"') or ""  # null means: no quoting at all

    columns = []
    for column in metadata["tableSchema"]["columns"]:
        if column.get("virtual"):
            continue  # a "virtual" column is not in the CSV file itself
        titles = titles_of(column)
        columns.append({
            "name": column["name"],
            "header": titles[0],
            "titles": titles,
            "datatype": column.get("datatype", "string"),
            "nulls": null_values(metadata, column),
        })

    return {
        "error": None,
        "delimiter": csvw_delimiter(metadata, described_by),
        "quote": quote,
        # doubleQuote (the default): a quote inside a quoted value is written twice ""
        "escape": quote if dialect.get("doubleQuote", True) else "\\",
        "skip": dialect.get("skipRows", 0),
        "header": header_rows == 1,
        "comment": dialect.get("commentPrefix"),
        "columns": columns,
    }


def get_endpoint_info(endpoint_id):
    """Everything the dashboard needs to know about one metadata endpoint"""
    graph = load_graph(endpoint_id)

    rows = list(graph.query(GROUP_QUERY))
    if len(rows) == 0:
        raise HTTPException(status_code=404, detail=f"No tss:SnippetGroup found in {endpoint_file(endpoint_id)}")
    row = rows[0]
    formats = list(graph.objects(row.group, DCTERMS.format))
    declared_format = formats[0] if len(formats) == 1 else None

    info = {
        "id": endpoint_id,
        "file": endpoint_file(endpoint_id),
        "online": is_url(endpoint_file(endpoint_id)),
        "label": str(row.label) if row.label is not None else endpoint_id,
        # the group: its data file and how that file changes
        "group": show(graph, row.group),
        "embedded": embedded_mode(graph, row.group),
        "dataFormat": str(declared_format) if declared_format is not None else None,
        "csvwApplicable": embedded_mode(graph, row.group) is False and formats == [CSV_FILE_TYPE],
        "csvUrl": iri_to_location(row.dataFile),
        "describedBy": iri_to_location(row.describedBy),
        "mutable": show(graph, row.mutable),
        "producedBy": show(graph, row.producedBy),
        # the snippet template
        "timeColumn": show(graph, row.timeColumn),
        "from": show(graph, row.fromTime),
        "until": show(graph, row.untilTime),
        "pointType": show(graph, row.pointType),
        "timePath": show(graph, row.timePath),
        "valuePath": show(graph, row.valuePath),
        # the snippets, filled in below
        "snippets": [],
    }

    for row in graph.query(SNIPPETS_QUERY):
        snippet = row.snippet
        group, template = find_group(graph, snippet)
        about = resolve(graph, snippet, template, TSS.about)
        unit = value_of(graph, about, QUDT.hasUnit)

        info["snippets"].append({
            "id": str(snippet),
            "label": str(row.label) if row.label is not None else show(graph, snippet),
            "identifier": show(graph, row.identifier),
            "comment": show(graph, row.comment),
            # The data file is stated on the group itself (one group = one file)
            "group": show(graph, group),
            "embedded": embedded_mode(graph, group),
            "csvUrl": iri_to_location(value_of(graph, group, TSS.dataFile)),
            "describedBy": iri_to_location(value_of(graph, group, TSS.describedby)),
            # Everything below can come from the group's template (the TSS2 rule)
            "valueColumn": show(graph, resolve(graph, snippet, template, TSS.valueColumn)),
            "timeColumn": show(graph, resolve(graph, snippet, template, TSS.timeColumn)),
            "from": show(graph, resolve(graph, snippet, template, TSS["from"])),
            "until": show(graph, resolve(graph, snippet, template, TSS["until"])),
            "observedProperty": show(graph, value_of(graph, about, SOSA.observedProperty)),
            "unit": READABLE_NAMES.get(str(unit), show(graph, unit)),
            "sensor": show(graph, value_of(graph, about, SOSA.madeBySensor)),
            "featureOfInterest": show(graph, value_of(graph, about, SOSA.hasFeatureOfInterest)),
        })

    return info


def embedded_mode(graph, group):
    """Require an explicit boolean, rather than guessing a storage profile."""
    values = list(graph.objects(group, TSS.embedded)) if group is not None else []
    if len(values) != 1 or not isinstance(values[0], Literal) or not isinstance(values[0].toPython(), bool):
        return None
    return values[0].toPython()


def embedded_points(graph, snippet):
    """Read the snippet's JSON points without losing string-valued series."""
    literal = graph.value(snippet, TSS.points)
    try:
        points = json.loads(str(literal))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="This embedded snippet has no valid JSON in tss:points.")
    if not isinstance(points, list) or any(
        not isinstance(point, dict) or "time" not in point or "value" not in point
        or not isinstance(point["time"], str) for point in points
    ):
        raise HTTPException(status_code=400, detail='tss:points must be an array of objects with a string "time" and a "value".')
    for point in points:
        parse_time(point["time"])
    return points


def embedded_value_type(graph, snippet):
    """Use the effective JSON-LD value mapping to distinguish numbers from text."""
    group, template = find_group(graph, snippet)
    context = resolve(graph, snippet, template, TSS.context)
    if context is None:
        return None
    try:
        context = json.loads(str(context))
        context = context.get("@context", context)
        mapping = context.get("value", {})
        datatype = mapping.get("@type") if isinstance(mapping, dict) else None
        if datatype and ":" in datatype and not datatype.startswith(("http:", "https:")):
            prefix, local = datatype.split(":", 1)
            namespace = context.get(prefix)
            if isinstance(namespace, str):
                datatype = namespace + local
        return datatype
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="tss:context must contain a usable JSON-LD context object.")


def numeric_points(graph, snippet, points):
    datatype = embedded_value_type(graph, snippet)
    if datatype is not None:
        return (datatype.startswith("http://www.w3.org/2001/XMLSchema#")
                and sql_box.duckdb_type(datatype.rsplit("#", 1)[-1]) in ("DOUBLE", "BIGINT"))
    return all(point["value"] is None or isinstance(point["value"], (int, float))
               and not isinstance(point["value"], bool) for point in points)


@api.get("/endpoints/{endpoint_id}/points")
def get_points_file(endpoint_id, snippet):
    graph = load_graph(endpoint_id)
    chosen = find_snippet(get_endpoint_info(endpoint_id), snippet)
    if chosen["embedded"] is not True:
        raise HTTPException(status_code=400, detail="This series uses external CSV data.")
    points = embedded_points(graph, URIRef(snippet))
    return {"path": "tss:points", "found": True, "note": f"{len(points)} embedded readings",
            "text": json.dumps(points, indent=2, ensure_ascii=False)}


@api.post("/endpoints/{endpoint_id}/rdf")
def generate_rdf(endpoint_id):
    """Generate standalone Turtle readings from either storage profile."""
    graph = load_graph(endpoint_id)
    try:
        groups = list(graph.subjects(RDF.type, TSS.SnippetGroup))
        modes = {embedded_mode(graph, group) for group in groups}
        if not groups or None in modes or len(modes) != 1:
            raise ValueError("Every group must state the same boolean tss:embedded storage profile.")
        if modes == {True}:
            result = rdf_generation.generate(graph, endpoint_base(endpoint_id))
        else:
            token = uuid4().hex
            target = Path(RDF_EXPORT_DIRECTORY.name) / f"{token}.ttl"

            def load_files(group):
                csv_iri = rdf_generation.single(graph, group, TSS.dataFile, required=True)
                csvw_iri = rdf_generation.single(graph, group, TSS.describedby, required=True)
                if not isinstance(csv_iri, URIRef) or not isinstance(csvw_iri, URIRef):
                    raise ValueError("dataFile and describedby must be file IRIs.")
                csv_location = iri_to_location(csv_iri)
                csvw_location = iri_to_location(csvw_iri)
                metadata = load_csvw_json(csvw_location)
                if not file_exists(csv_location):
                    raise ValueError(f"CSV file not found: {csv_location}")
                return metadata, local_copy(csv_location), csv_iri

            try:
                result = csv_rdf_generation.generate(graph, load_files, target)
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
    """Validate the original graph with the profile selected by tss:embedded."""
    graph = load_graph(endpoint_id)
    groups = list(graph.subjects(RDF.type, TSS.SnippetGroup))
    modes = {embedded_mode(graph, group) for group in groups}
    if not groups or None in modes or len(modes) != 1:
        raise HTTPException(status_code=400, detail="Cannot select SHACL shapes: every SnippetGroup must state one boolean tss:embedded, and all groups must use the same profile.")
    embedded = modes.pop()
    name = "embedded_shapes.ttl" if embedded else "external_shapes.ttl"
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
        result["text"] = "[ ] (a blank node)"

    return result


def make_line(graph, name, property_name, value, source, group_value):
    """One line of the series inspector"""
    return {
        "name": name,                                 # what the inspector calls it, e.g. "time column"
        "property": property_name,                    # the RDF property, e.g. tss:timeColumn
        "source": source,                             # "own", "group", "override" or "missing"
        "value": describe(graph, value),
        "groupValue": describe(graph, group_value),   # only for an override: the value it replaces
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


def mark_csvw(text, column_marks):
    """Split a CSVW JSON file into lines and mark the column objects of the columns
    this series reads. column_marks maps a column name to a mark, e.g. {"timestamp": "time"}"""
    lines = []
    for line in text.splitlines():
        lines.append({"text": line, "mark": None})

    for index in range(len(lines)):
        for name, mark in column_marks.items():
            if f'"name": "{name}"' in lines[index]["text"]:
                # walk up to the "{" that opens this column ...
                start = index
                while start > 0 and lines[start]["text"].strip() != "{":
                    start -= 1
                # ... and down to the "}" that closes it (counting nested { })
                depth = 0
                for end in range(start, len(lines)):
                    depth += lines[end]["text"].count("{") - lines[end]["text"].count("}")
                    lines[end]["mark"] = mark
                    if depth == 0:
                        break
    return lines


def first_csv_rows(csv_url, described_by, time_column, value_column, count=10):
    """The first rows of the CSV, with the columns this series reads marked"""
    result = {"file": csv_url, "error": None, "columns": [], "rows": []}

    csvw = read_csvw(described_by)
    if csvw is None:
        result["error"] = f"Can't tell which columns this series reads: CSVW file not found: {described_by}"
        return result
    if not file_exists(csv_url):
        result["error"] = f"CSV file not found: {csv_url}"
        return result
    delimiter, headers = csvw

    # Which CSV headers belong to the columns this series reads?
    bound = {}
    if time_column in headers:
        bound[headers[time_column]] = "time column"
    if value_column in headers:
        bound[headers[value_column]] = "value column"

    reader = csv.reader(read_lines(csv_url), delimiter=delimiter)
    for header in next(reader):  # the first line holds the headers
        result["columns"].append({"header": header, "bound": bound.get(header)})
    for row in reader:
        result["rows"].append(row)
        if len(result["rows"]) == count:
            break
    return result


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


@api.get("/endpoints/{endpoint_id}/csv")
def get_csv_file(endpoint_id, lines: int = 20):
    """Preview tss:dataFile as CSV text, converting a bounded Parquet sample."""
    info = get_endpoint_info(endpoint_id)
    lines = max(1, min(lines, 1000))
    location = info["csvUrl"]
    if not file_exists(location):
        return {"path": location, "found": False}

    if info["dataFormat"] == str(PARQUET_FILE_TYPE):
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
    if info["dataFormat"] not in (None, str(CSV_FILE_TYPE)):
        raise HTTPException(status_code=400, detail="A data preview is available for CSV and Parquet sources only.")

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


@api.get("/endpoints/{endpoint_id}/csv/download")
def download_csv_file(endpoint_id):
    """Download the original data file, including Parquet sources."""
    info = get_endpoint_info(endpoint_id)
    media_type = "application/vnd.apache.parquet" if info["dataFormat"] == str(PARQUET_FILE_TYPE) else "text/csv"
    return send_as_download(info["csvUrl"], media_type)


@api.get("/endpoints/{endpoint_id}/csvw")
def get_csvw_file(endpoint_id):
    """The CSVW metadata file the endpoint points to with tss:describedby"""
    info = get_endpoint_info(endpoint_id)
    location = info["describedBy"]
    if not info["csvwApplicable"]:
        return {
            "path": location,
            "found": False,
            "applicable": False,
            "note": "Not applicable. CSVW metadata is only used when the TSS declares dcterms:format filetype:CSV.",
        }
    if not file_exists(location):
        return {"path": location, "found": False}

    return {
        "path": location,
        "found": True,
        "note": "",
        "text": read_text(location),
    }


@api.get("/endpoints/{endpoint_id}/csvw/download")
def download_csvw_file(endpoint_id):
    """The CSVW metadata file (tss:describedby), sent as a download"""
    info = get_endpoint_info(endpoint_id)
    if not info["csvwApplicable"]:
        raise HTTPException(status_code=400, detail="CSVW metadata is not applicable: the TSS must declare dcterms:format filetype:CSV.")
    return send_as_download(info["describedBy"], "application/json")


@api.get("/endpoints/{endpoint_id}/validate")
def validate(endpoint_id):
    """Run the csvw validator: it reads the CSV through its CSVW metadata and
    checks every row against the table schema (datatypes, required values, headers)."""
    info = get_endpoint_info(endpoint_id)
    if not info["csvwApplicable"]:
        return {
            "applicable": False,
            "note": "Not applicable. CSVW validation is only available when the TSS declares dcterms:format filetype:CSV.",
        }
    location = info["describedBy"]
    if not file_exists(location):
        return {
            "valid": False,
            "seconds": 0,
            "messages": [f"CSVW file not found: {location}"],
            "messageCount": 1,
            "columns": [],
        }

    # The csvw library can open a local file or a web address itself
    if is_url(location):
        source = location
    else:
        source = str(DEMO_DIR / location)

    started = time.time()
    try:
        metadata = load_csvw_json(location)
        delimiter = csvw_delimiter(metadata, location)
        data = CSVW(source, validate=True)
        # Check the library's effective delimiter, including the comma default,
        # before it reads CSV cells; metadata may have changed between fetches.
        if any((table.dialect.delimiter if table.dialect is not None else ",") != delimiter
               for table in data.tables):
            raise ValueError("The validator's delimiter does not match the corresponding CSVW file. Reload the metadata and try again.")
        valid = data.is_valid
    except Exception as error:
        # For example: the CSV file named in the CSVW "url" does not exist
        return {
            "valid": False,
            "seconds": round(time.time() - started, 1),
            "messages": [f"{type(error).__name__}: {error}"],
            "messageCount": 1,
            "columns": [],
        }

    # A broken file can give thousands of warnings, so only send the first 50
    messages = []
    for warning in data.warnings[:50]:
        messages.append(str(warning.message))

    columns = []
    for column in data.tables[0].tableSchema.columns:
        columns.append({
            "name": column.name,
            "title": column.titles.getfirst() if column.titles else None,
            "datatype": column.datatype.base if column.datatype else "string",
        })

    return {
        "valid": valid,
        "seconds": round(time.time() - started, 1),
        "messages": messages,
        "messageCount": len(data.warnings),
        "columns": columns,
    }


def find_snippet(info, snippet):
    """The snippet (one time series) with this IRI, from get_endpoint_info"""
    for s in info["snippets"]:
        if s["id"] == snippet:
            return s
    raise HTTPException(status_code=404, detail=f"Unknown snippet: {snippet}")


def snippet_csv(chosen):
    """How to read the CSV of a snippet: (delimiter, headers, time header, value header).
    TSS2 uses CSVW column *names* (e.g. "timestamp"). The CSVW file says which
    CSV header belongs to each name (e.g. "Timestamp") and which delimiter the CSV uses."""
    csvw = read_csvw(chosen["describedBy"])
    if csvw is None:
        raise HTTPException(status_code=404, detail=f"CSVW file not found: {chosen['describedBy']}")
    delimiter, headers = csvw

    for name in [chosen["timeColumn"], chosen["valueColumn"]]:
        if name not in headers:
            raise HTTPException(status_code=400, detail=f"Column '{name}' is not described in {chosen['describedBy']}")
    if not file_exists(chosen["csvUrl"]):
        raise HTTPException(status_code=404, detail=f"CSV file not found: {chosen['csvUrl']}")
    return delimiter, headers, headers[chosen["timeColumn"]], headers[chosen["valueColumn"]]


@api.get("/endpoints/{endpoint_id}/filter-columns")
def filter_columns(endpoint_id, snippet):
    """The other CSV columns the query form can filter on (like a quality code),
    with the values that occur in them. Only columns with a few different values
    (2 to 30) are useful for that, so only those are sent."""
    info = get_endpoint_info(endpoint_id)
    chosen = find_snippet(info, snippet)
    if chosen["embedded"] is True:
        return []
    delimiter, headers, time_header, value_header = snippet_csv(chosen)

    # Start with every column except the time and the value, and drop a column
    # as soon as it has too many different values
    candidates = {}  # column name -> the different values seen so far
    for name in headers:
        if name != chosen["timeColumn"] and name != chosen["valueColumn"]:
            candidates[name] = set()

    for row in csv.DictReader(read_lines(chosen["csvUrl"]), delimiter=delimiter):
        for name in list(candidates):  # list(...): we may delete from candidates inside the loop
            candidates[name].add(row.get(headers[name], ""))
            if len(candidates[name]) > 30:
                del candidates[name]
        if len(candidates) == 0:
            break

    columns = []
    for name, values in candidates.items():
        if len(values) >= 2:  # a column with one value is no use as a filter
            columns.append({"name": name, "header": headers[name], "values": sorted(values)})
    return columns


@api.get("/endpoints/{endpoint_id}/query")
def query_series(endpoint_id, snippet, start=None, end=None, min_value=None, max_value=None,
                 filter_column=None, filter_value=None, resample="", aggregate="mean", limit: int = 100):
    """Get the readings of one snippet (one time series), with the filters of the query form,
    plus summary numbers and the gaps in the data.

    TSS2 tells us which CSV (csvw:url of the table), which columns
    (tss:timeColumn, tss:valueColumn) and which time window (tss:from,
    tss:until). Each of these can come from the group's template.
    The CSVW file tells us how to read that CSV."""
    info = get_endpoint_info(endpoint_id)
    limit = max(1, min(limit, 5000))  # don't send huge answers to the browser

    # 1. The snippet the user picked, and what the form asks for
    chosen = find_snippet(info, snippet)
    start_time = parse_time(start) if start else None
    end_time = parse_time(end) if end else None
    low = parse_number(min_value, "The lowest value")
    high = parse_number(max_value, "The highest value")
    if resample not in ["", "hour", "day"]:
        raise HTTPException(status_code=400, detail="resample must be empty, 'hour' or 'day'")
    if aggregate not in ["mean", "min", "max"]:
        raise HTTPException(status_code=400, detail="aggregate must be 'mean', 'min' or 'max'")

    # 2. Both storage profiles feed the same filtering and aggregation pipeline.
    embedded = chosen["embedded"] is True
    numeric = True
    if embedded:
        graph = load_graph(endpoint_id)
        points = embedded_points(graph, URIRef(snippet))
        numeric = numeric_points(graph, URIRef(snippet), points)
        if not numeric and (low is not None or high is not None or resample):
            raise HTTPException(status_code=400, detail="Value ranges and mean/min/max aggregation require a numeric series. This embedded series contains text or other non-numeric values.")
        if filter_column:
            raise HTTPException(status_code=400, detail="CSV column filters are only available for external CSV series.")
        delimiter, headers, time_header, value_header = None, {}, "time", "value"
        source_rows = sorted(points, key=lambda point: parse_time(point["time"]))
    else:
        delimiter, headers, time_header, value_header = snippet_csv(chosen)
        source_rows = csv.DictReader(read_lines(chosen["csvUrl"]), delimiter=delimiter)
    filter_header = None
    if filter_column:
        if filter_column not in headers:
            raise HTTPException(status_code=400, detail=f"Column '{filter_column}' is not described in {chosen['describedBy']}")
        filter_header = headers[filter_column]

    # 3. The series only covers its own time window: from is included, until is not
    #    (in all our files the last reading comes just before until)
    window_start = parse_time(chosen["from"]) if chosen["from"] else None
    window_end = parse_time(chosen["until"]) if chosen["until"] else None

    # 4. Go through the CSV row by row
    moments = []  # the time of every row inside the time range: to find the gaps
    kept = []     # the readings that also pass the column filter and the value range
    for row in source_rows:
        row_time = parse_time(row[time_header])
        if window_start and row_time < window_start:
            continue
        if window_end and row_time >= window_end:
            continue
        if start_time and row_time < start_time:
            continue
        if end_time and row_time > end_time:
            continue
        moments.append(row_time)

        if filter_header and row[filter_header] != filter_value:
            continue
        text = row[value_header]
        if embedded and not numeric:
            value = text
        else:
            try:
                value = float(text) if text not in ("", None) else None
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"The series declares numeric values, but contains {text!r}.")
            if value is not None and not math.isfinite(value):
                raise HTTPException(status_code=400, detail="The series contains a non-finite numeric value.")
        if value is None and (low is not None or high is not None):
            continue  # with a value range, an empty cell can never match
        if low is not None and value < low:
            continue
        if high is not None and value > high:
            continue
        kept.append({"time": row[time_header], "moment": row_time, "value": value})

    # 5. The rows to show: every reading, or one value per hour or per day
    if resample:
        rows = resample_readings(kept, resample, aggregate)
    else:
        rows = []
        for reading in kept:
            rows.append({"time": reading["time"], "value": reading["value"]})

    return {
        "snippet": chosen["label"],
        "embedded": embedded,
        "numeric": numeric,
        "unit": chosen["unit"],
        "csvFile": chosen["csvUrl"],
        "delimiter": delimiter,
        "timeColumn": chosen["timeColumn"],
        "timeHeader": time_header,
        "valueColumn": chosen["valueColumn"],
        "valueHeader": value_header,
        "filterHeader": filter_header,
        "filterValue": filter_value if filter_header else None,
        "resample": resample,
        "aggregate": aggregate,
        "summary": summarize(kept) if numeric else {
            "count": sum(reading["value"] is not None for reading in kept),
            "empty": sum(reading["value"] is None for reading in kept),
            "mean": None, "min": None, "max": None,
        },
        "gaps": find_gaps(moments),   # over every reading in the time range
        "totalRows": len(rows),
        "limitReached": len(rows) > limit,
        "readings": rows[:limit],     # [:limit] = only the first "limit" rows
    }


@api.get("/endpoints/{endpoint_id}/series/metadata")
def series_metadata(endpoint_id, snippet):
    """The series inspector: everything known about one snippet, and where each
    value comes from: stated on the snippet ("own"), inherited from its group's
    template ("group"), or stated on both with different values ("override")."""
    graph = load_graph(endpoint_id)
    snippet = URIRef(snippet)
    if (snippet, RDF.type, TSS.Snippet) not in graph:
        raise HTTPException(status_code=404, detail=f"Unknown snippet: {snippet}")
    group, template = find_group(graph, snippet)
    embedded = embedded_mode(graph, group) is True

    # 1. The lines of each section, with the TSS2 rule applied to every property
    sections = []
    shown = {RDF.type, RDFS.label, TSS.about}  # properties that never get a line of their own here
    for section in INSPECTOR_SECTIONS:
        lines = []
        for name, prop in section["properties"]:
            if embedded and prop in (TSS.timeColumn, TSS.valueColumn):
                continue
            value, source, group_value = resolve_with_source(graph, snippet, template, prop)
            lines.append(make_line(graph, name, show(graph, prop), value, source, group_value))
            shown.add(prop)
        sections.append({"title": section["title"], "explanation": section["explanation"], "lines": lines})

    #    The data file is stated on the group itself, not in its template (one group = one file),
    #    so every snippet of the group reads from it. It goes first in "Data binding".
    data_file = value_of(graph, group, TSS.dataFile)
    source = "group" if data_file is not None else "missing"
    data_file_line = make_line(graph, "data file", "tss:dataFile (on the group)", data_file, source, None)
    if data_file is not None:
        data_file_line["value"]["text"] = iri_to_location(data_file)  # e.g. data/waterinfo_289423042.csv
    if embedded:
        points = embedded_points(graph, snippet)
        shown.add(TSS.points)
        sections[0]["explanation"] = "The readings are embedded directly in this snippet as JSON points."
        sections[0]["lines"].append(make_line(graph, "readings", "tss:points", Literal(len(points)), "own", None))
        sections[2]["explanation"] = "The context maps each JSON point's time and value to RDF properties; the point template supplies its shared metadata."
    else:
        sections[0]["lines"].insert(0, data_file_line)

    # 2. The SOSA part ("made by", "observes", ...) sits inside tss:about.
    #    The rule works on tss:about as a whole: a snippet with its own tss:about
    #    takes nothing from the template's tss:about.
    about, about_source, group_about = resolve_with_source(graph, snippet, template, TSS.about)
    each_reading = sections[2]  # the "Each reading is" section
    for name, prop in ABOUT_PROPERTIES:
        value = value_of(graph, about, prop)
        group_value = value_of(graph, group_about, prop)
        if value is None:
            source = "missing"
        elif about_source == "override" and group_value is not None and value != group_value:
            source = "override"
        elif about_source == "group":
            source = "group"
        else:
            source = "own"
        if source != "override":
            group_value = None
        property_name = "tss:about / " + show(graph, prop)
        each_reading["lines"].append(make_line(graph, name, property_name, value, source, group_value))

    # 3. Anything else stated on the snippet or on its template, so nothing is hidden
    other_props = set(graph.predicates(snippet))
    if template is not None:
        other_props = other_props | set(graph.predicates(template))
    other_lines = []
    for prop in sorted(other_props - shown):
        value, source, group_value = resolve_with_source(graph, snippet, template, prop)
        other_lines.append(make_line(graph, show(graph, prop), show(graph, prop), value, source, group_value))
    if len(other_lines) > 0:
        sections.append({"title": "Also stated", "explanation": "Everything else the metadata says about this series.", "lines": other_lines})

    # 4. Count where the values come from (most of them come from the group)
    counts = {"own": 0, "group": 0, "override": 0, "missing": 0}
    for section in sections:
        for line in section["lines"]:
            counts[line["source"]] += 1

    # 5. The group and its template, for the group's own panel
    group_info = None
    if group is not None:
        template_lines = []
        if template is not None:
            for prop, value in graph.predicate_objects(template):
                if prop != RDF.type:
                    template_lines.append({"property": show(graph, prop), "value": describe(graph, value)})

        member_pairs = []
        for member in graph.objects(group, TSS.hasSnippet):
            member_pairs.append((str(graph.value(member, RDFS.label)), str(member)))
        member_pairs.sort()  # by label, because the label comes first in each pair
        members = []
        for label, member_id in member_pairs:
            members.append({"id": member_id, "label": label})

        group_info = {
            "short": show(graph, group),
            "value": describe(graph, group),
            "label": show(graph, graph.value(group, RDFS.label)),
            "size": len(members),
            "template": template_lines,
            "members": members,
        }

    # 6. The source files, with the parts this series uses marked
    time_column = show(graph, resolve(graph, snippet, template, TSS.timeColumn))
    value_column = show(graph, resolve(graph, snippet, template, TSS.valueColumn))
    csv_url = iri_to_location(data_file)
    described_by = iri_to_location(value_of(graph, group, TSS.describedby))

    turtle_marks = {}
    for node, mark in [(snippet, "snippet"), (group, "group")]:
        if node is not None:
            turtle_marks[show(graph, node)] = mark  # how our files write it, e.g. ex:snippet-grootdok-ec20
            turtle_marks[f"<{node}>"] = mark        # in case a file writes the full IRI
    turtle_text = read_text(endpoint_file(endpoint_id))

    csvw_lines = None
    if file_exists(described_by):
        csvw_lines = mark_csvw(read_text(described_by), {time_column: "time", value_column: "value"})

    return {
        "label": show(graph, graph.value(snippet, RDFS.label)),
        "snippet": {"short": show(graph, snippet), "iri": str(snippet)},
        "group": group_info,
        "counts": counts,
        "sections": sections,
        "files": {
            "turtleFile": endpoint_file(endpoint_id),
            "turtle": mark_turtle(turtle_text, turtle_marks),
            "csvwFile": described_by,
            "csvw": csvw_lines,
            "embedded": embedded,
            "points": json.dumps(points, indent=2, ensure_ascii=False) if embedded else None,
            "csv": None if embedded else first_csv_rows(csv_url, described_by, time_column, value_column),
        },
    }


# ---------------------------------------------------------------------------
# The SQL box (DuckDB): read-only SQL on the CSV files of an endpoint
# ---------------------------------------------------------------------------

def sql_tables(info):
    """The SQL tables of an endpoint: one per CSV file (the tss:dataFile of a group) its
    snippets use, with the column names and types from the CSVW file"""
    tables = []
    if info["embedded"] is True:
        graph = load_graph(info["id"])
        for snippet in info["snippets"]:
            node = URIRef(snippet["id"])
            points = embedded_points(graph, node)
            numeric = numeric_points(graph, node, points)
            bound = dict(snippet, timeColumn="time", valueColumn="value")
            tables.append({
                "name": sql_box.table_name(snippet["id"].rsplit("#", 1)[-1], [table["name"] for table in tables]),
                "group": snippet["group"], "file": "tss:points", "csvw": None,
                "snippets": [bound], "points": points, "error": None,
                "columns": [{"name": "time", "type": "TIMESTAMPTZ", "role": "time"},
                            {"name": "value", "type": "DOUBLE" if numeric else "VARCHAR", "role": snippet["label"]}],
            })
        return tables
    by_file = {}  # CSV file -> its entry in tables
    for snippet in info["snippets"]:
        if snippet["csvUrl"] is None:
            continue  # a snippet without a data file (e.g. not in any group) has no CSV to load
        if snippet["csvUrl"] not in by_file:
            taken = []
            for table in tables:
                taken.append(table["name"])
            entry = {
                "name": sql_box.table_name(snippet["csvUrl"], taken),
                "group": snippet["group"],
                "file": snippet["csvUrl"],
                "csvw": snippet["describedBy"],
                "snippets": [],
                "columns": [],
                "error": None,
            }
            by_file[snippet["csvUrl"]] = entry
            tables.append(entry)
        by_file[snippet["csvUrl"]]["snippets"].append(snippet)

    for table in tables:
        csvw = csvw_columns(table["csvw"])
        if csvw["error"] is not None:
            table["error"] = csvw["error"]
            continue
        if not file_exists(table["file"]):
            table["error"] = f"CSV file not found: {table['file']}"
            continue
        # How to read the CSV, from the CSVW dialect
        for key in ["delimiter", "quote", "escape", "skip", "header", "comment"]:
            table[key] = csvw[key]
        for column in csvw["columns"]:
            # What does TSS2 say about this column?
            role = None
            for snippet in table["snippets"]:
                if column["name"] == snippet["timeColumn"]:
                    role = "time column"
                if column["name"] == snippet["valueColumn"]:
                    unit = f" ({snippet['unit']})" if snippet["unit"] else ""
                    role = f"value of: {snippet['label']}{unit}"
            table["columns"].append({
                "name": column["name"],
                "header": column["header"],
                "titles": column["titles"],
                "nulls": column["nulls"],
                "type": sql_box.duckdb_type(column["datatype"]),
                "role": role,
            })
    return tables


@api.get("/endpoints/{endpoint_id}/sql/tables")
def sql_schema(endpoint_id):
    """What the SQL box can query for this endpoint: the tables, their columns, and example queries"""
    info = get_endpoint_info(endpoint_id)
    tables = sql_tables(info)

    snippet_columns = []
    for name, sql_type in sql_box.SNIPPET_COLUMNS:
        snippet_columns.append({"name": name, "type": sql_type})

    shown_tables = []
    for table in tables:
        shown_tables.append({
            "name": table["name"],
            "group": table["group"],
            "file": table["file"],
            "csvw": table["csvw"],
            "error": table["error"],
            "columns": table["columns"],
        })

    usable = []
    for table in tables:
        if table["error"] is None:
            usable.append(table)
    return {
        "tables": shown_tables,
        "snippetColumns": snippet_columns,
        "examples": sql_box.example_queries(usable),
        "maxRows": sql_box.MAX_ROWS,
        "maxSeconds": sql_box.MAX_SECONDS,
    }


class SqlQuery(BaseModel):
    """What the browser sends: {"sql": "SELECT ..."} (in the body, because a query can be long)"""
    sql: str


@api.post("/endpoints/{endpoint_id}/sql")
def run_sql(endpoint_id, query: SqlQuery):
    """Run one read-only SQL query with DuckDB on the CSV files of this endpoint"""
    info = get_endpoint_info(endpoint_id)

    # 1. The tables to load: every CSV file of the endpoint, as a file on this computer.
    #    A table that can't be made (e.g. its CSVW file is missing) is skipped:
    #    the other tables and the snippets table still work.
    tables = []
    skipped = []
    table_names = {}  # CSV file -> SQL table name, for the snippets table
    for table in sql_tables(info):
        table_names[table["file"]] = table["name"]
        if table["error"] is not None:
            skipped.append({"name": table["name"], "error": table["error"]})
            continue
        if "points" in table:
            table_names[table["snippets"][0]["id"]] = table["name"]
            tables.append(table)
            continue
        tables.append({
            "name": table["name"],
            "file": table["file"],
            "path": local_copy(table["file"]).as_posix(),
            "delimiter": table["delimiter"],
            "quote": table["quote"],
            "escape": table["escape"],
            "skip": table["skip"],
            "header": table["header"],
            "comment": table["comment"],
            "columns": table["columns"],
        })

    # 2. The TSS2 metadata, one row per snippet, for the "snippets" table
    snippet_rows = []
    for s in info["snippets"]:
        snippet_rows.append({
            "label": s["label"],
            "snippet": s["id"],
            "sql_table": table_names.get(s["id"] if s["embedded"] else s["csvUrl"]),
            "time_column": "time" if s["embedded"] else s["timeColumn"],
            "value_column": "value" if s["embedded"] else s["valueColumn"],
            "unit": s["unit"],
            "from_time": s["from"],
            "until_time": s["until"],
            "observed_property": s["observedProperty"],
            "sensor": s["sensor"],
            "feature_of_interest": s["featureOfInterest"],
        })

    # 3. Load, lock, run, throw away
    try:
        con, not_loaded = sql_box.open_database(tables, snippet_rows)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    skipped = skipped + not_loaded  # tables without a CSVW file + CSV files that don't match theirs
    try:
        result = sql_box.run_query(con, query.sql)
        result["skipped"] = skipped  # the tables that could not be loaded, and why
        return result
    except (ValueError, sql_box.duckdb.Error) as error:
        # a mistake in the query: show DuckDB's own message, it says what is wrong.
        # If a table was not loaded, say so too: that may be why the query fails.
        message = str(error)
        for table in skipped:
            message += f"\n(Table {table['name']} was not loaded: {table['error']})"
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
