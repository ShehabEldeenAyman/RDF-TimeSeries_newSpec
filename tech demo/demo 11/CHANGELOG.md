# Changelog: demo 11 compared with the repository

This changelog lists what changes in the specification and the vocabulary when the demo 11 versions replace the ones on `main`.

| | Before (repository) | After (demo 11) |
|---|---|---|
| Specification | [`spec.bs`](https://github.com/KNowledgeOnWebScale/rdf-timeseriessnippets/blob/main/spec.bs) at `main` (`5019a61`) | `spec/spec.bs` |
| Vocabulary | [`tss.ttl`](https://github.com/KNowledgeOnWebScale/rdf-timeseriessnippets/blob/main/tss.ttl) at `main` (`5019a61`) | `vocabulary/tss.ttl` |

Almost every change is breaking: a metadata document that conforms to `main` does not conform to demo 11. The [migration table](#migration-from-main-to-demo-11) maps each old term to its replacement.

## Summary

1. **Source becomes Snippet.** `tss:Source` is now `tss:Snippet`. This resolves the open issue about the name.
2. **The class of a snippet says where its readings are.** Five subclasses of `tss:Snippet` replace `tss:embedded` and `tss:format`: `tss:EmbeddedRDFSnippet`, `tss:EmbeddedJSONSnippet`, `tss:CSVSnippet`, `tss:ParquetSnippet` and `tss:ArrowSnippet`.
3. **New: readings as RDF.** An embedded snippet can hold its readings as RDF nodes (`tss:hasPoint`), not only as one JSON literal.
4. **New: Arrow data files** (`tss:ArrowSnippet`).
5. **New: time grid.** A data file can give each row's time as a step on a regular grid (`tss:timeBase`, `tss:timeStep`, `tss:stepField`).
6. **New: value encoding.** A data file can state values as differences from a baseline (`tss:valueEncoding`, `tss:baseline`, `tss:DeltaFromBaseline`).
7. **Every time must have a timezone.** All times are `xsd:dateTimeStamp`.
8. **The key is always the time field.** `tss:key` takes at most one value, and that value must be the time field.
9. **`tss:modifiedAt` is removed.** `tss:committedUntil` is the only time of change.
10. **Summaries use `tss:generatedAtTime`** in place of `prov:generatedAtTime`. Summaries get their own class, `tss:Summary`.

## 1. Source becomes Snippet

- `tss:Source` is now `tss:Snippet`, and all text, examples and anchors are renamed to match: `#the-source` → `#the-snippet`, and `example-embedded-source` → `example-embedded-snippet`. The name of the specification ("RDF Time Series Snippets") and the name of its central class now agree.
- `tss:sourceDescription` is now `tss:snippetDescription`.
- The observations of an expanded snippet still link back with `dcterms:source`, now to the `tss:Snippet`.

## 2. The class says where the readings are

The type of a snippet selects its rules. It replaces the boolean `tss:embedded` and the `tss:format` individuals:

| Class | Where the readings are | Replaces |
|---|---|---|
| `tss:EmbeddedRDFSnippet` | RDF nodes in the metadata, linked with `tss:hasPoint` | new |
| `tss:EmbeddedJSONSnippet` | one `rdf:JSON` literal, `tss:points` | `tss:embedded true` |
| `tss:CSVSnippet` | a CSV data file | `tss:embedded false` with `tss:format tss:CSV` |
| `tss:ParquetSnippet` | a Parquet data file | `tss:embedded false` with `tss:format tss:Parquet` |
| `tss:ArrowSnippet` | an Arrow IPC data file | new |

- Every snippet MUST state exactly one of these classes. A snippet without one does not say where its readings are.
- All snippets of a document MUST be embedded, or all MUST be external. On `main`, a document could mix them.
- Removed: `tss:embedded`, `tss:format`, `tss:CSV` and `tss:Parquet`.
- `tss:delimiter`, `tss:encoding` and `tss:header` now belong to a `tss:CSVSnippet`. On `main` they belonged to a source with `tss:format tss:CSV`.
- `tss:points` is "not allowed" on an external snippet. On `main` it was "not used".

## 3. Embedded readings

### New: RDF points (`tss:EmbeddedRDFSnippet`)

- The snippet links to each point with `tss:hasPoint`. A point is usually a blank node, written as one statement at the end of the document, so a write appends one statement.
- Each value is one triple: the point is the subject, the IRI of the field is the predicate, and the object is a literal with the field's `tss:datatype`.
- A point has no `rdf:type`: its type (`tss:Point`) follows from `tss:hasPoint`. A point belongs to exactly one snippet.
- A missing value has no triple. Every value of a field MUST be in a point of its snippet, and every property of a point SHOULD be a field.
- The fields of an RDF snippet need no `tss:columnName`, because the field's IRI names its values (see [§9](#9-fields)).
- The opening embedded example now uses RDF points. The multi-sensor example and the describing-field example do too.

### JSON points (`tss:EmbeddedJSONSnippet`): changed

The JSON form keeps `tss:points`, but its rules change:

| | `main` | demo 11 |
|---|---|---|
| Native JSON numbers and booleans | Allowed, and read by value | A value SHOULD be a JSON string in the lexical form of its datatype, because a JSON number loses its text when it is read |
| Keys starting with `@` | Not mentioned | MUST NOT be used (JSON-LD keywords) |
| Empty array `[]` | Not mentioned | Allowed: no points |
| Column names | Required for every field | Required for every field of a JSON snippet, stated explicitly |
| Order of the points after a write | The new point is added "in time order" | Existing points stay unchanged and the new point is added; there is no order |
| Values of an added point | Keep their JSON type | Written as JSON strings |

The JSON version of the opening example is now a separate example in `#json-points`.

### Shared rules for both forms (new section `#points`)

- Every point has a time.
- A point has at most one value for each field.
- Points have no order.
- A snippet may have no points yet.
- An embedded snippet has no time grid and no value encoding.

## 4. New: Arrow data files

A `tss:ArrowSnippet` reads an Arrow IPC file by its column names, as a Parquet file is read. A dictionary-encoded column is read as the values it encodes, and an Arrow null is an empty value. A new table maps each Arrow type to its lexical form and its allowed `tss:datatype`.

## 5. New: time grid

An external snippet MAY state its times as steps on a regular grid:

- `tss:timeBase`: the time of step 0. It anchors the grid and is not a bound of the time window.
- `tss:timeStep`: an `xsd:duration` of more than zero. Step *n* is at `tss:timeBase` + *n* × `tss:timeStep`, with calendar arithmetic for years and months.
- `tss:stepField`: the field (`xsd:integer`) whose column holds the step of each row. Each step is a whole number of 0 or more, and no two rows are at the same step.
- With a grid, the time field has no column: no `tss:columnName` and no `tss:columnIndex`.
- `tss:timeBase` and `tss:timeStep` come together. `tss:stepField` is required with a grid and not allowed without one. An embedded snippet MUST NOT state a grid.
- New definitions: [=Time Grid=] and [=Step Field=].

## 6. New: value encoding

A field of an external snippet MAY state that its values are not the readings themselves:

- `tss:valueEncoding tss:DeltaFromBaseline`: each value is the difference from `tss:baseline`. The decoded value is `tss:baseline` + the value, computed exactly on the decimal values.
- `tss:baseline` is required with `tss:DeltaFromBaseline` and not allowed without it.
- The time field has no value encoding. The fields of an embedded snippet MUST NOT state one.
- `tss:minimum`, `tss:maximum`, the stored summary and the expanded observations all describe the decoded values.
- New definition: [=Decoded Value=]. New class: `tss:ValueEncoding`.

## 7. Times: a timezone is required

| | `main` | demo 11 |
|---|---|---|
| Datatype of the time field | SHOULD be `xsd:dateTime`; `xsd:dateTimeStamp` allowed | MUST be `xsd:dateTimeStamp` |
| Times in the metadata (`tss:committedUntil`, `tss:from`, `tss:until`, `tss:timeBase`, summary times) | `xsd:dateTime` | literals with datatype `xsd:dateTimeStamp` |
| Timezone | SHOULD; a time without one is read as UTC | MUST; a time without one is not valid |

- A new section, `#times`, lists every time of a snippet and says that times are compared as the moments they stand for.
- In the Parquet and Arrow tables, a timestamp column without a timezone can no longer hold the time field. It may still be an `xsd:dateTime` value of another field.
- In the vocabulary, the range of `tss:committedUntil`, `tss:from`, `tss:until`, `tss:timeBase`, `tss:firstTime`, `tss:lastTime` and `tss:generatedAtTime` is now `xsd:dateTimeStamp`.
- `sosa:resultTime` in the observations example stays `xsd:dateTime`, the range that SOSA declares.

## 8. The key is the time field

| | `main` | demo 11 |
|---|---|---|
| `tss:key` | optional, one or more fields; default: the time field | optional, at most one, and it MUST be the time field |
| Uniqueness | no two points with the same values for all key fields | no two points with the same time; two times that are the same moment are the same time |

- A new note explains why: a field identifies its series (sensor, feature of interest, observed property), so a second key field such as a station would put several series in one field.
- The write check is now "no other point has the same time".
- In the vocabulary, `tss:key` is an `owl:FunctionalProperty`, with domain `tss:Snippet` and range `tss:Field`.

## 9. Fields

- `tss:columnName` is required for a field of an external snippet (except the time field of a snippet with a time grid) and for a field of a `tss:EmbeddedJSONSnippet`. It is optional for a field of a `tss:EmbeddedRDFSnippet`. On `main` it was required for every field.
- New field properties: `tss:valueEncoding` and `tss:baseline` (see [§6](#6-new-value-encoding)).
- `tss:columnIndex` is not allowed on the time field of a snippet with a time grid.
- [=Empty Value=] is redefined for every storage form: an empty CSV cell, a Parquet or Arrow null, an RDF point without a triple for the field, a JSON point without the key or with `null`, or `""`.
- In an RDF point, every value MUST be a literal whose datatype is the field's `tss:datatype`.

## 10. Changes over time

- **`tss:modifiedAt` is removed**, from the snippet table, the examples, the write steps and the vocabulary, along with its `rdfs:subPropertyOf dcterms:modified`. `tss:committedUntil` is the only time of change, and every write sets it.
- **`tss:mutable` on an external snippet now has a meaning**: `true` means rows may be added to the file, but no row is changed or removed. This resolves the open issue on `main` about whether external snippets are append-only.
- **Writes** now cover both embedded forms: they append a `tss:hasPoint` statement or rewrite `tss:points`. A new example, `example-write`, shows a point being added.

## 11. Stored summaries

- `prov:generatedAtTime` is now `tss:generatedAtTime`, an `rdfs:subPropertyOf prov:generatedAtTime` with range `xsd:dateTimeStamp`. The spec no longer uses PROV-O directly.
- New classes: `tss:Summary`, the range of `tss:summary` and the domain of every summary attribute, and `tss:MissingValuePolicy`, of which `tss:ExcludeEmptyValues` is now an instance.
- Summaries are computed from the decoded values (see [§6](#6-new-value-encoding)).

## 12. Vocabulary (`tss.ttl`)

**Removed (7):** `tss:Source`, `tss:embedded`, `tss:format`, `tss:CSV`, `tss:Parquet`, `tss:modifiedAt`, `tss:sourceDescription`.

**Added (19):**

| Kind | Terms |
|---|---|
| Classes | `tss:Snippet`, `tss:EmbeddedRDFSnippet`, `tss:EmbeddedJSONSnippet`, `tss:CSVSnippet`, `tss:ParquetSnippet`, `tss:ArrowSnippet`, `tss:Point`, `tss:Summary`, `tss:MissingValuePolicy`, `tss:ValueEncoding` |
| Individual | `tss:DeltaFromBaseline` |
| Properties | `tss:hasPoint`, `tss:snippetDescription`, `tss:timeBase`, `tss:timeStep`, `tss:stepField`, `tss:valueEncoding`, `tss:baseline`, `tss:generatedAtTime` |

**Changed:**

- **Domains** move from `tss:Source` to `tss:Snippet`. Exceptions: `tss:points` has domain `tss:EmbeddedJSONSnippet`, and `tss:hasPoint` has domain `tss:EmbeddedRDFSnippet`.
- **New domains and ranges:**
  - `tss:timeField` and `tss:key`: `tss:Snippet` → `tss:Field`.
  - `tss:summary` → `tss:Summary`.
  - Summary attributes: domain `tss:Summary`.
  - `tss:missingValuePolicy` → `tss:MissingValuePolicy`.
  - `tss:minimum`, `tss:maximum`, `tss:observedMinimum`, `tss:observedMaximum`, `tss:mean` and `tss:median` → `rdfs:Literal`.
- **CSVW alignment is weakened from subsumption to `skos:closeMatch`:**
  - `tss:Field rdfs:subClassOf csvw:Column` becomes `skos:closeMatch csvw:Column`.
  - `tss:columnName rdfs:subPropertyOf csvw:name` becomes `skos:closeMatch csvw:name`.

  A field of an RDF snippet is not a table column, and it may have no column name.
- **`tss:key`** becomes an `owl:FunctionalProperty`.
- **Ranges of the time properties** become `xsd:dateTimeStamp` (see [§7](#7-times-a-timezone-is-required)).
- **Ontology header:** the two editors (Pieter Colpaert, Shehab Abdelfatah) are stated as `dcterms:creator` instead of `dcterms:contributor`; `dcterms:license` is added (CC BY 4.0); `dcterms:modified` and the description are updated.

## 13. Document structure

- **Abstract and introduction** describe the five snippet classes, RDF and JSON points, the time grid and value encoding.
- **New sections:** `#storage` (rewritten as a class table), `#times`, `#rdf-points`, `#json-points`, `#arrow`, `#time-grid`, `#value-encoding`.
- **The "Open issues" section is removed:**
  - "Source or Snippet": resolved (Snippet).
  - "Mutable external source": resolved (append-only, see [§10](#10-changes-over-time)).
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
| `a tss:Source ; tss:embedded true ; tss:points "…"` | `a tss:EmbeddedJSONSnippet ; tss:points "…"` |
| `a tss:Source ; tss:embedded false ; tss:format tss:CSV` | `a tss:CSVSnippet` |
| `a tss:Source ; tss:embedded false ; tss:format tss:Parquet` | `a tss:ParquetSnippet` |
| `tss:sourceDescription` | `tss:snippetDescription` |
| `tss:modifiedAt` | remove (`tss:committedUntil` remains) |
| `prov:generatedAtTime` on a summary | `tss:generatedAtTime` |
| `"…"^^xsd:dateTime` for any time, time field `tss:datatype xsd:dateTime` | `"…"^^xsd:dateTimeStamp`, with a timezone |
| `tss:key` with several fields, or a non-time field | the time field only, or leave `tss:key` out |
| JSON points with native numbers or booleans | JSON strings in the lexical form of the datatype |

## Open points

- **Version not bumped.** The vocabulary is still `owl:versionInfo "0.2.0"` with `owl:versionIRI <https://w3id.org/tss/0.2.0#>`, although the changes above break compatibility. It should probably become 0.3.0.
- **`dcterms:modified` is out of date.** It still says 2026-11-09, but the vocabulary was edited after that date.
- **Tool support for `xsd:dateTimeStamp`.** `main` preferred `xsd:dateTime` because `xsd:dateTimeStamp` "is not as well supported by RDF tooling". This is still true: pyshacl accepts `"2021-03-03T23:45:00"^^xsd:dateTimeStamp` without a timezone. The demo 11 SHACL shapes check the timezone with a separate rule (`S23`).
- **Summaries of a part of the window.** This open issue was removed, not resolved.
- **Not in the repository.** Demo 11 also has SHACL shapes (`SHACL/embedded_shapes.ttl`, `SHACL/external_shapes.ttl`) and example metadata documents. The repository has neither, so they are not covered by this changelog.
- **HTML not rebuilt.** The Bikeshed output has not been regenerated from the demo 11 `spec.bs`.
