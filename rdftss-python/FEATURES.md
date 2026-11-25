# RDFTSS library: feature analysis

The `rdftss` library should be the one place for working with RDF Time Series Sources: reading the metadata and the data, converting data between formats and into points and observations, aligning units with QUDT, parsing RDF, and testing conformance. This document lists candidate features, as a basis for choosing the scope.

**Status (rdftss 0.2.0):** sections 1–5 are implemented, see the [README](README.md) and the [guide](docs/guide.md). Decisions taken while building them: a `Document` holds one source, and several sources are several objects (no catalogue object; `rdftss.find()` searches a list of them); data files on the web are downloaded by default; *DCAT export* and *CSVW interop* are **pinned**: not built for now; the SQL feature became **SPARQL** (rdflib, no DuckDB). In section 7, *SHACL validation*, *Summary verification* and the *Conformance test suite* are implemented, see [docs/conformance.md](docs/conformance.md); *Data checks beyond SHACL* is postponed and *One report* is not needed for now. The library follows the final specification (vocabulary of 2026-11-20): value and context fields, rows of one time whose readings are points with `tss:field`, `tss:time` and `tss:value`, describing fields and their field property, `tss:aboutUrl`, time grids with an optional step field, `tss:immutable` with `tss:rangeRequests` as an opt-in, and the alignment with SOSA, SAREF and QUDT. Growing RDF sources are read and written as RDF Message Logs, and growing CSV files and Arrow streams are read; adding rows, and following a growing source over HTTP, are not built yet.

The library targets the latest specification, in [`tech demo/demo 11`](<../tech demo/demo 11/>): [`spec/spec.bs`](<../tech demo/demo 11/spec/spec.bs>), [`vocabulary/tss.ttl`](<../tech demo/demo 11/vocabulary/tss.ttl>) and the shapes in [`SHACL/`](<../tech demo/demo 11/SHACL/>). Demos 1–10 hold working code for many of the features below, but none of them implements the demo 11 model: demo 10 still reads one `tss:Source` with `tss:embedded`, `tss:format` and `tss:pointOf`.

Each description ends with its basis:

- *Port*: a demo has working code, which must move to the demo 11 model.
- *New*: the demo 11 specification requires or describes it, and no demo implements it.
- *Beyond spec*: the specification does not ask for it; it serves the library's own goals.

## 1. Metadata and RDF parsing

| Feature | Description |
| --- | --- |
| Document loading | Read a metadata document from a path, URL or string in any syntax rdflib knows (Turtle, N-Triples, N3, RDF/XML, TriG, N-Quads, JSON-LD); rdflib finds the syntax from the file name or media type. The base IRI is the document's address unless given, so relative IRIs such as `<../data/x.csv>` resolve the same way in every tool. *Port: demo 10 `documents.py`, `rdf.js`.* |
| Exact lexical forms | Keep the text of every literal as written: `" 12 "^^xsd:double` stays `" 12 "`, so checks can report it instead of rdflib normalising it away. Safe when several parses run at once. *Port: demo 10 `parse_exact` and `NORMALIZE_LOCK` in `tss2rdf.py`.* |
| Sources and their classes | Find every source of a document and pick its reading rules from its one source class. Report a source with no class or with several, and a document that mixes embedded and external sources. *New: demo 10 reads exactly one `tss:Source`.* |
| Object model | Typed `Source`, `Field`, `Row`, `Point`, `Summary`, `TimeGrid` and `ValueEncoding` objects, with the spec's defaults filled in (delimiter `,`, encoding `utf-8`, header `true`, key = time field, the default field property of a describing field) and each field's kind (value or context) and role: time, step, value, describing or context. Terms are compared by full IRI, so prefixes never matter; statements the model does not cover stay reachable. *Port: demo 10 `source_reading.py`, `tss.js`.* |
| Vocabulary bundle | Constants for every `tss:` term, and the demo 11 `tss.ttl` and SHACL shapes inside the package, so the library works offline and can say which version of the specification it implements. *New.* |

## 2. Reading the data

| Feature | Description |
| --- | --- |
| One row reader | One call gives the rows of any source in time order: the time, and per field the decoded value and the original text, with `None` for an empty value; `points()` gives each reading as a point. Strict mode stops at the errors the spec names, such as a row without a time, and says where; lenient mode collects them. *Port: demo 10 `data_reading.py`.* |
| RDF points | Read the points that a source links to with `tss:hasPoint` (`tss:field`, `tss:time`, `tss:value`, and the field properties of describing fields), and group them into rows by time; no triple or `""` is an empty value. Refuse two points of one field at one time, a point without `tss:field` when the source has several value fields, and a describing value on a point of a field it does not describe; warn about other properties. *Port: demo 10 `point_graph`, which still uses `tss:pointOf`.* |
| JSON rows | Parse the `rdf:JSON` array of rows in `tss:points`, keyed by column name: `null`, `""` and a missing key are empty values. Refuse keys that start with `@`; warn about JSON numbers and booleans, whose text is lost, and about keys that name no field. *New: demo 10 no longer reads JSON points.* |
| CSV files | Read RFC 4180 CSV with `tss:delimiter`, `tss:encoding` and `tss:header`: columns by name, or by `tss:columnIndex` when there is no header. Never guess the delimiter; check that the header names every field's column; read in chunks. *Port: demo 10 `data_reading.py`, `files.py`.* |
| Parquet files | Read columns by name and write each native value in the lexical form of the spec's Parquet table: UTC times as `…Z`, DECIMAL with exactly *scale* fraction digits, `INF` and `NaN`. Check that the field's datatype is allowed for the column type; refuse unsupported types, and a timestamp without timezone as the time column. *Port: demo 10 reads Parquet with DuckDB; the type table is new.* |
| Arrow IPC files | The same for Arrow IPC files, with the spec's Arrow table. A dictionary-encoded column is read as the values it encodes. *New.* |
| Finding the data file | Resolve `tss:dataFile` against the document IRI. Read a local file, download an HTTP(S) file (on by default, can be turned off; kept per document, or in a cache folder that is checked with the server before reuse; with a size limit), or take a file or stream from the caller. An optional sandbox folder blocks paths that escape it with `..`. *Port: demo 10 `files.py`, `dataSource.js`.* |
| Times | Parse `xsd:dateTime` and `xsd:dateTimeStamp` with their required timezone, and compare times as moments: `…Z` and `…+00:00` are the same time. Keep the nanoseconds of Parquet and Arrow timestamps, which Python's `datetime` drops. *Port in part: demo 10 `value_reading.py`; the required timezone is new.* |
| Time window | Apply [`tss:from`, `tss:until`), with an open side where a bound is missing. Rows of an external source outside the window are skipped; a point of an embedded source outside it is an error. *Port.* |
| Time grid | Give each row the time `tss:timeBase` + n × `tss:timeStep`, where n is its step, with XML Schema duration arithmetic: each part of the step is multiplied by n, and months are calendar months (2021-01-31 + P1M = 2021-02-28). Check that every step is a whole number of 0 or more, and unique; without a step field, the step of a row is its position, and an empty CSV line is a row. *New.* |
| Value encoding | Decode `tss:DeltaFromBaseline` values as baseline + value, computed exactly on the decimals, then read as the field's datatype. Encodings are pluggable, so the spec can add more. *New.* |
| Values and datatypes | Check each value against the lexical form of its datatype: the numeric and integer types, `xsd:boolean`, and `xsd:dateTime`, `xsd:dateTimeStamp` and `xsd:date` with real calendar days. Return Python values (`Decimal`, `float`, `int`, `bool`, `datetime`, or text with the field's `tss:language`) next to the original text. *Port: demo 10 `value_reading.py`, `LEXICAL_FORMS`.* |
| DataFrames | Return a source's rows, in the window and decoded, as a pandas or Polars DataFrame or an Arrow table, with the field IRIs, labels and units as column metadata. *New.* |

## 3. Querying and analysis

| Feature | Description |
| --- | --- |
| Filters | Select points by time range, value range and the values of describing fields (for example, only readings whose quality code is "good"). Flag values outside `tss:minimum` and `tss:maximum` as suspicious without dropping them, as the spec allows. *Port: demo 10 `/query`, `/filter-columns`.* |
| Statistics | Count, empty values, mean, median, minimum, maximum, and first and last time of a selection, with the definitions of the stored summaries. *Port: demo 10 `query_helpers.py`.* |
| Gaps | Find the usual step (the most frequent interval) and list the longer intervals as gaps, with the total missing time. On a time grid, list the missing steps. *Port: demo 10 `find_gaps`; the grid part is new.* |
| Resampling | Aggregate the readings per hour, per day or per any period, as mean, minimum or maximum, in UTC. *Port: demo 10 `resample_readings`.* |
| SPARQL | **Done (replaces SQL).** SPARQL queries with rdflib over one in-memory graph of the metadata and the points: every source's points as the expansion makes them (`tss:field`, `tss:time`, `tss:value` and the describing values), whatever the storage class, and optionally the SOSA and SAREF observations that the alignment derives. Read only: no SPARQL Update, `SERVICE` or `FROM`; limits on rows, size and time; example queries. *Was: a DuckDB database (demo 10 `sql_box.py`, `sql_routes.py`); replaced at the author's request.* |

## 4. Discovery across documents

| Feature | Description |
| --- | --- |
| ~~Catalogue~~ | *Dropped:* a `Document` holds one source; to work with several, load several. The discovery queries below take a list of documents. |
| Discovery queries | `rdftss.find(documents, ...)`: find sources by observed property, feature of interest, sensor, unit and window overlap, and say how to read them: class, data file or embedded predicate, field bindings and field properties, grid and encoding parameters, whether the source grows and allows Range requests, and summaries. This turns the competency questions into functions. *Port: demo 11 `evaluation/competency_questions`.* |
| DCAT export | **Pinned.** Write a DCAT catalogue of the sources (`tss:Source` is a subclass of `dcat:Dataset`), with a distribution for each data file and its media type. *Port: demo 10 `catalog_routes.py`.* |

## 5. Materialisation and conversion

| Feature | Description |
| --- | --- |
| Expansion into points | **Done.** A conforming expander: one point per value of a value field inside the window, with its value field, the time of its row or step, the decoded reading, the values of the describing fields with their field properties, and the IRI that `tss:aboutUrl` makes; added to the metadata without `tss:points`, with the default `tss:property` of each describing field. Handles several sources per document. *Port: demo 10 `tss2rdf.py`, extended to the five classes, grids and encodings.* |
| Observations by alignment | **Done.** The spec aligns its vocabulary with SOSA, SAREF and QUDT (subclasses, subproperties, domains, ranges and property chains), so the observations follow from the points: `rdftss.derive()` applies those axioms without a reasoner, and `expand(derive=True)` adds what they derive. *Port for SOSA; the alignment is new.* |
| Streaming RDF output | Write N-Triples or N-Quads in batches with fresh blank nodes, so memory stays bounded for millions of triples. Write to a temporary file and replace the output only on success; report counts per field and give a short Turtle preview. *Port: demo 10 `write_batch`, `exact_turtle`.* |
| Storage conversion | Convert a source between the five classes: CSV, Parquet and Arrow into each other, and external into embedded RDF or JSON points and back. Write the data and rewrite the metadata (class, column names, CSV properties) so the result conforms; a source that changes between embedded and external goes into a new document, since a document cannot mix them. *New; demo 10 `csv2parquet.py` and demo 4 `csv_to_points.py` covered parts.* |
| Grid and delta encoding | Turn a time grid into an explicit time column, or detect a regular step and store steps instead; decode delta-from-baseline values, or encode them against a chosen baseline. *New.* |
| Split, merge and subset | Split a long source into windowed sources, for example per month, each with its own window and summaries; merge sources that share their fields; keep a subset of the fields. *Beyond spec.* |
| Observations to sources | The reverse of expansion: group SOSA observations by observed property, sensor, feature of interest and unit into fields that share one time field, and write a source with its data. *Beyond spec.* |
| CSVW interop | **Pinned.** Write CSVW table metadata for a CSV source (`tss:Field` is a close match of `csvw:Column`), so CSVW tools can read the file, and import the CSVW descriptions of demos 2–5 as fields. *Beyond spec.* |

## 6. Units and QUDT alignment

| Feature | Description |
| --- | --- |
| QUDT data | A pinned, offline copy of the QUDT units and quantity kinds: labels, symbols, UCUM codes, dimension vectors, conversion multipliers and offsets. It can be refreshed on demand. *Beyond spec.* |
| Unit checks and lookup | Check that `tss:unit` is a known QUDT unit that is not deprecated (suggest the replacement), and that it applies to the field's `tss:observedProperty` when that is a quantity kind. Fix near misses such as `https://qudt.org/vocab/…` for `http://qudt.org/vocab/…`; both occur in this repository. Suggest unit IRIs from a symbol, UCUM code or label (`µS/cm` → `unit:MicroS-PER-CentiM`). *Beyond spec.* |
| Unit conversion | Convert values between two units with the same dimension vector through their QUDT multipliers and offsets, such as µS/cm to S/m or °C to K, and refuse incompatible pairs. *Beyond spec.* |
| Source alignment | Convert one or more sources to a target unit, such as the SI unit of their quantity kind, as new data and metadata (`tss:unit`, `tss:minimum`, `tss:maximum`, baselines and summaries) or as observations. An affine conversion carries the stored summaries over without reading the data; for a delta-encoded field the baseline takes the offset and the differences only the multiplier. *Beyond spec.* |
| Discovery by quantity kind | Find series by quantity kind whatever their unit, and optionally through QUDT broader or exact matches. `rdftss.find()` now matches exact IRIs only. *Beyond spec.* |

## 7. Conformance testing

| Feature | Description |
| --- | --- |
| SHACL validation | **Done.** Validate with the bundled demo 11 shapes, embedded or external as the source classes say, or with custom shapes. Parse with exact lexical forms so check P4 sees malformed values. Return the report as RDF and as a summary: conforms or not, counts per severity, and check IDs such as S23. *Port: demo 10 `shacl_report.py`.* |
| Data checks beyond SHACL | **Postponed.** Check what the shapes cannot see: the contents of JSON points, and of data files (lexical forms, timezones, unique times, steps), Parquet and Arrow column types against `tss:datatype`, and a CSV header against the column names. *New.* |
| One report | **Not needed for now.** Merge SHACL results and these checks into one report with shared check IDs and locations (CSV line, row number, point time), as text, JSON or `sh:ValidationReport`, with a verdict for each conformance class: metadata document and data. *New.* |
| Summary verification | **Done.** Recompute each `tss:summary` from the data in the window and compare count, missing, first and last time, minimum, maximum, mean and median, naming the numbers that are out of date. *Port: demo 10 summary check in the Query tab and the SQL box.* |
| Conformance test suite | **Done (221 tests, see [docs/conformance.md](docs/conformance.md)).** Small valid and invalid documents and data files, at least one per check ID, each with its expected result. They test this library as consumer and expander, and can test other implementations; writer tests wait for section 8. *New.* |

## 8. Writing and publishing

| Feature | Description |
| --- | --- |
| Appending rows | A conforming writer for growing embedded sources (the spec's #updating-embedded): check that the new row's values have their lexical forms, that no row has the same time and that it lies in the time window, append one RDF message (the row's points in one `tss:hasPoint` statement, and a `MESSAGE` delimiter) or rewrite `tss:points` with the new object at the end, change nothing else, and keep the change only if the document still conforms. *Port: demo 10 `appendPoints.js`, `metadataUpdate.js` (browser only, old model).* |
| Document writing | Write a document as Turtle with its own prefixes, summaries nested, and RDF points last, one statement per row, so that a later append adds text at the end. Numbers and booleans keep their full form. A growing RDF source is written as an RDF Message Log, one message per row (**done**). Ending a source (`tss:immutable true` and the summaries, as one last message in a log) keeps the document's comments and layout. *Port: demo 10 `documentTurtle`, `exact_turtle`; keeping the layout is new, since demo 10 drops comments.* |
| Summary generation | Compute and attach `tss:summary` nodes for chosen fields over the window, with `tss:ExcludeEmptyValues`, numeric attributes only on numeric fields, and `tss:generatedAtTime`. *Port in part: demo 10 `metadataUpdate.js`.* |
| Metadata scaffolding | Draft a metadata document from a data file: fields from the column names and native types (per the Parquet and Arrow tables), the time column, a time grid when the steps are regular, the window and the summaries. The author then adds what each field observes. *New.* |
| Builder API | Build sources and fields in code, from scratch or from a DataFrame, and write them only after they pass validation. *New.* |
| External source upkeep | For a growing data file: append complete CSV records ending with a line break, or complete record batches to an Arrow IPC stream, or write a Parquet file or an Arrow IPC file anew (the spec's #appending-data-files), and end the source with `tss:immutable true` and the summaries (#ending). *New since PR #25; demo 1's publisher did this for CSV.* |
| Following a growing source | Poll a growing source with conditional requests and `Cache-Control`, and, when the source states `tss:rangeRequests true`, read only the new bytes with `Range: bytes=N-` in whole units (RDF messages, CSV records, Arrow messages), with the fallback to a full re-read (the spec's #following). *New since PR #25.* |

## 9. Tooling and integration

| Feature | Description |
| --- | --- |
| Command line | An `rdftss` command with subcommands such as `info`, `validate`, `read`, `query`, `sparql`, `expand`, `convert`, `summarize`, `append`, `migrate`, `units` and `cq`, replacing the separate scripts `tss2rdf.py`, `shacl_report.py`, `csv2parquet.py`, `csv_to_points.py` and `evaluation_runner.py`. *Port.* |
| Migration | Rewrite documents written for `main` or demos 7–10 in demo 11 terms, following the changelog's [migration table](<../tech demo/demo 11/CHANGELOG.md#migration-from-main-to-demo-11>): `tss:Source`, `tss:embedded`, `tss:format`, `tss:pointOf`, `tss:modifiedAt`, `prov:generatedAtTime`, `xsd:dateTime` times and keys with several fields. List what needs a person's decision, such as a time without a timezone. Terms of earlier drafts (`tss:CSVSnippet`, `tss:MetaField`, `tss:mutable`, ...) are already reported, with the term that replaces them. *New.* |
| Dashboard service layer | Optional FastAPI routers for the catalogue, data, queries, SPARQL, SHACL and RDF generation, so the dashboard's backend becomes a thin layer over the library. *Port: demo 10 `*_routes.py`.* |
| Browser use | Keep the core pure Python, with pyarrow and pyshacl as optional extras, so the dashboard can run the library in the browser through Pyodide, as it already runs pyshacl. This would replace the JavaScript ports in `frontend/src/browser/`, which must now be kept equal to the Python by hand. *Beyond spec.* |
| Untrusted input | No JSON-LD remote contexts, data files only inside an allowed folder, network access that can be turned off, limits on size and time, and read-only SPARQL. *Port: demo 10 security rules.* |
| Scale | Stream rows and triples so memory stays bounded; the demo data expands to up to 440,000 observations and 3.4 million triples per document. Push the time window down into Parquet and Arrow reads. *Port, to improve.* |

## 10. Possible extensions

| Feature | Description |
| --- | --- |
| LDES publication | Publish windowed sources as members of a Linked Data Event Stream, with TREE fragmentation by time, so clients can page through the history. *Beyond spec.* |
| Live ingestion | Feed readings from a live channel, such as MQTT with SenML as in demo 1, into a growing source with the append rules. *Beyond spec.* |
| Triple store loading | Load expanded points, and the observations they derive, into a SPARQL store such as Fuseki or Oxigraph, with one named graph per source. *Beyond spec.* |

## Open decisions

- **Language.** Python first, since rdflib, pyshacl and pyarrow already do the work in the demos, with browser use through Pyodide; or also a JavaScript or TypeScript package for web clients.
- **Specification version.** Settled: the library follows the final specification, and `rdftss.SPEC_VERSION` (2026-11-20) is the `dcterms:modified` date of its bundled vocabulary, which a test checks.
- **Home of the vocabulary and shapes.** Copy them into the package, or make the package their one home, which the demos and the specification repository then reference.
- **First release.** QUDT alignment is a stated goal. LDES publication, live ingestion and triple-store loading could wait, or live in add-on packages.
