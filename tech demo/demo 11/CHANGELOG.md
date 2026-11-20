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
5. **New: time grid.** A data file can give each row's time as a step on a regular grid (`tss:timeBase`, `tss:timeStep`, and optionally `tss:stepField`: without one, a row's step is its position in the file).
6. **New: value encoding.** A data file can state values as differences from a baseline (`tss:valueEncoding`, `tss:baseline`, `tss:DeltaFromBaseline`).
7. **Every time must have a timezone.** Times are `xsd:dateTime` with a timezone; `xsd:dateTimeStamp` is still allowed ([PR #33](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/33), issue #26).
8. **The key is always the time field.** `tss:key` takes at most one value, and that value must be the time field.
9. **`tss:modifiedAt`, `tss:committedUntil` and `tss:mutable` are removed.** The optional `tss:immutable` (default `false`) says whether a source is final; a consumer detects changes with HTTP validators.
10. **Summaries use `tss:generatedAtTime`** in place of `prov:generatedAtTime`. Summaries get their own class, `tss:Summary`, and only an immutable source has them.
11. **New: growing sources can be followed** ([PR #25](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/25)). A growing `tss:EmbeddedRDFSource` is an RDF Message Log, a writer ends a source with `tss:immutable true`, and a consumer reads a growing source again as a whole, or, when the publisher states `tss:rangeRequests true` ([PR #35](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/35)), fetches only the new bytes with HTTP Range requests.
12. **New: RDF Message Stream Profile** ([PR #33](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/33), issue #32). A stream of TSS readings holds a description message, one point message per point, and an ending message; the profile says where an engine finds their focus nodes and timestamps. New term `tss:MessageStreamProfile`. The `messages/` folder streams a conductivity source this way.
13. **New: HTTP Range requests are opt-in** ([PR #35](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/35)). By default, a publisher may write the document or data file of a growing source anew on every change, and a consumer reads it again as a whole. A source that states `tss:rangeRequests true` promises that its bytes only grow, so a consumer fetches only the new bytes. Parquet sources and Arrow files in the IPC file format may now grow too, rewritten as a whole.

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
- `tss:minimum`, `tss:maximum`, the stored summary and the expanded observations all describe the decoded values.
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

**Added (20):**

| Kind | Terms |
|---|---|
| Classes | `tss:EmbeddedRDFSource`, `tss:EmbeddedJSONSource`, `tss:CSVSource`, `tss:ParquetSource`, `tss:ArrowSource`, `tss:Point`, `tss:Summary`, `tss:MissingValuePolicy`, `tss:ValueEncoding` |
| Individuals | `tss:DeltaFromBaseline`, `tss:MessageStreamProfile` (a `prof:Profile`) |
| Properties | `tss:immutable` (replaces `tss:mutable`), `tss:rangeRequests`, `tss:hasPoint`, `tss:timeBase`, `tss:timeStep`, `tss:stepField`, `tss:valueEncoding`, `tss:baseline`, `tss:generatedAtTime` |

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
- **Ranges of the time properties** stay `xsd:dateTime`; the timezone is required by the specification (see [§7](#7-times-a-timezone-is-required)).
- **Comments** of `tss:EmbeddedRDFSource` (a growing one is an RDF Message Log), `tss:ParquetSource` and `tss:ArrowSource` (when they are always immutable), `tss:summary` and `tss:generatedAtTime` (only an immutable source has summaries) follow the new rules.
- **Ontology header:** the two editors (Pieter Colpaert, Shehab Abdelfatah) are stated as `dcterms:creator` instead of `dcterms:contributor`; `dcterms:license` is added (CC BY 4.0); the version is now 0.3.0 (`owl:versionIRI <https://w3id.org/tss/0.3.0#>`), `dcterms:modified` is 2026-11-18, and the description is updated.

## 13. Document structure

- **Abstract and introduction** describe the five source classes, RDF and JSON points, the time grid and value encoding.
- **New sections:** `#storage` (rewritten as a class table), `#times`, `#rdf-points`, `#json-points`, `#arrow`, `#time-grid`, `#value-encoding`, `#message-profile`, `#range-opt-in`, `#following-full`. Removed: `#following-json`, now part of `#following-full`.
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

This section follows the third part of [PR #33](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/33) (issue #32), adapted to demo 11: demo 11 does not have PR #31, so a point is still a row, and the IRI of a field is the predicate of its values.

- **New section `#message-profile`**: RDF Time Series Sources is an RDF Message Stream Profile. A stream that follows it is the RDF Message Log of a `tss:EmbeddedRDFSource`: growing, or ended. The merge of its messages is a conforming metadata document.
- **Streaming a source of another class** (`#streaming-other-sources`): a CSV, Parquet, Arrow or embedded JSON source is streamed as a `tss:EmbeddedRDFSource` of its own, with its own IRIs (such as `<#source>`, `<#value>`), that states `prov:wasDerivedFrom` the original source. It keeps the labels, window, key and fields, and leaves out what describes a data file or JSON points (`tss:dataFile`, `tss:delimiter`, `tss:encoding`, `tss:header`, `tss:points`, the time grid and its step field, `tss:columnIndex`, `tss:valueEncoding`, `tss:baseline`). Its points state their time and decoded values. Its ending message holds `tss:immutable true` and the summaries of the original source.
- **Three kinds of messages:**

  | Message | Holds | Focus nodes | Timestamp |
  |---|---|---|---|
  | description | the sources and their fields, without points; in a stream of an immutable source, without `tss:immutable true` and the summaries | each source | none |
  | point | one point of one source (`tss:hasPoint`) | the point | the point's value for the time field |
  | ending | `tss:immutable true` and the summaries | the source | none |

- A point message is read with the description message of its source, which comes before it. Message order is write order, not time order.
- **The timestamp needs the description message**: it is the value of the time field, whose IRI the description names (`tss:timeField`). In PR #33, which builds on PR #31, the time is stated with `tss:time`, a subproperty of `sosa:resultTime`, so an engine usually finds it without the description.
- **One point per message**: the log of a growing `tss:EmbeddedRDFSource` now holds exactly one point per message (it was one or more), and a Write that adds several points appends one message per point.
- A consumer that starts mid-stream needs the description from elsewhere; a publisher helps it by serving the metadata document at the IRI of the source. (PR #33 only says "from elsewhere".)
- **Announcing the profile**: `<> dcterms:conformsTo tss:MessageStreamProfile` in the first description message, or an HTTP `Link: <https://w3id.org/tss#MessageStreamProfile>; rel="profile"` header. The growing log `waterinfo_289441042_tss2_points_time_value.ttl` now announces it.
- **New example** in the specification: the CSV example streamed as a log of its own `tss:EmbeddedRDFSource`.
- **New folder `messages/`**: a CSV source of five EC20 readings at Zevenaarshaven, streamed by `make_messages.py` into an RDF Message Log of 7 messages (description, five points, ending) of a `tss:EmbeddedRDFSource` derived from it. The merge of the log conforms to the embedded SHACL shapes.

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

## Open points

- **Tool support for RDF Message Logs.** rdflib 7.6 and pyshacl cannot parse `VERSION "1.2-messages"` or the `MESSAGE` delimiter, so the growing example (`waterinfo_289441042_tss2_points_time_value.ttl`) is validated as the merge of its messages: the document without its `VERSION` line and its delimiters.
- **Arrow format not checked by SHACL.** The shapes cannot tell an Arrow IPC file from a stream (that is in the first bytes of the data file), so they don't check that an Arrow source that states `tss:rangeRequests true` is in the IPC streaming format.
- **Byte ranges and content coding.** PR #35 says a server "MAY still compress a response with an HTTP content coding, as byte ranges then count the bytes without content coding". In RFC 9110, byte ranges count the bytes of the representation after its content coding. A consumer that sends `Accept-Encoding: identity` with a Range request, as `#range-requests` says, avoids the problem; the sentence itself is kept as in the PR.
- **The name `tss:rangeRequests`** names the technique a consumer uses, not what the publisher promises (that the bytes only grow). Kept as in PR #35.
- **The PR's `tss.ttl` still has an old duplicate block.** The `tss.ttl` of PR #25 (and of `main`) begins with an earlier "Properties of a field" section that defines 20 terms a second time, with older comments, `rdfs:isDefinedBy <>` and, for `tss:firstTime` and `tss:lastTime`, a second range (`xsd:dateTime` next to `xsd:dateTimeStamp`). Demo 11's `vocabulary/tss.ttl` has the PR's changes without that block, and keeps its `dcterms:license` and `dcterms:creator`; apart from these, the two files state the same triples.
- **Left open in [PR #25](https://github.com/KNowledgeOnWebScale/rdf-tss/pull/25):** the time window of a growing source is fixed; growing sources have no stored summaries; `tss:next` is left to the collection layer (LDES).
- **Two datatypes for one moment.** With both `xsd:dateTime` and `xsd:dateTimeStamp` allowed, the same time can be two different RDF terms. SPARQL comparisons still work, but joins, `DISTINCT` and `GROUP BY` treat them as different values. Tools also differ: pyshacl accepts `"2021-03-03T23:45:00"^^xsd:dateTimeStamp` without a timezone, and rdflib writes `Z` as `+00:00` for `xsd:dateTime`. The SHACL shapes accept both datatypes and check the timezone with a separate rule (`S23`).
- **Steps by position.** Without a step field, a row's time depends on its position in the data file. A CSV file with one value column writes a step without a reading as an empty line, and many CSV readers skip empty lines by default (pandas, for one), which moves every later row to an earlier time. The spec does not yet say how such a row is written, or that readers must keep it. The SHACL shapes cannot check this: they do not read the data file.
- **The profile section of RDF Messages is still a TODO.** The profile follows the direction of the RDF Stream Processing Community Group, and may have to change when RDF Messages defines profiles.
- **Points of an external source in PR #33.** In PR #33, the expansion of a Parquet source written as messages states `tss:hasPoint` on the `tss:ParquetSource`, while the specification says an external source has no `tss:hasPoint`, and the vocabulary gives `tss:hasPoint` the domain `tss:EmbeddedRDFSource`: a reasoner would infer that the Parquet source is an embedded RDF source too. Demo 11 streams such a source as a `tss:EmbeddedRDFSource` of its own, derived from it with `prov:wasDerivedFrom`.
- **Summaries of a part of the window.** This open issue was removed, not resolved.
- **Not in the repository.** Demo 11 also has SHACL shapes (`SHACL/embedded_shapes.ttl`, `SHACL/external_shapes.ttl`), example metadata documents and the `messages/` example. The repository has none of these, so, apart from the short mention in §14, they are not covered by this changelog.
- **Name of the URL and the repository.** The specification is now "RDF Time Series Sources", but its URL (`knowledgeonwebscale.github.io/rdf-timeseriessnippets/`) and its repository still say snippets. Renaming them is a separate step, with redirects.
- **The rdftss library** (`rdftss-python/`) still uses `tss:Snippet` and the `...Snippet` classes, so it does not read the demo 11 documents until it is updated.
- **HTML not rebuilt.** The Bikeshed output has not been regenerated from the demo 11 `spec.bs`.
