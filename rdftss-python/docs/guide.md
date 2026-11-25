# rdftss guide

This guide shows every feature of rdftss with the example documents in [`examples/`](../examples/). The code runs from the library's main folder, the one with `pyproject.toml`. Each section stands on its own.

1. [Loading a document](#1-loading-a-document)
2. [Sources and fields](#2-sources-and-fields)
3. [Reading the rows and the points](#3-reading-the-rows-and-the-points)
4. [Where the data comes from](#4-where-the-data-comes-from)
5. [Selecting and analysing](#5-selecting-and-analysing)
6. [DataFrames](#6-dataframes)
7. [SPARQL](#7-sparql)
8. [Finding sources across documents](#8-finding-sources-across-documents)
9. [Expanding into points](#9-expanding-into-points)
10. [Converting sources](#10-converting-sources)
11. [Validating a document](#11-validating-a-document)
12. [Checking stored summaries](#12-checking-stored-summaries)
13. [Growing sources](#13-growing-sources)
14. [Errors and issues](#14-errors-and-issues)

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

A document describes one or more sources:

```python
source = document.source()              # the only source (an error if there are several)
source = document.source(0)             # by position
source = document.source("ex:json-source")   # by IRI, prefixed name, last part of the IRI or label
for source in document:                  # all of them
    print(source)
```

## 2. Sources and fields

```python
source = rdftss.load("examples/readings_grid.ttl").source()

source.kind                # SourceKind.CSV: where the rows are (the source's class)
source.kind.is_external    # True
source.window              # [2024-05-12T00:00:00Z, 2024-05-13T00:00:00Z)
source.immutable           # True: final, no row is added any more (source.growing is the opposite)
source.range_requests      # False: it does not promise that its bytes only grow
source.data_file           # 'file:///.../examples/sample-data/readings_grid.csv'
source.csv                 # CSVDialect(delimiter=',', encoding='utf-8', header=True), defaults filled in
source.time_grid           # 2024-05-12T00:00:00Z + n × PT15M
source.time_grid.time_of(5).text      # '2024-05-12T01:15:00Z'

source.fields              # [<Field 'grid-time-field' ...>, <Field 'step' ...>, <Field 'value' ...>, ...]
source.time_field, source.key, source.step_field
source.value_fields        # the fields whose values are readings (tss:ValueField)
source.context_fields      # the others: the time field, the step field, describing fields, ...
source.describing_fields   # the context fields that describe a value field (tss:describesField)
```

Every field is a **value field** (`tss:ValueField`): its values are readings, and each of them is a point; or a **context field** (`tss:ContextField`): its values give the context of a row. The time field, the step field and every describing field are context fields, and only a value field says what it observes (`tss:observedProperty`, `tss:unit`, `tss:sensor`, `tss:featureOfInterest`).

A field can be looked up by its name, its column name, its IRI (in full or with one of the document's prefixes), the last part of its IRI, or its label:

```python
value = source.field("value")
value is source.field("ex:grid-value-field")       # True
value.kind, value.role                  # ('value', 'value'); a role is 'time', 'step', 'value', 'describing' or 'context'
value.datatype, value.unit, value.observed_property, value.sensor, value.feature_of_interest
value.minimum, value.maximum            # the expected range
value.summary                           # its stored tss:summary, or None
print(value.describe())

quality = source.field("quality")
quality.describes                       # [<Field 'value' xsd:double (value)>]
quality.field_property                  # the property that states its values on the points
```

A **describing field**, such as a quality code, says something about the values of the value fields it describes (`tss:describesField`). On a point, its value is stated with its **field property**: its `tss:property`, or else its default property, made of a base IRI, `#`, and its column name, as CSVW does. The base is the data file of an external source, and the metadata document of an embedded one. A value field may state with `tss:aboutUrl` how the IRI of each of its points is made, as a URI template (`field.about_url`).

`source.how_to_read()` gives everything a client needs to read the source, as a dict: class, data file or embedded predicate, the binding of each field (its column, JSON key or property), the time grid and the value encodings. For anything the model does not cover, use the RDF itself: `source.node`, `field.node` and `document.graph`, or `source.values_of(predicate)`.

## 3. Reading the rows and the points

A source is a table: each **row** has one time and at most one value for each field. Each value of a value field in a row is one reading: a **point**.

```python
source = rdftss.load("examples/readings_delta.ttl").source()
rows = source.read()

row = rows[0]
row.time                # datetime(2024, 5, 12, 0, 0, tzinfo=timezone.utc)
row.time_text           # '2024-05-12T00:00:00Z', as written
row["value_delta"]      # 4747.0: the decoded value (baseline 4750 + -3.00)
row.text("value_delta")     # '4747.00': the lexical form of the decoded value
row.raw("value_delta")      # '-3.00': the text in the data file
row["quality"]          # 'suspect'
row.as_dict()           # {'time': ..., 'value_delta': 4747.0, 'quality': 'suspect'}
```

Every class is read the same way, as the specification says:

- the rows come in time order;
- only the rows in the time window are kept: a data file may hold more rows, which `rows.outside_window` counts;
- an empty value (empty cell, null, missing triple or key, `""`) is no value: `row["x"]` is `None`;
- a field with a value encoding is decoded exactly, on the decimal values;
- on a time grid, the time of a row is `tss:timeBase` + step × `tss:timeStep`, and `row.step` is its step: the value of its step field, or, without a step field, its position in the data file (an empty line of a CSV file is then a step without readings).

The RDF points of a `tss:EmbeddedRDFSource` state their value field, their time and their reading with `tss:field`, `tss:time` and `tss:value`; a point may leave out `tss:field` when the source has one value field. The points of one time form a row: no two points of a value field may have the same time, and a describing value goes only on the points of the fields it describes.

```python
weather = rdftss.load("examples/weather_rdf.ttl").source()
len(weather.read())                    # 3 rows
len(list(weather.points()))            # 5 points: the row at 08:10 has no humidity
```

`points()` gives the points one by one, as the specification's expansion makes them ([section 9](#9-expanding-into-points)): each with its value field, the time of its row, its reading as an RDF literal, and the values of the describing fields that describe it.

```python
point = next(rdftss.load("examples/readings_csv.ttl").source().points())
point.field.name, point.time.text, str(point.value)      # ('value', '2024-05-12T00:00:00Z', '4747.00')
point.node                                              # a new blank node, or the IRI tss:aboutUrl makes
```

Options:

```python
rows = source.read(window=False)             # every row of the data file
rows = source.read(fields=["value_delta"])   # only some fields (and the time)
rows = source.read(strict=False)             # leave bad values out instead of raising
print(rows.issues)                            # ... and list them
```

`read()` holds all the rows in memory. For very large files, use `iter_rows()`, which streams them (in file order for a data file):

```python
reader = source.iter_rows()
total = sum(1 for row in reader)
reader.outside_window
```

## 4. Where the data comes from

| `tss:dataFile` | rdftss |
| --- | --- |
| `file:` IRI or relative to a local document | reads the local file |
| `http:` or `https:` IRI, or relative to an online document | downloads it (on by default) |
| anything | `source.set_data_file(path_or_binary_stream)` reads your copy instead |

```python
document = rdftss.load("examples/readings_csv.ttl", allow_network=False)   # never download
document = rdftss.load("examples/readings_csv.ttl", data_folder="examples/sample-data")  # only read files in there
document = rdftss.load("examples/readings_csv.ttl", cache_folder="cache")  # keep downloads between runs
```

A download is kept as long as its document, so reading a source twice reads the same data. With `cache_folder`, downloads are kept between runs and checked with the server before reuse (ETag and Last-Modified), so the data file of a growing source is never stale. Turn downloads off (`allow_network=False`) for documents you don't trust, because they could make your program fetch any address. For the same reason, `data_folder` keeps a document from reading files elsewhere on your computer.

## 5. Selecting and analysing

`read()` gives `Rows`: a list with methods. Selections give new `Rows`:

```python
rows = rdftss.load("examples/readings_csv.ttl").source().read()

morning = rows.between("2024-05-12T06:00:00Z", "2024-05-12T12:00:00Z")   # [start, end)
suspect = rows.where("quality", equals="suspect")
high = rows.where("value", at_least=4790)
custom = rows.where(lambda row: row["value"] > 4790 and row["quality"] == "good")
rows.values("value")[:3], rows.times()[:3]
```

**Statistics** follow the definitions of the stored summaries. `count` is the number of rows with a value and `missing` the number without one. Minimum, maximum, mean and median are given for numeric fields only. Decimal and integer fields are computed exactly.

```python
statistics = rows.statistics("value")
print(statistics)

stored = rdftss.load("examples/embedded_rdf.ttl").source()
stored.statistics().differences()      # {}: the stored tss:summary is up to date
```

**Expected range.** A value outside `tss:minimum`/`tss:maximum` is suspicious, not wrong: it stays in the data, and this lists it:

```python
for flag in rows.outside_expected_range():
    print(flag)          # 2024-...: value = ... is above 10000
```

**Gaps** are the intervals longer than the usual one (the most frequent interval). **Missing steps** are the grid steps that have no row:

```python
print(rows.gaps())       # Usual step: 15 min; 3 gap(s), 2 h 45 min missing in total. ...
grid = rdftss.load("examples/readings_grid.ttl").source().read()
for run in grid.missing_steps():
    print(run)           # steps 20-23 (2024-05-12T05:00:00Z .. 2024-05-12T05:45:00Z), ...
```

**Resampling** aggregates a field per period. Periods are given as `"15min"`, `"1h"`, `"1d"`, `"1w"`, `"1 month"`, `"1y"`, or as an `xsd:duration` such as `"PT15M"` or `"P1M"`. They are in UTC: days start at midnight, weeks on Monday, and months are calendar months.

```python
for item in rows.resample("6h", "value", how="mean"):    # or min, max, sum, count, median, first, last
    print(item)          # 2024-05-12T00:00:00Z  4782.2125  (20 values)
hourly = rows.resample("1h", "value", include_empty=True)   # also the hours without rows
```

When a source has only one value field, you can leave the field out: `rows.statistics()`, `rows.resample("1h")`.

## 6. DataFrames

```python
source = rdftss.load("examples/readings_arrow.ttl").source()
frame = source.to_pandas()                  # indexed by time (UTC); index=False keeps a column
frame = source.to_polars()
table = source.to_arrow(time_unit="ns")     # a pyarrow.Table; nanoseconds kept
table.schema.field("value").metadata         # the field's IRI, datatype, unit, observed property, label
```

The DataFrame has one column per field, holding the decoded values typed by datatype (see [`frames.py`](../src/rdftss/frames.py)). `names="label"` or `names="iri"` names the columns by label or IRI instead.

## 7. SPARQL

`sparql()` runs a SPARQL query, with rdflib, on one in-memory graph that holds the source's points and its document:

```python
source = rdftss.load("examples/readings_csv.ttl").source()
result = source.sparql("""
    SELECT ?hour (COUNT(?value) AS ?n) (AVG(?value) AS ?mean)
    WHERE { ?point tss:field ex:csv-value-field ; tss:time ?time ; tss:value ?value . BIND(HOURS(?time) AS ?hour) }
    GROUP BY ?hour ORDER BY ?hour
""")
print(result)
result.columns, result.rows[0]      # ['hour', 'n', 'mean'], (0, 4, 4754.865)
result.terms[0]                     # the same row as rdflib terms, with their exact texts
result.to_pandas()
```

**The graph.** Whatever its storage class, a source is there as the specification expands it ([section 9](#9-expanding-into-points)):

```text
ex:csv-source tss:hasPoint _:p1 .
_:p1 tss:field ex:csv-value-field ;
     tss:time  "2024-05-12T00:00:00Z"^^xsd:dateTime ;
     tss:value "4747.00"^^xsd:double ;
     <file:///.../sample-data/readings.csv#quality> "suspect"^^xsd:string .
ex:csv-quality-field tss:property <file:///.../sample-data/readings.csv#quality> .
```

- Only the points inside the window are there, with their decoded values (time grids and value encodings applied), each in its exact text. An empty value has no point, or, for a describing field, no triple.
- A point links to its value field with `tss:field`, so a query can go from a reading to the field's unit, observed property, sensor or summary. The value of a describing field is stated with its field property; the store states the default property of a describing field that states none, so a query can find it with `tss:property`.
- The document is there as written, apart from its own embedded points (`tss:hasPoint`, `tss:points`), which are replaced by the points above, so that no point is there twice.
- rdflib can't compare `xsd:dateTimeStamp` values, so every `xsd:dateTimeStamp` literal, times and metadata alike, is an `xsd:dateTime` literal with the same text. Times then compare and sort as moments. `HOURS()`, `DAY()` and the like give the parts in the time's own offset.
- A shared variable matches identical RDF terms only: `"4747.00"` and `"4747.0"` are different terms with the same value. To compare values, use two variables and `FILTER(?a = ?b)`.
- With `derive=True`, the graph also holds what the alignment of the TSS vocabulary with SOSA, SAREF and QUDT derives: every point is a `sosa:Observation` with `sosa:resultTime`, `sosa:hasSimpleResult`, `sosa:observedProperty`, `qudt:hasUnit`, ..., and a `saref:Measurement` too.

The prefixes of the document are known in every query, and so are `tss:`, `xsd:`, `rdf:`, `rdfs:`, `sosa:`, `saref:`, `qudt:`, `unit:` and `quantitykind:`.

**The answer** depends on the query form:

| Query | `sparql()` gives |
| --- | --- |
| `SELECT` | a `QueryResult`: `columns`, `rows` (Python values), `terms` (rdflib terms), `more_rows`, `seconds`; `as_dicts()`, `to_pandas()`, and a table when printed |
| `ASK` | `True` or `False` |
| `CONSTRUCT`, `DESCRIBE` | an rdflib `Graph` |

**Several sources, several queries.** `sparql()` builds the graph for one query. A `TripleStore` builds it once, for any number of sources:

```python
csv = rdftss.load("examples/readings_csv.ttl").source()
parquet = rdftss.load("examples/readings_parquet.ttl").source()
with rdftss.TripleStore([csv, parquet], max_rows=1000) as store:
    print(store.describe())               # each source, and how its points state their values
    for query in store.examples():        # example queries with the real IRIs
        print(store.query(query))
    store.query("""SELECT (COUNT(*) AS ?n) WHERE {
        ex:csv-source tss:hasPoint ?a . ?a tss:time ?time ; tss:value ?value .
        ex:parquet-source tss:hasPoint ?b . ?b tss:time ?other ; tss:value ?number .
        FILTER(?time = ?other && ?value = ?number) }""")      # 85: the same readings
    store.graph                           # the rdflib Graph itself, to save or use with other tools
```

`document.sparql(query)` queries all sources of a document. A source that can't be read is left out and listed in `store.skipped`; with `strict=False`, its bad values are left out instead.

**Limits.** Queries only read:

- SPARQL Update is not accepted;
- `SERVICE` is refused, since it would send the query to another server, and so are `FROM` and `FROM NAMED`, since rdflib would download those graphs;
- an answer has at most `max_rows` rows (10,000; `more_rows` says whether there were more) or triples, `max_bytes` of text (50 MB), and takes at most `timeout` seconds (30). A query that takes longer is stopped.

**Size.** rdflib keeps the graph in memory, at a few KB per point: 200,000 points take some 10 seconds to load and over 1 GB. Load the period and the value fields you need (each with the values of its describing fields):

```python
store = rdftss.TripleStore([csv], between=("2024-05-12T06:00:00Z", "2024-05-12T12:00:00Z"), fields=["value"])
```

## 8. Finding sources across documents

A `Document` holds one metadata document. To search several, load them and give the list to `rdftss.find`:

```python
from pathlib import Path

documents = [rdftss.load(path) for path in sorted(Path("examples").glob("*.ttl"))]
for match in rdftss.find(documents, observed_property="quantitykind:ElectricConductivity",
                         feature_of_interest="ex:feature-1",
                         overlapping=("2024-05-12T08:00:00Z", "2024-05-12T09:00:00Z")):
    print(match)                       # the source, its window, the fields that matched
    match.source.how_to_read()         # how to read it

rdftss.find(documents, unit="unit:DEG_C")
rdftss.find(documents, kind="parquet", immutable=True)
```

Prefixed names are read with each document's own prefixes; full IRIs work too. `find` matches IRIs exactly, without inference, on the value fields of each source. Windows that only touch do not overlap. Typical discovery questions map onto the library like this:

| Question | rdftss |
| --- | --- |
| Which sources observe a property of a feature? | `find(documents, observed_property=..., feature_of_interest=...)` |
| ... whose window overlaps a period? | `find(..., overlapping=(start, end))` |
| Where are the readings? | `source.kind`, `source.data_file`, `source.how_to_read()` |
| Which fields hold the time and the readings? | `source.time_field`, `source.value_fields`, `field.column_name`, `how_to_read()["fields"]` |
| What do the values of a field mean? | `field.datatype`, `field.unit`, `field.describe()` |
| What are the time grid and the encodings? | `source.time_grid`, `field.value_encoding` |
| Is it final, or still growing? Can it be followed with Range requests? | `source.immutable`, `source.growing`, `source.range_requests` |
| What summaries are there? | `field.summary`, `source.window` |

## 9. Expanding into points

The specification expands a source of any class into its points as RDF, in the shape of the points of a `tss:EmbeddedRDFSource`. For each row in the window, and for each value field with a value in it, there is one point. It states its value field (`tss:field`, also when the source has one value field), the time of its row (`tss:time`, with the time field's datatype; on a time grid, the time of the row's step), its reading (`tss:value`, the decoded value with the field's datatype; a value without a value encoding keeps its text as written), and the value of each describing field that describes it, with that field's property. The point is the RDF point itself for a `tss:EmbeddedRDFSource`, the IRI that `tss:aboutUrl` makes when the value field states one, and a new blank node otherwise.

The expansion of a source is its metadata document, without `tss:points`, with the points added, and with the default `tss:property` of each describing field that states none. A document with several sources is expanded source by source.

```python
source = rdftss.load("examples/readings_csv.ttl").source()
result = source.expand("out/points.nt")            # N-Triples, written in batches
print(result)                                      # points, triples, size, per field
print(result.preview[:600])                        # the first points as Turtle

graph = rdftss.load("examples/embedded_rdf.ttl").source().expand().graph   # small: in memory
rdftss.load("examples/readings_grid.ttl").expand("out/grid.nq")              # N-Quads, a graph per source
source.expand("out/only-points.nt", metadata=False)                         # the points without the metadata
```

A file is replaced only when the whole expansion worked. To get the points without RDF:

```python
for point in source.points():
    point.field.name, point.time.text, point.value, point.describing
```

**SOSA and SAREF.** The specification does not write its points with another vocabulary: the TSS vocabulary is aligned with SOSA, SAREF and QUDT, so that a reasoner derives their observations and measurements from the points. `tss:Point` is a subclass of `sosa:Observation` and `saref:Measurement`, `tss:time` and `tss:value` are subproperties of `sosa:resultTime` and `sosa:hasSimpleResult`, and property chains such as `tss:field` followed by `tss:observedProperty` give `sosa:observedProperty`. With `derive=True`, the expansion also holds what these axioms derive; `rdftss.derive(graph)` does the same for any graph of points:

```python
result = rdftss.load("examples/embedded_rdf.ttl").source().expand(derive=True)
result.derived                                   # 117: the triples the alignment derives

graph = rdftss.load("examples/weather_rdf.ttl").source().expand().graph
observations = rdftss.derive(graph)              # only the derived triples, as a new graph
```

`derive` reads the axioms from the bundled vocabulary (`rdfs:domain`, `rdfs:range`, `rdfs:subClassOf`, `rdfs:subPropertyOf` and two-step `owl:propertyChainAxiom`) and applies them as a reasoner does, without one. The values of describing fields are not aligned: they say something about a reading, and are not results of their own.

## 10. Converting sources

`convert` writes the source in another storage class, as a new document plus data file, and returns the new document read back:

```python
source = rdftss.load("examples/readings_csv.ttl").source()

rdftss.convert(source, "parquet", document="out/readings.ttl")      # data: out/readings.parquet
rdftss.convert(source, "arrow", document="out/readings-arrow.ttl")
rdftss.convert(source, "json", document="out/readings-json.ttl")    # rows in the metadata
rdftss.convert(source, "rdf", document="out/readings-rdf.ttl")      # one point per reading
rdftss.convert(source, "csv", document="out/plain.ttl", csv={"delimiter": ",", "header": False})
```

What stays the same and what changes:

- **Kept:** the rows in the window (their times as moments, and their decoded values), the labels, window, summaries, whether the source is immutable or growing, and every field's kind and meaning. A growing copy has no summaries ([section 13](#13-growing-sources)). What the document says about other things, such as sensors, is kept too.
- **New IRIs:** the source and its fields get IRIs with a suffix (`-parquet`, …), so two documents never describe one node in two ways. `iri_suffix=None` keeps the IRIs, and field names stay the same either way.
- **Describing fields** state their field property with `tss:property` in the copy: a default property is made from the data file or the document, so it would change with them.
- **Embedded classes** state every reading themselves: a time grid becomes a time column, and encodings are decoded. A `tss:EmbeddedRDFSource` gets one point per reading, with the IRI that `tss:aboutUrl` made for it, if any.
- **Parquet and Arrow** store typed values following the specification's tables, so `3.0E1` reads back as `30.0` and times read back in UTC. `keep_texts=True` stores every value as a string, exactly as it is.
- **`tss:rangeRequests true`** is kept for an RDF, CSV or Arrow copy (an Arrow copy is then an IPC stream), and left out of a JSON or Parquet copy, which is written anew when rows are added.

Time grids and value encodings:

```python
rdftss.convert(source, "csv", document="out/grid.ttl", time_grid="auto")    # step found from the data
rdftss.convert(source, "csv", document="out/hourly.ttl",
               time_grid=("2024-05-12T00:00:00Z", "PT15M"))                  # a grid you choose
grid = rdftss.load("examples/readings_grid.ttl").source()
rdftss.convert(grid, "csv", document="out/no-grid.ttl", time_grid=False)     # a time column again

rdftss.convert(source, "parquet", document="out/delta.ttl", encode={"value": "4750"})  # differences
rdftss.convert(source, "csv", document="out/delta-auto.ttl", encode={"value": "auto"}) # median baseline
delta = rdftss.load("examples/readings_delta.ttl").source()
rdftss.convert(delta, "csv", document="out/decoded.ttl", decode=True)                   # readings again
```

A time grid without a step field (the step of a row is its position) gets a step field in the copy, since the window may leave rows out.

Subsets, splits and merges:

```python
rdftss.subset(source, ["value"], document="out/values.ttl")         # only some fields
days = rdftss.split(source, "6h", document="out/parts.ttl")         # one source per period with rows
merged = rdftss.merge(days.sources, iri="https://example.org/ex#whole", document="out/whole.ttl")
```

Each part of a split gets its share of the window and new stored summaries. A merge needs sources with the same fields; it gets the smallest window that holds all windows, and is immutable when every part is.

From SOSA observations to a source, such as the observations that the alignment derives:

```python
observations = rdftss.load("examples/weather_rdf.ttl").source().expand(derive=True).graph
document = rdftss.from_observations(observations, iri="https://example.org/ex#again",
                                    to="parquet", document="out/again.ttl")
```

Observations with the same observed property, sensor, feature of interest, unit and datatype become one value field, and all value fields share the time field.

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
| --- | --- | --- |
| `embedded_shapes.ttl` | `tss:EmbeddedRDFSource`, `tss:EmbeddedJSONSource` | the source, its fields and summaries, and the RDF points (the JSON inside `tss:points` is not read by SHACL) |
| `external_shapes.ttl` | `tss:CSVSource`, `tss:ParquetSource`, `tss:ArrowSource` | the source, its fields, summaries, time grid and value encodings; not the data file |

The source classes of the document choose the file. A document with both kinds of sources, or with none, is checked with both files, so that S24 (one kind of source per document) or S1 (no source) says what is wrong. `shapes="embedded"` or `shapes="external"` picks a file yourself, and a path or an rdflib `Graph` gives your own shapes.

**Exact texts.** The document is validated as it was read: every literal keeps its text, so P4 sees that `" 4810.5 "^^xsd:double` is not a valid double. rdflib on its own would have read it as 4810.5.

**The results.** A document conforms when no result is a violation. Each result says what is wrong and where:

```python
text = open("examples/readings_grid.ttl").read().replace('"PT15M"^^xsd:duration', '"PT0S"^^xsd:duration')
report = rdftss.loads(text).validate()
result = report.violations[0]
result.check, result.name               # ('S19', 'S19 time step')
result.severity, result.group           # ('Violation', None): SPARQL checks have no group
result.focus, result.where              # the node, and the same as a prefixed name: 'ex:grid-source'
result.message                          # 'S19 time step: ...'
result.shapes                           # 'external'
report.checks                           # {'S19'}
report.to_turtle("out/report.ttl")      # the sh:ValidationReport, with every result, as Turtle
report.graph                            # ... and as an rdflib Graph
```

Every check has an ID: **S** for the source, **F** for a field, **P** for an RDF point, **D** for a value encoding and **Q** for a summary. A property check also has a group: *Required* (missing or malformed), *Optional* (may be left out, but well-formed when there) or *Not allowed*; all three give violations. The checks written in SPARQL have no group, and five of them give warnings: F9 (a value field without points), P5 (a reading outside the expected range), P6 (a property of a point that is not read), S15 (a CSV setting on a Parquet or Arrow source) and S29 (a source without a value field). The IDs are those of the shapes files, which number some source checks differently: the source class is S3 for embedded sources and S7 for external ones, so read an ID together with `result.shapes`. [conformance.md](conformance.md) lists every check.

Validation takes about 0.2 to 0.5 seconds for a small document. Embedded documents with many RDF points take longer, since the point checks look at every point.

## 12. Checking stored summaries

A stored summary is not refreshed by itself. `verify_summaries()` reads the rows in each source's window and recomputes every number a summary states from the decoded values of its field:

```python
document = rdftss.load("examples/embedded_rdf.ttl")
report = document.verify_summaries()        # also source.verify_summaries(), rdftss.verify_summaries(sources)
report.up_to_date                           # True
for check in report.checks:
    check.field.name, check.up_to_date      # ('value-field', True)
    check.compared                          # ['count', 'missing', 'first_time', 'last_time', 'observed_minimum', ...]
    check.computed.mean                     # the Statistics of the values: 4757.54
```

The numbers are compared like this:

| Number | Same when |
| --- | --- |
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

A source whose data can't be read (a missing data file, a data error) is listed in `report.not_checked`, with the reason. With `strict=False`, bad values are left out of the numbers instead.

## 13. Growing sources

`tss:immutable` says whether rows may still be added to a source. `true` means the source is final. `false`, or no `tss:immutable` at all, means it is *growing*: append-only, so rows may be added, but none is changed or removed. The description of a growing source does not change while it grows: its class, fields, time window, data file and time grid stay as they are.

```python
document = rdftss.load("examples/growing_rdf.ttl")
source = document.source()
source.immutable, source.growing      # (False, True)
document.message_log, document.messages # (True, 4)
source.read()                          # the 3 rows the log holds now
document.to_turtle()[:22]               # 'VERSION "1.2-messages"'
```

The rules for a growing source, which rdftss reports as issues (and the SHACL shapes as S26 and S28):

| Rule | Issue |
| --- | --- |
| A growing source has no `tss:summary` on any field (its numbers would be outdated by the next row) | error |
| The document of a growing `tss:EmbeddedRDFSource` is an RDF Message Log | error |
| An RDF Message Log states no `tss:immutable false` | error |
| Only a `tss:EmbeddedRDFSource`, a `tss:CSVSource` and a `tss:ArrowSource` may state `tss:rangeRequests true`: the bytes of a JSON literal or a Parquet file are written anew when rows are added | error |
| The data file of an Arrow source that states `tss:rangeRequests true` is an IPC stream, not an IPC file | error, when the file is read |
| `tss:mutable` and `tss:committedUntil` are from earlier drafts | warning |

A growing Parquet source, or an Arrow source in the IPC file format, is written anew when rows are added, and is read again as a whole.

**RDF Message Logs.** The document starts with `VERSION "1.2-messages"`; its first message describes the source and its fields, and every next message holds one row: the points of one time. A message ends with a line that holds only `MESSAGE` (or `@message .`). rdftss reads the document as the merge of its messages: each message is parsed on its own, with the prefixes of the messages before it, so the blank node `_:p` of one message is never the `_:p` of another. Text after the last delimiter is a message still being written: it is not read, and the document gets a warning. `document.save()` writes a document with a growing RDF source as a log again, with one message per row.

**Data files that grow.** A writer appends complete CSV records, each ended by a line break, and complete Arrow messages to an Arrow IPC stream. So when rdftss reads a growing source, a last CSV record without its line break, or a last Arrow message that is not complete, is still being written: it is left out, with a warning. An Arrow data file is read as an IPC file when it starts with `ARROW1`, and as an IPC stream otherwise, whatever its name.

**Converting.** A converted source keeps being growing or immutable. A growing copy gets no summaries, a growing Arrow copy is an IPC stream (`.arrows`), and a growing RDF copy a Message Log.

Not in rdftss yet: adding rows to a source and ending it (the spec's Write and #ending), and following a growing source over HTTP: polling with `ETag` and `Cache-Control`, and reading only the new bytes with `Range: bytes=N-` when the source states `tss:rangeRequests true`.

## 14. Errors and issues

| Raised | When |
| --- | --- |
| `DocumentError` | the document can't be read or parsed, or a source lacks what reading needs (see `source.reading_problems`) |
| `DataError` | the data breaks a rule the specification calls an error; `error.where` says where (`CSV line 12`, `Parquet row 7`, `the row at …`, `the point of 'value' at …`) |
| `DataFileError` | a data file can't be found, opened or downloaded, or lies outside `data_folder` |
| `UnsupportedError` | for example, a Parquet or Arrow column type that is not in the specification's tables |
| `MissingDependencyError` | a feature needs an optional package; the message says which |
| `rdftss.QueryError` | a SPARQL query is refused, fails, or is too slow or too big |
| `rdftss.validation.ValidationError` | the SHACL shapes can't be read or run |

All of them are `rdftss.RDFTSSError`.

What breaks the specification in the metadata is reported, not raised. `document.all_issues` lists it, with a severity (`error` for a MUST, `warning` for a SHOULD) and where it is. A document of an earlier draft, with `tss:CSVSnippet` or `tss:MetaField`, gets an issue that names the term that replaces it.

```python
document = rdftss.load("examples/readings_csv.ttl")
print(document.all_issues)          # No issues.
```
