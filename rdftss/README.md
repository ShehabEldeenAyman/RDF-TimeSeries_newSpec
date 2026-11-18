# rdftss

A Python library for **RDF Time Series Snippets** (TSS). It reads metadata documents and their data in all five storage classes, queries and analyses the points, finds snippets across documents, expands snippets into observations, and converts them from one storage class to another.

It implements the specification in [`tech demo/demo 11`](<../tech demo/demo 11/spec/spec.bs>) (vocabulary version 0.2.0). [FEATURES.md](FEATURES.md) lists what the library could do; this version covers its sections 1–5. The [guide](docs/guide.md) explains every feature with examples.

```python
import rdftss

document = rdftss.load("examples/readings_csv.ttl")
snippet = document.snippet()
print(snippet.describe())

points = snippet.read()                       # in time order, inside the window, decoded
print(points.statistics("value"))             # count, mean, median, ... as tss:summary defines them
frame = snippet.to_pandas()                   # a DataFrame indexed by time

snippet.expand("observations.nt")             # SOSA observations, streamed to a file
rdftss.convert(snippet, "parquet", document="out/readings.ttl")   # writes out/readings.parquet
```

## Install

Python 3.10 or newer. From this folder:

```bash
pip install -e .            # rdflib only: RDF and JSON points, CSV files
pip install -e ".[all]"     # also Parquet and Arrow, pandas, polars and SQL
```

| Extra | Adds | Needed for |
|---|---|---|
| `files` | pyarrow | Parquet and Arrow files, `to_arrow()` |
| `pandas` | pyarrow, pandas | `to_pandas()` |
| `polars` | pyarrow, polars | `to_polars()` |
| `sql` | pyarrow, duckdb | `sql()` and `Database` |

A feature whose package is missing raises an error that tells you what to install.

## The model in one picture

```
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

## What it does

| Area | Main entry points |
|---|---|
| Loading and RDF | `load(path_or_url)`, `loads(text)`, `Document`, `Snippet`, `Field`, `describe()`, `field("name")`, `how_to_read()` |
| Reading the data | `read(strict=, window=, fields=)`, `iter_points()`, `Point`, `set_data_file()`; RDF and JSON points, CSV, Parquet and Arrow; time grids; `tss:DeltaFromBaseline` |
| Data files | local paths, `http(s)` downloads (`allow_network=True` by default), `cache_folder`, `data_folder` (sandbox) |
| Querying and analysis | `between`, `where`, `having`, `statistics` (+ `differences()` from the stored summary), `outside_expected_range`, `gaps`, `missing_steps`, `resample`, `to_pandas`/`to_polars`/`to_arrow`, `sql()` and `Database` (DuckDB, locked down) |
| Discovery | `rdftss.find(documents, observed_property=, feature_of_interest=, sensor=, unit=, overlapping=, kind=, mutable=)`; the competency questions CQ1–CQ8 map onto it (see [`discovery.py`](src/rdftss/discovery.py)) |
| Materialisation | `expand()` (SOSA by default, or your own `ObservationMapping`; N-Triples or N-Quads, streamed), `observations()`, `convert(to=, time_grid=, encode=, decode=, keep_texts=)`, `subset`, `split`, `merge`, `from_observations` |

Not in this version: QUDT alignment, conformance testing (SHACL plus data checks), appending points, and the command line, which are sections 6–9 of [FEATURES.md](FEATURES.md). DCAT export and CSVW interop are pinned.

## Examples

[`examples/`](examples/) has a metadata document for each storage class. Most hold the same synthetic day of conductivity readings: CSV with `;`, CSV without a header, a time grid, delta encoding, Parquet, and Arrow with a dictionary-encoded column. There are also the spec's embedded, JSON and multi-sensor examples. To see the library at work, run `python examples/quickstart.py`.

## Tests

```bash
python -m pytest
```

The tests use the examples and the demo 11 metadata documents. They check, among other things:

- the spec's examples: the summary numbers and the five observations;
- every rule the spec calls an error;
- the Parquet and Arrow type tables;
- conversions between every pair of classes that must keep the same points.

## Choices this version makes

These are the points where the spec leaves the choice to the implementer:

- **New IRIs on conversion.** `convert`, `split` and `merge` give the snippet and its fields new IRIs (with a suffix such as `-parquet`), so two documents never describe one node in two ways (`iri_suffix=None` keeps them). Field names stay the same, because every converted field states its `tss:columnName`.
- **Parquet and Arrow store values, not texts.** After a conversion, `3.0E1` reads back as `30.0` and times read back in UTC. `keep_texts=True` stores strings instead.
- **`split` and `merge`.** `split` makes parts only for periods that have points. `merge` takes the smallest window that holds all windows and the earliest `tss:committedUntil`.
- **`from_observations` windows.** The window runs from the first observation until one second after the last.
- **No JSON-LD guard.** JSON-LD documents are parsed by rdflib, which may download remote `@context`s. Guarding against that is part of "untrusted input" in section 9.
