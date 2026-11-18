# rdftss

A Python library for **RDF Time Series Snippets** (TSS). It reads metadata documents and their data in all five storage classes, queries and analyses the points, finds snippets across documents, expands snippets into observations, and converts them from one storage class to another.

It implements the [RDF Time Series Snippets specification](https://knowledgeonwebscale.github.io/rdf-timeseriessnippets/), with the vocabulary `https://w3id.org/tss#` at version 0.2.0, which comes with the package. The [guide](docs/guide.md) explains every feature in detail.

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
pip install -e .            # rdflib only: RDF and JSON points, CSV files
pip install -e ".[all]"     # also Parquet and Arrow, pandas, polars and SQL
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `files` | pyarrow | Parquet and Arrow files, `to_arrow()` |
| `pandas` | pyarrow, pandas | `to_pandas()` |
| `polars` | pyarrow, polars | `to_polars()` |
| `sql` | pyarrow, duckdb, pytz | `sql()` and `Database` |

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
6. [DataFrames and SQL](#6-dataframes-and-sql)
7. [Where the data files come from](#7-where-the-data-files-come-from)
8. [Find snippets across documents](#8-find-snippets-across-documents)
9. [Expand into observations](#9-expand-into-observations)
10. [Convert to another storage class](#10-convert-to-another-storage-class)
11. [Handle errors](#11-handle-errors)

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

### 6. DataFrames and SQL

A DataFrame has one column per field, with values typed by their datatype. SQL runs in DuckDB, in memory: each snippet is a table, and the view `points` is the snippet itself. The database is locked down: no files, no network, one reading statement per query, and limits on rows, size and time.

```python
import rdftss

snippet = rdftss.load("examples/readings_csv.ttl").snippet()

frame = snippet.to_pandas()               # indexed by time (UTC)
frame["value"].resample("1h").mean()      # from here on, it is plain pandas
snippet.to_polars()                       # or polars; to_arrow() gives a pyarrow Table

result = snippet.sql("""
    SELECT quality, count(*) AS n, round(avg(value), 2) AS mean
    FROM points GROUP BY quality ORDER BY n DESC
""")
print(result)
# quality | n  | mean
# --------+----+--------
# good    | 81 | 4751.58
# suspect | 4  | 4746.49
```

For several snippets in one database, use `rdftss.Database([snippet1, snippet2])`.

### 7. Where the data files come from

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

### 8. Find snippets across documents

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

rdftss.find(documents, unit="unit:MicroS-PER-CentiM", kind="parquet")    # also: sensor, mutable, label
```

### 9. Expand into observations

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

### 10. Convert to another storage class

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

### 11. Handle errors

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
| Loading and RDF | `load(path_or_url)`, `loads(text)`, `Document`, `Snippet`, `Field`, `describe()`, `field("name")`, `how_to_read()` |
| Reading the data | `read(strict=, window=, fields=)`, `iter_points()`, `Point`, `set_data_file()`; RDF and JSON points, CSV, Parquet and Arrow; time grids; `tss:DeltaFromBaseline` |
| Data files | local paths, `http(s)` downloads (`allow_network=True` by default), `cache_folder`, `data_folder` (sandbox) |
| Querying and analysis | `between`, `where`, `having`, `statistics` (+ `differences()` from the stored summary), `outside_expected_range`, `gaps`, `missing_steps`, `resample`, `to_pandas`/`to_polars`/`to_arrow`, `sql()` and `Database` (DuckDB, locked down) |
| Discovery | `rdftss.find(documents, observed_property=, feature_of_interest=, sensor=, unit=, overlapping=, kind=, mutable=)` |
| Materialisation | `expand()` (SOSA by default, or your own `ObservationMapping`; N-Triples or N-Quads, streamed), `observations()`, `convert(to=, time_grid=, encode=, decode=, keep_texts=)`, `subset`, `split`, `merge`, `from_observations` |

Not in this version yet: QUDT alignment, conformance testing (SHACL shapes and data checks), appending points to a snippet, and a command line.

## Example documents

[`examples/`](examples/) has a metadata document for each storage class. Most hold the same synthetic day of conductivity readings: CSV with `;`, CSV without a header, a time grid, delta encoding, Parquet, and Arrow with a dictionary-encoded column. There are also the spec's embedded, JSON and multi-sensor examples. To see the library at work, run `python examples/quickstart.py`.

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
- the code in this README and in the guide, which runs as part of the tests.

## Choices this version makes

These are the points where the spec leaves the choice to the implementer:

- **New IRIs on conversion.** `convert`, `split` and `merge` give the snippet and its fields new IRIs (with a suffix such as `-parquet`), so two documents never describe one node in two ways (`iri_suffix=None` keeps them). Field names stay the same, because every converted field states its `tss:columnName`.
- **Parquet and Arrow store values, not texts.** After a conversion, `3.0E1` reads back as `30.0` and times read back in UTC. `keep_texts=True` stores strings instead.
- **`split` and `merge`.** `split` makes parts only for periods that have points. `merge` takes the smallest window that holds all windows and the earliest `tss:committedUntil`.
- **`from_observations` windows.** The window runs from the first observation until one second after the last.
- **No JSON-LD guard.** JSON-LD documents are parsed by rdflib, which may download remote `@context`s. A guard against that is planned, together with the other protections for untrusted documents.
