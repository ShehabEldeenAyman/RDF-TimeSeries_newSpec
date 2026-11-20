# Changelog: demo 11 compared with the repository

This changelog lists what changes in the specification and the vocabulary when the demo 11 versions replace the ones on `main`.

| | Before (repository) | After (demo 11) |
|---|---|---|
| Specification | [`spec.bs`](https://github.com/KNowledgeOnWebScale/rdf-timeseriessnippets/blob/main/spec.bs) at `main` (`5019a61`) | `spec/spec.bs` |
| Vocabulary | [`tss.ttl`](https://github.com/KNowledgeOnWebScale/rdf-timeseriessnippets/blob/main/tss.ttl) at `main` (`5019a61`) | `vocabulary/tss.ttl` |

Almost every change is breaking: a metadata document that conforms to `main` does not conform to demo 11. The [migration table](#migration-from-main-to-demo-11) maps each old term to its replacement.

## Summary

1. **The specification is renamed "RDF Time Series Sources".** The central class stays `tss:Source`, as on `main`, and the specification now takes its name, so the two agree. This resolves the open issue about the name.
2. **The class of a source says where its readings are.** Five subclasses of `tss:Source` replace `tss:embedded` and `tss:format`: `tss:EmbeddedRDFSource`, `tss:EmbeddedJSONSource`, `tss:CSVSource`, `tss:ParquetSource` and `tss:ArrowSource`.
3. **New: readings as RDF.** An embedded source can hold its readings as RDF nodes (`tss:hasPoint`), not only as one JSON literal.
4. **New: Arrow data files** (`tss:ArrowSource`).
5. **New: time grid.** A data file can give each row's time as a step on a regular grid (`tss:timeBase`, `tss:timeStep`, `tss:stepField`).
6. **New: value encoding.** A data file can state values as differences from a baseline (`tss:valueEncoding`, `tss:baseline`, `tss:DeltaFromBaseline`).
7. **Every time must have a timezone.** All times are `xsd:dateTimeStamp`.
8. **The key is always the time field.** `tss:key` takes at most one value, and that value must be the time field.
9. **`tss:modifiedAt`, `tss:committedUntil` and `tss:mutable` are removed.** The optional `tss:immutable` (default `false`) says whether a source is final; a consumer detects changes with HTTP validators.
10. **Summaries use `tss:generatedAtTime`** in place of `prov:generatedAtTime`. Summaries get their own class, `tss:Summary`, and only an immutable source has them.
11. **New: growing sources can be followed** ([PR #25](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/25)). A growing `tss:EmbeddedRDFSource` is an RDF Message Log, data files only grow, a writer ends a source with `tss:immutable true`, and a consumer fetches only the new bytes with HTTP Range requests.

## 1. The name: RDF Time Series Sources

- The title of the specification is now "RDF Time Series Sources" (it was "RDF Time Series Snippets"), and its Bikeshed shortname `RDF-TS-Sources`. The published URL and the repository keep their names, `rdf-timeseriessnippets`.
- The central class stays `tss:Source`, and `tss:sourceDescription` stays too. The text, the examples and the anchors (`#the-source`, `example-embedded-source`) call it a source.
- The new terms are named after it: the five classes of [§2](#2-the-class-says-where-the-readings-are) (`tss:EmbeddedRDFSource`, ..., `tss:ArrowSource`) and the defined terms Embedded Source, External Source and Growing Source.
- The observations of an expanded source link back with `dcterms:source` to the `tss:Source`, as on `main`.
- An earlier version of demo 11 renamed `tss:Source` to `tss:Snippet` (and the subclasses to `tss:CSVSnippet`, ...). That rename was undone on 2026-11-20: a document written for that version uses `tss:Source` and the `...Source` classes instead.

## 2. The class says where the readings are

The type of a source selects its rules. It replaces the boolean `tss:embedded` and the `tss:format` individuals:

| Class | Where the readings are | Replaces |
|---|---|---|
| `tss:EmbeddedRDFSource` | RDF nodes in the metadata, linked with `tss:hasPoint` | new |
| `tss:EmbeddedJSONSource` | one `rdf:JSON` literal, `tss:points` | `tss:embedded true` |
| `tss:CSVSource` | a CSV data file | `tss:embedded false` with `tss:format tss:CSV` |
| `tss:ParquetSource` | a Parquet data file | `tss:embedded false` with `tss:format tss:Parquet` |
| `tss:ArrowSource` | an Arrow IPC data file | new |

- Every source MUST state exactly one of these classes. A source without one does not say where its readings are.
- All sources of a document MUST be embedded, or all MUST be external. On `main`, a document could mix them.
- Removed: `tss:embedded`, `tss:format`, `tss:CSV` and `tss:Parquet`.
- `tss:delimiter`, `tss:encoding` and `tss:header` now belong to a `tss:CSVSource`. On `main` they belonged to a source with `tss:format tss:CSV`.
- `tss:points` is "not allowed" on an external source. On `main` it was "not used".

## 3. Embedded readings

### New: RDF points (`tss:EmbeddedRDFSource`)

- The source links to each point with `tss:hasPoint`. A point is usually a blank node, written as one statement at the end of the document, so a write appends one statement.
- Each value is one triple: the point is the subject, the IRI of the field is the predicate, and the object is a literal with the field's `tss:datatype`.
- A point has no `rdf:type`: its type (`tss:Point`) follows from `tss:hasPoint`. A point belongs to exactly one source.
- A missing value has no triple. Every value of a field MUST be in a point of its source, and every property of a point SHOULD be a field.
- The fields of an RDF source need no `tss:columnName`, because the field's IRI names its values (see [§9](#9-fields)).
- The opening embedded example now uses RDF points. The multi-sensor example and the describing-field example do too.

### JSON points (`tss:EmbeddedJSONSource`): changed

The JSON form keeps `tss:points`, but its rules change:

| | `main` | demo 11 |
|---|---|---|
| Native JSON numbers and booleans | Allowed, and read by value | A value SHOULD be a JSON string in the lexical form of its datatype, because a JSON number loses its text when it is read |
| Keys starting with `@` | Not mentioned | MUST NOT be used (JSON-LD keywords) |
| Empty array `[]` | Not mentioned | Allowed: no points |
| Column names | Required for every field | Required for every field of a JSON source, stated explicitly |
| Order of the points after a write | The new point is added "in time order" | Existing points stay unchanged and the new point is added; there is no order |
| Values of an added point | Keep their JSON type | Written as JSON strings |

The JSON version of the opening example is now a separate example in `#json-points`.

### Shared rules for both forms (new section `#points`)

- Every point has a time.
- A point has at most one value for each field.
- Points have no order.
- A source may have no points yet.
- An embedded source has no time grid and no value encoding.

## 4. New: Arrow data files

A `tss:ArrowSource` reads an Arrow IPC file by its column names, as a Parquet file is read. A dictionary-encoded column is read as the values it encodes, and an Arrow null is an empty value. A new table maps each Arrow type to its lexical form and its allowed `tss:datatype`.

## 5. New: time grid

An external source MAY state its times as steps on a regular grid:

- `tss:timeBase`: the time of step 0. It anchors the grid and is not a bound of the time window.
- `tss:timeStep`: an `xsd:duration` of more than zero. Step *n* is at `tss:timeBase` + *n* × `tss:timeStep`, with calendar arithmetic for years and months.
- `tss:stepField`: the field (`xsd:integer`) whose column holds the step of each row. Each step is a whole number of 0 or more, and no two rows are at the same step.
- With a grid, the time field has no column: no `tss:columnName` and no `tss:columnIndex`.
- `tss:timeBase` and `tss:timeStep` come together. `tss:stepField` is required with a grid and not allowed without one. An embedded source MUST NOT state a grid.
- New definitions: [=Time Grid=] and [=Step Field=].

## 6. New: value encoding

A field of an external source MAY state that its values are not the readings themselves:

- `tss:valueEncoding tss:DeltaFromBaseline`: each value is the difference from `tss:baseline`. The decoded value is `tss:baseline` + the value, computed exactly on the decimal values.
- `tss:baseline` is required with `tss:DeltaFromBaseline` and not allowed without it.
- The time field has no value encoding. The fields of an embedded source MUST NOT state one.
- `tss:minimum`, `tss:maximum`, the stored summary and the expanded observations all describe the decoded values.
- New definition: [=Decoded Value=]. New class: `tss:ValueEncoding`.

## 7. Times: a timezone is required

| | `main` | demo 11 |
|---|---|---|
| Datatype of the time field | SHOULD be `xsd:dateTime`; `xsd:dateTimeStamp` allowed | MUST be `xsd:dateTimeStamp` |
| Times in the metadata (`tss:from`, `tss:until`, `tss:timeBase`, summary times) | `xsd:dateTime` | literals with datatype `xsd:dateTimeStamp` |
| Timezone | SHOULD; a time without one is read as UTC | MUST; a time without one is not valid |

- A new section, `#times`, lists every time of a source and says that times are compared as the moments they stand for.
- In the Parquet and Arrow tables, a timestamp column without a timezone can no longer hold the time field. It may still be an `xsd:dateTime` value of another field.
- In the vocabulary, the range of `tss:from`, `tss:until`, `tss:timeBase`, `tss:firstTime`, `tss:lastTime` and `tss:generatedAtTime` is now `xsd:dateTimeStamp`.
- `sosa:resultTime` in the observations example stays `xsd:dateTime`, the range that SOSA declares.

## 8. The key is the time field

| | `main` | demo 11 |
|---|---|---|
| `tss:key` | optional, one or more fields; default: the time field | optional, at most one, and it MUST be the time field |
| Uniqueness | no two points with the same values for all key fields | no two points with the same time; two times that are the same moment are the same time |

- A new note explains why: a field identifies its series (sensor, feature of interest, observed property), so a second key field such as a station would put several series in one field.
- The write check is now "no other point has the same time".
- In the vocabulary, `tss:key` is an `owl:FunctionalProperty`, with domain `tss:Source` and range `tss:Field`.

## 9. Fields

- `tss:columnName` is required for a field of an external source (except the time field of a source with a time grid) and for a field of a `tss:EmbeddedJSONSource`. It is optional for a field of a `tss:EmbeddedRDFSource`. On `main` it was required for every field.
- New field properties: `tss:valueEncoding` and `tss:baseline` (see [§6](#6-new-value-encoding)).
- `tss:columnIndex` is not allowed on the time field of a source with a time grid.
- [=Empty Value=] is redefined for every storage form: an empty CSV cell, a Parquet or Arrow null, an RDF point without a triple for the field, a JSON point without the key or with `null`, or `""`.
- In an RDF point, every value MUST be a literal whose datatype is the field's `tss:datatype`.

## 10. Changes over time

This section follows [PR #25](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/25), "Defining how to follow a growing source" (closes issues #18, #20, #21 and #23).

- **`tss:modifiedAt` is removed**, from the source table, the examples, the write steps and the vocabulary, along with its `rdfs:subPropertyOf dcterms:modified`.
- **`tss:mutable` becomes `tss:immutable`**, named after `ldes:immutable`. It is optional, with default `false`. `true` means the source is final: no point is added, changed or removed, and its description does not change any more. `false`, or no `tss:immutable`, means the source is a new defined term, a **Growing Source**: it is append-only. An immutable source never becomes growing again. `tss:mutable true` is now the default, and `tss:mutable false` is `tss:immutable true`.
- **`tss:committedUntil` is removed.** A consumer finds out whether a growing source changed with HTTP validators (`ETag`, `Last-Modified`) and `Cache-Control`, not from the metadata.
- **A growing source's description does not change**: its class, fields, time window, data file and time grid stay as they are, and every new point MUST lie in the window. A Write no longer widens the window, sets a time of change or recomputes summaries: it only adds points.
- **Some sources are always immutable**: a `tss:ParquetSource` (a Parquet file cannot be appended to) and a `tss:ArrowSource` in the Arrow IPC file format MUST state `tss:immutable true`.
- **Arrow**: the IPC streaming format is now allowed, and a growing Arrow source MUST use it. A consumer tells the two formats apart by the first bytes of the file (`ARROW1` or `0xFFFFFFFF`).
- **RDF Message Log**: the metadata document of a growing `tss:EmbeddedRDFSource` MUST be an RDF Message Log (RDF Messages, version label `1.2-messages`). The first message describes the source, each next message holds points, and every message ends with a delimiter (`MESSAGE` or `@message .`). The embedded example is now written this way.
- **Writes** (`#updating`, renamed "Adding points to a growing source") are split in three: embedded sources (`#updating-embedded`: append one message, or rewrite `tss:points` with the existing points unchanged and in the same order), data files (`#appending-data-files`: complete CSV records ending with a line break; complete Arrow stream messages, no end-of-stream marker while growing), and **ending a growing source** (`#ending`: add `tss:immutable true` and, optionally, the summaries; in a log, as one last message, which never states `tss:immutable false`).
- **New section: following a growing source** (`#following`): polling with conditional requests and `Cache-Control`, reading only the new bytes with `Range: bytes=N-` in units that a writer appends whole (an RDF message, a CSV record, an Arrow message), handling `206`, `416`, `200`, `404` and `410`, and a fallback to a full re-read. Embedded JSON points are re-read as a whole document.
- **Conformance**: a conforming consumer follows a growing source as `#following` says; a conforming writer also appends to data files and ends sources as the spec says.

## 11. Stored summaries

- `prov:generatedAtTime` is now `tss:generatedAtTime`, an `rdfs:subPropertyOf prov:generatedAtTime` with range `xsd:dateTimeStamp`. The spec no longer uses PROV-O directly.
- New classes: `tss:Summary`, the range of `tss:summary` and the domain of every summary attribute, and `tss:MissingValuePolicy`, of which `tss:ExcludeEmptyValues` is now an instance.
- Summaries are computed from the decoded values (see [§6](#6-new-value-encoding)).
- **Only an immutable source has summaries.** A field of a growing source MUST NOT have a `tss:summary`: a summary is computed once, from the final points, when the writer ends the source. A Write no longer recomputes summaries.

## 12. Vocabulary (`tss.ttl`)

**Removed (7):** `tss:embedded`, `tss:format`, `tss:CSV`, `tss:Parquet`, `tss:modifiedAt`, `tss:committedUntil`, `tss:mutable`.

**Added (18):**

| Kind | Terms |
|---|---|
| Classes | `tss:EmbeddedRDFSource`, `tss:EmbeddedJSONSource`, `tss:CSVSource`, `tss:ParquetSource`, `tss:ArrowSource`, `tss:Point`, `tss:Summary`, `tss:MissingValuePolicy`, `tss:ValueEncoding` |
| Individual | `tss:DeltaFromBaseline` |
| Properties | `tss:immutable` (replaces `tss:mutable`), `tss:hasPoint`, `tss:timeBase`, `tss:timeStep`, `tss:stepField`, `tss:valueEncoding`, `tss:baseline`, `tss:generatedAtTime` |

**Changed:**

- **Domains** stay `tss:Source`. Exceptions: `tss:points` now has domain `tss:EmbeddedJSONSource`, and the new `tss:hasPoint` has domain `tss:EmbeddedRDFSource`.
- **New domains and ranges:**
  - `tss:timeField` and `tss:key`: `tss:Source` → `tss:Field`.
  - `tss:summary` → `tss:Summary`.
  - Summary attributes: domain `tss:Summary`.
  - `tss:missingValuePolicy` → `tss:MissingValuePolicy`.
  - `tss:minimum`, `tss:maximum`, `tss:observedMinimum`, `tss:observedMaximum`, `tss:mean` and `tss:median` → `rdfs:Literal`.
- **CSVW alignment is weakened from subsumption to `skos:closeMatch`:**
  - `tss:Field rdfs:subClassOf csvw:Column` becomes `skos:closeMatch csvw:Column`.
  - `tss:columnName rdfs:subPropertyOf csvw:name` becomes `skos:closeMatch csvw:name`.

  A field of an RDF source is not a table column, and it may have no column name.
- **`tss:key`** becomes an `owl:FunctionalProperty`.
- **Ranges of the time properties** become `xsd:dateTimeStamp` (see [§7](#7-times-a-timezone-is-required)).
- **Comments** of `tss:EmbeddedRDFSource` (a growing one is an RDF Message Log), `tss:ParquetSource` and `tss:ArrowSource` (when they are always immutable), `tss:summary` and `tss:generatedAtTime` (only an immutable source has summaries) follow the new rules.
- **Ontology header:** the two editors (Pieter Colpaert, Shehab Abdelfatah) are stated as `dcterms:creator` instead of `dcterms:contributor`; `dcterms:license` is added (CC BY 4.0); the version is now 0.3.0 (`owl:versionIRI <https://w3id.org/tss/0.3.0#>`), `dcterms:modified` is 2026-11-18, and the description is updated.

## 13. Document structure

- **Abstract and introduction** describe the five source classes, RDF and JSON points, the time grid and value encoding.
- **New sections:** `#storage` (rewritten as a class table), `#times`, `#rdf-points`, `#json-points`, `#arrow`, `#time-grid`, `#value-encoding`.
- **The "Open issues" section is removed:**
  - "Source or Snippet": resolved (Source: the class keeps its name, and the specification is renamed to match).
  - "Mutable external source": resolved (a growing data file only grows, see [§10](#10-changes-over-time)).
  - "Empty string": kept, as a note under the Empty Value definition.
  - "Summaries of a part of the window": **dropped without a resolution**.
- **Conformance:**
  - A consumer selects rules by class, not by `tss:embedded`.
  - Reading times and decoding values are part of conformance.
  - The four conformance classes are exported definitions (`dfn export`).
  - "Conforming data" covers time-grid steps.
- **Vocabulary overview** lists the new classes and properties, grouped by where they are used.

## Migration from `main` to demo 11

| On `main` | In demo 11 |
|---|---|
| `a tss:Source ; tss:embedded true ; tss:points "…"` | `a tss:EmbeddedJSONSource ; tss:points "…"` |
| `a tss:Source ; tss:embedded false ; tss:format tss:CSV` | `a tss:CSVSource` |
| `a tss:Source ; tss:embedded false ; tss:format tss:Parquet` | `a tss:ParquetSource` |
| `tss:modifiedAt`, `tss:committedUntil` | remove (consumers use HTTP validators) |
| `tss:mutable false` | `tss:immutable true` (required on a Parquet source and on an Arrow source in the IPC file format) |
| `tss:mutable true` | leave out (or `tss:immutable false`), and remove the summaries |
| a growing `tss:EmbeddedRDFSource` in plain Turtle | an RDF Message Log: `VERSION "1.2-messages"`, and `MESSAGE` after the description and after each point |
| `prov:generatedAtTime` on a summary | `tss:generatedAtTime` |
| `"…"^^xsd:dateTime` for any time, time field `tss:datatype xsd:dateTime` | `"…"^^xsd:dateTimeStamp`, with a timezone |
| `tss:key` with several fields, or a non-time field | the time field only, or leave `tss:key` out |
| JSON points with native numbers or booleans | JSON strings in the lexical form of the datatype |

## Open points

- **Tool support for RDF Message Logs.** rdflib 7.6 and pyshacl cannot parse `VERSION "1.2-messages"` or the `MESSAGE` delimiter, so the growing example (`waterinfo_289441042_tss2_points_time_value.ttl`) is validated as the merge of its messages: the document without its `VERSION` line and its delimiters.
- **Arrow format not checked by SHACL.** The shapes cannot tell an Arrow IPC file from a stream (that is in the first bytes of the data file), so they don't check that an Arrow source in the file format states `tss:immutable true`.
- **The PR's `tss.ttl` still has an old duplicate block.** The `tss.ttl` of PR #25 (and of `main`) begins with an earlier "Properties of a field" section that defines 20 terms a second time, with older comments, `rdfs:isDefinedBy <>` and, for `tss:firstTime` and `tss:lastTime`, a second range (`xsd:dateTime` next to `xsd:dateTimeStamp`). Demo 11's `vocabulary/tss.ttl` has the PR's changes without that block, and keeps its `dcterms:license` and `dcterms:creator`; apart from these, the two files state the same triples.
- **Left open in [PR #25](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/25):** the time window of a growing source is fixed; growing sources have no stored summaries; `tss:next` is left to the collection layer (LDES).
- **Tool support for `xsd:dateTimeStamp`.** `main` preferred `xsd:dateTime` because `xsd:dateTimeStamp` "is not as well supported by RDF tooling". This is still true: pyshacl accepts `"2021-03-03T23:45:00"^^xsd:dateTimeStamp` without a timezone. The demo 11 SHACL shapes check the timezone with a separate rule (`S23`).
- **Summaries of a part of the window.** This open issue was removed, not resolved.
- **Not in the repository.** Demo 11 also has SHACL shapes (`SHACL/embedded_shapes.ttl`, `SHACL/external_shapes.ttl`) and example metadata documents. The repository has neither, so they are not covered by this changelog.
- **Name of the URL and the repository.** The specification is now "RDF Time Series Sources", but its URL (`knowledgeonwebscale.github.io/rdf-timeseriessnippets/`) and its repository still say snippets. Renaming them is a separate step, with redirects.
- **The rdftss library** (`rdftss-python/`) still uses `tss:Snippet` and the `...Snippet` classes, so it does not read the demo 11 documents until it is updated.
- **HTML not rebuilt.** The Bikeshed output has not been regenerated from the demo 11 `spec.bs`.
