# rdftss guide

This guide shows every feature of rdftss with the example documents in [`examples/`](../examples/). The code runs from the library's main folder, the one with `pyproject.toml`. Each section stands on its own.

1. [Loading a document](#1-loading-a-document)
2. [Snippets and fields](#2-snippets-and-fields)
3. [Reading the points](#3-reading-the-points)
4. [Where the data comes from](#4-where-the-data-comes-from)
5. [Selecting and analysing](#5-selecting-and-analysing)
6. [DataFrames](#6-dataframes)
7. [SPARQL](#7-sparql)
8. [Finding snippets across documents](#8-finding-snippets-across-documents)
9. [Expanding into observations](#9-expanding-into-observations)
10. [Converting snippets](#10-converting-snippets)
11. [Validating a document](#11-validating-a-document)
12. [Checking stored summaries](#12-checking-stored-summaries)
13. [Errors and issues](#13-errors-and-issues)

## 1. Loading a document

```python
import rdftss

document = rdftss.load("examples/readings_csv.ttl")              # a path
# document = rdftss.load("https://example.org/meta/doc.ttl")     # or a URL
print(document.describe())
```

`load` takes a path, a URL or a binary file object, and `loads` takes the text itself. rdflib finds the syntax from the file name or the media type the server sends: Turtle, N-Triples, N3, RDF/XML, TriG, N-Quads or JSON-LD. Pass `format="turtle"` (or another rdflib format name) when the name doesn't say.

Relative IRIs are resolved against the document's own address. So `tss:dataFile <data/readings.csv>` is found next to the document, both on disk and online. Give `base=` to resolve them against another IRI:

```python
text = open("examples/embedded_json.ttl").read()
document = rdftss.loads(text, base="https://example.org/meta/embedded_json.ttl")
```

A document describes one or more snippets:

```python
snippet = document.snippet()              # the only snippet (an error if there are several)
snippet = document.snippet(0)             # by position
snippet = document.snippet("ex:json-snippet")   # by IRI, prefixed name, last part of the IRI or label
for snippet in document:                  # all of them
    print(snippet)
```

## 2. Snippets and fields

```python
snippet = rdftss.load("examples/readings_grid.ttl").snippet()

snippet.kind                # SnippetKind.CSV: where the points are (the snippet's class)
snippet.kind.is_external    # True
snippet.window              # [2024-05-12T00:00:00Z, 2024-05-13T00:00:00Z)
snippet.mutable, snippet.committed_until
snippet.data_file           # 'file:///.../examples/sample-data/readings_grid.csv'
snippet.csv                 # CSVDialect(delimiter=',', encoding='utf-8', header=True), defaults filled in
snippet.time_grid           # 2024-05-12T00:00:00Z + n × PT15M
snippet.time_grid.time_of(5).text      # '2024-05-12T01:15:00Z'

snippet.fields              # [<Field 'grid-time-field' ...>, <Field 'step' ...>, <Field 'value' ...>, ...]
snippet.time_field, snippet.key, snippet.step_field
snippet.measured_fields     # the fields with a tss:observedProperty
```

A field can be looked up by its name, its column name, its IRI (in full or with one of the document's prefixes), the last part of its IRI, or its label:

```python
value = snippet.field("value")
value is snippet.field("ex:grid-value-field")       # True
value.datatype, value.unit, value.observed_property, value.sensor, value.feature_of_interest
value.minimum, value.maximum            # the expected range
value.role                              # 'measured' (or 'time', 'step', 'describing', 'other')
value.summary                           # its stored tss:summary, or None
print(value.describe())
```

`snippet.how_to_read()` gives everything a client needs to read the snippet, as a dict: class, data file or embedded predicate, the binding of each field, the time grid and the value encodings. For anything the model does not cover, use the RDF itself: `snippet.node`, `field.node` and `document.graph`, or `snippet.values_of(predicate)`.

## 3. Reading the points

```python
snippet = rdftss.load("examples/readings_delta.ttl").snippet()
points = snippet.read()

point = points[0]
point.time              # datetime(2024, 5, 12, 0, 0, tzinfo=timezone.utc)
point.time_text         # '2024-05-12T00:00:00Z', as written
point["value_delta"]    # 4747.0: the decoded value (baseline 4750 + -3.00)
point.text("value_delta")   # '4747.00': the lexical form of the decoded value
point.raw("value_delta")    # '-3.00': the text in the data file
point["quality"]        # 'suspect'
point.as_dict()         # {'time': ..., 'value_delta': 4747.0, 'quality': 'suspect'}
```

Every class is read the same way, as the specification says:

- the points come in time order;
- only the points in the time window are kept: a data file may hold more rows, which `points.outside_window` counts;
- an empty value (empty cell, null, missing triple or key, `""`) is no value: `point["x"]` is `None`;
- a field with a value encoding is decoded exactly, on the decimal values;
- on a time grid, the time of a row is `tss:timeBase` + step × `tss:timeStep`, and `point.step` is its step.

Options:

```python
points = snippet.read(window=False)             # every row of the data file
points = snippet.read(fields=["value_delta"])   # only some fields (and the time)
points = snippet.read(strict=False)             # leave bad values out instead of raising
print(points.issues)                            # ... and list them
```

`read()` holds all the points in memory. For very large files, use `iter_points()`, which streams them (in file order for a data file):

```python
reader = snippet.iter_points()
total = sum(1 for point in reader)
reader.outside_window
```

## 4. Where the data comes from

| `tss:dataFile` | rdftss |
|---|---|
| `file:` IRI or relative to a local document | reads the local file |
| `http:` or `https:` IRI, or relative to an online document | downloads it (on by default) |
| anything | `snippet.set_data_file(path_or_binary_stream)` reads your copy instead |

```python
document = rdftss.load("examples/readings_csv.ttl", allow_network=False)   # never download
document = rdftss.load("examples/readings_csv.ttl", data_folder="examples/sample-data")  # only read files in there
document = rdftss.load("examples/readings_csv.ttl", cache_folder="cache")  # keep downloads between runs
```

A download is kept as long as its document, so reading a snippet twice reads the same data. With `cache_folder`, downloads are kept between runs and checked with the server before reuse (ETag and Last-Modified), so the growing file of a mutable snippet is never stale. Turn downloads off (`allow_network=False`) for documents you don't trust, because they could make your program fetch any address. For the same reason, `data_folder` keeps a document from reading files elsewhere on your computer.

## 5. Selecting and analysing

`read()` gives `Points`: a list with methods. Selections give new `Points`:

```python
points = rdftss.load("examples/readings_csv.ttl").snippet().read()

morning = points.between("2024-05-12T06:00:00Z", "2024-05-12T12:00:00Z")   # [start, end)
suspect = points.where("quality", equals="suspect")
high = points.where("value", at_least=4790)
custom = points.where(lambda point: point["value"] > 4790 and point["quality"] == "good")
points.values("value")[:3], points.times()[:3]
```

**Statistics** follow the definitions of the stored summaries. `count` is the number of points with a value and `missing` the number without one. Minimum, maximum, mean and median are given for numeric fields only. Decimal and integer fields are computed exactly.

```python
statistics = points.statistics("value")
print(statistics)

stored = rdftss.load("examples/embedded_rdf.ttl").snippet()
stored.statistics().differences()      # {}: the stored tss:summary is up to date
```

**Expected range.** A value outside `tss:minimum`/`tss:maximum` is suspicious, not wrong: it stays in the data, and this lists it:

```python
for flag in points.outside_expected_range():
    print(flag)          # 2024-...: value = ... is above 10000
```

**Gaps** are the intervals longer than the usual one (the most frequent interval). **Missing steps** are the grid steps that have no row:

```python
print(points.gaps())     # Usual step: 15 min; 3 gap(s), 2 h 45 min missing in total. ...
grid = rdftss.load("examples/readings_grid.ttl").snippet().read()
for run in grid.missing_steps():
    print(run)           # steps 20-23 (2024-05-12T05:00:00Z .. 2024-05-12T05:45:00Z), ...
```

**Resampling** aggregates a field per period. Periods are given as `"15min"`, `"1h"`, `"1d"`, `"1w"`, `"1 month"`, `"1y"`, or as an `xsd:duration` such as `"PT15M"` or `"P1M"`. They are in UTC: days start at midnight, weeks on Monday, and months are calendar months.

```python
for item in points.resample("6h", "value", how="mean"):    # or min, max, sum, count, median, first, last
    print(item)          # 2024-05-12T00:00:00Z  4782.2125  (20 values)
hourly = points.resample("1h", "value", include_empty=True)   # also the hours without points
```

When a snippet has only one measured field, you can leave the field out: `points.statistics()`, `points.resample("1h")`.

## 6. DataFrames

```python
snippet = rdftss.load("examples/readings_arrow.ttl").snippet()
frame = snippet.to_pandas()                  # indexed by time (UTC); index=False keeps a column
frame = snippet.to_polars()
table = snippet.to_arrow(time_unit="ns")     # a pyarrow.Table; nanoseconds kept
table.schema.field("value").metadata         # the field's IRI, datatype, unit, observed property, label
```

The DataFrame has one column per field, holding the decoded values typed by datatype (see [`frames.py`](../src/rdftss/frames.py)). `names="label"` or `names="iri"` names the columns by label or IRI instead.

## 7. SPARQL

`sparql()` runs a SPARQL query, with rdflib, on one in-memory graph that holds the snippet's points and its document:

```python
snippet = rdftss.load("examples/readings_csv.ttl").snippet()
result = snippet.sparql("""
    SELECT ?hour (COUNT(?value) AS ?n) (AVG(?value) AS ?mean)
    WHERE { ?point ex:csv-time-field ?time ; ex:csv-value-field ?value . BIND(HOURS(?time) AS ?hour) }
    GROUP BY ?hour ORDER BY ?hour
""")
print(result)
result.columns, result.rows[0]      # ['hour', 'n', 'mean'], (0, 4, 4754.865)
result.terms[0]                     # the same row as rdflib terms, with their exact texts
result.to_pandas()
```

**The graph.** Whatever their storage class, the points are in the shape of embedded RDF points (`tss:EmbeddedRDFSnippet`):

```text
ex:csv-snippet tss:hasPoint _:p1 .
_:p1 ex:csv-time-field    "2024-05-12T00:00:00Z"^^xsd:dateTime ;
     ex:csv-value-field   "4747.00"^^xsd:double ;
     ex:csv-quality-field "suspect"^^xsd:string .
```

- Only the points inside the window are there, with their decoded values (time grids and value encodings applied), each in its exact text. An empty value has no triple.
- The predicate of a value is its field's IRI, so a query can go from a value to the field's unit, observed property, sensor or summary. A field that is a blank node gets an IRI `urn:rdftss:field:…`.
- The document is there as written, apart from its own embedded points (`tss:hasPoint`, `tss:points`), which are replaced by the points above, so that no point is there twice.
- rdflib can't compare `xsd:dateTimeStamp` values, so every `xsd:dateTimeStamp` literal, times and metadata alike, is an `xsd:dateTime` literal with the same text. Times then compare and sort as moments. `HOURS()`, `DAY()` and the like give the parts in the time's own offset.
- A shared variable matches identical RDF terms only: `"4747.00"` and `"4747.0"` are different terms with the same value. To compare values, use two variables and `FILTER(?a = ?b)`.

The prefixes of the document are known in every query, and so are `tss:`, `xsd:`, `rdf:`, `rdfs:`, `sosa:`, `qudt:`, `unit:` and `quantitykind:`.

**The answer** depends on the query form:

| Query | `sparql()` gives |
|---|---|
| `SELECT` | a `QueryResult`: `columns`, `rows` (Python values), `terms` (rdflib terms), `more_rows`, `seconds`; `as_dicts()`, `to_pandas()`, and a table when printed |
| `ASK` | `True` or `False` |
| `CONSTRUCT`, `DESCRIBE` | an rdflib `Graph` |

**Several snippets, several queries.** `sparql()` builds the graph for one query. A `TripleStore` builds it once, for any number of snippets:

```python
csv = rdftss.load("examples/readings_csv.ttl").snippet()
parquet = rdftss.load("examples/readings_parquet.ttl").snippet()
with rdftss.TripleStore([csv, parquet], max_rows=1000) as store:
    print(store.describe())               # each snippet, and the predicate of each field
    for query in store.examples():        # example queries with the real IRIs
        print(store.query(query))
    store.query("""SELECT (COUNT(*) AS ?n) WHERE {
        ex:csv-snippet tss:hasPoint ?a . ?a ex:csv-time-field ?time ; ex:csv-value-field ?value .
        ex:parquet-snippet tss:hasPoint ?b . ?b ex:parquet-time-field ?other ; ex:parquet-value-field ?number .
        FILTER(?time = ?other && ?value = ?number) }""")      # 85: the same readings
    store.graph                           # the rdflib Graph itself, to save or use with other tools
```

`document.sparql(query)` queries all snippets of a document. A snippet that can't be read is left out and listed in `store.skipped`; with `strict=False`, its bad values are left out instead.

**Limits.** Queries only read:

- SPARQL Update is not accepted;
- `SERVICE` is refused, since it would send the query to another server, and so are `FROM` and `FROM NAMED`, since rdflib would download those graphs;
- an answer has at most `max_rows` rows (10,000; `more_rows` says whether there were more) or triples, `max_bytes` of text (50 MB), and takes at most `timeout` seconds (30). A query that takes longer is stopped.

**Size.** rdflib keeps the graph in memory, at about 6 KB per point with three fields: 200,000 such points take about 10 seconds to load and over 1 GB. Load the period and the fields you need:

```python
store = rdftss.TripleStore([csv], between=("2024-05-12T06:00:00Z", "2024-05-12T12:00:00Z"), fields=["value"])
```

## 8. Finding snippets across documents

A `Document` holds one metadata document. To search several, load them and give the list to `rdftss.find`:

```python
from pathlib import Path

documents = [rdftss.load(path) for path in sorted(Path("examples").glob("*.ttl"))]
for match in rdftss.find(documents, observed_property="quantitykind:ElectricConductivity",
                         feature_of_interest="ex:feature-1",
                         overlapping=("2024-05-12T08:00:00Z", "2024-05-12T09:00:00Z")):
    print(match)                       # the snippet, its window, the fields that matched
    match.snippet.how_to_read()        # how to read it

rdftss.find(documents, unit="unit:DEG_C")
rdftss.find(documents, kind="parquet", mutable=False)
```

Prefixed names are read with each document's own prefixes; full IRIs work too. Like the evaluation's competency questions, `find` matches IRIs exactly, without inference. Windows that only touch do not overlap. The questions map onto the library like this:

| Question | rdftss |
|---|---|
| CQ1 snippets for a property and a feature | `find(documents, observed_property=..., feature_of_interest=...)` |
| CQ2 … whose window overlaps a period | `find(..., overlapping=(start, end))` |
| CQ3 where the readings are | `snippet.kind`, `snippet.data_file`, `snippet.how_to_read()` |
| CQ4 which fields hold the time and the values | `snippet.time_field`, `field.column_name`, `how_to_read()["fields"]` |
| CQ5 datatype and meaning of a field | `field.datatype`, `field.unit`, `field.describe()` |
| CQ6 grid and encoding parameters | `snippet.time_grid`, `field.value_encoding` |
| CQ7 update policy | `snippet.mutable`, `snippet.committed_until` |
| CQ8 stored summaries | `field.summary`, `snippet.window` |

## 9. Expanding into observations

Every value of a measured field in the window becomes an observation. By default it is written with SOSA, QUDT and Dublin Core, as the specification's informative mapping does:

```python
snippet = rdftss.load("examples/readings_csv.ttl").snippet()
result = snippet.expand("out/observations.nt")       # N-Triples, written in batches
print(result)                                          # observations, triples, size, per field
print(result.preview[:600])                            # the first observations as Turtle

graph = rdftss.load("examples/embedded_rdf.ttl").snippet().expand().graph   # small: in memory
rdftss.load("examples/readings_grid.ttl").expand("out/grid.nq")              # N-Quads, a graph per snippet
```

The output holds no TSS vocabulary. It does hold what the document says about the sensors, features of interest, observed properties and units the observations use (their concise bounded descriptions). A file is replaced only when the whole expansion worked.

To get the observations without RDF, or to write them with another vocabulary:

```python
for observation in snippet.observations():
    observation.time.text, observation.field.name, observation.result

from rdflib import URIRef

class MyMapping(rdftss.ObservationMapping):
    def triples(self, observation, node):
        yield node, URIRef("https://example.org/value"), observation.result
        yield node, URIRef("https://example.org/at"), URIRef(observation.time.text)

snippet.expand("out/mine.nt", mapping=MyMapping())
```

## 10. Converting snippets

`convert` writes the snippet in another storage class, as a new document plus data file, and returns the new document read back:

```python
snippet = rdftss.load("examples/readings_csv.ttl").snippet()

rdftss.convert(snippet, "parquet", document="out/readings.ttl")      # data: out/readings.parquet
rdftss.convert(snippet, "arrow", document="out/readings-arrow.ttl")
rdftss.convert(snippet, "json", document="out/readings-json.ttl")    # points in the metadata
rdftss.convert(snippet, "rdf", document="out/readings-rdf.ttl")
rdftss.convert(snippet, "csv", document="out/plain.ttl", csv={"delimiter": ",", "header": False})
```

What stays the same and what changes:

- **Kept:** the points in the window (their times as moments, and their decoded values), the labels, window, mutability, `tss:committedUntil`, summaries, and every field's meaning. What the document says about other things, such as sensors, is kept too.
- **New IRIs:** the snippet and its fields get IRIs with a suffix (`-parquet`, …), so two documents never describe one node in two ways. `iri_suffix=None` keeps the IRIs, and field names stay the same either way.
- **Embedded classes** state every reading themselves: a time grid becomes a time column, and encodings are decoded.
- **Parquet and Arrow** store typed values following the specification's tables, so `3.0E1` reads back as `30.0` and times read back in UTC. `keep_texts=True` stores every value as a string, exactly as it is.

Time grids and value encodings:

```python
rdftss.convert(snippet, "csv", document="out/grid.ttl", time_grid="auto")    # step found from the data
rdftss.convert(snippet, "csv", document="out/hourly.ttl",
               time_grid=("2024-05-12T00:00:00Z", "PT15M"))                  # a grid you choose
grid = rdftss.load("examples/readings_grid.ttl").snippet()
rdftss.convert(grid, "csv", document="out/no-grid.ttl", time_grid=False)     # a time column again

rdftss.convert(snippet, "parquet", document="out/delta.ttl", encode={"value": "4750"})  # differences
rdftss.convert(snippet, "csv", document="out/delta-auto.ttl", encode={"value": "auto"}) # median baseline
delta = rdftss.load("examples/readings_delta.ttl").snippet()
rdftss.convert(delta, "csv", document="out/decoded.ttl", decode=True)                   # readings again
```

Subsets, splits and merges:

```python
rdftss.subset(snippet, ["value"], document="out/values.ttl")         # only some fields
days = rdftss.split(snippet, "6h", document="out/parts.ttl")         # one snippet per period with points
merged = rdftss.merge(days.snippets, iri="https://example.org/ex#whole", document="out/whole.ttl")
```

Each part of a split gets its share of the window and new stored summaries. A merge needs snippets with the same fields; it gets the smallest window that holds all windows and the earliest `tss:committedUntil`.

From observations back to a snippet:

```python
observations = rdftss.load("examples/weather_rdf.ttl").snippet().expand().graph
document = rdftss.from_observations(observations, iri="https://example.org/ex#again",
                                    to="parquet", document="out/again.ttl")
```

Observations with the same observed property, sensor, feature of interest, unit and datatype become one field, and all fields share the time field.

## 11. Validating a document

`validate()` checks a metadata document with SHACL shapes. It needs the package `pyshacl` (`pip install -e ".[shacl]"`).

```python
document = rdftss.load("examples/readings_grid.ttl")
report = document.validate()            # or rdftss.validate(document), or rdftss.validate("path/or/url.ttl")
report.conforms, report.counts          # (True, {'Violation': 0, 'Warning': 0, 'Info': 0})
report.shapes                           # ['external']
```

**The shapes.** The package holds the two shapes files of the specification, in [`src/rdftss/shapes/`](../src/rdftss/shapes/):

| File | For | It checks |
|---|---|---|
| `embedded_shapes.ttl` | `tss:EmbeddedRDFSnippet`, `tss:EmbeddedJSONSnippet` | the snippet, its fields and summaries, and the RDF points (the JSON inside `tss:points` is not read by SHACL) |
| `external_shapes.ttl` | `tss:CSVSnippet`, `tss:ParquetSnippet`, `tss:ArrowSnippet` | the snippet, its fields, summaries, time grid and value encodings; not the data file |

The snippet classes of the document choose the file. A document with both kinds of snippets, or with none, is checked with both files, so that S24 (one kind of snippet per document) or S1 (no snippet) says what is wrong. `shapes="embedded"` or `shapes="external"` picks a file yourself, and a path or an rdflib `Graph` gives your own shapes.

**Exact texts.** The document is validated as it was read: every literal keeps its text, so P4 sees that `" 4810.5 "^^xsd:double` is not a valid double. rdflib on its own would have read it as 4810.5.

**The results.** A document conforms when no result is a violation. Each result says what is wrong and where:

```python
text = open("examples/readings_grid.ttl").read().replace('"PT15M"^^xsd:duration', '"PT0S"^^xsd:duration')
report = rdftss.loads(text).validate()
result = report.violations[0]
result.check, result.name               # ('S19', 'S19 time step')
result.severity, result.group           # ('Violation', None): SPARQL checks have no group
result.focus, result.where              # the node, and the same as a prefixed name: 'ex:grid-snippet'
result.message                          # 'S19 time step: ...'
result.shapes                           # 'external'
report.checks                           # {'S19'}
report.to_turtle("out/report.ttl")      # the sh:ValidationReport, with every result, as Turtle
report.graph                            # ... and as an rdflib Graph
```

Every check has an ID: **S** for the snippet, **F** for a field, **P** for an RDF point, **D** for a value encoding and **Q** for a summary. A property check also has a group: *Required* (missing or malformed), *Optional* (may be left out, but well-formed when there) or *Not allowed*; all three give violations. The checks written in SPARQL have no group, and four of them give warnings: F9, P5, P6 and S15. The IDs are those of the shapes files, which number some snippet checks differently: the snippet class is S3 for embedded snippets and S7 for external ones, so read an ID together with `result.shapes`. [conformance.md](conformance.md) lists every check.

Validation takes about 0.2 to 0.5 seconds for a small document. Embedded documents with many RDF points take longer, since the point checks look at every point.

## 12. Checking stored summaries

A stored summary is not refreshed by itself. `verify_summaries()` reads the points in each snippet's window and recomputes every number a summary states from the decoded values of its field:

```python
document = rdftss.load("examples/embedded_rdf.ttl")
report = document.verify_summaries()        # also snippet.verify_summaries(), rdftss.verify_summaries(snippets)
report.up_to_date                           # True
for check in report.checks:
    check.field.name, check.up_to_date      # ('value-field', True)
    check.compared                          # ['count', 'missing', 'first_time', 'last_time', 'observed_minimum', ...]
    check.computed.mean                     # the Statistics of the values: 4757.54
```

The numbers are compared like this:

| Number | Same when |
|---|---|
| `tss:count`, `tss:missing` | equal |
| `tss:firstTime`, `tss:lastTime` | the same moment: `…T10:30:00+02:00` is `…T08:30:00Z` |
| `tss:observedMinimum`, `tss:observedMaximum`, `tss:mean`, `tss:median` | equal for decimal and integer fields; equal within a relative `tolerance` (1e-12) for `xsd:double` and `xsd:float` fields, since a sum of floating-point numbers depends on the order of the additions |

A number the summary leaves out is not compared, and neither is `tss:generatedAtTime`. The numbers follow `tss:ExcludeEmptyValues`, the only missing-value policy the specification defines: an empty value counts in `tss:missing` and is left out of the other numbers. A summary without a policy is checked with it; one with another policy is reported as not checked.

```python
text = open("examples/embedded_rdf.ttl").read().replace('tss:mean "4757.54"', 'tss:mean "4757.5"')
report = rdftss.loads(text).verify_summaries()
report.out_of_date[0].differences           # {'mean': (4757.5, 4757.54)}: (stored, computed)
rdftss.loads(text).verify_summaries(tolerance=1e-4).up_to_date    # True: a writer that rounds
```

A snippet whose data can't be read (a missing data file, a data error) is listed in `report.not_checked`, with the reason. With `strict=False`, bad values are left out of the numbers instead.

## 13. Errors and issues

| Raised | When |
|---|---|
| `DocumentError` | the document can't be read or parsed, or a snippet lacks what reading needs (see `snippet.reading_problems`) |
| `DataError` | the data breaks a rule the specification calls an error; `error.where` says where (`CSV line 12`, `Parquet row 7`, `the point at …`) |
| `DataFileError` | a data file can't be found, opened or downloaded, or lies outside `data_folder` |
| `UnsupportedError` | for example, a Parquet or Arrow column type that is not in the specification's tables |
| `MissingDependencyError` | a feature needs an optional package; the message says which |
| `rdftss.QueryError` | a SPARQL query is refused, fails, or is too slow or too big |
| `rdftss.validation.ValidationError` | the SHACL shapes can't be read or run |

All of them are `rdftss.RDFTSSError`.

What breaks the specification in the metadata is reported, not raised. `document.all_issues` lists it, with a severity (`error` for a MUST, `warning` for a SHOULD) and where it is:

```python
document = rdftss.load("examples/readings_csv.ttl")
print(document.all_issues)          # No issues.
```
