# rdftss

A Python library for **RDF Time Series Sources** (TSS). It reads metadata documents and the rows of their sources in all five storage classes, analyses them and queries their points with SPARQL, finds sources across documents, expands sources into points as RDF (and derives SOSA and SAREF observations from them), and converts sources from one storage class to another. It also checks documents against the specification: with its SHACL shapes, by recomputing stored summaries, and with a conformance test suite.

It implements the [RDF Time Series Sources specification](https://knowledgeonwebscale.github.io/rdf-tss/), with the vocabulary `https://w3id.org/tss#` of 2026-11-20, which comes with the package. The [guide](docs/guide.md) explains every feature in detail.

```python
import rdftss

document = rdftss.load("examples/readings_csv.ttl")
source = document.source()
print(source.describe())

rows = source.read()                          # in time order, inside the window, decoded
print(rows.statistics("value"))               # count, mean, median, ... as tss:summary defines them
frame = source.to_pandas()                    # a DataFrame indexed by time

source.expand("out/points.nt")                # the points as RDF, streamed to a file
rdftss.convert(source, "parquet", document="out/readings.ttl")   # writes out/readings.parquet
```

## Install

Python 3.10 or newer. From this folder, one command installs rdftss and every library it uses, as listed in [requirements.txt](requirements.txt):

```bash
pip install -r requirements.txt
```

To install only what you need:

```bash
pip install -e .            # rdflib only: RDF points, JSON rows, CSV files, SPARQL
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
 └─ Source ── a table of readings; its class says where its rows are
     ├─ Field ── one column: a value field (its values are readings) or a context field
     │           (the time, a step, a describing field such as a quality code, ...)
     ├─ Row   ── one time, and at most one value for each field (source.read())
     └─ Point ── one reading: the value of a value field in a row (source.points())
```

- **The storage is the source's class.** The spec requires each source to state exactly one class (`tss:EmbeddedRDFSource`, `tss:EmbeddedJSONSource`, `tss:CSVSource`, `tss:ParquetSource` or `tss:ArrowSource`). A table maps the class to its reader, so nothing is guessed, and a data file's name is never inspected. rdflib finds the RDF syntax of the document itself from the file name or media type.
- **Rows and points.** A row holds one time and the values of the fields. Each value of a value field in it is one point: in RDF, a node with `tss:field`, `tss:time` and `tss:value`. The multi-sensor example of the specification has three rows that hold five points.
- **Every text is kept exactly as written.** By default, rdflib rewrites literals while parsing (`" 12 "^^xsd:double` becomes `12.0`, `"yes"^^xsd:boolean` becomes `false`). rdftss turns that off, so wrong values are reported instead of silently fixed, and points carry the data's own texts. [`rdf.py`](src/rdftss/rdf.py) explains it.
- **Times are moments.** `…T10:00:00+02:00` and `…T08:00:00Z` are the same time. Comparisons use exact instants, including nanoseconds.
- **Problems are issues, errors are errors.**
  - What breaks the spec in the metadata is listed in `document.all_issues`; loading does not fail on it.
  - What stops a source from being read is in `source.reading_problems`; reading such a source raises a `DocumentError` that lists it.
  - A data error that the spec names (a row without a time, two rows at the same time, a value not in its lexical form, …) raises a `DataError` that says where, for example `CSV line 12`. With `read(strict=False)`, the bad value is left out and the problem is listed in `rows.issues` instead.

## Usage examples

The examples use the documents in [`examples/`](examples/) and run from this folder. Each one stands on its own. The comments show what a line gives for the example data, and files the examples write go to `out/`.

1. [Load a document](#1-load-a-document)
2. [Look at the fields](#2-look-at-the-fields)
3. [Read the rows and the points](#3-read-the-rows-and-the-points)
4. [Select rows](#4-select-rows)
5. [Statistics, gaps and resampling](#5-statistics-gaps-and-resampling)
6. [DataFrames](#6-dataframes)
7. [Query with SPARQL](#7-query-with-sparql)
8. [Where the data files come from](#8-where-the-data-files-come-from)
9. [Find sources across documents](#9-find-sources-across-documents)
10. [Expand into points](#10-expand-into-points)
11. [Convert to another storage class](#11-convert-to-another-storage-class)
12. [Validate a document with SHACL](#12-validate-a-document-with-shacl)
13. [Check stored summaries](#13-check-stored-summaries)
14. [Growing sources](#14-growing-sources)
15. [Handle errors](#15-handle-errors)

### 1. Load a document

A metadata document describes one or more sources. `load` reads it from a path or a URL; `loads` reads it from a string.

```python
import rdftss

# rdflib finds the RDF syntax (Turtle, JSON-LD, RDF/XML, ...) from the file name or media type
document = rdftss.load("examples/readings_csv.ttl")
print(document.describe())        # every source: its class, data file, window and fields

source = document.source()                     # the only source
source = document.source("ex:csv-source")      # or one of several: by IRI, prefixed name, label or position
source.kind                       # SourceKind.CSV: the source's class says where its rows are
print(source.window)              # [2024-05-12T00:00:00Z, 2024-05-13T00:00:00Z)
source.data_file                  # 'file:///.../examples/sample-data/readings.csv'
```

`for source in document:` goes through all the sources of a document.

### 2. Look at the fields

Each field is a column of the source. It is a value field (`tss:ValueField`), whose values are readings, or a context field (`tss:ContextField`), whose values give the context of a row: the time, a step, or a describing field, such as a quality code, that says something about the values of a value field.

```python
import rdftss

source = rdftss.load("examples/readings_csv.ttl").source()
for field in source.fields:
    print(f"{field.name}: {field.role}")     # time: time, value: value, quality: describing

value = source.field("value")      # by name, column name, IRI, prefixed name or label
print(value.describe())            # label, column, datatype, unit, observed property, sensor, ...
value.unit                         # URIRef('http://qudt.org/vocab/unit/MicroS-PER-CentiM')
value.minimum, value.maximum       # (0.0, 10000.0): the expected range
source.value_fields                # [<Field 'value' xsd:double (value)>]

quality = source.field("quality")
quality.describes                  # [<Field 'value' xsd:double (value)>]
quality.field_property             # URIRef('file:///.../sample-data/readings.csv#quality'): its default property
```

### 3. Read the rows and the points

Every storage class is read the same way: `read()` gives the rows inside the source's window, in time order, with each value decoded to a Python value of its datatype. An empty value (an empty cell, `null`, a missing triple, `""`) is `None`. `points()` gives the points: one per reading.

```python
import rdftss

source = rdftss.load("examples/readings_csv.ttl").source()
rows = source.read()
print(rows)                 # <Rows of csv-source: 85, 2024-05-12T00:00:00Z .. 2024-05-12T23:45:00Z>

row = rows[0]
row.time                    # datetime(2024, 5, 12, 0, 0, tzinfo=timezone.utc)
row["value"]                # 4747.0: a float, because the field's datatype is xsd:double
row.raw("value")            # '4747.00': the text in the data file
row["quality"]              # 'suspect'

# read() keeps every row in memory; iter_rows() streams them, for very large files
total = sum(1 for row in source.iter_rows())     # 85

point = next(source.points())     # one reading: a value of a value field, as RDF terms
point.field.name, point.time.text, str(point.value)          # ('value', '2024-05-12T00:00:00Z', '4747.00')
{field.name: str(value) for field, value in point.describing.items()}    # {'quality': 'suspect'}
```

### 4. Select rows

Selections give new `Rows`, so they can be chained. Besides `equals` and `above`, `where` takes `one_of`, `below`, `at_least` and `at_most`, or any function of a row.

```python
import rdftss

rows = rdftss.load("examples/readings_csv.ttl").source().read()

morning = rows.between("2024-05-12T06:00:00Z", "2024-05-12T12:00:00Z")    # from 06:00, before 12:00: 24 rows
good = rows.where("quality", equals="good")                                # 81 rows
high = rows.where("value", above=4800)                                     # 14 rows
custom = rows.where(lambda row: row["value"] > 4790 and row["quality"] == "good")   # 22 rows

good.between("2024-05-12T00:00:00Z", "2024-05-12T01:00:00Z").values("value")   # [4754.42, 4755.33, 4762.71]
```

### 5. Statistics, gaps and resampling

Statistics follow the definitions of `tss:summary`: `count` is the number of rows with a value, and `missing` the number without one. Gaps are the intervals longer than the usual one. Resampling works per period in UTC: `"15min"`, `"1h"`, `"1d"`, `"1w"`, `"1 month"`, or an `xsd:duration` such as `"PT15M"`.

```python
import rdftss

rows = rdftss.load("examples/readings_csv.ttl").source().read()

statistics = rows.statistics("value")
statistics.count, statistics.missing        # (85, 0)
statistics.minimum, statistics.maximum      # (4689.15, 4813.0)
statistics.median                           # 4752.92

print(rows.gaps())
# Usual step: 15 min; 3 gap(s), 2 h 45 min missing in total.
#   2024-05-12T17:15:00Z .. 2024-05-12T19:00:00Z (1 h 45 min)
#   ...

for period in rows.resample("6h", "value", how="mean"):     # or min, max, sum, count, median, first, last
    print(period)                                           # 2024-05-12T00:00:00Z  4782.2125  (20 values)

# Is a stored tss:summary still up to date?
stored = rdftss.load("examples/embedded_rdf.ttl").source()
stored.statistics().differences()           # {}: the summary matches the data
```

### 6. DataFrames

A DataFrame has one column per field, with values typed by their datatype.

```python
import rdftss

source = rdftss.load("examples/readings_csv.ttl").source()

frame = source.to_pandas()                # indexed by time (UTC)
frame["value"].resample("1h").mean()      # from here on, it is plain pandas
source.to_polars()                        # or polars; to_arrow() gives a pyarrow Table
```

### 7. Query with SPARQL

SPARQL queries run in memory, with rdflib, on one graph that holds the metadata and the points. Whatever its storage class, a source is there as the specification expands it: it links to each point with `tss:hasPoint`, and a point states its value field, its time and its reading with `tss:field`, `tss:time` and `tss:value`, and the value of each describing field with that field's property. The document's own statements are there as well, so a query can join the points with their fields, units, sensors and summaries. Every query knows the document's prefixes.

```python
import rdftss

source = rdftss.load("examples/readings_csv.ttl").source()

# The number of readings and their mean per quality code
result = source.sparql("""
    SELECT ?quality (COUNT(?point) AS ?n) (ROUND(AVG(?value) * 100) / 100 AS ?mean)
    WHERE {
        ex:csv-quality-field tss:property ?quality_property .
        ?point tss:field ex:csv-value-field ; tss:value ?value ; ?quality_property ?quality .
    }
    GROUP BY ?quality ORDER BY DESC(?n)
""")
print(result)
# quality | n  | mean
# --------+----+--------
# good    | 81 | 4751.58
# suspect | 4  | 4746.49
result.rows                 # [('good', 81, Decimal('4751.58')), ('suspect', 4, Decimal('4746.49'))]

# Points and metadata together: every reading in µS/cm, from 23:00 on
result = source.sparql("""
    SELECT ?time ?value WHERE {
        ?point tss:field ?field ; tss:time ?time ; tss:value ?value .
        ?field tss:unit unit:MicroS-PER-CentiM .
        FILTER(?time >= "2024-05-12T23:00:00Z"^^xsd:dateTime)
    } ORDER BY ?time
""")
len(result)                 # 4

# For several queries or several sources, build the graph once
with rdftss.TripleStore([source]) as store:
    print(store.describe())             # how the points of each source state their values
    print(store.examples()[0])          # example queries with the source's own IRIs
    store.query("ASK { ?point tss:field ex:csv-value-field ; tss:value ?value FILTER(?value > 4800) }")   # True

# With derive=True, the store also holds the SOSA and SAREF observations the points align with
with rdftss.TripleStore([source], derive=True) as store:
    store.query("SELECT (COUNT(?o) AS ?n) WHERE { ?o a sosa:Observation }").rows    # [(85,)]
```

Good to know:

- **Times compare as moments.** rdflib can't compare `xsd:dateTimeStamp` values, so in the graph every time is an `xsd:dateTime` literal with the same text: `…T10:00:00+02:00` equals `…T08:00:00Z`.
- **Values keep the text of the data.** A shared variable matches identical RDF terms, so `"4747.00"` from a CSV file doesn't match `"4747.0"` from a Parquet file. To compare numbers, use two variables and `FILTER(?a = ?b)`.
- **Queries only read.** SPARQL Update is not accepted, `SERVICE` and `FROM` are refused (they would reach other servers), and an answer is limited to 10,000 rows and 30 seconds by default (`max_rows=`, `timeout=`).
- **The graph lives in memory.** 200,000 points take some 10 seconds to load and over 1 GB. Load only the period and the value fields you need: `TripleStore([source], between=(start, end), fields=["value"])`.

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
source = document.source()
source.set_data_file("examples/sample-data/readings.csv")    # a path or a binary file object
len(source.read())                                           # 85
```

### 9. Find sources across documents

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
    print(match)                    # ex:csv-source (CSVSource, window [...)) in examples/readings_csv.ttl: value
    rows = match.source.read()      # each match holds its source, ready to read, and the fields that matched

rdftss.find(documents, unit="unit:MicroS-PER-CentiM", kind="parquet")    # also: sensor, immutable, label
```

### 10. Expand into points

The specification expands a source of any class into its points as RDF, in the shape of the points of a `tss:EmbeddedRDFSource`: each point states its value field, the time of its row and its reading with `tss:field`, `tss:time` and `tss:value`, and the values of the describing fields that describe it. The expansion is the metadata document, without `tss:points`, with the points added. A file is replaced only when the whole expansion worked.

```python
import rdftss

source = rdftss.load("examples/readings_csv.ttl").source()

result = source.expand("out/points.nt")     # N-Triples, written in batches (.nq gives N-Quads)
print(result)             # Points: 85, Triples: 461, the output file and the count per field

# A small source can stay in memory, as an rdflib Graph
graph = rdftss.load("examples/embedded_rdf.ttl").source().expand().graph
len(graph)                # 57 triples: the metadata and the five points, each with its tss:field

# The TSS vocabulary is aligned with SOSA, SAREF and QUDT. With derive=True, the expansion also
# holds what that alignment derives: each point is then a sosa:Observation and a saref:Measurement
result = rdftss.load("examples/embedded_rdf.ttl").source().expand(derive=True)
result.derived            # 117 derived triples
```

`rdftss.derive(graph)` gives what the alignment derives from any graph of points; the guide shows more.

### 11. Convert to another storage class

`convert` writes a source in another storage class (`"csv"`, `"parquet"`, `"arrow"`, `"json"` or `"rdf"`), as a new metadata document plus data file, and returns the new document. The rows, labels, window and the meaning of every field stay the same.

```python
import rdftss

source = rdftss.load("examples/readings_csv.ttl").source()

parquet = rdftss.convert(source, "parquet", document="out/readings.ttl")      # data: out/readings.parquet
parquet.source().read().values("value") == source.read().values("value")      # True: the same rows

rdftss.convert(source, "json", document="out/readings-json.ttl")              # rows inside the metadata
rdftss.convert(source, "csv", document="out/grid.ttl", time_grid="auto")      # times as steps of a grid

parts = rdftss.split(source, "6h", document="out/parts.ttl")                  # one source per 6 hours
len(parts.sources)                                                            # 4
```

`subset`, `merge` and `from_observations` are in the [guide](docs/guide.md#10-converting-sources).

### 12. Validate a document with SHACL

`validate()` checks a metadata document with the SHACL shapes of the specification, which come with the package. The source classes choose the shapes: one file for embedded sources, which also checks their RDF points, and one for external sources, which checks the metadata but not the data file. The document is checked as it is written, so a value such as `" 4810.5 "^^xsd:double` is found. It needs `pyshacl` (the `shacl` extra).

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
    print(result)       # Violation  S23   ex:source  S23 timezone: 2024-05-12T08:00:00 (the source's tss:from) has no timezone. ...
report.checks           # {'S23'}: the IDs of the checks that failed
report.to_turtle("out/report.ttl")      # the sh:ValidationReport as RDF
```

Every check has an ID: S for the source, F for a field, P for an RDF point, D for a value encoding and Q for a summary. `validate(shapes=...)` takes `"embedded"`, `"external"` or your own shapes file. [docs/conformance.md](docs/conformance.md) explains the checks and the conformance test suite.

### 13. Check stored summaries

A stored `tss:summary` is not refreshed by itself. `verify_summaries()` recomputes every number a summary states from the rows in the time window, and names the numbers that differ. Times are compared as moments, and floating-point numbers within a relative `tolerance` (1e-12).

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

### 14. Growing sources

A source that states `tss:immutable true` is final: no row is added any more. Without it, the source is *growing*: rows may still be added, but none is changed or removed. A growing source has no stored summaries, and its description, its time window included, does not change. The metadata document of a growing `tss:EmbeddedRDFSource` is an RDF Message Log: Turtle that starts with `VERSION "1.2-messages"` and ends every message with the delimiter `MESSAGE`: the description first, then one row per message ([`examples/growing_rdf.ttl`](examples/growing_rdf.ttl)).

```python
import rdftss

document = rdftss.load("examples/growing_rdf.ttl")
source = document.source()
source.growing                  # True: it does not state tss:immutable true
document.message_log            # True: read as the merge of its messages
document.messages               # 4: the description, then one message per row
len(source.read())              # 3
source.range_requests           # False: it does not promise that its bytes only grow

document.save("out/growing.ttl")     # written as a log again, so a new row is one more message

rdftss.load("examples/readings_csv.ttl").source().immutable    # True
```

A source that states `tss:rangeRequests true` promises that its bytes only grow, so a consumer can fetch only the new bytes with HTTP Range requests. Only an RDF Message Log, a CSV file and an Arrow IPC stream can keep that promise; a JSON or Parquet source is written anew when rows are added. rdftss reads what a growing source holds right now. A last message without its delimiter, a last CSV record without its line break, and a last Arrow stream message that is not complete are all still being written: they are left out, with a warning. An Arrow data file can be in the IPC file format or the IPC streaming format; the first bytes of the file tell them apart.

### 15. Handle errors

Reading stops at the first data error that the specification names, and says where it is. With `strict=False`, the bad value is left out and the problem is listed instead. Every error rdftss raises is an `rdftss.RDFTSSError`.

```python
import io

import rdftss

source = rdftss.load("examples/readings_csv.ttl").source()
bad = b"time;value;quality\n2024-05-12T00:00:00Z;4747,00;good\n2024-05-12T00:15:00Z;4754.42;good\n"

source.set_data_file(io.BytesIO(bad))
try:
    source.read()
except rdftss.DataError as error:
    print(error)            # CSV line 2: field 'value': '4747,00' is not an xsd:double

source.set_data_file(io.BytesIO(bad))
rows = source.read(strict=False)
print(rows.issues)          # error: CSV line 2: field 'value': '4747,00' is not an xsd:double
rows[0]["value"]            # None

# What the metadata gets wrong is listed, not raised
print(rdftss.load("examples/readings_csv.ttl").all_issues)    # No issues.
```

## What it does

| Area | Main entry points |
| --- | --- |
| Loading and RDF | `load(path_or_url)`, `loads(text)` (also RDF Message Logs), `Document`, `Source`, `Field`, `describe()`, `field("name")`, `how_to_read()` |
| Reading the data | `read(strict=, window=, fields=)`, `iter_rows()`, `Row`, `points()`, `set_data_file()`; RDF points, JSON rows, CSV, Parquet and Arrow; time grids with or without a step field; `tss:DeltaFromBaseline`; describing fields and their field property |
| Data files | local paths, `http(s)` downloads (`allow_network=True` by default), `cache_folder`, `data_folder` (sandbox) |
| Querying and analysis | `between`, `where`, `having`, `statistics` (+ `differences()` from the stored summary), `outside_expected_range`, `gaps`, `missing_steps`, `resample`, `to_pandas`/`to_polars`/`to_arrow`, `sparql()` and `TripleStore` (SPARQL with rdflib, read only, optionally with the derived observations) |
| Discovery | `rdftss.find(documents, observed_property=, feature_of_interest=, sensor=, unit=, overlapping=, kind=, immutable=)` |
| Conformance | `validate()` (the specification's SHACL shapes, or yours), `verify_summaries()`, and the test suite in [`conformance/`](conformance/) |
| Materialisation | `expand()` (the points as RDF, streamed as N-Triples or N-Quads, `derive=True` for SOSA and SAREF), `derive()`, `convert(to=, time_grid=, encode=, decode=, keep_texts=)`, `subset`, `split`, `merge`, `from_observations` |

Not in this version yet: following a growing source over HTTP (polling, and `Range` requests when it states `tss:rangeRequests true`), adding rows to a growing source and ending it, streaming a source as RDF messages, checks of the data beyond what the SHACL shapes see, and a command line.

## Example documents

[`examples/`](examples/) has a metadata document for each storage class. Most hold the same synthetic day of conductivity readings: CSV with `;`, CSV without a header, a time grid, delta encoding, Parquet, and Arrow with a dictionary-encoded column. There are also the spec's embedded, JSON rows and multi-sensor examples, and a growing source written as an RDF Message Log. To see the library at work, run `python examples/quickstart.py`.

## Tests

```bash
pip install -r requirements.txt     # once: it includes pytest
python -m pytest
```

The tests use the example documents. They check, among other things:

- the spec's examples: the rows and points, and the summary numbers;
- every rule the spec calls an error;
- the Parquet and Arrow type tables;
- conversions between every pair of classes that must keep the same rows;
- the code in this README and in the guide, which runs as part of the tests;
- the conformance test suite in [`conformance/`](conformance/): 221 tests of metadata documents, reading, stored summaries and expansion ([docs/conformance.md](docs/conformance.md)).

## Choices this version makes

These are the points where the spec leaves the choice to the implementer:

- **New IRIs on conversion.** `convert`, `split` and `merge` give the source and its fields new IRIs (with a suffix such as `-parquet`), so two documents never describe one node in two ways (`iri_suffix=None` keeps them). Field names stay the same, because every converted field states its `tss:columnName`.
- **Describing fields keep their property.** A describing field without `tss:property` has a default one, made from the data file or the document. A conversion changes those, so the converted field states the original field property with `tss:property`.
- **A time grid by position gets a step field on conversion.** The window may leave rows out, so the copy states the step of each row in a column of its own.
- **Parquet and Arrow store values, not texts.** After a conversion, `3.0E1` reads back as `30.0` and times read back in UTC. `keep_texts=True` stores strings instead.
- **`split` and `merge`.** `split` makes parts only for periods that have rows. `merge` takes the smallest window that holds all windows; the merged source is immutable when all its parts are.
- **`from_observations` windows.** The window runs from the first observation until one second after the last, and the new source is immutable.
- **Converting a growing source.** The copy is growing too, without summaries. A growing Arrow copy, or one that states `tss:rangeRequests true`, is written in the IPC streaming format (`.arrows`), and a growing RDF copy as an RDF Message Log. `tss:rangeRequests true` is kept only where the target class allows it.
- **No JSON-LD guard.** JSON-LD documents are parsed by rdflib, which may download remote `@context`s. A guard against that is planned, together with the other protections for untrusted documents.
