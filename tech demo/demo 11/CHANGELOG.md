# Changelog: demo 11 compared with the repository

This changelog lists what changes in the specification and the vocabulary when the demo 11 versions replace the ones on `main`.

| | Before (repository) | After (demo 11) |
|---|---|---|
| Specification | [`spec.bs`](https://github.com/KNowledgeOnWebScale/rdf-timeseriessnippets/blob/main/spec.bs) at `main` (`5019a61`) | `spec/spec.bs` |
| Vocabulary | [`tss.ttl`](https://github.com/KNowledgeOnWebScale/rdf-timeseriessnippets/blob/main/tss.ttl) at `main` (`5019a61`) | `vocabulary/tss.ttl` |

Almost every change is breaking: a metadata document that conforms to `main` does not conform to demo 11. The [migration table](#migration-from-main-to-demo-11) maps each old term to its replacement.

## Demo 11 and the repository today

On 2026-11-20 the repository, now [`KNowledgeOnWebScale/rdf-tss`](https://github.com/KNowledgeOnWebScale/rdf-tss), merged PRs #25, #28, #31, #33, #35 and #37. Its `main` (`aa7dcf7`) now holds almost everything this changelog lists. Demo 11's `spec/spec.bs` and `vocabulary/tss.ttl` are those of `main` at `aa7dcf7`, with these differences:

| Where | Repository `main` (`aa7dcf7`) | Demo 11 |
|---|---|---|
| Introduction, field table | a stored summary is "optional" | optional only once no reading is added any more: "optional on a field of an immutable source; not allowed on a field of a growing source", as `#summaries` already requires |
| `#updating` | a publisher MAY rewrite the file of a growing source, "as long as it holds the same rows" | also: on a time grid without a step field, a rewrite keeps every row at its position, since the position is its step |
| Conformance, writer | "only appends to the data file" | "only adds rows to the data file": since PR #35, a writer MAY rewrite the file by default |
| `#expanding` | the expansion "is its metadata document" with points | the expansion is not a metadata document: for an external source it states `tss:hasPoint`, which its metadata document does not |
| `#message-profile` | a stream is the log of an embedded RDF source, or the expansion of any source written as messages; its example states `ex:csv-source tss:hasPoint` on a `tss:CSVSource` | a stream is the log of an embedded RDF source; a source of another class is streamed as a `tss:EmbeddedRDFSource` of its own, derived from it with `prov:wasDerivedFrom` (new section `#streaming-other-sources`, new example); a late joiner finds the description at the IRI of the source |
| `tss.ttl` | `dcterms:contributor` | `dcterms:creator`, and `dcterms:license` (CC BY 4.0) |
| `tss.ttl` | comments of `tss:Summary` and `tss:MessageStreamProfile` | say that only an immutable source has summaries, and how a source of another class is streamed |

Demo 11 also has what the repository does not: SHACL shapes, example metadata documents, competency questions and the `messages/` example. They follow the final model of fields, rows and points ([§16](#16-fields-rows-and-points)).

## Summary

1. **The specification is renamed "RDF Time Series Sources".** The central class stays `tss:Source`, as on `main`, and the specification now takes its name, so the two agree. This resolves the open issue about the name.
2. **The class of a source says where its readings are.** Five subclasses of `tss:Source` replace `tss:embedded` and `tss:format`: `tss:EmbeddedRDFSource`, `tss:EmbeddedJSONSource`, `tss:CSVSource`, `tss:ParquetSource` and `tss:ArrowSource`.
3. **New: readings as RDF.** An embedded source can hold its readings as RDF nodes (`tss:hasPoint`), not only as one JSON literal.
4. **New: Arrow data files** (`tss:ArrowSource`).
5. **New: time grid.** A data file can give each row's time as a step on a regular grid (`tss:timeBase`, `tss:timeStep`, and optionally `tss:stepField`: without one, a row's step is its position in the file).
6. **New: value encoding.** A data file can state values as differences from a baseline (`tss:valueEncoding`, `tss:baseline`, `tss:DeltaFromBaseline`).
7. **Every time must have a timezone.** Times are `xsd:dateTime` with a timezone; `xsd:dateTimeStamp` is still allowed ([PR #33](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/33), issue #26).
8. **The key is always the time field.** `tss:key` takes at most one value, and that value must be the time field.
9. **`tss:modifiedAt`, `tss:committedUntil` and `tss:mutable` are removed.** The optional `tss:immutable` (default `false`) says whether a source is final; a consumer detects changes with HTTP validators.
10. **Summaries use `tss:generatedAtTime`** in place of `prov:generatedAtTime`. Summaries get their own class, `tss:Summary`, and only an immutable source has them.
11. **New: growing sources can be followed** ([PR #25](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/25)). A growing `tss:EmbeddedRDFSource` is an RDF Message Log, a writer ends a source with `tss:immutable true`, and a consumer reads a growing source again as a whole, or, when the publisher states `tss:rangeRequests true` ([PR #35](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/35)), fetches only the new bytes with HTTP Range requests.
12. **New: RDF Message Stream Profile** ([PR #33](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/33), issue #32). A stream of TSS readings holds a description message, one point message per point, and an ending message; the profile says where an engine finds their focus nodes and timestamps. New term `tss:MessageStreamProfile`. The `messages/` folder streams a conductivity source this way.
13. **New: HTTP Range requests are opt-in** ([PR #35](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/35)). By default, a publisher may write the document or data file of a growing source anew on every change, and a consumer reads it again as a whole. A source that states `tss:rangeRequests true` promises that its bytes only grow, so a consumer fetches only the new bytes. Parquet sources and Arrow files in the IPC file format may now grow too, rewritten as a whole.
14. **Fields, rows and points** ([PR #31](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/31), [PR #37](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/37)). Every field is a value field (`tss:ValueField`, its values are readings) or a context field (`tss:ContextField`: the time, a step, a describing field, an identifier). A point is one reading: one value of one value field, which states its value field, its time and its reading with `tss:field`, `tss:time` and `tss:value`; the points of one time form a row. Only a describing field states its values with a property of its own (`tss:property`). A source expands into points, and the vocabulary aligns them with SOSA and SAREF.

## 1. The name: RDF Time Series Sources

- The title of the specification is now "RDF Time Series Sources" (it was "RDF Time Series Snippets"). The repository was renamed too: its Bikeshed shortname is `RDF-TSS`, its repository `KNowledgeOnWebScale/rdf-tss`, and its URL `https://knowledgeonwebscale.github.io/rdf-tss/`.
- The central class stays `tss:Source`, and `tss:sourceDescription` stays too. The text, the examples and the anchors (`#the-source`, `example-embedded-source`) call it a source.
- The new terms are named after it: the five classes of [§2](#2-the-class-says-where-the-readings-are) (`tss:EmbeddedRDFSource`, ..., `tss:ArrowSource`) and the defined terms Embedded Source, External Source and Growing Source.
- The points of an expanded source link from the `tss:Source` with `tss:hasPoint` ([§16](#16-fields-rows-and-points)).
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

- The source links to each point with `tss:hasPoint`. A point is usually a blank node; the points of one time form a row, written in one statement, so a write appends one statement.
- A point is one reading of one value field: it states its value field (`tss:field`), its time (`tss:time`) and its reading (`tss:value`), each a literal with its field's `tss:datatype`, and the values of the describing fields that describe its value field. A point of a source with one value field may leave out `tss:field` ([§16](#16-fields-rows-and-points)).
- A point has no `rdf:type`: its type (`tss:Point`) follows from `tss:hasPoint`. A point belongs to exactly one source.
- A row without a value for a value field has no point for it. No two points of a source have the same value field and the same time.
- The fields of an RDF source need no `tss:columnName`, except a describing field that states no `tss:property` (see [§9](#9-fields)).
- The opening embedded example now uses RDF points. The multi-sensor example and the describing-field example do too.
- *An earlier version of demo 11 stated each value with the IRI of its field as predicate, one point per row. Changed by PR #31 and PR #37 ([§16](#16-fields-rows-and-points)).*

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

### Shared rules for both forms (new section `#points`, "Embedded rows")

- Every row has a time.
- A row has at most one value for each field.
- Rows have no order.
- A source may have no rows yet.
- An embedded source has no time grid and no value encoding.

## 4. New: Arrow data files

A `tss:ArrowSource` reads an Arrow IPC file by its column names, as a Parquet file is read. A dictionary-encoded column is read as the values it encodes, and an Arrow null is an empty value. A new table maps each Arrow type to its lexical form and its allowed `tss:datatype`.

## 5. New: time grid

An external source MAY state its times as steps on a regular grid:

- `tss:timeBase`: the time of step 0. It anchors the grid and is not a bound of the time window.
- `tss:timeStep`: an `xsd:duration` of more than zero. Step *n* is at `tss:timeBase` + *n* × `tss:timeStep`, with calendar arithmetic for years and months.
- `tss:stepField` (optional): the field (`xsd:integer`) whose column holds the step of each row. Each step is a whole number of 0 or more, and no two rows are at the same step. Rows may be left out, and may be in any order.
- Without a step field, a row's step is its position in the data file: the first row after the CSV header is step 0, and Parquet and Arrow rows are counted across row groups or record batches. Every step then has a row, with empty values for a step without readings. A growing file continues the steps by position, and a consumer that follows it with Range requests keeps count of the rows it has read ([PR #33](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/33), issue #30).
- With a grid, the time field has no column: no `tss:columnName` and no `tss:columnIndex`.
- `tss:timeBase` and `tss:timeStep` come together. `tss:stepField` is optional with a grid and not allowed without one. An embedded source MUST NOT state a grid.
- New definitions: [=Time Grid=] and [=Step Field=].
- A new example shows a grid without a step field.

## 6. New: value encoding

A field of an external source MAY state that its values are not the readings themselves:

- `tss:valueEncoding tss:DeltaFromBaseline`: each value is the difference from `tss:baseline`. The decoded value is `tss:baseline` + the value, computed exactly on the decimal values.
- `tss:baseline` is required with `tss:DeltaFromBaseline` and not allowed without it.
- The time field has no value encoding. The fields of an embedded source MUST NOT state one.
- `tss:minimum`, `tss:maximum`, the stored summary and the points of the expansion all describe the decoded values.
- New definition: [=Decoded Value=]. New class: `tss:ValueEncoding`.

## 7. Times: a timezone is required

| | `main` | demo 11 |
|---|---|---|
| Datatype of the time field | SHOULD be `xsd:dateTime`; `xsd:dateTimeStamp` allowed | MUST be `xsd:dateTime` or `xsd:dateTimeStamp`; SHOULD be `xsd:dateTime` |
| Times in the metadata (`tss:from`, `tss:until`, `tss:timeBase`, summary times) | `xsd:dateTime` | literals with datatype `xsd:dateTime` (or `xsd:dateTimeStamp`) |
| Timezone | SHOULD; a time without one is read as UTC | MUST; a time without one is not valid, also as an `xsd:dateTime` |

- A new section, `#times`, lists every time of a source and says that times are compared as the moments they stand for.
- In the Parquet and Arrow tables, a timestamp column without a timezone can no longer hold the time field. A timestamp column with a timezone is read as `xsd:dateTime` or `xsd:dateTimeStamp`.
- In the vocabulary, the range of `tss:from`, `tss:until`, `tss:timeBase`, `tss:firstTime`, `tss:lastTime` and `tss:generatedAtTime` is `xsd:dateTime`, which also matches `sosa:resultTime`.
- An earlier version of demo 11 required `xsd:dateTimeStamp`. Following [PR #33](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/33) (issue #26), the examples, the metadata documents and the competency-question samples now use `xsd:dateTime`, because fewer RDF systems support `xsd:dateTimeStamp`. The timezone stays required: SHACL check S23 reads it from the text, for both datatypes.

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
- Every field states one of `tss:ValueField` and `tss:ContextField` as its type; only a value field states what its values observe; only a describing field states `tss:property`; a value field may state `tss:aboutUrl` for the IRIs of its points ([§16](#16-fields-rows-and-points)).

## 10. Changes over time

This section follows [PR #25](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/25), "Defining how to follow a growing source" (closes issues #18, #20, #21 and #23).

- **`tss:modifiedAt` is removed**, from the source table, the examples, the write steps and the vocabulary, along with its `rdfs:subPropertyOf dcterms:modified`.
- **`tss:mutable` becomes `tss:immutable`**, named after `ldes:immutable`. It is optional, with default `false`. `true` means the source is final: no point is added, changed or removed, and its description does not change any more. `false`, or no `tss:immutable`, means the source is a new defined term, a **Growing Source**: it is append-only. An immutable source never becomes growing again. `tss:mutable true` is now the default, and `tss:mutable false` is `tss:immutable true`.
- **`tss:committedUntil` is removed.** A consumer finds out whether a growing source changed with HTTP validators (`ETag`, `Last-Modified`) and `Cache-Control`, not from the metadata.
- **A growing source's description does not change**: its class, fields, time window, data file and time grid stay as they are, and every new point MUST lie in the window. A Write no longer widens the window, sets a time of change or recomputes summaries: it only adds points.
- **Some sources are always immutable**: a `tss:ParquetSource` (a Parquet file cannot be appended to) and a `tss:ArrowSource` in the Arrow IPC file format MUST state `tss:immutable true`. *Undone by [PR #35](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/35): such a source may grow, rewritten as a whole (see [§15](#15-new-range-requests-are-opt-in)).*
- **Arrow**: the IPC streaming format is now allowed, and a growing Arrow source MUST use it (since PR #35: an Arrow source that states `tss:rangeRequests true`). A consumer tells the two formats apart by the first bytes of the file (`ARROW1` or `0xFFFFFFFF`).
- **RDF Message Log**: the metadata document of a growing `tss:EmbeddedRDFSource` MUST be an RDF Message Log (RDF Messages, version label `1.2-messages`). The first message describes the source, each next message holds points, and every message ends with a delimiter (`MESSAGE` or `@message .`). The embedded example is now written this way.
- **Writes** (`#updating`, renamed "Adding points to a growing source") are split in three: embedded sources (`#updating-embedded`: append one message, or rewrite `tss:points` with the existing points unchanged and in the same order), data files (`#appending-data-files`: complete CSV records ending with a line break; complete Arrow stream messages, no end-of-stream marker while growing), and **ending a growing source** (`#ending`: add `tss:immutable true` and, optionally, the summaries; in a log, as one last message, which never states `tss:immutable false`).
- **New section: following a growing source** (`#following`): polling with conditional requests and `Cache-Control`, reading only the new bytes with `Range: bytes=N-` in units that a writer appends whole (an RDF message, a CSV record, an Arrow message), handling `206`, `416`, `200`, `404` and `410`, and a fallback to a full re-read. Embedded JSON points are re-read as a whole document. *Since PR #35, Range requests are only for a source that states `tss:rangeRequests true`; every other source is re-read as a whole (see [§15](#15-new-range-requests-are-opt-in)).*
- **Conformance**: a conforming consumer follows a growing source as `#following` says; a conforming writer also appends to data files and ends sources as the spec says.

## 11. Stored summaries

- `prov:generatedAtTime` is now `tss:generatedAtTime`, an `rdfs:subPropertyOf prov:generatedAtTime` with range `xsd:dateTime`. The spec no longer uses PROV-O directly.
- New classes: `tss:Summary`, the range of `tss:summary` and the domain of every summary attribute, and `tss:MissingValuePolicy`, of which `tss:ExcludeEmptyValues` is now an instance.
- Summaries are computed from the decoded values (see [§6](#6-new-value-encoding)).
- **Only an immutable source has summaries.** A field of a growing source MUST NOT have a `tss:summary`: a summary is computed once, from the final points, when the writer ends the source. A Write no longer recomputes summaries.

## 12. Vocabulary (`tss.ttl`)

**Removed (7):** `tss:embedded`, `tss:format`, `tss:CSV`, `tss:Parquet`, `tss:modifiedAt`, `tss:committedUntil`, `tss:mutable`.

**Added (27):**

| Kind | Terms |
|---|---|
| Classes | `tss:EmbeddedRDFSource`, `tss:EmbeddedJSONSource`, `tss:CSVSource`, `tss:ParquetSource`, `tss:ArrowSource`, `tss:ValueField`, `tss:ContextField`, `tss:Point`, `tss:Summary`, `tss:MissingValuePolicy`, `tss:ValueEncoding` |
| Individuals | `tss:DeltaFromBaseline`, `tss:MessageStreamProfile` (a `prof:Profile`) |
| Properties | `tss:immutable` (replaces `tss:mutable`), `tss:rangeRequests`, `tss:hasPoint`, `tss:field`, `tss:time`, `tss:value`, `tss:property`, `tss:aboutUrl`, `tss:timeBase`, `tss:timeStep`, `tss:stepField`, `tss:valueEncoding`, `tss:baseline`, `tss:generatedAtTime` |

**Changed:**

- **Domains** stay `tss:Source`. Exception: `tss:points` now has domain `tss:EmbeddedJSONSource`. The new `tss:hasPoint` has domain `tss:Source`, since the expansion of any source states it (an earlier version of demo 11 had `tss:EmbeddedRDFSource`). `tss:observedProperty`, `tss:unit`, `tss:sensor`, `tss:featureOfInterest` and `tss:aboutUrl` have domain `tss:ValueField`, and `tss:property` and `tss:describesField` domain `tss:ContextField`.
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
- **Alignment with SOSA and SAREF** replaces the mapping section: `tss:Point` is a subclass of `sosa:Observation` and `saref:Measurement`, `tss:time` a subproperty of `sosa:resultTime` and `saref:hasTimestamp`, `tss:value` of `sosa:hasSimpleResult` and `saref:hasValue`, and OWL 2 RL property chains through `tss:field` give each point the observed property, sensor, feature of interest and unit of its field.
- **Ranges of the time properties** stay `xsd:dateTime`; the timezone is required by the specification (see [§7](#7-times-a-timezone-is-required)).
- **Comments** of `tss:EmbeddedRDFSource` (a growing one is an RDF Message Log), `tss:ParquetSource` and `tss:ArrowSource` (when they are always immutable), `tss:summary` and `tss:generatedAtTime` (only an immutable source has summaries) follow the new rules.
- **Ontology header:** the two editors (Pieter Colpaert, Shehab Abdelfatah) are stated as `dcterms:creator` instead of `dcterms:contributor`; `dcterms:license` is added (CC BY 4.0); the version number (an earlier 0.3.0) is removed until there is a first stable version, as PR #37 does; `dcterms:modified` is 2026-11-20, and the description is updated.

## 13. Document structure

- **Abstract and introduction** describe the five source classes, RDF and JSON points, the time grid and value encoding.
- **New sections:** `#storage` (rewritten as a class table), `#times`, `#rdf-points`, `#json-points`, `#arrow`, `#time-grid`, `#value-encoding`, `#field-kinds`, `#point-values`, `#point-iris`, `#alignment`, `#message-profile`, `#streaming-other-sources`, `#range-opt-in`, `#following-full`. Removed: `#following-json`, now part of `#following-full`, and `#sosa-mapping`, replaced by `#alignment`.
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

## 14. New: RDF Message Stream Profile

This section follows the third part of [PR #33](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/33) (issue #32), with the model of fields, rows and points of PR #31 and PR #37 ([§16](#16-fields-rows-and-points)), and one change of demo 11's own: how a source of another class is streamed.

- **New section `#message-profile`**: RDF Time Series Sources is an RDF Message Stream Profile. A stream that follows it is the RDF Message Log of a `tss:EmbeddedRDFSource`: growing, or ended. The merge of its messages is a conforming metadata document.
- **Streaming a source of another class** (`#streaming-other-sources`, demo 11 only): a CSV, Parquet, Arrow or embedded JSON source is streamed as a `tss:EmbeddedRDFSource` of its own, with its own IRIs (such as `<#source>`, `<#value-field>`), that states `prov:wasDerivedFrom` the original source. It keeps the labels, window, key and fields, and leaves out what describes a data file, JSON rows or the IRIs of points (`tss:dataFile`, `tss:delimiter`, `tss:encoding`, `tss:header`, `tss:points`, the time grid and its step field, `tss:columnIndex`, `tss:valueEncoding`, `tss:baseline`, `tss:aboutUrl`). Each describing field states the `tss:property` of the original. Its row messages hold the points of the expansion, each with `tss:field`. Its ending message holds `tss:immutable true` and the summaries of the original source. The repository instead writes the expansion as messages, with `tss:hasPoint` on the original CSV, Parquet or Arrow source, whose merge is not a conforming metadata document.
- **Three kinds of messages:**

  | Message | Holds | Focus nodes | Timestamp |
  |---|---|---|---|
  | description | the sources and their fields, without points; in a stream of an immutable source, without `tss:immutable true` and the summaries | each source | none |
  | row | one row of one source: one point for each value field with a value (`tss:hasPoint`) | its points | `tss:time` on its points |
  | ending | `tss:immutable true` and the summaries | the source | none |

- A row message is read with the description message of its source, which comes before it. Message order is write order, not time order.
- **The timestamp is `tss:time`**, a subproperty of `sosa:resultTime`, so an engine finds it without the description message.
- **One row per message**: the log of a growing `tss:EmbeddedRDFSource` holds exactly one row per message, and a Write that adds several rows appends one message per row.
- A consumer that starts mid-stream needs the description from elsewhere; a publisher helps it by serving the metadata document at the IRI of the source. (PR #33 only says "from elsewhere".)
- **Announcing the profile**: `<> dcterms:conformsTo tss:MessageStreamProfile` in the first description message, or an HTTP `Link: <https://w3id.org/tss#MessageStreamProfile>; rel="profile"` header. The growing log `waterinfo_289441042_tss2_points_time_value.ttl` now announces it.
- **New example** in the specification: the CSV example streamed as a log of its own `tss:EmbeddedRDFSource`.
- **New folder `messages/`**: a CSV source of five EC20 readings at Zevenaarshaven, streamed by `make_messages.py` into an RDF Message Log of 7 messages (description, five rows, ending) of a `tss:EmbeddedRDFSource` derived from it; each point states `tss:field`, `tss:time`, `tss:value` and the quality colour with its `tss:property`. The merge of the log conforms to the embedded SHACL shapes.

## 15. New: Range requests are opt-in

This section follows [PR #35](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/35), "Make HTTP Range following opt-in with `tss:rangeRequests`".

- **Growing no longer means appending in place.** A growing source is append-only in its points: none is changed or removed. By default, the bytes that hold them need not only grow: a publisher MAY write its metadata document or data file anew on every change, for example to compress, sort or re-serialise it. A consumer then reads it again as a whole (new section `#following-full`): a conditional request, and on `200 OK` it emits the points whose time it has not emitted yet.
- **New property `tss:rangeRequests`** (optional, `xsd:boolean`, default `false`). `true` promises that, while the source grows, every representation of its RDF Message Log or data file, without content coding, is a byte prefix of every later one, and that the resource is not compressed as a whole (no `.csv.gz`). A consumer then fetches only the new bytes with `Range: bytes=N-` (`#range-requests`), and MUST NOT do so for a source that does not state it. Its server SHOULD answer Range requests. It does not change while the source grows.
- **Only some sources may state `tss:rangeRequests true`**: a `tss:EmbeddedRDFSource`, a `tss:CSVSource`, and a `tss:ArrowSource` in the IPC streaming format. Not a `tss:ParquetSource` or an Arrow file in the IPC file format (rewritten on every append), and not a `tss:EmbeddedJSONSource` (a Write rewrites `tss:points`).
- **Parquet sources and Arrow files in the IPC file format may grow**, rewritten as a whole. `tss:immutable true` is no longer required on them; SHACL check S27 is removed.
- **Data files**: by default a producer MAY write the whole file anew, and a file that a consumer reads holds complete rows only. With `tss:rangeRequests true`, the earlier rules apply: CSV records and Arrow messages are appended in place, and the header line, schema and earlier records do not change.
- **Time grid without a step field** (demo 11 addition): a rewrite keeps every row at its position, as the position of a row is its step.
- **Examples**: the embedded example and the growing Arrow stream state `tss:rangeRequests true`, and so do the growing log `waterinfo_289441042_tss2_points_time_value.ttl` and the growing Arrow sample of the competency questions. CQ7 now also reports `tss:rangeRequests`.
- **SHACL**: new check S28 in both shape files: `tss:rangeRequests` is at most one `xsd:boolean`, and is not `true` on a `tss:ParquetSource` or a `tss:EmbeddedJSONSource`.
- **Conformance**: a conforming writer "only adds rows to the data file of a growing source as `#appending-data-files` says" (was: "only appends").

## 16. Fields, rows and points

This section follows [PR #31](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/31), "Separate fields from the properties that state their values" (issue #22), and [PR #37](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/37), "State readings with `tss:value` and rename `tss:MetaField` to `tss:ContextField`" (issue #36). PR #31 gave each field a property of its own; PR #37 replaced that with fixed properties for every point. Demo 11 has the final model of PR #37.

- **Two kinds of fields** (`#field-kinds`). Every field states exactly one of `tss:ValueField` and `tss:ContextField`. A value field holds readings. A context field holds the context of a row: the time field, the step field and every describing field MUST be context fields, and so is any other field whose values are not readings, such as an identifier. A context field MUST NOT state `tss:observedProperty`, `tss:sensor`, `tss:featureOfInterest` or `tss:aboutUrl`. A source SHOULD have at least one value field. (PR #31 called a context field `tss:MetaField`.)
- **Rows and points** (`#definitions`). A row is one time with values for the fields (what an earlier version called a point). A point (`tss:Point`) is one reading: the value of one value field in one row, with the time of the row and the values of the describing fields that describe it.
- **How points state values** (`#point-values`). Every point states its value field with `tss:field`, its time with `tss:time` and its reading with `tss:value`. A field is never the predicate of its values. A point of a source with exactly one value field MAY leave out `tss:field`; the expansion always adds it. No two points of a source have the same value field and time.
- **Describing fields** keep a property of their own, `tss:property`, as `csvw:propertyUrl` does; without one, the default is the base IRI (the data file, or the metadata document of an embedded source), `#` and the column name. In a `tss:EmbeddedRDFSource`, a describing field states `tss:property` or `tss:columnName`. Only a describing field states `tss:property`; it is not `rdf:type` or a TSS term, and two describing fields of a source have different properties.
- **Point IRIs** (`#point-iris`): a value field of a source that is not a `tss:EmbeddedRDFSource` MAY state `tss:aboutUrl`, a URI template (RFC 6570) whose variables are column names, as `csvw:aboutUrl` does. Without it, points are blank nodes.
- **Expansion into points** (`#expanding`). Every source expands into points in the shape of the RDF points, each linked to its value field with `tss:field`; the expansion replaces the expansion into SOSA observations and its mapping table.
- **Alignment with SOSA and SAREF** (`#alignment`, informative): through the vocabulary ([§12](#12-vocabulary-tssttl)), a reasoner derives a `sosa:Observation` and a `saref:Measurement` from each point. A property of a domain vocabulary, such as `ex:temperature`, needs a rule (N3 or SPARQL `CONSTRUCT`), not OWL.
- **JSON rows**: the objects in `tss:points` are rows; each expands into a point per value field.
- **Demo 11**: every field of the metadata documents and of the competency-question samples is typed; the quality code colour fields are context fields without `tss:observedProperty`. The RDF points of `waterinfo_289441042_tss2_points_embedded.ttl` state `tss:time`, `tss:value` and `ex:qualityCodeColor` (the `tss:property` of the quality colour field), and leave out `tss:field`, since the source has one value field; the points of the growing log `waterinfo_289441042_tss2_points_time_value.ttl` state `tss:field` too, for consumers that start reading mid-stream. The competency questions give the same answers as before.
- **SHACL**: new checks F1 (field kind), F11 (time, step and describing fields are context fields), F12 (no observed property, sensor, feature of interest or `tss:aboutUrl` on a context field), F13 (`tss:property`), F14 (`tss:aboutUrl`), F15 (a describing field describes a value field of its source) and S29 (warning: a source without a value field). The point checks read `tss:field`, `tss:time` and `tss:value`: P8 (`tss:field`, left out only with one value field), P9 (`tss:value`), P10 (describing values only on the points they describe), and P1–P7 and F9 rewritten.

## Migration from `main` to demo 11

| On `main` | In demo 11 |
|---|---|
| `a tss:Source ; tss:embedded true ; tss:points "…"` | `a tss:EmbeddedJSONSource ; tss:points "…"` |
| `a tss:Source ; tss:embedded false ; tss:format tss:CSV` | `a tss:CSVSource` |
| `a tss:Source ; tss:embedded false ; tss:format tss:Parquet` | `a tss:ParquetSource` |
| `tss:modifiedAt`, `tss:committedUntil` | remove (consumers use HTTP validators) |
| `tss:mutable false` | `tss:immutable true` |
| a growing source whose consumers fetch only the new bytes with Range requests | add `tss:rangeRequests true` (only on a `tss:EmbeddedRDFSource`, a `tss:CSVSource`, or a `tss:ArrowSource` in the IPC streaming format), and keep the bytes append-only |
| `tss:mutable true` | leave out (or `tss:immutable false`), and remove the summaries |
| a growing `tss:EmbeddedRDFSource` in plain Turtle, or a log message with several points | an RDF Message Log: `VERSION "1.2-messages"`, and `MESSAGE` after the description and after each point |
| `prov:generatedAtTime` on a summary | `tss:generatedAtTime` |
| a time without a timezone | the same time with a timezone, such as `Z` or `+02:00` (`xsd:dateTime` stays; `xsd:dateTimeStamp` is allowed too) |
| `tss:key` with several fields, or a non-time field | the time field only, or leave `tss:key` out |
| JSON points with native numbers or booleans | JSON strings in the lexical form of the datatype |
| `a tss:Field` | `a tss:ValueField` for a field whose values are readings; `a tss:ContextField` for the time field, the step field, describing fields and identifiers |
| `tss:observedProperty`, `tss:sensor` or `tss:featureOfInterest` on a describing or other context field | remove; say what it is in `tss:fieldDescription` |
| an RDF point with the IRI of each field as predicate: `[ ex:time-field "…" ; ex:value-field "…" ]` | one point per value field: `[ tss:field ex:value-field ; tss:time "…" ; tss:value "…" ]` (`tss:field` may be left out when the source has one value field) |
| the value of a describing field on an RDF point, with the field's IRI | the same value, with the field's `tss:property` (state one, or a `tss:columnName` for the default property) |
| the expansion into SOSA observations | the expansion into points; a reasoner derives SOSA observations through the alignment of the vocabulary |

## Open points

- **Tool support for RDF Message Logs.** rdflib 7.6 and pyshacl cannot parse `VERSION "1.2-messages"` or the `MESSAGE` delimiter, so the growing example (`waterinfo_289441042_tss2_points_time_value.ttl`) is validated as the merge of its messages: the document without its `VERSION` line and its delimiters.
- **Arrow format not checked by SHACL.** The shapes cannot tell an Arrow IPC file from a stream (that is in the first bytes of the data file), so they don't check that an Arrow source that states `tss:rangeRequests true` is in the IPC streaming format.
- **Byte ranges and content coding.** PR #35 says a server "MAY still compress a response with an HTTP content coding, as byte ranges then count the bytes without content coding". In RFC 9110, byte ranges count the bytes of the representation after its content coding. A consumer that sends `Accept-Encoding: identity` with a Range request, as `#range-requests` says, avoids the problem; the sentence itself is kept as in the PR.
- **The name `tss:rangeRequests`** names the technique a consumer uses, not what the publisher promises (that the bytes only grow). Kept as in PR #35.
- **Left open in [PR #25](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/25):** the time window of a growing source is fixed; growing sources have no stored summaries; `tss:next` is left to the collection layer (LDES).
- **Two datatypes for one moment.** With both `xsd:dateTime` and `xsd:dateTimeStamp` allowed, the same time can be two different RDF terms. SPARQL comparisons still work, but joins, `DISTINCT` and `GROUP BY` treat them as different values. Tools also differ: pyshacl accepts `"2021-03-03T23:45:00"^^xsd:dateTimeStamp` without a timezone, and rdflib writes `Z` as `+00:00` for `xsd:dateTime`. The SHACL shapes accept both datatypes and check the timezone with a separate rule (`S23`).
- **Steps by position.** Without a step field, a row's time depends on its position in the data file. A CSV file with one value column writes a step without a reading as an empty line, and many CSV readers skip empty lines by default (pandas, for one), which moves every later row to an earlier time. The spec does not yet say how such a row is written, or that readers must keep it. The SHACL shapes cannot check this: they do not read the data file.
- **The profile section of RDF Messages is still a TODO.** The profile follows the direction of the RDF Stream Processing Community Group, and may have to change when RDF Messages defines profiles.
- **Points of an external source in a message stream.** The repository writes the expansion of a CSV, Parquet or Arrow source as messages with `tss:hasPoint` on that source. Since PR #31 gave `tss:hasPoint` the domain `tss:Source`, this no longer makes a reasoner infer a second source class; but the merge of such a stream is not a metadata document that meets the specification (an external source has no `tss:hasPoint`), and the repository's own example of it fails the demo 11 shapes (S22). Demo 11 keeps its own rule: a source of another class is streamed as a `tss:EmbeddedRDFSource` of its own, derived with `prov:wasDerivedFrom`.
- **Leaving out `tss:field`.** A point of a source with one value field may leave out `tss:field`. Such a point does not say what it measures on its own: a reasoner only gives it the observed property, sensor and unit of its field after the expansion adds `tss:field`, and a consumer that starts reading a message log mid-stream cannot tell which field a reading belongs to. Demo 11's growing log states `tss:field` anyway.
- **Default properties of describing fields depend on where files are.** The default `tss:property` of a describing field is made from the IRI of the data file, or of the metadata document of an embedded source, so the same status column gets a different predicate when the file moves or is read from `file://`. An explicit `tss:property` avoids this; the demo 11 documents state one where the predicate is used in RDF points.
- **A describing value without a reading is lost.** The value of a describing field is stated on the point of the reading it describes; a row with a status but no reading has no point, so the status is not in the RDF points or the expansion.
- **Summaries of a part of the window.** This open issue was removed, not resolved.
- **Not in the repository.** Demo 11 also has SHACL shapes (`SHACL/embedded_shapes.ttl`, `SHACL/external_shapes.ttl`), example metadata documents and the `messages/` example. The repository has none of these, so, apart from the short mentions in §14 and §16, they are not covered by this changelog.
- **The rdftss library** (`rdftss-python/`) still uses `tss:Snippet` and the `...Snippet` classes, so it does not read the demo 11 documents until it is updated.
- **HTML not rebuilt.** The Bikeshed output has not been regenerated from the demo 11 `spec.bs`.
