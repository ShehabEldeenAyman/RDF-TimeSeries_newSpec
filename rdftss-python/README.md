# rdftss

A Python library for **RDF Time Series Snippets** (TSS). It reads metadata documents and their data in all five storage classes, queries the points with SPARQL and analyses them, finds snippets across documents, expands snippets into observations, and converts them from one storage class to another. It also checks documents against the specification: with its SHACL shapes, by recomputing stored summaries, and with a conformance test suite.

It implements the [RDF Time Series Snippets specification](https://knowledgeonwebscale.github.io/rdf-timeseriessnippets/), with the vocabulary `https://w3id.org/tss#` at version 0.3.0, which comes with the package. The [guide](docs/guide.md) explains every feature in detail.

```python
import rdftss

document = rdftss.load("examples/readings_csv.ttl")
snippet = document.snippet()
print(snippet.describe())

points = snippet.read()                       # in time order, inside the window, decoded
print(points.statistics("value"))             # count, mean, median, ... as tss:summary defines them
frame = snippet.to_pandas()                   # a DataFrame indexed by time

snippet.expand("out/observations.nt")         # SOSA observations, streamed to a file
rdftss.convert(snippet, "parquet", document="out/readings.ttl")   # writes out/readings.parquet
```

## Install

Python 3.10 or newer. From this folder, one command installs rdftss and every library it uses, as listed in [requirements.txt](requirements.txt):

```bash
pip install -r requirements.txt
```

To install only what you need:

```bash
pip install -e .            # rdflib only: RDF and JSON points, CSV files, SPARQL
pip install -e ".[all]"     # also Parquet and Arrow, pandas, polars and SHACL validation
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `files` | pyarrow | Parquet and Arrow files, `to_arrow()` |
| `pandas` | pyarrow, pandas | `to_pandas()` |
| `polars` | pyarrow, polars | `to_polars()` |
| `shacl` | pyshacl | `validate()` |

A feature whose package is missing raises an error that tells you what to install.

## The model in one picture

```text
Document  ── one metadata document (rdftss.load)
 └─ Snippet ── a table of readings; its class says where the points are
     ├─ Field ── one column: datatype, column, unit, observed property, summary, ...
     └─ Point ── one row: a time and a value per field (snippet.read())
```

- **The storage is the snippet's class.** The spec requires each snippet to state exactly one class (`tss:EmbeddedRDFSnippet`, `tss:EmbeddedJSONSnippet`, `tss:CSVSnippet`, `tss:ParquetSnippet` or `tss:ArrowSnippet`). A table maps the class to its reader, so nothing is guessed, and a data file's name is never inspected. rdflib finds the RDF syntax of the document itself from the file name or media type.
- **Every text is kept exactly as written.** By default, rdflib rewrites literals while parsing (`" 12 "^^xsd:double` becomes `12.0`, `"yes"^^xsd:boolean` becomes `false`). rdftss turns that off, so wrong values are reported instead of silently fixed, and observations carry the data's own texts. [`rdf.py`](src/rdftss/rdf.py) explains it.
- **Times are moments.** `…T10:00:00+02:00` and `…T08:00:00Z` are the same time. Comparisons use exact instants, including nanoseconds.
- **Problems are issues, errors are errors.**
  - What breaks the spec in the metadata is listed in `document.all_issues`; loading does not fail on it.
  - What stops a snippet from being read is in `snippet.reading_problems`; reading such a snippet raises a `DocumentError` that lists it.
  - A data error that the spec names (a point without a time, two points at the same time, a value not in its lexical form, …) raises a `DataError` that says where, for example `CSV line 12`. With `read(strict=False)`, the bad value is left out and the problem is listed in `points.issues` instead.

## Usage examples

The examples use the documents in [`examples/`](examples/) and run from this folder. Each one stands on its own. The comments show what a line gives for the example data, and files the examples write go to `out/`.

1. [Load a document](#1-load-a-document)
2. [Look at the fields](#2-look-at-the-fields)
3. [Read the points](#3-read-the-points)
4. [Select points](#4-select-points)
5. [Statistics, gaps and resampling](#5-statistics-gaps-and-resampling)
6. [DataFrames](#6-dataframes)
7. [Query with SPARQL](#7-query-with-sparql)
8. [Where the data files come from](#8-where-the-data-files-come-from)
9. [Find snippets across documents](#9-find-snippets-across-documents)
10. [Expand into observations](#10-expand-into-observations)
11. [Convert to another storage class](#11-convert-to-another-storage-class)
12. [Validate a document with SHACL](#12-validate-a-document-with-shacl)
13. [Check stored summaries](#13-check-stored-summaries)
14. [Growing snippets](#14-growing-snippets)
15. [Handle errors](#15-handle-errors)

### 1. Load a document

A metadata document describes one or more snippets. `load` reads it from a path or a URL; `loads` reads it from a string.

```python
import rdftss

# rdflib finds the RDF syntax (Turtle, JSON-LD, RDF/XML, ...) from the file name or media type
document = rdftss.load("examples/readings_csv.ttl")
print(document.describe())        # every snippet: its class, data file, window and fields

snippet = document.snippet()                     # the only snippet
snippet = document.snippet("ex:csv-snippet")     # or one of several: by IRI, prefixed name, label or position
snippet.kind                      # SnippetKind.CSV: the snippet's class says where its points are
print(snippet.window)             # [2024-05-12T00:00:00Z, 2024-05-13T00:00:00Z)
snippet.data_file                 # 'file:///.../examples/sample-data/readings.csv'
```

`for snippet in document:` goes through all the snippets of a document.

### 2. Look at the fields

Each field is a column of the snippet. Its role says what it holds: the time, a measured value (a field with a `tss:observedProperty`), or a value that describes another one, such as a quality code.

```python
import rdftss

snippet = rdftss.load("examples/readings_csv.ttl").snippet()
for field in snippet.fields:
    print(f"{field.name}: {field.role}")     # time: time, value: measured, quality: describing

value = snippet.field("value")     # by name, column name, IRI, prefixed name or label
print(value.describe())            # label, column, datatype, unit, observed property, sensor, ...
value.unit                         # URIRef('http://qudt.org/vocab/unit/MicroS-PER-CentiM')
value.minimum, value.maximum       # (0.0, 10000.0): the expected range
```

### 3. Read the points

Every storage class is read the same way: `read()` gives the points inside the snippet's window, in time order, with each value decoded to a Python value of its datatype. An empty value (an empty cell, `null`, a missing triple, `""`) is `None`.

```python
import rdftss

snippet = rdftss.load("examples/readings_csv.ttl").snippet()
points = snippet.read()
print(points)               # <Points of csv-snippet: 85, 2024-05-12T00:00:00Z .. 2024-05-12T23:45:00Z>

point = points[0]
point.time                  # datetime(2024, 5, 12, 0, 0, tzinfo=timezone.utc)
point["value"]              # 4747.0: a float, because the field's datatype is xsd:double
point.raw("value")          # '4747.00': the text in the data file
point["quality"]            # 'suspect'

# read() keeps every point in memory; iter_points() streams them, for very large files
total = sum(1 for point in snippet.iter_points())     # 85
```

### 4. Select points

Selections give new `Points`, so they can be chained. Besides `equals` and `above`, `where` takes `one_of`, `below`, `at_least` and `at_most`, or any function of a point.

```python
import rdftss

points = rdftss.load("examples/readings_csv.ttl").snippet().read()

morning = points.between("2024-05-12T06:00:00Z", "2024-05-12T12:00:00Z")   # from 06:00, before 12:00: 24 points
good = points.where("quality", equals="good")                               # 81 points
high = points.where("value", above=4800)                                    # 14 points
custom = points.where(lambda p: p["value"] > 4790 and p["quality"] == "good")   # 22 points

good.between("2024-05-12T00:00:00Z", "2024-05-12T01:00:00Z").values("value")   # [4754.42, 4755.33, 4762.71]
```

### 5. Statistics, gaps and resampling

Statistics follow the definitions of `tss:summary`: `count` is the number of points with a value, and `missing` the number without one. Gaps are the intervals longer than the usual one. Resampling works per period in UTC: `"15min"`, `"1h"`, `"1d"`, `"1w"`, `"1 month"`, or an `xsd:duration` such as `"PT15M"`.

```python
import rdftss

points = rdftss.load("examples/readings_csv.ttl").snippet().read()

statistics = points.statistics("value")
statistics.count, statistics.missing        # (85, 0)
statistics.minimum, statistics.maximum      # (4689.15, 4813.0)
statistics.median                           # 4752.92

print(points.gaps())
# Usual step: 15 min; 3 gap(s), 2 h 45 min missing in total.
#   2024-05-12T17:15:00Z .. 2024-05-12T19:00:00Z (1 h 45 min)
#   ...

for period in points.resample("6h", "value", how="mean"):    # or min, max, sum, count, median, first, last
    print(period)                                            # 2024-05-12T00:00:00Z  4782.2125  (20 values)

# Is a stored tss:summary still up to date?
stored = rdftss.load("examples/embedded_rdf.ttl").snippet()
stored.statistics().differences()           # {}: the summary matches the data
```

### 6. DataFrames

A DataFrame has one column per field, with values typed by their datatype.

```python
import rdftss

snippet = rdftss.load("examples/readings_csv.ttl").snippet()

frame = snippet.to_pandas()               # indexed by time (UTC)
frame["value"].resample("1h").mean()      # from here on, it is plain pandas
snippet.to_polars()                       # or polars; to_arrow() gives a pyarrow Table
```

### 7. Query with SPARQL

SPARQL queries run in memory, with rdflib, on one graph that holds the points and the metadata. Whatever its storage class, each snippet's points are in the graph as embedded RDF points: the snippet links to each point with `tss:hasPoint`, and a point has one triple per value, with the field's IRI as the predicate. The document's own statements are there as well, so a query can join the points with their fields, units, sensors and summaries. Every query knows the document's prefixes.

```python
import rdftss

snippet = rdftss.load("examples/readings_csv.ttl").snippet()

# The number of readings and their mean per quality code
result = snippet.sparql("""
    SELECT ?quality (COUNT(?point) AS ?n) (ROUND(AVG(?value) * 100) / 100 AS ?mean)
    WHERE { ?point ex:csv-value-field ?value ; ex:csv-quality-field ?quality . }
    GROUP BY ?quality ORDER BY DESC(?n)
""")
print(result)
# quality | n  | mean
# --------+----+--------
# good    | 81 | 4751.58
# suspect | 4  | 4746.49
result.rows                 # [('good', 81, Decimal('4751.58')), ('suspect', 4, Decimal('4746.49'))]

# Points and metadata together: every value measured in µS/cm, from 23:00 on
result = snippet.sparql("""
    SELECT ?time ?value WHERE {
        ?snippet tss:hasPoint ?point ; tss:timeField ?time_field .
        ?field tss:unit unit:MicroS-PER-CentiM .
        ?point ?time_field ?time ; ?field ?value .
        FILTER(?time >= "2024-05-12T23:00:00Z"^^xsd:dateTime)
    } ORDER BY ?time
""")
len(result)                 # 4

# For several queries or several snippets, build the graph once
with rdftss.TripleStore([snippet]) as store:
    print(store.describe())             # the predicate and datatype of each field
    print(store.examples()[0])          # example queries with the snippet's own IRIs
    store.query("ASK { ?point ex:csv-value-field ?value FILTER(?value > 4800) }")   # True
```

Good to know:

- **Times compare as moments.** rdflib can't compare `xsd:dateTimeStamp` values, so in the graph every time is an `xsd:dateTime` literal with the same text: `…T10:00:00+02:00` equals `…T08:00:00Z`.
- **Values keep the text of the data.** A shared variable matches identical RDF terms, so `"4747.00"` from a CSV file doesn't match `"4747.0"` from a Parquet file. To compare numbers, use two variables and `FILTER(?a = ?b)`.
- **Queries only read.** SPARQL Update is not accepted, `SERVICE` and `FROM` are refused (they would reach other servers), and an answer is limited to 10,000 rows and 30 seconds by default (`max_rows=`, `timeout=`).
- **The graph lives in memory.** 200,000 points with three fields take about 10 seconds to load and over 1 GB. Load only the period and the fields you need: `TripleStore([snippet], between=(start, end), fields=["value"])`.

### 8. Where the data files come from

`tss:dataFile` is resolved against the document's own address. So a relative path finds the data next to the document, both on disk and online.

```python
import rdftss

# Data files at http(s) addresses, or next to an online document, are downloaded:
# document = rdftss.load("https://example.org/metadata/readings.ttl")
# document = rdftss.load("https://example.org/metadata/readings.ttl", cache_folder="downloads")   # kept between runs

# For a document you don't trust: never download, and read only files inside one folder
document = rdftss.load("examples/readings_csv.ttl", allow_network=False, data_folder="examples/sample-data")

# Read your own copy of the data instead of the file that tss:dataFile names
snippet = document.snippet()
snippet.set_data_file("examples/sample-data/readings.csv")    # a path or a binary file object
len(snippet.read())                                           # 85
```

### 9. Find snippets across documents

A `Document` holds one metadata document. To search several, load them and give the list to `find`. It matches IRIs exactly, with each document's own prefixes; windows that only touch do not overlap.

```python
from pathlib import Path

import rdftss

documents = [rdftss.load(path) for path in sorted(Path("examples").glob("*.ttl"))]

matches = rdftss.find(
    documents,
    observed_property="quantitykind:ElectricConductivity",    # a prefixed name or a full IRI
    feature_of_interest="ex:feature-1",
    overlapping=("2024-05-12T08:00:00Z", "2024-05-12T09:00:00Z"),
)
for match in matches:
    print(match)                    # ex:csv-snippet (CSVSnippet, window [...)) in examples/readings_csv.ttl: value
    points = match.snippet.read()   # each match holds its snippet, ready to read, and the fields that matched

rdftss.find(documents, unit="unit:MicroS-PER-CentiM", kind="parquet")    # also: sensor, immutable, label
```

### 10. Expand into observations

Each value of a measured field inside the window becomes a SOSA observation, written with SOSA, QUDT and Dublin Core as the specification's informative mapping does. The output also holds what the document says about the sensors, features of interest, observed properties and units, and no TSS terms. A file is replaced only when the whole expansion worked.

```python
import rdftss

snippet = rdftss.load("examples/readings_csv.ttl").snippet()

result = snippet.expand("out/observations.nt")    # N-Triples, written in batches (.nq gives N-Quads)
print(result)             # Observations: 85, Triples: 684, the output file and the count per field

# A small snippet can stay in memory, as an rdflib Graph
graph = rdftss.load("examples/embedded_rdf.ttl").snippet().expand().graph
len(graph)                # 44 triples
```

`expand(..., mapping=...)` writes the observations with another vocabulary; the guide shows how.

### 11. Convert to another storage class

`convert` writes a snippet in another storage class (`"csv"`, `"parquet"`, `"arrow"`, `"json"` or `"rdf"`), as a new metadata document plus data file, and returns the new document. The points, labels, window and the meaning of every field stay the same.

```python
import rdftss

snippet = rdftss.load("examples/readings_csv.ttl").snippet()

parquet = rdftss.convert(snippet, "parquet", document="out/readings.ttl")     # data: out/readings.parquet
parquet.snippet().read().values("value") == snippet.read().values("value")    # True: the same points

rdftss.convert(snippet, "json", document="out/readings-json.ttl")             # points inside the metadata
rdftss.convert(snippet, "csv", document="out/grid.ttl", time_grid="auto")     # times as steps of a grid

parts = rdftss.split(snippet, "6h", document="out/parts.ttl")                 # one snippet per 6 hours
len(parts.snippets)                                                           # 4
```

`subset`, `merge` and `from_observations` are in the [guide](docs/guide.md#10-converting-snippets).

### 12. Validate a document with SHACL

`validate()` checks a metadata document with the SHACL shapes of the specification, which come with the package. The snippet classes choose the shapes: one file for embedded snippets, which also checks their RDF points, and one for external snippets, which checks the metadata but not the data file. The document is checked as it is written, so a value such as `" 4810.5 "^^xsd:double` is found. It needs `pyshacl` (the `shacl` extra).

```python
import rdftss

report = rdftss.load("examples/embedded_rdf.ttl").validate()
report.conforms         # True: no violation (warnings don't change that)
print(report)           # examples/embedded_rdf.ttl: CONFORMS (0 violations, 0 warnings) with the embedded shapes

# The same document with a time that has no timezone
text = open("examples/embedded_rdf.ttl").read().replace('tss:from "2024-05-12T08:00:00Z"', 'tss:from "2024-05-12T08:00:00"')
report = rdftss.loads(text).validate()
report.conforms         # False
for result in report.results:
    print(result)       # Violation  S23   ex:snippet  S23 timezone: 2024-05-12T08:00:00 (the snippet's tss:from) has no timezone. ...
report.checks           # {'S23'}: the IDs of the checks that failed
report.to_turtle("out/report.ttl")      # the sh:ValidationReport as RDF
```

Every check has an ID: S for the snippet, F for a field, P for an RDF point, D for a value encoding and Q for a summary. `validate(shapes=...)` takes `"embedded"`, `"external"` or your own shapes file. [docs/conformance.md](docs/conformance.md) explains the checks and the conformance test suite.

### 13. Check stored summaries

A stored `tss:summary` is not refreshed by itself. `verify_summaries()` recomputes every number a summary states from the points in the time window, and names the numbers that differ. Times are compared as moments, and floating-point numbers within a relative `tolerance` (1e-12).

```python
import rdftss

report = rdftss.load("examples/embedded_rdf.ttl").verify_summaries()
report.up_to_date       # True
print(report)
# 1 stored summary: 1 up to date, 0 out of date
#   ex:value-field: up to date (tss:count, tss:missing, tss:firstTime, tss:lastTime, ...)

# A summary that was not recomputed after a change
text = open("examples/embedded_rdf.ttl").read().replace('tss:count "5"', 'tss:count "4"')
report = rdftss.loads(text).verify_summaries()
print(report.out_of_date[0])        # ex:value-field: OUT OF DATE: tss:count is 4, the data gives 5
```

### 14. Growing snippets

A snippet that states `tss:immutable true` is final: no point is added any more. Without it, the snippet is *growing*: points may still be added, but none is changed or removed. A growing snippet has no stored summaries, and its time window does not change. The metadata document of a growing `tss:EmbeddedRDFSnippet` is an RDF Message Log: Turtle that starts with `VERSION "1.2-messages"` and ends every message with the delimiter `MESSAGE`, one point per message ([`examples/growing_rdf.ttl`](examples/growing_rdf.ttl)).

```python
import rdftss

document = rdftss.load("examples/growing_rdf.ttl")
snippet = document.snippet()
snippet.growing                 # True: it does not state tss:immutable true
document.message_log            # True: read as the merge of its messages
document.messages               # 4: the description, then one message per point
len(snippet.read())             # 3

document.save("out/growing.ttl")     # written as a log again, so a new point is one more message

rdftss.load("examples/readings_csv.ttl").snippet().immutable    # True
```

rdftss reads what a growing snippet holds right now. A last message without its delimiter, a last CSV record without its line break, and a last Arrow stream message that is not complete are all still being written: they are left out, with a warning. An Arrow data file can be in the IPC file format or the IPC streaming format; the first bytes of the file tell them apart.

### 15. Handle errors

Reading stops at the first data error that the specification names, and says where it is. With `strict=False`, the bad value is left out and the problem is listed instead. Every error rdftss raises is an `rdftss.RDFTSSError`.

```python
import io

import rdftss

snippet = rdftss.load("examples/readings_csv.ttl").snippet()
bad = b"time;value;quality\n2024-05-12T00:00:00Z;4747,00;good\n2024-05-12T00:15:00Z;4754.42;good\n"

snippet.set_data_file(io.BytesIO(bad))
try:
    snippet.read()
except rdftss.DataError as error:
    print(error)            # CSV line 2: field 'value': '4747,00' is not an xsd:double

snippet.set_data_file(io.BytesIO(bad))
points = snippet.read(strict=False)
print(points.issues)        # error: CSV line 2: field 'value': '4747,00' is not an xsd:double
points[0]["value"]          # None

# What the metadata gets wrong is listed, not raised
print(rdftss.load("examples/readings_csv.ttl").all_issues)    # No issues.
```

## What it does

| Area | Main entry points |
| --- | --- |
| Loading and RDF | `load(path_or_url)`, `loads(text)` (also RDF Message Logs), `Document`, `Snippet`, `Field`, `describe()`, `field("name")`, `how_to_read()` |
| Reading the data | `read(strict=, window=, fields=)`, `iter_points()`, `Point`, `set_data_file()`; RDF and JSON points, CSV, Parquet and Arrow; time grids; `tss:DeltaFromBaseline` |
| Data files | local paths, `http(s)` downloads (`allow_network=True` by default), `cache_folder`, `data_folder` (sandbox) |
| Querying and analysis | `between`, `where`, `having`, `statistics` (+ `differences()` from the stored summary), `outside_expected_range`, `gaps`, `missing_steps`, `resample`, `to_pandas`/`to_polars`/`to_arrow`, `sparql()` and `TripleStore` (SPARQL with rdflib, read only) |
| Discovery | `rdftss.find(documents, observed_property=, feature_of_interest=, sensor=, unit=, overlapping=, kind=, immutable=)` |
| Conformance | `validate()` (the specification's SHACL shapes, or yours), `verify_summaries()`, and the test suite in [`conformance/`](conformance/) |
| Materialisation | `expand()` (SOSA by default, or your own `ObservationMapping`; N-Triples or N-Quads, streamed), `observations()`, `convert(to=, time_grid=, encode=, decode=, keep_texts=)`, `subset`, `split`, `merge`, `from_observations` |

Not in this version yet: QUDT alignment, checks of the data beyond what the SHACL shapes see, appending points to a growing snippet and ending it, following a growing snippet over HTTP (polling and `Range` requests), and a command line.

## Example documents

[`examples/`](examples/) has a metadata document for each storage class. Most hold the same synthetic day of conductivity readings: CSV with `;`, CSV without a header, a time grid, delta encoding, Parquet, and Arrow with a dictionary-encoded column. There are also the spec's embedded, JSON and multi-sensor examples, and a growing snippet written as an RDF Message Log. To see the library at work, run `python examples/quickstart.py`.

## Tests

```bash
pip install -r requirements.txt     # once: it includes pytest
python -m pytest
```

The tests use the example documents. They check, among other things:

- the spec's examples: the summary numbers and the five observations;
- every rule the spec calls an error;
- the Parquet and Arrow type tables;
- conversions between every pair of classes that must keep the same points;
- the code in this README and in the guide, which runs as part of the tests;
- the conformance test suite in [`conformance/`](conformance/): 184 tests of metadata documents, reading, stored summaries and expansion ([docs/conformance.md](docs/conformance.md)).

## Choices this version makes

These are the points where the spec leaves the choice to the implementer:

- **New IRIs on conversion.** `convert`, `split` and `merge` give the snippet and its fields new IRIs (with a suffix such as `-parquet`), so two documents never describe one node in two ways (`iri_suffix=None` keeps them). Field names stay the same, because every converted field states its `tss:columnName`.
- **Parquet and Arrow store values, not texts.** After a conversion, `3.0E1` reads back as `30.0` and times read back in UTC. `keep_texts=True` stores strings instead.
- **`split` and `merge`.** `split` makes parts only for periods that have points. `merge` takes the smallest window that holds all windows; the merged snippet is immutable when all its parts are.
- **`from_observations` windows.** The window runs from the first observation until one second after the last, and the new snippet is immutable.
- **Converting a growing snippet.** The copy is growing too, without summaries, except as Parquet, which is always immutable. A growing Arrow copy is written in the IPC streaming format (`.arrows`), and a growing RDF copy as an RDF Message Log.
- **No JSON-LD guard.** JSON-LD documents are parsed by rdflib, which may download remote `@context`s. A guard against that is planned, together with the other protections for untrusted documents.
