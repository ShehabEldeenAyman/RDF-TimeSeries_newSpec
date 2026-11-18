# rdftss guide

This guide shows every feature of rdftss with the example documents in [`examples/`](../examples/). The code runs from the `rdftss` folder. Each section stands on its own.

1. [Loading a document](#1-loading-a-document)
2. [Snippets and fields](#2-snippets-and-fields)
3. [Reading the points](#3-reading-the-points)
4. [Where the data comes from](#4-where-the-data-comes-from)
5. [Selecting and analysing](#5-selecting-and-analysing)
6. [DataFrames](#6-dataframes)
7. [SQL](#7-sql)
8. [Finding snippets across documents](#8-finding-snippets-across-documents)
9. [Expanding into observations](#9-expanding-into-observations)
10. [Converting snippets](#10-converting-snippets)
11. [Errors and issues](#11-errors-and-issues)

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

## 7. SQL

```python
snippet = rdftss.load("examples/readings_csv.ttl").snippet()
result = snippet.sql("SELECT time_bucket(INTERVAL 6 hour, time) AS t, avg(value) AS mean FROM points GROUP BY t ORDER BY t")
print(result)
result.to_pandas()
```

The database lives in memory and holds a few tables:

- one table per snippet, and with one snippet also the view `points`;
- `snippets`, `fields` and `summaries`, with the metadata.

It is locked down before your query runs:

- no files, no network and no extensions;
- settings can't be changed back;
- only one reading statement per query;
- limits on rows, size and time.

For several snippets, or several queries, open a `Database` yourself:

```python
csv = rdftss.load("examples/readings_csv.ttl").snippet()
parquet = rdftss.load("examples/readings_parquet.ttl").snippet()
with rdftss.Database([csv, parquet], max_rows=1000) as database:
    print(database.describe())
    for query in database.examples():
        print(query)
    database.query("SELECT count(*) FROM csv_snippet c JOIN parquet_snippet p USING (time)")
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

## 11. Errors and issues

| Raised | When |
|---|---|
| `DocumentError` | the document can't be read or parsed, or a snippet lacks what reading needs (see `snippet.reading_problems`) |
| `DataError` | the data breaks a rule the specification calls an error; `error.where` says where (`CSV line 12`, `Parquet row 7`, `the point at …`) |
| `DataFileError` | a data file can't be found, opened or downloaded, or lies outside `data_folder` |
| `UnsupportedError` | for example, a Parquet or Arrow column type that is not in the specification's tables |
| `MissingDependencyError` | a feature needs an optional package; the message says which |
| `rdftss.sql.SQLError` | a query is refused, fails, or is too slow or too big |

All of them are `rdftss.RDFTSSError`.

What breaks the specification in the metadata is reported, not raised. `document.all_issues` lists it, with a severity (`error` for a MUST, `warning` for a SHOULD) and where it is:

```python
document = rdftss.load("examples/readings_csv.ttl")
print(document.all_issues)          # No issues.
```
