# Gaps and issues in RDF Time Series Sources

This document lists defects in the RDF Time Series Sources specification and its vocabulary. Each issue has an example of the problem, a suggested fix, and an example of how it looks after the fix. Each issue also says whether demo 11 already fixes it.

- **Reviewed:** `spec.bs` and `tss.ttl` of [KNowledgeOnWebScale/rdf-tss](https://github.com/KNowledgeOnWebScale/rdf-tss), `main` at commit `a1195c9` (2026-11-20), after PRs #25, #28, #31, #33, #35 and #37 were merged.
- **References:** `§name` is a section of the specification (its anchor, `#name`). `L123` is line 123 of `spec.bs`, and `T123` line 123 of `tss.ttl`, at that commit.
- **Demo 11:** demo 11's `spec/spec.bs` and `vocabulary/tss.ttl` are the same files with the changes listed at the top of [CHANGELOG.md](CHANGELOG.md).
- **Examples:** Turtle examples use the prefixes of the specification (`tss:`, `xsd:`, `rdf:`, `rdfs:`, `owl:`, `dcterms:`, `prov:`, `sosa:`, `saref:`, `quantitykind:`, `unit:`), and `ex:` for `https://example.org/ex#`. Most leave out the parts of a description that do not matter for the issue. A `diff` block shows text of the specification before (`-`) and after (`+`) the fix.

## How the specification was checked

- The specification and the vocabulary were read in full.
- The Bikeshed build of `main` passes, and every `[[#anchor]]` link and `[=term=]` resolves.
- Every Turtle example parses. Every example that describes a whole source was validated with the demo 11 SHACL shapes: all conform, except the message stream example (I-03).
- Claims about other standards were checked against RFC 9110, RFC 6570, the SAREF v3.1.1 and v3.2.1 ontologies, and the SSN ontology.

## Severity

- **Breaking:** data or software that follows every rule still fails to work.
- **Conflict:** two rules contradict each other, or a rule contradicts a standard the specification relies on.
- **Gap:** the specification does not say enough, so two implementations can disagree on the same data.
- **Minor:** wording, leftovers from earlier versions, or a summary that misstates a rule.

## Overview

| ID | Issue | Severity | Demo 11 |
|---|---|---|---|
| [I-01](#i-01-a-message-log-cannot-refer-to-a-source-or-field-written-as-a-blank-node) | A message log cannot refer to a source or field written as a blank node | Breaking | open |
| [I-02](#i-02-byte-ranges-and-content-coding-contradict-rfc-9110) | Byte ranges and content coding contradict RFC 9110 | Breaking | open |
| [I-03](#i-03-the-expansion-breaks-the-rules-of-the-source-table) | The expansion breaks the rules of the source table | Breaking | fixed |
| [I-04](#i-04-tssrangerequests-is-stated-per-source-but-its-promise-covers-the-whole-document) | `tss:rangeRequests` is stated per source, but its promise covers the whole document | Breaking | open |
| [I-05](#i-05-a-stream-repeats-or-contradicts-tssimmutable-and-the-summaries) | A stream repeats or contradicts `tss:immutable` and the summaries | Conflict | partly fixed |
| [I-06](#i-06-every-point-states-its-value-field-against-the-optional-tssfield) | "Every point states its value field" against the optional `tss:field` | Conflict | open |
| [I-07](#i-07-must-be-in-the-lexical-form-against-accepts-any-text) | "MUST be in the lexical form" against "accepts any text" | Conflict | open |
| [I-08](#i-08-a-describing-field-of-several-value-fields-one-value-per-row-or-per-point) | A describing field of several value fields: one value per row, or per point? | Conflict | open |
| [I-09](#i-09-the-ending-message-is-not-one-row) | The ending message is not "one row" | Conflict | open |
| [I-10](#i-10-the-field-table-calls-a-summary-optional) | The field table calls a summary optional | Conflict | fixed |
| [I-11](#i-11-the-conformance-classes-do-not-match-the-rules) | The conformance classes do not match the rules | Conflict | partly fixed |
| [I-12](#i-12-the-saref-alignment-uses-terms-that-saref-v321-deprecates) | The SAREF alignment uses terms that SAREF v3.2.1 deprecates | Conflict | open |
| [I-13](#i-13-the-text-of-a-value-is-not-fixed-but-iris-and-literals-are-built-from-it) | The text of a value is not fixed, but IRIs and literals are built from it | Gap | open |
| [I-14](#i-14-column-names-do-not-fit-uri-template-variables-and-the-default-property-has-two-encodings) | Column names do not fit URI template variables, and the default property has two encodings | Gap | open |
| [I-15](#i-15-a-describing-value-in-a-row-without-a-reading-is-lost) | A describing value in a row without a reading is lost | Gap | open |
| [I-16](#i-16-a-rewrite-can-move-rows-on-a-time-grid-without-a-step-field) | A rewrite can move rows on a time grid without a step field | Gap | fixed |
| [I-17](#i-17-default-properties-of-describing-fields-depend-on-where-files-are) | Default properties of describing fields depend on where files are | Gap | open |
| [I-18](#i-18-a-point-without-tssfield-depends-on-its-context) | A point without `tss:field` depends on its context | Gap | open |
| [I-19](#i-19-tsscolumnindex-is-optional-but-needed-without-a-header-line) | `tss:columnIndex` is optional, but needed without a header line | Gap | open |
| [I-20](#i-20-the-parquet-table-mixes-physical-and-logical-types) | The Parquet table mixes physical and logical types | Gap | open |
| [I-21](#i-21-the-step-field-must-be-xsdinteger-which-an-unsigned-column-cannot-use) | The step field must be `xsd:integer`, which an unsigned column cannot use | Gap | open |
| [I-22](#i-22-the-baseline-rule-leaves-out-most-integer-types) | The baseline rule leaves out most integer types | Gap | open |
| [I-23](#i-23-tssabouturl-cannot-give-unique-iris-on-a-time-grid-without-a-step-field) | `tss:aboutUrl` cannot give unique IRIs on a time grid without a step field | Gap | open |
| [I-24](#i-24-an-rdflangstring-field-without-tsslanguage) | An `rdf:langString` field without `tss:language` | Gap | open |
| [I-25](#i-25-json-rows-no-key-against-null) | JSON rows: "no key" against `null` | Minor | open |
| [I-26](#i-26-leftover-the-time-base-stays-the-same-when-the-window-changes) | Leftover: the time base "stays the same" when the window changes | Minor | open |
| [I-27](#i-27-vocabulary-comments-still-use-reading-for-a-row) | Vocabulary comments still use "reading" for a row | Minor | open |
| [I-28](#i-28-the-vocabulary-overview-misstates-where-terms-are-used) | The vocabulary overview misstates where terms are used | Minor | open |
| [I-29](#i-29-the-property-chains-take-tss-with-ssn-or-saref-outside-owl-2-dl) | The property chains take TSS with SSN or SAREF outside OWL 2 DL | Minor | open |

## Breaking

### I-01 A message log cannot refer to a source or field written as a blank node

**Severity:** Breaking · **Demo 11:** open

**Where:** §rdf-points (L562), §updating-embedded (L1091), §ending (L1126), §message-kinds (L1222). The definition of a source (L160) only says it is "described by one node".

RDF Messages scopes blank node labels to one message, and L562 relies on that. If the first message of a log writes the source, or a value field, as a blank node, a later message that uses the same label means another node:

- the points of a row message hang from that other node, not from the source, and `tss:field _:value` names another field;
- the ending message makes that other node immutable, and the source stays growing.

No rule asks for an IRI, so a writer that mints blank nodes produces such logs. The demo 11 shapes report the points (S6: the node that links to them is no source), but nothing reports the lost ending.

#### Example of the issue

```turtle
VERSION "1.2-messages"

_:source a tss:EmbeddedRDFSource ;
    tss:timeField _:time ;
    tss:hasField _:time, _:value .
_:time a tss:ContextField ; tss:datatype xsd:dateTime .
_:value a tss:ValueField ; tss:datatype xsd:double .
MESSAGE
_:source tss:hasPoint [                  # a new node: the _:source of the first message is out of scope
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "4810.5"^^xsd:double
] .
MESSAGE
_:source tss:immutable true .            # yet another node: the source stays growing
MESSAGE
```

The merge holds a source without points, a node without a class that has the point, and a third node that is immutable.

**Fix:** in an RDF Message Log or Stream, every source, and every field that a later message refers to, MUST be an IRI. A point may stay a blank node: no other message refers to it.

```diff
-The metadata document of a [=Growing Source=] of this class MUST be an RDF Message Log [[RDF-MESSAGES]], ...
+The metadata document of a [=Growing Source=] of this class MUST be an RDF Message Log [[RDF-MESSAGES]], ... Blank node labels are scoped to one message, so every source, and every field that a later message refers to, MUST be an IRI.
```

#### Example after the fix

```turtle
VERSION "1.2-messages"

ex:source a tss:EmbeddedRDFSource ;
    tss:timeField ex:time-field ;
    tss:hasField ex:time-field, ex:value-field .
ex:time-field a tss:ContextField ; tss:datatype xsd:dateTime .
ex:value-field a tss:ValueField ; tss:datatype xsd:double .
MESSAGE
ex:source tss:hasPoint [                 # the point may stay a blank node
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "4810.5"^^xsd:double
] .
MESSAGE
ex:source tss:immutable true .
MESSAGE
```

Every message refers to the same source and fields.

### I-02 Byte ranges and content coding contradict RFC 9110

**Severity:** Breaking · **Demo 11:** open

**Where:** §range-opt-in (L1084): "A server MAY still compress a response with an HTTP content coding, as byte ranges then count the bytes without content coding". §range-requests (L1187): "Byte offsets count the bytes of the representation without content coding, so the consumer SHOULD send `Accept-Encoding: identity`".

In HTTP, a content coding is part of the representation (RFC 9110 §8.4), and a byte range selects octets of that representation (§14.1.2). With `Content-Encoding: gzip`, the offsets count compressed bytes, not the uncompressed bytes the consumer counted. A server that compresses as L1084 allows, or a consumer that skips the SHOULD, reads the new bytes at the wrong offset.

#### Example of the issue

```http
GET /readings.csv HTTP/1.1
Host: example.org
Range: bytes=5000-
Accept-Encoding: gzip

HTTP/1.1 206 Partial Content
Content-Encoding: gzip
Content-Range: bytes 5000-6123/6124
```

The consumer has read 5000 bytes of the CSV text. The server counts the range in the gzip stream, as RFC 9110 says, and returns its bytes from 5000 on: they start in the middle of the compressed stream, so the consumer can neither decompress them nor find its next row.

**Fix:** remove the sentence at L1084, and make `Accept-Encoding: identity` a MUST. A response that still has a content coding is read as in the fall-back of step 4.

```diff
-The promise is about the bytes as they are stored and served. A server MAY still compress a response with an HTTP content coding, as byte ranges then count the bytes without content coding ([[#range-requests]]). The resource itself MUST NOT be compressed as a whole, ...
+The promise is about the bytes as they are stored and served. A consumer counts these bytes without content coding, so it asks for them with `Accept-Encoding: identity` ([[#range-requests]]). The resource itself MUST NOT be compressed as a whole, ...
```

```diff
-Byte offsets count the bytes of the representation without content coding, so the consumer SHOULD send `Accept-Encoding: identity` with a Range request.
+Byte offsets count the bytes of the representation without content coding, so the consumer MUST send `Accept-Encoding: identity` with a Range request. A `206` response with a `Content-Encoding` is not read: the consumer falls back (step 4).
```

#### Example after the fix

```http
GET /readings.csv HTTP/1.1
Host: example.org
Range: bytes=5000-
Accept-Encoding: identity

HTTP/1.1 206 Partial Content
Content-Range: bytes 5000-5420/5421
```

Without a content coding, byte 5000 of the response is byte 5000 of the CSV text: the start of the first row the consumer has not read.

### I-03 The expansion breaks the rules of the source table

**Severity:** Breaking · **Demo 11:** fixed

**Where:** §expanding (L998): "The expansion of a source is its metadata document, without `tss:points`, to which it adds the points". The source table (L216, L217) and §storage (L251): an external source "has no `tss:hasPoint` and no `tss:points`", and a JSON source requires `tss:points`. The example of §message-profile (L1235) and the vocabulary overview (L1294) rely on the expansion.

- For a CSV, Parquet or Arrow source, the expansion states `tss:hasPoint`, which an external source may not state.
- For a JSON source, the expansion drops `tss:points`, which a JSON source must have.

Read as a metadata document, every expansion of a source that is not a `tss:EmbeddedRDFSource` breaks the specification, and a consumer finds a CSV source with both a data file and points, without a rule for which to read.

#### Example of the issue

The expansion of the CSV example (L1019), with the fields as in that example:

```turtle
ex:csv-source
    a tss:CSVSource ;
    tss:immutable true ;
    tss:dataFile <https://example.org/data.csv> ;    # the rows are in the data file ...
    tss:timeField ex:csv-time-field ;
    tss:hasField ex:csv-time-field, ex:csv-value-field .

ex:csv-source tss:hasPoint [                          # ... and also in points
    tss:field ex:csv-value-field ;
    tss:time "2024-05-01T00:00:00Z"^^xsd:dateTime ;
    tss:value "12.5"^^xsd:double
] .
```

A `tss:CSVSource` has no `tss:hasPoint`: read as a metadata document, this fails, and the demo 11 shapes report S22. The message stream example (L1235) streams such an expansion, so its merge fails too.

**Fix:** say that the expansion is not a metadata document, and stream a source of another class as a `tss:EmbeddedRDFSource` of its own, derived from the original.

```diff
 A document with several sources is expanded source by source: the points of a source only link to that source and its fields.
+
+The expansion is not a metadata document: for a source that is not a `tss:EmbeddedRDFSource`, it links the source to points with `tss:hasPoint`, which its metadata document does not ([[#storage]]). A source of another class is streamed as RDF messages as [[#streaming-other-sources]] says.
```

#### Example after the fix

The CSV example streamed as demo 11's §streaming-other-sources says:

```turtle
VERSION "1.2-messages"
PREFIX : <#>

<> dcterms:conformsTo tss:MessageStreamProfile .

:source
    a tss:EmbeddedRDFSource ;                  # a source of its own, so tss:hasPoint is allowed
    prov:wasDerivedFrom ex:csv-source ;
    tss:from "2024-05-01T00:00:00Z"^^xsd:dateTime ;
    tss:timeField :time-field ;
    tss:hasField :time-field, :value-field .
:time-field a tss:ContextField ; tss:columnName "time" ; tss:datatype xsd:dateTime .
:value-field a tss:ValueField ; tss:columnName "value" ; tss:datatype xsd:double .
MESSAGE
:source tss:hasPoint [
    tss:field :value-field ;
    tss:time "2024-05-01T00:00:00Z"^^xsd:dateTime ;
    tss:value "12.5"^^xsd:double
] .
MESSAGE
:source tss:immutable true .
MESSAGE
```

The merge is the metadata document of an immutable `tss:EmbeddedRDFSource`, and it conforms.

**Demo 11:** has both changes: §expanding says that the expansion is not a metadata document, and §streaming-other-sources streams a CSV, Parquet, Arrow or JSON source as shown above.

### I-04 tss:rangeRequests is stated per source, but its promise covers the whole document

**Severity:** Breaking · **Demo 11:** open

**Where:** the source table (L210) and §range-opt-in (L1076, L1082). A metadata document may describe several sources (L202), as long as they are all embedded or all external (L253). By default, a publisher may write the document anew (L1072).

The promise of `tss:rangeRequests true` is about the bytes of the whole metadata document or data file, but each source states it on its own:

- one document can hold two growing `tss:EmbeddedRDFSource`s with different values. The one without it may rewrite the document (L1072), which breaks the promise of the other;
- one document can hold a growing `tss:EmbeddedJSONSource` next to a growing `tss:EmbeddedRDFSource` that states `true`. Every Write to the JSON source rewrites its `tss:points` literal, which changes earlier bytes of the document.

These rules cannot all be met.

#### Example of the issue

```turtle
VERSION "1.2-messages"

ex:conductivity-source a tss:EmbeddedRDFSource ;
    tss:rangeRequests true ;                    # promise: this document only grows, byte for byte
    tss:timeField ex:c-time ;
    tss:hasField ex:c-time, ex:c-value .
ex:c-time a tss:ContextField ; tss:datatype xsd:dateTime .
ex:c-value a tss:ValueField ; tss:datatype xsd:double .

ex:level-source a tss:EmbeddedJSONSource ;      # in the same document
    tss:points """[ { "time": "2024-05-12T08:00:00Z", "level": "2.31" } ]"""^^rdf:JSON ;
    tss:timeField ex:l-time ;
    tss:hasField ex:l-time, ex:l-value .
ex:l-time a tss:ContextField ; tss:columnName "time" ; tss:datatype xsd:dateTime .
ex:l-value a tss:ValueField ; tss:columnName "level" ; tss:datatype xsd:double .
MESSAGE
```

The next Write to `ex:level-source` rewrites the `tss:points` literal in the first message. Every byte after it moves, and a consumer of `ex:conductivity-source` that asks for `bytes=N-` gets bytes from the middle of a message.

**Fix:** allow `true` only when every source of the document states it and none is a `tss:EmbeddedJSONSource`.

```diff
-Only these sources MAY state `tss:rangeRequests true`.
+Only these sources MAY state `tss:rangeRequests true`, and an embedded source only when every source of its metadata document states it and none is a `tss:EmbeddedJSONSource`: the promise is about the bytes of the whole document.
```

#### Example after the fix

Two documents, with the fields left out:

```turtle
# readings.ttl: an RDF Message Log, and every source in it makes the promise
ex:conductivity-source a tss:EmbeddedRDFSource ;
    tss:rangeRequests true .
ex:temperature-source a tss:EmbeddedRDFSource ;
    tss:rangeRequests true .

# levels.ttl: the JSON source has a document of its own, read again as a whole
ex:level-source a tss:EmbeddedJSONSource .
```

## Conflicts

### I-05 A stream repeats or contradicts tss:immutable and the summaries

**Severity:** Conflict · **Demo 11:** partly fixed

**Where:** §message-kinds: the description message holds the description "as in a metadata document" (L1216), and the ending message holds `tss:immutable true` and the summaries (L1218). §ending (L1144): a log "MUST NOT state `tss:immutable false`". `tss:immutable` and `tss:summary` are functional properties (T151, T372).

- For an immutable source, the description message holds `tss:immutable true` and the summaries, as the metadata document does, and the ending message holds them again. Blank nodes are scoped to one message, so the merge gives each field two `tss:summary` nodes.
- For a growing source whose document states `tss:immutable false`, nothing says that the description message leaves it out. Once the stream ends, its merge states both `false` and `true`.
- The example (L1235) streams the CSV example, an immutable source, but has no ending message.

#### Example of the issue

An immutable source written as a stream, with the description "as in a metadata document":

```turtle
VERSION "1.2-messages"

ex:source a tss:EmbeddedRDFSource ;
    tss:immutable true ;                         # in the description message ...
    tss:timeField ex:time-field ;
    tss:hasField ex:time-field, ex:value-field .
ex:time-field a tss:ContextField ; tss:datatype xsd:dateTime .
ex:value-field a tss:ValueField ; tss:datatype xsd:double ;
    tss:summary [ tss:count "2"^^xsd:integer ] .
MESSAGE
ex:source tss:hasPoint [ tss:time "2024-05-01T00:00:00Z"^^xsd:dateTime ; tss:value "12.5"^^xsd:double ] .
MESSAGE
ex:source tss:hasPoint [ tss:time "2024-05-01T00:15:00Z"^^xsd:dateTime ; tss:value "12.7"^^xsd:double ] .
MESSAGE
ex:source tss:immutable true .                   # ... and again in the ending message
ex:value-field tss:summary [ tss:count "2"^^xsd:integer ] .
MESSAGE
```

In the merge, `ex:value-field` has two `tss:summary` nodes, one per message, while a field has at most one summary. Had the description stated `tss:immutable false`, the merge of the ended stream would state both `false` and `true`.

**Fix:** the description message of a stream leaves out `tss:immutable` and the summaries; only the ending message holds them.

```diff
-<tr><td>description message</td><td>the description of one or more sources and their fields, as in a metadata document, without points; ...</td>
+<tr><td>description message</td><td>the description of one or more sources and their fields, as in a metadata document, without points, without <code>tss:immutable</code> and without the [=Stored Summaries=], which only the ending message holds; ...</td>
```

#### Example after the fix

```turtle
VERSION "1.2-messages"

ex:source a tss:EmbeddedRDFSource ;              # no tss:immutable, no summary
    tss:timeField ex:time-field ;
    tss:hasField ex:time-field, ex:value-field .
ex:time-field a tss:ContextField ; tss:datatype xsd:dateTime .
ex:value-field a tss:ValueField ; tss:datatype xsd:double .
MESSAGE
ex:source tss:hasPoint [ tss:time "2024-05-01T00:00:00Z"^^xsd:dateTime ; tss:value "12.5"^^xsd:double ] .
MESSAGE
ex:source tss:hasPoint [ tss:time "2024-05-01T00:15:00Z"^^xsd:dateTime ; tss:value "12.7"^^xsd:double ] .
MESSAGE
ex:source tss:immutable true .                   # only here
ex:value-field tss:summary [ tss:count "2"^^xsd:integer ] .
MESSAGE
```

**Demo 11:** for a stream of a source of another class, the description message leaves out `tss:immutable true` and the summaries. The `tss:immutable false` case is not stated yet.

### I-06 "Every point states its value field" against the optional tss:field

**Severity:** Conflict · **Demo 11:** open

**Where:** the introduction (L46), §point-values (L357) and §meaning (L407) say that every point states its value field with `tss:field`. §rdf-points (L553): "When the source has exactly one value field, a point MAY leave it out".

The specification's own examples leave `tss:field` out. The introduction is not marked informative, so it is normative too. A validator built from L357 rejects the specification's own examples.

#### Example of the issue

The first point of the embedded example (L109):

```turtle
ex:source tss:hasPoint [
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "4810.5"^^xsd:double                 # no tss:field: the source has one value field
] .
```

§rdf-points allows this point; §point-values says that every point states its value field.

**Fix:** add the exception, or a link to §rdf-points, to L46, L357 and L407.

```diff
-In RDF, every [=Point=] states its value field, its time and its reading with the same three properties of the TSS vocabulary: ...
+In RDF, every [=Point=] states its value field, its time and its reading with the same three properties of the TSS vocabulary, except that a point of a source with exactly one value field MAY leave out its value field ([[#rdf-points]]): ...
```

#### Example after the fix

The point above conforms under every section, and so does the same point with `tss:field ex:value-field`.

### I-07 "MUST be in the lexical form" against "accepts any text"

**Severity:** Conflict · **Demo 11:** open

**Where:** §datatypes (L395): "A value MUST be in the lexical form of its datatype", followed by a list of the forms that implementations check at least. L401: "Any other datatype, such as `xsd:string`, accepts any text". §expanding (L1008): a value not in the lexical form of its datatype is an error.

For a datatype that is not in the list, such as `xsd:time` (the datatype of Parquet and Arrow TIME columns), `xsd:duration` or `xsd:anyURI`, L395 makes `"foo"` an error and L401 accepts it. Two conforming expanders disagree on the same data.

#### Example of the issue

```turtle
ex:high-tide-field a tss:ValueField ;
    tss:datatype xsd:time .                      # the time of day of the high tide

ex:source tss:hasPoint [
    tss:field ex:high-tide-field ;
    tss:time "2024-05-12T00:00:00Z"^^xsd:dateTime ;
    tss:value "foo"^^xsd:time                    # L395: not an xsd:time, an error. L401: any text is accepted.
] .
```

**Fix:** check the XSD lexical form of every XSD datatype; only `xsd:string` and `rdf:langString` accept any text.

```diff
-Any other datatype, such as `xsd:string`, accepts any text.
+Every other XSD datatype is checked against its lexical form [[XMLSCHEMA11-2]] too; only `xsd:string` and `rdf:langString` accept any text.
```

#### Example after the fix

```turtle
ex:source tss:hasPoint [
    tss:field ex:high-tide-field ;
    tss:time "2024-05-12T00:00:00Z"^^xsd:dateTime ;
    tss:value "06:42:00"^^xsd:time               # an xsd:time: valid for every implementation
] .
```

`"foo"^^xsd:time` is then an error for every implementation.

### I-08 A describing field of several value fields: one value per row, or per point?

**Severity:** Conflict · **Demo 11:** open

**Where:** §describing (L480): a describing field may describe several value fields, and its value is stated on the point of each field it describes. §points (L545): "A row has at most one value for each field". §rdf-points (L557): a point without a triple for a describing field has an empty value for it.

In RDF points, the points of one row can disagree. The row then has and lacks a value, or has two. Implementations count `tss:count` and `tss:missing` of the describing field differently, and RDF points can hold rows that a CSV or JSON form cannot.

#### Example of the issue

```turtle
ex:status-field a tss:ContextField ;
    tss:datatype xsd:string ;
    tss:property ex:status ;
    tss:describesField ex:temperature-field, ex:humidity-field .

ex:source tss:hasPoint [
    tss:field ex:temperature-field ;
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "18.2"^^xsd:double ;
    ex:status "ok"
], [
    tss:field ex:humidity-field ;
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "61.0"^^xsd:double ;
    ex:status "sensor fault"                     # the same row, another status
] .
```

The row at 08:00 has two values for the status field, while a row has at most one value for each field. A CSV file, with one status cell per row, cannot hold this row.

**Fix:** every point of a row that states the value of a describing field states the same value.

```diff
-... Its value is not a point of its own: it is stated, with the describing field's [=Field Property=], on the point of each field it describes in that row.
+... Its value is not a point of its own: it is stated, with the describing field's [=Field Property=], on the point of each field it describes in that row, and every point of the row that states it states the same value.
```

#### Example after the fix

```turtle
ex:source tss:hasPoint [
    tss:field ex:temperature-field ;
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "18.2"^^xsd:double ;
    ex:status "ok"
], [
    tss:field ex:humidity-field ;
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "61.0"^^xsd:double ;
    ex:status "ok"                               # one status per row, on every point it describes
] .
```

### I-09 The ending message is not "one row"

**Severity:** Conflict · **Demo 11:** open

**Where:** §rdf-points (L562): "every next message holds one row". §ending (L1126) appends "one last message that holds these statements", and §message-kinds (L1218) lists the ending message as a kind of its own.

Read strictly, every log that has been ended breaks L562.

#### Example of the issue

The end of a log whose source has ended:

```turtle
ex:source tss:hasPoint [
    tss:time "2024-05-12T09:45:00Z"^^xsd:dateTime ;
    tss:value "4783.6"^^xsd:double
] .
MESSAGE
ex:source tss:immutable true .   # §ending: one last message. §rdf-points: "every next message holds one row".
MESSAGE
```

**Fix:** name the ending message as the one exception.

```diff
-Its first message describes the sources and their fields, and every next message holds one row: the points of one time.
+Its first message describes the sources and their fields, and every next message holds one row: the points of one time. When the source ends, a last message holds `tss:immutable true` and, optionally, the summaries ([[#ending]]).
```

#### Example after the fix

The log above is unchanged, and now meets both sections.

### I-10 The field table calls a summary optional

**Severity:** Conflict · **Demo 11:** fixed

**Where:** the field table (L340): `tss:summary` is "optional". §summaries (L928): "A field of a [=Growing Source=] MUST NOT have a `tss:summary`".

The table is where readers look for the rules of a field, and it misses the restriction.

#### Example of the issue

```turtle
ex:source a tss:CSVSource ;                      # growing: it states no tss:immutable true
    tss:dataFile <https://example.org/data.csv> ;
    tss:timeField ex:time-field ;
    tss:hasField ex:time-field, ex:value-field .
ex:value-field a tss:ValueField ;
    tss:summary [ tss:count "3"^^xsd:integer ] .   # the table: optional. §summaries: MUST NOT.
```

**Fix:** state the restriction in the table.

```diff
-<tr><td><code>tss:summary</code></td><td>a node</td><td>optional</td><td>a [=Stored Summary=] of the values ([[#summaries]])</td></tr>
+<tr><td><code>tss:summary</code></td><td>a node</td><td>optional on a field of an immutable source; not allowed on a field of a [=Growing Source=]</td><td>a [=Stored Summary=] of the values ([[#summaries]])</td></tr>
```

#### Example after the fix

```turtle
ex:source a tss:CSVSource ;
    tss:immutable true ;                         # final: its fields may have summaries
    tss:dataFile <https://example.org/data.csv> ;
    tss:timeField ex:time-field ;
    tss:hasField ex:time-field, ex:value-field .
ex:value-field a tss:ValueField ;
    tss:summary [ tss:count "3"^^xsd:integer ] .
```

**Demo 11:** says so in the table and in the introduction.

### I-11 The conformance classes do not match the rules

**Severity:** Conflict · **Demo 11:** partly fixed

**Where:** a conforming writer (L1330) "only appends to the data file of a [=Growing Source=]". But §appending-data-files (L1112) and §updating (L1072) let a writer write the whole file anew by default. The byte-prefix rule on what a server returns (L1076), and the order and delimiter rules of a stream (L1222, L1226), belong to no conformance class.

A writer that rewrites the file, as the specification allows, does not conform; and nothing can fail the rules for servers and streams.

#### Example of the issue

A Write to a growing CSV source without `tss:rangeRequests true`. The data file before:

```csv
time,value
2024-05-12T08:15:00Z,4722.4
2024-05-12T08:00:00Z,4810.5
```

The data file after: written anew, sorted, with the new row, as §appending-data-files allows:

```csv
time,value
2024-05-12T08:00:00Z,4810.5
2024-05-12T08:15:00Z,4722.4
2024-05-12T08:30:00Z,4751.9
```

The writer follows §appending-data-files, but a conforming writer "only appends", so it does not conform.

**Fix:** let the writer class follow §appending-data-files, and add a class for publishers and streams.

```diff
-:: Software that changes the rows of an [=Embedded Source=] only by the [=Write=] of [[#updating-embedded]], that only appends to the data file of a [=Growing Source=] as [[#appending-data-files]] says, ...
+:: Software that changes the rows of an [=Embedded Source=] only by the [=Write=] of [[#updating-embedded]], that only adds rows to the data file of a [=Growing Source=] as [[#appending-data-files]] says, ...
+: <dfn export>Conforming publisher</dfn>
+:: Software that serves metadata documents, data files and RDF message streams: it keeps the promise of `tss:rangeRequests` ([[#range-opt-in]]), and writes streams as [[#message-profile]] says.
```

#### Example after the fix

The rewrite above is the Write of a conforming writer. A server that rewrites the file of a source that states `tss:rangeRequests true`, or a stream with its messages out of order, is then not a conforming publisher.

**Demo 11:** the writer "only adds rows to the data file". The rules for servers and streams still belong to no class.

### I-12 The SAREF alignment uses terms that SAREF v3.2.1 deprecates

**Severity:** Conflict · **Demo 11:** open

**Where:** the bibliography cites SAREF v3.2.1 (L24). §alignment (L1027). `tss:Point rdfs:subClassOf saref:Measurement` (T99), and property chains to `saref:relatesToProperty`, `saref:measurementMadeBy` and `saref:isMeasurementOf` (T524 to T526).

SAREF v3.2.1 marks `saref:Measurement`, `saref:relatesToProperty`, `saref:measurementMadeBy` and `saref:isMeasurementOf` as `owl:deprecated true`, so a reasoner derives deprecated types and properties. `saref:hasValue`, `saref:hasTimestamp` and `saref:isMeasuredIn` are not deprecated. In SAREF v3.1.1, all of these terms are current.

#### Example of the issue

What a reasoner derives from the first point of the embedded example, through `tss.ttl`:

```turtle
[] a saref:Measurement ;                                      # deprecated in v3.2.1
    saref:relatesToProperty quantitykind:ElectricConductivity ;   # deprecated
    saref:measurementMadeBy ex:sensor-1 ;                      # deprecated
    saref:isMeasurementOf ex:feature-1 ;                       # deprecated
    saref:isMeasuredIn unit:MicroS-PER-CentiM ;
    saref:hasTimestamp "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    saref:hasValue "4810.5"^^xsd:double .
```

**Fix:** either cite SAREF v3.1.1, whose terms the alignment uses, or follow SAREF v3.2.1. The second is more than a rename: in v3.2.1, a `saref:Observation` links to its value through a result node (`saref:hasResult`, a `saref:PropertyValue`). OWL axioms cannot derive a new node from a point, so that alignment needs a rule (N3 or SPARQL `CONSTRUCT`), as the note in §alignment already says for domain properties.

```diff
   "SAREF": {
-    "href": "https://saref.etsi.org/core/v3.2.1/",
+    "href": "https://saref.etsi.org/core/v3.1.1/",
```

#### Example after the fix

With SAREF v3.1.1 cited, the derived triples above stay the same, and every term in them is current. With SAREF v3.2.1, a rule would derive this shape instead (the link to the feature of interest is left out here; it needs checking against SAREF v3.2.1):

```turtle
[] a saref:Observation ;
    saref:madeBy ex:sensor-1 ;
    saref:hasResultTime "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    saref:hasResult [
        a saref:PropertyValue ;
        saref:hasValue "4810.5"^^xsd:double ;
        saref:isMeasuredIn unit:MicroS-PER-CentiM ;
        saref:isValueOfProperty quantitykind:ElectricConductivity
    ] .
```

## Gaps

### I-13 The text of a value is not fixed, but IRIs and literals are built from it

**Severity:** Gap · **Demo 11:** open

**Where:** §point-iris (L377): a template variable stands for the value "as it is written". §expanding (L1006): "A value without a value encoding keeps its text as written".

For several kinds of values, the text is not fixed:

- Parquet and Arrow floating point values are written in "the XSD lexical form of the value" (L706, L747), and one value has many lexical forms, such as `12.5` and `1.25E1`;
- Parquet and Arrow timestamps get a fraction "only when the value has one" (L711, L752), with no number of digits, such as `.5` or `.500`;
- a JSON value only SHOULD be a string (L569), and a JSON number loses its text when it is read;
- decoded values (L880) and the times of a time grid (L781) are computed, and no text is given for the result.

Two expanders give the same reading different literals, and the same point different IRIs.

#### Example of the issue

A Parquet file with a TIMESTAMP column `time` (08:00:00.500 UTC) and a DOUBLE column `value` (12.5). The value field states `tss:aboutUrl "https://example.org/obs/{time}"`. Two expanders, both following the specification:

```turtle
# expander A
<https://example.org/obs/2024-05-12T08%3A00%3A00.5Z> tss:value "12.5"^^xsd:double .

# expander B
<https://example.org/obs/2024-05-12T08%3A00%3A00.500Z> tss:value "1.25E1"^^xsd:double .
```

The same reading gets two IRIs and two literals.

**Fix:** write a value that the specification computes, or reads from a binary column, in the canonical form of its datatype.

```diff
-<td>the XSD lexical form of the value, with <code>INF</code>, <code>-INF</code> and <code>NaN</code> for the special values</td>
+<td>the canonical form of the value [[XMLSCHEMA11-2]], with <code>INF</code>, <code>-INF</code> and <code>NaN</code> for the special values</td>
```

The same holds for the timestamp rows, for decoded values, and for the times of a time grid.

#### Example after the fix

```turtle
# every expander
<https://example.org/obs/2024-05-12T08%3A00%3A00.5Z> tss:value "1.25E1"^^xsd:double .
```

### I-14 Column names do not fit URI template variables, and the default property has two encodings

**Severity:** Gap · **Demo 11:** open

**Where:** §point-iris (L377): "Each variable of the template is the `tss:columnName` of a field". §point-values (L365): the default property of a describing field is made from the column name "percent-encoded as a fragment [[RFC3986]], as the default `{#_name}` of `csvw:propertyUrl` is made".

- RFC 6570 variable names allow only letters, digits, `_`, `.` and percent-encoded octets. Column names such as `station-id` or `Quality Code` cannot be template variables. CSVW restricts column names to that syntax; TSS cannot, since a `tss:columnName` must equal the header of the data file.
- The two encodings at L365 differ. Percent-encoding as a fragment encodes `[`, `]` and `#`; the `#` operator of the template `{#_name}` leaves reserved characters as they are.

#### Example of the issue

```turtle
ex:level-field a tss:ValueField ;
    tss:columnName "water-level" ;
    tss:datatype xsd:double ;
    tss:aboutUrl "https://example.org/obs/{station-id}/{time}" .   # "-" is not allowed in a variable name
```

A describing field with the column name `Temperature [°C]`, in the data file `https://example.org/data.csv`, has two default properties:

```text
percent-encoded as a fragment:   https://example.org/data.csv#Temperature%20%5B%C2%B0C%5D
the template {#_name}:           https://example.org/data.csv#Temperature%20[%C2%B0C]
```

**Fix:** a template variable is the column name, with every character other than a letter, a digit or `_` percent-encoded (in UTF-8). The default property uses one encoding: percent-encoding as a fragment.

```diff
-- Each variable of the template is the `tss:columnName` of a field of the source, and stands for the value of that field in the row, as it is written.
+- Each variable of the template is the `tss:columnName` of a field of the source, with every character other than a letter, a digit or `_` percent-encoded in UTF-8 [[RFC6570]], and stands for the value of that field in the row, as it is written.
```

```diff
-... the field's `tss:columnName` percent-encoded as a fragment [[RFC3986]], as the default `{#_name}` of `csvw:propertyUrl` is made.
+... the field's `tss:columnName` percent-encoded as a fragment [[RFC3986]].
```

#### Example after the fix

```turtle
ex:level-field a tss:ValueField ;
    tss:columnName "water-level" ;
    tss:datatype xsd:double ;
    tss:aboutUrl "https://example.org/obs/{station%2Did}/{time}" .   # the variable of the column "station-id"
```

```text
default property:   https://example.org/data.csv#Temperature%20%5B%C2%B0C%5D
```

### I-15 A describing value in a row without a reading is lost

**Severity:** Gap · **Demo 11:** open

**Where:** the note in §expanding (L1013): "The expansion keeps every value of a value field and of a describing field". Step 3 of §expanding (L1002) makes a point only "for each value field that has a value", and a describing value is stated on a point.

An embedded RDF source cannot hold such a row at all, and the expansion of any other source drops its describing values, while the stored summary of the describing field counts them.

#### Example of the issue

The data file, with a status field that describes the value field (`tss:property ex:status`):

```csv
time,value,status
2024-05-12T08:00:00Z,4810.5,ok
2024-05-12T08:15:00Z,,offline
```

Its expansion:

```turtle
ex:source tss:hasPoint [
    tss:field ex:value-field ;
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "4810.5"^^xsd:double ;
    ex:status "ok"
] .
# The row at 08:15 has no reading, so no point: "offline" is not in the expansion,
# while the stored summary of the status field counts it (tss:count 2).
```

**Fix:** correct the note. Keeping such values would need a place for them, such as a node for the row, which is a larger change.

```diff
-Note: The expansion keeps every value of a value field and of a describing field. ...
+Note: The expansion keeps every value of a value field, and the values of the describing fields on the points they describe. A row without a reading has no point, so the values of its describing fields are not in the expansion. ...
```

#### Example after the fix

The expansion above is unchanged; the specification now says that it leaves out `"offline"`.

### I-16 A rewrite can move rows on a time grid without a step field

**Severity:** Gap · **Demo 11:** fixed

**Where:** §updating (L1072): a publisher MAY write the file of a growing source anew, "as long as it holds the same rows and the new ones". §time-grid (L788): without a step field, the step of a row is its position in the data file.

A rewrite that sorts the rows, or drops a row of empty values, moves every later row to another time, while it still holds "the same rows".

#### Example of the issue

```turtle
ex:grid-source a tss:CSVSource ;
    tss:dataFile <https://example.org/grid.csv> ;
    tss:timeBase "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:timeStep "PT15M"^^xsd:duration ;          # no tss:stepField: the step of a row is its position
    tss:timeField ex:grid-time-field ;
    tss:hasField ex:grid-time-field, ex:grid-value-field .
```

The data file before and after a rewrite that drops the row without a reading:

```text
before                     after
value                      value
4810.5    step 0, 08:00    4810.5    step 0, 08:00
          step 1, 08:15    4751.9    step 1, 08:15   <- the 08:30 reading moved to 08:15
4751.9    step 2, 08:30
```

**Fix:** on a time grid without a step field, a rewrite keeps every row at its position.

```diff
-... as long as it holds the same rows and the new ones. A consumer then reads the whole document or data file again ([[#following-full]]).
+... as long as it holds the same rows and the new ones. On a [=Time Grid=] without a [=Step Field=], the position of a row is its step ([[#time-grid]]), so a rewrite keeps every row at its position. A consumer then reads the whole document or data file again ([[#following-full]]).
```

#### Example after the fix

```text
value
4810.5    step 0, 08:00
          step 1, 08:15   (no reading: the empty line stays)
4751.9    step 2, 08:30
```

**Demo 11:** says so in §updating.

### I-17 Default properties of describing fields depend on where files are

**Severity:** Gap · **Demo 11:** open

**Where:** §point-values (L365): without `tss:property`, the property of a describing field is made of a base IRI (the IRI of the data file, or of the metadata document for an embedded source), `#`, and its column name.

The same status column gets another predicate when the file moves, is mirrored, or is read from `file://`. Two copies of one source then state their describing values with different predicates.

#### Example of the issue

```turtle
ex:status-field a tss:ContextField ;
    tss:columnName "status" ;
    tss:datatype xsd:string ;
    tss:describesField ex:value-field .            # no tss:property: the default is made from the data file's IRI
```

```text
data file at                          default property of ex:status-field
https://example.org/data.csv          https://example.org/data.csv#status
https://mirror.example.net/data.csv   https://mirror.example.net/data.csv#status
file:///home/user/data.csv            file:///home/user/data.csv#status
```

**Fix:** a describing field SHOULD state `tss:property`.

```diff
 A describing field that states no `tss:property` has a default property: ...
+A describing field SHOULD state `tss:property`: a default property changes when the data file or the metadata document moves.
```

#### Example after the fix

```turtle
ex:status-field a tss:ContextField ;
    tss:columnName "status" ;
    tss:datatype xsd:string ;
    tss:property ex:status ;                       # the same predicate wherever the file is
    tss:describesField ex:value-field .
```

**Demo 11:** the text is unchanged. The demo 11 documents state an explicit `tss:property` where RDF points use the value (`ex:qualityCodeColor`).

### I-18 A point without tss:field depends on its context

**Severity:** Gap · **Demo 11:** open

**Where:** §rdf-points (L553): a point of a source with one value field may leave out `tss:field`. §alignment (L1046): the observed property, sensor, feature of interest and unit reach a point through `tss:field`.

- A point without `tss:field` gets its reading aligned (`tss:value` is a subproperty of `sosa:hasSimpleResult`), but not its observed property, sensor, feature of interest or unit, until the expansion adds `tss:field`.
- In an RDF Message Log, a consumer that starts reading after the first message cannot tell what a reading measures.

#### Example of the issue

A row message, read by a consumer that started after the first message:

```turtle
ex:source tss:hasPoint [
    tss:time "2024-05-12T08:30:00Z"^^xsd:dateTime ;
    tss:value "4751.9"^^xsd:double
] .
MESSAGE
```

The consumer knows a reading of 4751.9 at 08:30, but not what it measures. A reasoner derives `sosa:hasSimpleResult` and `sosa:resultTime`, but no `sosa:observedProperty`, sensor or unit.

**Fix:** a point of an RDF Message Log or Stream states `tss:field`.

```diff
 When the source has exactly one value field, a point MAY leave it out: its value field is then that field. The expansion adds it to every point ([[#expanding]]).
+A point in an RDF Message Log or Stream states `tss:field`, so that each row message says what its readings measure.
```

#### Example after the fix

```turtle
ex:source tss:hasPoint [
    tss:field ex:value-field ;          # the observed property, unit, sensor and feature of interest, through the field
    tss:time "2024-05-12T08:30:00Z"^^xsd:dateTime ;
    tss:value "4751.9"^^xsd:double
] .
MESSAGE
```

**Demo 11:** the text is unchanged. Demo 11's growing log states `tss:field` on every point.

### I-19 tss:columnIndex is optional, but needed without a header line

**Severity:** Gap · **Demo 11:** open

**Where:** the field table (L329): `tss:columnIndex` is "optional". §csv (L621): without a header line (`tss:header false`), a field's column is the column at its `tss:columnIndex`. The vocabulary (T297): "Optional when tss:columnName identifies the column".

A CSV source with `tss:header false`, whose fields state no `tss:columnIndex`, meets the rules but cannot be read.

#### Example of the issue

```turtle
ex:headerless-source a tss:CSVSource ;
    tss:dataFile <data-no-header.csv> ;
    tss:header false ;
    tss:timeField ex:headerless-time-field ;
    tss:hasField ex:headerless-time-field, ex:headerless-value-field .
ex:headerless-time-field a tss:ContextField ;
    tss:columnName "time" ;
    tss:datatype xsd:dateTime .                    # no tss:columnIndex
ex:headerless-value-field a tss:ValueField ;
    tss:columnName "value" ;
    tss:datatype xsd:double .                      # no tss:columnIndex
```

The data file holds lines such as `2024-05-12T08:00:00Z,4810.5`. Without a header line, nothing says which column is which.

**Fix:** every field that has a column states `tss:columnIndex` when `tss:header false`.

```diff
-<tr><td><code>tss:columnIndex</code></td><td><code>xsd:integer</code>, 0 or more</td><td>optional; none for the time field of a source with a [=Time Grid=]</td>...
+<tr><td><code>tss:columnIndex</code></td><td><code>xsd:integer</code>, 0 or more</td><td>required for a field with a column of a CSV source with <code>tss:header false</code>; otherwise optional; none for the time field of a source with a [=Time Grid=]</td>...
```

#### Example after the fix

```turtle
ex:headerless-time-field a tss:ContextField ;
    tss:columnName "time" ;
    tss:columnIndex 0 ;
    tss:datatype xsd:dateTime .
ex:headerless-value-field a tss:ValueField ;
    tss:columnName "value" ;
    tss:columnIndex 1 ;
    tss:datatype xsd:double .
```

### I-20 The Parquet table mixes physical and logical types

**Severity:** Gap · **Demo 11:** open

**Where:** the Parquet table (L704) lists the physical types `INT32` and `INT64` next to the logical integer types.

- DATE, TIME, TIMESTAMP and DECIMAL columns are stored as `INT32` or `INT64` too, so they match two rows of the table, with different texts and datatypes.
- An unsigned `INT(32, false)` value of 3000000000, read by the `INT32` row, becomes −1294967296.
- "The XSD integer type whose range holds the values" does not say whether that is the range of the column type or of the values in the file.

#### Example of the issue

A Parquet schema:

```text
message readings {
  required int64 time (TIMESTAMP(MICROS,true));
  required int32 count (INTEGER(32,false));
}
```

- The column `time` matches two rows. The `INT64` row gives the text `1715500800000000` and the datatype `xsd:integer` or `xsd:long`; the TIMESTAMP row gives `2024-05-12T08:00:00Z` and `xsd:dateTime` or `xsd:dateTimeStamp`.
- The column `count` is unsigned: an expander that reads it with the `INT32` row gets −1294967296 for the value 3000000000.

**Fix:** the logical type of a column decides which row reads it; the `INT32` and `INT64` row is for columns without a logical type.

```diff
-<tr><td><code>INT32</code>, <code>INT64</code>, <code>INT(8, 16, 32 or 64, signed)</code></td>...
+<tr><td><code>INT32</code> or <code>INT64</code> without a logical type, <code>INT(8, 16, 32 or 64, signed)</code></td>...
```

Add before the table: "A column with a logical type is read by the row of its logical type."

#### Example after the fix

The same schema has one reading: `time` is `2024-05-12T08:00:00Z` (`xsd:dateTime`), and `count` is `3000000000` (`xsd:nonNegativeInteger`, or an unsigned type such as `xsd:unsignedInt`).

### I-21 The step field must be xsd:integer, which an unsigned column cannot use

**Severity:** Gap · **Demo 11:** open

**Where:** §time-grid (L787): the `tss:datatype` of the step field MUST be `xsd:integer`. §parquet (L694) and §arrow (L737): a field's `tss:datatype` MUST be one that the table allows for its column, and an unsigned integer column allows only `xsd:nonNegativeInteger` or unsigned types (L705, L746).

A step column stored as an unsigned integer, the natural type for a step, cannot conform.

#### Example of the issue

A Parquet source on a time grid, whose step column is `required int32 step (INTEGER(32,false))`:

```turtle
ex:grid-step-field a tss:ContextField ;
    tss:columnName "step" ;
    tss:datatype xsd:integer .        # §time-grid requires xsd:integer;
                                      # §parquet allows only xsd:nonNegativeInteger or an unsigned type here
```

No datatype meets both rules.

**Fix:** the step field may have any XSD integer type that its column allows.

```diff
-... and MUST NOT be its time field. Its `tss:datatype` MUST be `xsd:integer`.
+... and MUST NOT be its time field. Its `tss:datatype` MUST be an XSD integer type, such as `xsd:integer` or `xsd:unsignedInt`, that its column allows ([[#parquet]], [[#arrow]]).
```

#### Example after the fix

```turtle
ex:grid-step-field a tss:ContextField ;
    tss:columnName "step" ;
    tss:datatype xsd:unsignedInt .    # an integer type that the Parquet table allows for this column
```

### I-22 The baseline rule leaves out most integer types

**Severity:** Gap · **Demo 11:** open

**Where:** §value-encoding (L882): `tss:baseline` is "a number (`xsd:integer`, `xsd:decimal`, `xsd:double` or `xsd:float`) with the field's `tss:datatype`".

A field typed `xsd:int` or `xsd:long`, which the Parquet table suggests for `INT32` and `INT64` columns (L704), can never have a baseline, if the list is meant to be complete.

#### Example of the issue

```turtle
ex:count-field a tss:ValueField ;
    tss:columnName "count" ;
    tss:datatype xsd:long ;                      # what the Parquet table suggests for an INT64 column
    tss:valueEncoding tss:DeltaFromBaseline ;
    tss:baseline "1000"^^xsd:long .              # not xsd:integer, xsd:decimal, xsd:double or xsd:float
```

**Fix:** the baseline is a literal of the field's datatype, which is numeric.

```diff
-- A field with `tss:DeltaFromBaseline` MUST state exactly one `tss:baseline`: a number (`xsd:integer`, `xsd:decimal`, `xsd:double` or `xsd:float`) with the field's `tss:datatype`.
+- A field with `tss:DeltaFromBaseline` MUST state exactly one `tss:baseline`: a literal with the field's `tss:datatype`, which is a numeric datatype ([[#datatypes]]).
```

#### Example after the fix

The field above conforms, unchanged.

### I-23 tss:aboutUrl cannot give unique IRIs on a time grid without a step field

**Severity:** Gap · **Demo 11:** open

**Where:** §time-grid (L785): on a time grid, the time field has no column. §point-iris (L378): two points of a source MUST NOT get the same IRI, and the template "usually holds the time".

On a time grid without a step field, no column holds the time or the step of a row, so no template variable tells the rows apart.

#### Example of the issue

```turtle
ex:grid-source a tss:CSVSource ;
    tss:dataFile <https://example.org/grid.csv> ;
    tss:timeBase "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:timeStep "PT15M"^^xsd:duration ;             # no step field: steps by position
    tss:timeField ex:grid-time-field ;
    tss:hasField ex:grid-time-field, ex:grid-value-field .
ex:grid-time-field a tss:ContextField ;
    tss:datatype xsd:dateTime .                      # no column on a time grid
ex:grid-value-field a tss:ValueField ;
    tss:columnName "value" ;
    tss:datatype xsd:double ;
    tss:aboutUrl "https://example.org/obs/{value}" . # the only column: two rows with 4810.5 get the same IRI
```

**Fix:** give templates a variable for the time of the row, `_time` (and for its step, `_step`), as CSVW gives `_row`.

```diff
 - Each variable of the template is the `tss:columnName` of a field of the source, and stands for the value of that field in the row, as it is written.
+- The variable `_time` stands for the time of the row, and `_step` for its step on a [=Time Grid=]. No column name starts with `_` in a template.
```

#### Example after the fix

```turtle
ex:grid-value-field a tss:ValueField ;
    tss:columnName "value" ;
    tss:datatype xsd:double ;
    tss:aboutUrl "https://example.org/obs/{_time}" . # the row at 08:15: https://example.org/obs/2024-05-12T08%3A15%3A00Z
```

### I-24 An rdf:langString field without tss:language

**Severity:** Gap · **Demo 11:** open

**Where:** the field table (L330): `tss:language` is optional. §datatypes (L403). §expanding (L1006) makes the literal "with its `tss:language`".

Without `tss:language`, the expansion cannot make an `rdf:langString` literal. For RDF points, the specification does not say whether a literal with another language tag than `tss:language` is an error.

#### Example of the issue

```turtle
ex:weather-field a tss:ValueField ;
    tss:columnName "weather" ;
    tss:datatype rdf:langString .                # no tss:language: optional in the field table
```

The row `2024-05-12T08:00:00Z,cloudy` expands to `tss:value "cloudy"@??`: an `rdf:langString` literal needs a language tag, and there is none.

**Fix:** an `rdf:langString` field states `tss:language`, and the literals of its values in RDF points have that language tag.

```diff
-<tr><td><code>tss:language</code></td><td>language tag</td><td>optional</td><td>the language of the values of an <code>rdf:langString</code> field</td></tr>
+<tr><td><code>tss:language</code></td><td>language tag</td><td>required for an <code>rdf:langString</code> field; otherwise not allowed</td><td>the language of the values of an <code>rdf:langString</code> field; in RDF points, their literals have this language tag</td></tr>
```

#### Example after the fix

```turtle
ex:weather-field a tss:ValueField ;
    tss:columnName "weather" ;
    tss:datatype rdf:langString ;
    tss:language "en" .

ex:source tss:hasPoint [                         # the expansion of the row above
    tss:field ex:weather-field ;
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "cloudy"@en
] .
```

## Minor

### I-25 JSON rows: "no key" against null

**Severity:** Minor · **Demo 11:** open

**Where:** §json-points (L568): "A row has no key for a field it has no value for". L570: "A missing key, `null` and `""` are an [=Empty Value=]".

L568 reads as a rule that forbids `null`; L570 allows it.

#### Example of the issue

```turtle
ex:json-source tss:points """[
  { "time": "2024-05-12T08:00:00Z", "value": "4810.5", "status": null }
]"""^^rdf:JSON .
```

L568: the row has no status, so it has no `status` key. L570: `null` is an empty value, so the key may stay.

**Fix:** make leaving out the key an option.

```diff
-A row has no key for a field it has no value for.
+A row may leave out the key of a field it has no value for.
```

#### Example after the fix

The row above conforms, and so does the same row without the `status` key.

### I-26 Leftover: the time base "stays the same" when the window changes

**Severity:** Minor · **Demo 11:** open

**Where:** §time-grid (L778) and the comment of `tss:timeBase` (T262): the time base "stays the same when `tss:from` or `tss:until` change".

Since PR #25, the window does not change: not while a source grows (L279), and not once it is immutable (L285). The clause describes a change that the specification no longer allows.

#### Example of the issue

```text
§time-grid (L778): the time base "stays the same when tss:from or tss:until change"
§change (L279):    "The window of a Growing Source does not change"
§change (L285):    an immutable source: "nothing in its description changes any more"
```

**Fix:** drop the clause, in the specification and in the vocabulary.

```diff
-- `tss:timeBase` is a time of the source ([[#times]]). It anchors the grid and is not a bound of the [=Time Window=]: it stays the same when `tss:from` or `tss:until` change, so no row's time moves.
+- `tss:timeBase` is a time of the source ([[#times]]). It anchors the grid and is not a bound of the [=Time Window=].
```

#### Example after the fix

The three passages above no longer disagree.

### I-27 Vocabulary comments still use "reading" for a row

**Severity:** Minor · **Demo 11:** open

**Where:**

- `tss:key` (T195): "whose values identify a reading … No two readings have the same time";
- `tss:missing` (T414): "How many readings in the source's time window have an empty value";
- `tss:firstTime` and `tss:lastTime` (T421, T428): "the first/last reading … with a value";
- `tss:describesField` (T367): "in each reading";
- `tss:datatype` (T304): "In the JSON points".

Since PR #31, a reading is a point: one value of one value field. With two value fields, two readings have the same time, so the comment of `tss:key` is false, and a reading always has a value.

#### Example of the issue

The multi-sensor example (L415) has two readings at 08:00, as the specification allows:

```turtle
ex:source tss:hasPoint [
    tss:field ex:temperature-field ;
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;
    tss:value "18.2"^^xsd:double
], [
    tss:field ex:humidity-field ;
    tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ;   # a second reading at 08:00
    tss:value "61.0"^^xsd:double
] .
```

The comment of `tss:key` says: "No two readings have the same time".

**Fix:** use "row" in these comments, and "JSON rows".

```diff
-    rdfs:comment "The field of the source whose values identify a reading: always its tss:timeField. ... No two readings have the same time; ..."@en ;
+    rdfs:comment "The field of the source whose values identify a row: always its tss:timeField. ... No two rows have the same time; ..."@en ;
```

#### Example after the fix

The comment matches the example: the two readings are in one row, and no two rows have the same time.

### I-28 The vocabulary overview misstates where terms are used

**Severity:** Minor · **Demo 11:** open

**Where:** the vocabulary overview (L1306, L1307) lists `tss:observedProperty`, `tss:unit`, `tss:sensor`, `tss:featureOfInterest`, `tss:describesField` and `tss:fieldDescription` as used on a "field".

A context field MUST NOT state `tss:observedProperty`, `tss:sensor` or `tss:featureOfInterest` (§field-kinds), and `tss:describesField` is for context fields. The vocabulary gives these terms the domains `tss:ValueField` and `tss:ContextField`; only `tss:unit` and `tss:fieldDescription` are for any field.

#### Example of the issue

A field written from the overview:

```turtle
ex:quality-field a tss:ContextField ;
    tss:observedProperty ex:qualityCodeColor ;   # the overview: used on a field. §field-kinds: MUST NOT.
    tss:describesField ex:value-field .
```

Demo 11's quality colour fields had exactly this before they were updated to the final model.

**Fix:** split the rows by the kind of field.

```diff
-<tr><td><code>tss:observedProperty</code>, <code>tss:unit</code>, <code>tss:sensor</code>, <code>tss:featureOfInterest</code></td><td>property</td><td>field</td><td>[[#meaning]]</td></tr>
-<tr><td><code>tss:describesField</code>, <code>tss:fieldDescription</code></td><td>property</td><td>field</td><td>[[#describing]]</td></tr>
+<tr><td><code>tss:observedProperty</code>, <code>tss:sensor</code>, <code>tss:featureOfInterest</code></td><td>property</td><td>value field</td><td>[[#meaning]]</td></tr>
+<tr><td><code>tss:unit</code>, <code>tss:fieldDescription</code></td><td>property</td><td>field</td><td>[[#meaning]], [[#describing]]</td></tr>
+<tr><td><code>tss:describesField</code></td><td>property</td><td>context field</td><td>[[#describing]]</td></tr>
```

#### Example after the fix

```turtle
ex:quality-field a tss:ContextField ;
    tss:property ex:qualityCodeColor ;           # a describing field states its own property instead
    tss:describesField ex:value-field .
```

### I-29 The property chains take TSS with SSN or SAREF outside OWL 2 DL

**Severity:** Minor · **Demo 11:** open

**Where:** the property chains of the vocabulary (T519 to T527). §alignment (L1046): "Property chains are part of OWL 2 RL".

The chains make `sosa:observedProperty`, `sosa:madeBySensor`, `sosa:hasFeatureOfInterest`, `saref:relatesToProperty` and `saref:isMeasuredIn` composite properties. SSN restricts the cardinality of the three SOSA properties on `sosa:Observation`, and SAREF v3.2.1 the qualified cardinality of `saref:relatesToProperty` and `saref:isMeasuredIn` on `saref:Measurement`. OWL 2 DL does not allow cardinality restrictions on composite properties, so TSS loaded with SSN or SAREF is not OWL 2 DL, and DL reasoners report it. Rule engines for OWL 2 RL work as L1046 says.

#### Example of the issue

```turtle
# tss.ttl
sosa:observedProperty owl:propertyChainAxiom ( tss:field tss:observedProperty ) .

# SSN
sosa:Observation rdfs:subClassOf [
    a owl:Restriction ;
    owl:onProperty sosa:observedProperty ;
    owl:cardinality "1"^^xsd:nonNegativeInteger
] .
```

Together, `sosa:observedProperty` is a property defined by a chain, with a cardinality restriction: OWL 2 DL does not allow this.

**Fix:** say so in §alignment.

```diff
-Property chains are part of OWL 2 RL [[OWL2-PROFILES]], so a rule reasoner can derive them.
+Property chains are part of OWL 2 RL [[OWL2-PROFILES]], so a rule reasoner can derive them. Loaded together with SSN or SAREF, which restrict the cardinality of these properties, the alignment is not in OWL 2 DL: a reasoner for OWL 2 DL does not accept it.
```

#### Example after the fix

The axioms are unchanged. A reader now knows to use a rule reasoner, not an OWL 2 DL reasoner, for the alignment.

## Design concerns raised before

These are not defects, but choices with known costs. They are kept as open points in [CHANGELOG.md](CHANGELOG.md):

- **Two datatypes for one moment.** With both `xsd:dateTime` and `xsd:dateTimeStamp` allowed, the same time can be two different RDF terms: joins, `DISTINCT` and `GROUP BY` treat them as different values.
- **Steps by position.** Without a step field, a row's time depends on its position. A CSV file with one value column writes a step without a reading as an empty line, and many CSV readers skip empty lines by default, which moves every later row to an earlier time.
- **The name `tss:rangeRequests`** names the technique a consumer uses, not what the publisher promises: that the bytes only grow.
- **RDF Messages has no profile section yet.** The RDF Message Stream Profile follows the direction of the RDF Stream Processing Community Group, and may have to change.
- **Tools.** rdflib and pyshacl cannot parse `VERSION "1.2-messages"` or the `MESSAGE` delimiter yet; demo 11 validates the merge of the messages.
