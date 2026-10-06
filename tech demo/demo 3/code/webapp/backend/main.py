from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from rdflib import Graph, URIRef, Literal, RDF, RDFS
from csvw import CSVW
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import url2pathname
from datetime import datetime, timezone
import requests
import csv
import json
import time

# The TSS2 inheritance rule (snippet <- group template) lives in tss2.py
from tss2 import TSS, TSS2, SOSA, QUDT, CSVW_TERMS, WDRS
from tss2 import value_of, find_group, resolve, resolve_with_source


# This file lives in demo 3/code/webapp/backend/, so demo 3/ is three folders up
DEMO_DIR = Path(__file__).resolve().parents[3]
METADATA_DIR = DEMO_DIR / "metadata endpoint"

# The online endpoints the user adds are remembered in this file
ONLINE_ENDPOINTS_FILE = Path(__file__).parent / "online_endpoints.json"

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
        "explanation": "Where the values are: the table, and the columns that hold the time and the value of each reading.",
        "properties": [("table", TSS2.table), ("time column", TSS2.timeColumn), ("value column", TSS2.valueColumn)],
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

# The snippet group, its template, and the csvw:Table the template points to.
# In this demo every metadata endpoint holds exactly one group and one table.
TABLE_QUERY = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX csvw: <http://www.w3.org/ns/csvw#>
PREFIX wdrs: <http://www.w3.org/2007/05/powder-s#>
PREFIX tss:  <https://w3id.org/tss#>
PREFIX TSS2: <https://example.org/tss2#>

SELECT ?label ?table ?csvUrl ?describedBy
       ?mutable ?startingFrom ?committedUntil ?producedBy
       ?timeColumn ?fromTime ?untilTime ?pointType ?timePath ?valuePath
WHERE {
  ?group a TSS2:SnippetGroup ;
         rdfs:label ?label ;
         TSS2:snippetTemplate ?template .

  ?template TSS2:table ?table ;
            TSS2:timeColumn ?timeColumn ;
            tss:from ?fromTime ;
            tss:until ?untilTime .

  ?table csvw:url ?csvUrl ;
         wdrs:describedby ?describedBy .

  OPTIONAL { ?template tss:pointType ?pointType }
  OPTIONAL { ?template tss:timePath ?timePath }
  OPTIONAL { ?template tss:valuePath ?valuePath }
  OPTIONAL { ?table TSS2:mutable ?mutable }
  OPTIONAL { ?table TSS2:startingFrom ?startingFrom }
  OPTIONAL { ?table TSS2:committedUntil ?committedUntil }
  OPTIONAL { ?table TSS2:producedBy ?producedBy }
}
"""

# One row per snippet (= one time series = one value column in the CSV).
# Only what a snippet states itself. The values it can inherit from its group's
# template (columns, time window, tss:about, ...) are looked up with the TSS2
# rule in get_endpoint_info.
SNIPPETS_QUERY = """
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX TSS2:    <https://example.org/tss2#>

SELECT ?snippet ?label ?identifier ?comment
WHERE {
  ?snippet a TSS2:Snippet ;
           rdfs:label ?label .

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
# The browser only lets the frontend read our answers if its address is in this list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def read_csvw(described_by):
    """Read a CSVW file. Returns (delimiter, headers), where headers maps every
    column name (what TSS2 uses, e.g. "timestamp") to its CSV header (e.g. "Timestamp").
    Returns None when there is no such file."""
    if not file_exists(described_by):
        return None

    metadata = json.loads(read_text(described_by))
    delimiter = metadata.get("dialect", {}).get("delimiter", ",")
    headers = {}
    for column in metadata["tableSchema"]["columns"]:
        headers[column["name"]] = column.get("titles", column["name"])
    return delimiter, headers


def get_endpoint_info(endpoint_id):
    """Everything the dashboard needs to know about one metadata endpoint"""
    graph = load_graph(endpoint_id)

    rows = list(graph.query(TABLE_QUERY))
    if len(rows) == 0:
        raise HTTPException(status_code=404, detail=f"No TSS2:SnippetGroup with a csvw:Table found in {endpoint_file(endpoint_id)}")
    row = rows[0]

    info = {
        "id": endpoint_id,
        "file": endpoint_file(endpoint_id),
        "online": is_url(endpoint_file(endpoint_id)),
        "label": str(row.label),
        # the table
        "table": show(graph, row.table),
        "csvUrl": iri_to_location(row.csvUrl),
        "describedBy": iri_to_location(row.describedBy),
        "mutable": show(graph, row.mutable),
        "startingFrom": show(graph, row.startingFrom),
        "committedUntil": show(graph, row.committedUntil),
        "producedBy": show(graph, row.producedBy),
        # the snippet template
        "timeColumn": str(row.timeColumn),
        "from": str(row.fromTime),
        "until": str(row.untilTime),
        "pointType": show(graph, row.pointType),
        "timePath": show(graph, row.timePath),
        "valuePath": show(graph, row.valuePath),
        # the snippets, filled in below
        "snippets": [],
    }

    for row in graph.query(SNIPPETS_QUERY):
        snippet = row.snippet
        group, template = find_group(graph, snippet)
        table = resolve(graph, snippet, template, TSS2.table)
        about = resolve(graph, snippet, template, TSS.about)
        unit = value_of(graph, about, QUDT.hasUnit)

        info["snippets"].append({
            "id": str(snippet),
            "label": str(row.label),
            "identifier": show(graph, row.identifier),
            "comment": show(graph, row.comment),
            # Everything below can come from the group's template (the TSS2 rule)
            "valueColumn": show(graph, resolve(graph, snippet, template, TSS2.valueColumn)),
            "timeColumn": show(graph, resolve(graph, snippet, template, TSS2.timeColumn)),
            "from": show(graph, resolve(graph, snippet, template, TSS["from"])),
            "until": show(graph, resolve(graph, snippet, template, TSS["until"])),
            "csvUrl": iri_to_location(value_of(graph, table, CSVW_TERMS.url)),
            "describedBy": iri_to_location(value_of(graph, table, WDRS.describedby)),
            "observedProperty": show(graph, value_of(graph, about, SOSA.observedProperty)),
            "unit": READABLE_NAMES.get(str(unit), show(graph, unit)),
            "sensor": show(graph, value_of(graph, about, SOSA.madeBySensor)),
            "featureOfInterest": show(graph, value_of(graph, about, SOSA.hasFeatureOfInterest)),
        })

    return info


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
        "property": property_name,                    # the RDF property, e.g. TSS2:timeColumn
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

@app.get("/")
def hello():
    return {"message": "Hello from the demo 3 backend"}


@app.get("/endpoints")
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


@app.post("/endpoints/online")
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


@app.delete("/endpoints/{endpoint_id}")
def remove_online_endpoint(endpoint_id):
    """Take an online endpoint out of the list (the local .ttl files can't be removed here)"""
    online = load_online_endpoints()
    if endpoint_id not in online:
        raise HTTPException(status_code=404, detail=f"{endpoint_id} is not an online endpoint")
    del online[endpoint_id]
    save_online_endpoints(online)
    return {"removed": endpoint_id}


@app.get("/endpoints/{endpoint_id}")
def get_endpoint(endpoint_id):
    """What one metadata endpoint holds, plus its raw Turtle text"""
    info = get_endpoint_info(endpoint_id)
    info["turtle"] = read_text(endpoint_file(endpoint_id))
    return info


@app.get("/endpoints/{endpoint_id}/csv")
def get_csv_file(endpoint_id, lines: int = 20):
    """The data file the endpoint points to with csvw:url.
    These files are big (up to 17 MB), so we only send the first lines."""
    info = get_endpoint_info(endpoint_id)
    lines = max(1, min(lines, 1000))
    location = info["csvUrl"]
    if not file_exists(location):
        return {"path": location, "found": False}

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


@app.get("/endpoints/{endpoint_id}/csv/download")
def download_csv_file(endpoint_id):
    """The whole CSV file (csvw:url), sent as a download"""
    info = get_endpoint_info(endpoint_id)
    return send_as_download(info["csvUrl"], "text/csv")


@app.get("/endpoints/{endpoint_id}/csvw")
def get_csvw_file(endpoint_id):
    """The CSVW metadata file the endpoint points to with wdrs:describedby"""
    info = get_endpoint_info(endpoint_id)
    location = info["describedBy"]
    if not file_exists(location):
        return {"path": location, "found": False}

    return {
        "path": location,
        "found": True,
        "note": "",
        "text": read_text(location),
    }


@app.get("/endpoints/{endpoint_id}/csvw/download")
def download_csvw_file(endpoint_id):
    """The CSVW metadata file (wdrs:describedby), sent as a download"""
    info = get_endpoint_info(endpoint_id)
    return send_as_download(info["describedBy"], "application/json")


@app.get("/endpoints/{endpoint_id}/validate")
def validate(endpoint_id):
    """Run the csvw validator: it reads the CSV through its CSVW metadata and
    checks every row against the table schema (datatypes, required values, headers)."""
    info = get_endpoint_info(endpoint_id)
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
        data = CSVW(source, validate=True)
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
        "valid": data.is_valid,
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


@app.get("/endpoints/{endpoint_id}/filter-columns")
def filter_columns(endpoint_id, snippet):
    """The other CSV columns the query form can filter on (like a quality code),
    with the values that occur in them. Only columns with a few different values
    (2 to 30) are useful for that, so only those are sent."""
    info = get_endpoint_info(endpoint_id)
    chosen = find_snippet(info, snippet)
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


@app.get("/endpoints/{endpoint_id}/query")
def query_series(endpoint_id, snippet, start=None, end=None, min_value=None, max_value=None,
                 filter_column=None, filter_value=None, resample="", aggregate="mean", limit: int = 100):
    """Get the readings of one snippet (one time series), with the filters of the query form,
    plus summary numbers and the gaps in the data.

    TSS2 tells us which CSV (csvw:url of the table), which columns
    (TSS2:timeColumn, TSS2:valueColumn) and which time window (tss:from,
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

    # 2. How to read the CSV (from the CSVW file)
    delimiter, headers, time_header, value_header = snippet_csv(chosen)
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
    for row in csv.DictReader(read_lines(chosen["csvUrl"]), delimiter=delimiter):
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
        value = float(text) if text != "" else None  # an empty cell means: no reading
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
        "summary": summarize(kept),   # over the single readings that passed all filters
        "gaps": find_gaps(moments),   # over every reading in the time range
        "totalRows": len(rows),
        "limitReached": len(rows) > limit,
        "readings": rows[:limit],     # [:limit] = only the first "limit" rows
    }


@app.get("/endpoints/{endpoint_id}/series/metadata")
def series_metadata(endpoint_id, snippet):
    """The series inspector: everything known about one snippet, and where each
    value comes from: stated on the snippet ("own"), inherited from its group's
    template ("group"), or stated on both with different values ("override")."""
    graph = load_graph(endpoint_id)
    snippet = URIRef(snippet)
    if (snippet, RDF.type, TSS2.Snippet) not in graph:
        raise HTTPException(status_code=404, detail=f"Unknown snippet: {snippet}")
    group, template = find_group(graph, snippet)

    # 1. The lines of each section, with the TSS2 rule applied to every property
    sections = []
    shown = {RDF.type, RDFS.label, TSS.about}  # properties that never get a line of their own here
    for section in INSPECTOR_SECTIONS:
        lines = []
        for name, prop in section["properties"]:
            value, source, group_value = resolve_with_source(graph, snippet, template, prop)
            lines.append(make_line(graph, name, show(graph, prop), value, source, group_value))
            shown.add(prop)
        sections.append({"title": section["title"], "explanation": section["explanation"], "lines": lines})

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
        for member in graph.objects(group, TSS2.hasSnippet):
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
    table = resolve(graph, snippet, template, TSS2.table)
    time_column = show(graph, resolve(graph, snippet, template, TSS2.timeColumn))
    value_column = show(graph, resolve(graph, snippet, template, TSS2.valueColumn))
    csv_url = iri_to_location(value_of(graph, table, CSVW_TERMS.url))
    described_by = iri_to_location(value_of(graph, table, WDRS.describedby))

    turtle_marks = {}
    for node, mark in [(snippet, "snippet"), (group, "group"), (table, "table")]:
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
            "csv": first_csv_rows(csv_url, described_by, time_column, value_column),
        },
    }
