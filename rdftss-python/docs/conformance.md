# Conformance testing

This document explains how rdftss checks that documents, data and software follow the RDF Time Series Snippets specification, and what the conformance test suite in [`conformance/`](../conformance/) is made of.

1. [What conformance means in the specification](#1-what-conformance-means-in-the-specification)
2. [What rdftss checks](#2-what-rdftss-checks)
3. [The SHACL shapes and their check IDs](#3-the-shacl-shapes-and-their-check-ids)
4. [The conformance test suite](#4-the-conformance-test-suite)
5. [The four kinds of tests](#5-the-four-kinds-of-tests)
6. [Running the suite](#6-running-the-suite)
7. [Changing the suite](#7-changing-the-suite)
8. [What the suite does not cover yet](#8-what-the-suite-does-not-cover-yet)

## 1. What conformance means in the specification

The specification's *Conformance* section defines four conformance classes. Each says what has to follow the rules: a document, data, or a piece of software.

| Class | What it is | In short |
|---|---|---|
| **Conforming metadata document** | An RDF document that describes at least one snippet and meets every requirement on a metadata document, a snippet, a field, embedded points and a stored summary | The RDF is right. For an external snippet this includes the properties that describe the data file, its time grid and its value encoding, but not what is inside the data file. |
| **Conforming data** | The points of a snippet, embedded or in its data file, that meet the requirements on points | Every point has a time (on a time grid, a step), no two points share a time, every value is in the lexical form of its datatype, and the points lie in the time window. |
| **Conforming consumer** | Software that reads a conforming document and its data as the specification says | It picks the rules by snippet class, reads the points, times and values, decodes value encodings, applies the time window and the empty-value rules, and reports an error where the specification calls something an error. A **conforming expander** is a consumer that also expands snippets into observations. |
| **Conforming writer** | Software that adds points to a growing snippet only as the specification says, and ends it as the specification says | It appends a point to an embedded snippet as one more message (RDF) or at the end of `tss:points` (JSON), appends complete records to a growing data file, adds `tss:immutable true` and the summaries when the snippet ends, and leaves the document conforming. |

The first two classes are about *things* (a document, data), and can be checked by looking at them. The last two are about *behaviour*, and can only be checked by giving software inputs and comparing what it does with what the specification says. That is what a test suite is for.

## 2. What rdftss checks

| What | How in rdftss | Status |
|---|---|---|
| Conforming metadata document | `document.validate()`: SHACL validation with the specification's two shapes files ([section 3](#3-the-shacl-shapes-and-their-check-ids)) | Done |
| Conforming data: RDF points | The same validation: the embedded shapes check RDF points (checks P1 to P7) | Done |
| Conforming data: JSON points and data files | Reading reports the errors the specification names (a point without a time, a value not in its lexical form). A full data check, which would also report duplicate times and the like as a report rather than an error, is not built yet. | Partly |
| Stored summaries are true | `document.verify_summaries()`: recomputes every number a summary states from the data, and names those that differ | Done |
| Conforming consumer and expander | rdftss itself; the conformance test suite tests it ([section 4](#4-the-conformance-test-suite)). It reads RDF Message Logs and growing data files; it does not follow a growing snippet over HTTP yet (polling, `Range` requests) | Done, except following |
| Conforming writer | rdftss does not append points or end snippets yet; it does write a growing RDF snippet as a Message Log | Not yet |

A stored summary is not a conformance class of its own. A summary can be well-formed (SHACL checks that) and still be out of date, since nothing in the specification refreshes it: it is computed once, when the snippet ends. `verify_summaries()` checks that its numbers are true. The [guide](guide.md#12-checking-stored-summaries) explains how the numbers are compared.

## 3. The SHACL shapes and their check IDs

SHACL is the W3C language for describing what an RDF graph must look like. A *shapes file* holds the rules; a *validator* (rdftss uses pyshacl) checks a graph against them and gives a *validation report* with one *result* per problem.

The specification comes with two shapes files, bundled with rdftss in [`src/rdftss/shapes/`](../src/rdftss/shapes/):

| File | For documents with | It checks | It cannot see |
|---|---|---|---|
| `embedded_shapes.ttl` | `tss:EmbeddedRDFSnippet`, `tss:EmbeddedJSONSnippet` | the snippet, its fields and summaries, and every RDF point | the points inside a `tss:points` JSON literal (SHACL does not read inside literals) |
| `external_shapes.ttl` | `tss:CSVSnippet`, `tss:ParquetSnippet`, `tss:ArrowSnippet` | the snippet, its fields and summaries, its time grid and value encodings | the data file |

A document never mixes the two kinds of snippets, so one file applies. `validate()` picks it from the snippet classes the document states. A document with both kinds, or with no snippet class at all, is checked with both files, so that the check that says what is wrong (S24 or S1) runs.

The document is validated as rdftss read it: every literal keeps its exact text. This matters for check P4. By default, rdflib reads `" 4810.5 "^^xsd:double` as the number 4810.5, so the validator would never see the spaces.

### Check IDs

Every rule in the shapes has an ID: a letter for what it is about, and a number.

| Letter | About | Examples |
|---|---|---|
| **S** | the snippet | S23 every time has a timezone; S24 one kind of snippet per document; S26 no summary on a growing snippet |
| **F** | a field | F2 column name; F3 datatype; F8 maximum not below minimum |
| **P** | an RDF point (embedded file only) | P1 has a time; P2 lies in the window; P3 no two points at one moment; P4 values in their lexical form |
| **D** | a value encoding | D1 only `tss:DeltaFromBaseline`; D2 its baseline; D3 not allowed where it can't be |
| **Q** | a stored summary | Q1, Q2 no window of its own; Q4 `tss:count` is a non-negative integer |

A rule is named after what is wrong or missing. For example, a time grid with a `tss:timeBase` but no `tss:timeStep` gives S19, the time step check, not S18.

Each property check also has a *group*, which says how strictly it applies:

| Group | Meaning | Severity of a result |
|---|---|---|
| Required | must be there and well-formed | violation |
| Optional | may be left out, but must be well-formed when it is there | violation |
| Not allowed | contradicts the kind of snippet | violation |

The checks written in SPARQL have no group; their ID says where they belong. S26 (a field of a growing snippet has a summary) and S27 (a Parquet snippet that does not state `tss:immutable true`) are among them. Four of them are warnings: F9 (the snippet has points, but none has a value for a field), P5 (a value outside the expected range), P6 (a point has a property that is not a field) and S15 (a delimiter or encoding on a Parquet or Arrow snippet, where it has no effect). **A document conforms when its report holds no violation.** Warnings point at things that look wrong, but don't change that.

### The two files number some checks differently

The F, P, D and Q checks mean the same in both files, but some S numbers don't:

| Check | Embedded file | External file |
|---|---|---|
| immutable | S2 | S3 |
| snippet class | S3 | S7 |
| has field | S5 | S6 |
| label | S7 | S12 |
| produced by | S8 | S13 |
| key | S9 | S5 |
| from | S10 | S2 |
| until | S11 | S14 |
| properties not allowed | S12 | S22 |

The same in both: S1 (the snippet), S4 (time field), S20 (time field datatype), S23 (timezone), S24 (one kind) and S26 (no summary on a growing snippet). S17 (committed until) is gone: the specification no longer has `tss:committedUntil`. Only in one file: S6 (points) and S25 (JSON keys) in the embedded file; S8 (data file), S10 (delimiter), S11 (encoding), S15 (not used), S18 (time base), S19 (time step), S21 (step field) and S27 (a Parquet snippet is immutable) in the external file. So an ID alone, such as S12, is ambiguous: read it together with its file (`result.shapes` in rdftss, and `tsst:shapes` in the test suite).

## 4. The conformance test suite

The suite is a set of small examples, each with its expected result. It makes the rules of the specification concrete: for each rule there is at least one document that breaks it, and the result an implementation must give. It tests three things at once:

- **the shapes**: every check must find the problem it is written for, and nothing in the valid documents;
- **rdftss**: its validation, reading, summary verification and expansion;
- **other implementations**: anyone can run the same tests, since they are plain files and a manifest, not Python code.

It holds 184 tests:

| Kind | Tests | What they check |
|---|---|---|
| Metadata tests | 152 | SHACL validation: 19 valid documents, and 133 documents that each break one rule |
| Read tests | 22 | reading points (consumer): 18 with the expected points, 4 with an expected error |
| Summary tests | 7 | stored summaries: up to date or not, and which numbers differ |
| Expansion tests | 3 | expanding into observations (expander) |

Every check ID of both shapes files has at least one test.

### Files

```text
conformance/
├── manifest.ttl            every test: its kind, input, expected result and the rule it tests
├── make_suite.py           writes all the files below from tables (section 7)
├── metadata/
│   ├── embedded/           80 documents for the embedded shapes, e.g. S23-from-without-timezone.ttl
│   ├── external/           70 documents for the external shapes
│   └── mixed/              2 documents checked with both files (S1, S24)
├── reading/<test>/         metadata.ttl, a data file when it has one, and expected.csv
├── summaries/<test>/       metadata.ttl and a data file when it has one
└── expansion/<test>/       metadata.ttl, a data file when it has one, and expected.csv
```

A metadata test is named after the check it is about and what is wrong, such as `S23-from-without-timezone` or `P3-two-points-at-the-same-moment`. A test of a valid document starts with `valid-`. The first line of every test document says what it tests, as a Turtle comment.

### How a metadata test is made

Each metadata test is a valid *base document* with one small edit that breaks one rule. The base documents are the specification's own examples, shortened: an embedded snippet with three RDF points and a stored summary, the same readings as JSON points, a CSV snippet, a Parquet and an Arrow snippet, a time grid, and a value encoding. For example, `metadata/embedded/S23-from-without-timezone.ttl` is the embedded base document with one change:

```text
-    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;
+    tss:from "2024-05-12T08:00:00"^^xsd:dateTimeStamp ;
```

Since only one thing differs from a valid document, a test shows exactly which rule each check enforces. Some edits break a second rule as a side effect: an `tss:until` before `tss:from` (S11) also leaves every point outside the window (P2). A test therefore says which checks must fail at least, not that no other check may fail.

### The manifest

[`manifest.ttl`](../conformance/manifest.ttl) lists every test in RDF, with the W3C test manifest vocabulary (`mf:`) that RDF test suites use, and a small vocabulary for this suite (`tsst:`, `https://w3id.org/tss/tests#`). A metadata test:

```turtle
<#embedded-S23-from-without-timezone> a tsst:MetadataTest ;
    mf:name "embedded-S23-from-without-timezone" ;
    rdfs:comment "tss:from has no timezone." ;
    tsst:rule <https://knowledgeonwebscale.github.io/rdf-timeseriessnippets/#times> ;
    mf:action <metadata/embedded/S23-from-without-timezone.ttl> ;
    tsst:shapes "embedded" ;
    tsst:conforms false ;
    tsst:violation "S23" .
```

| Property | Meaning |
|---|---|
| `mf:name`, `rdfs:comment` | the test's name, and what it tests |
| `tsst:rule` | the section of the specification with the rule |
| `mf:action` | the input: the metadata document |
| `mf:result` | the expected result, when it is a file (`expected.csv`), or `tsst:Error` |
| `tsst:shapes` | which shapes files apply (`"embedded"`, `"external"`, or both) |
| `tsst:conforms` | whether the document conforms |
| `tsst:violation`, `tsst:warning` | the IDs of checks that must give a violation or a warning |
| `tsst:upToDate`, `tsst:outOfDate` | for a summary test: whether all summaries are up to date, and which numbers are not |

The `tsst:` namespace is not registered at w3id.org yet. Its IRIs only name things inside the manifest.

## 5. The four kinds of tests

### Metadata tests (`tsst:MetadataTest`): conforming metadata documents

**Input:** a metadata document. **Do:** validate it with the shapes files its snippet classes call for. **Pass when:**

- the document conforms if and only if `tsst:conforms` is true;
- every `tsst:violation` ID is among the IDs of the violations, and every `tsst:warning` ID among those of the warnings;
- a valid document without an expected warning gives no result at all.

The metadata tests cover:

- every snippet class and its valid forms: no window, times with offsets, an empty text as no value, no points yet, an empty JSON array, a CSV file without a header line, a time grid, a value encoding;
- growing snippets: a growing JSON snippet, CSV snippet and Arrow stream, an RDF Message Log (validated as the merge of its messages), and a log whose last message ends the snippet;
- every Required and Not-allowed property, and every check written in SPARQL: the time field, the key, the window, timezones, one kind of snippet, unique column names and indexes, the time grid, the value encoding, and the RDF points (time, window, key, lexical forms, one value per field);
- every Optional property when it is malformed: a label that is an IRI, a negative `tss:count`, a summary time that is an `xsd:date`, and so on;
- the warnings: F9, P5, P6 and S15.

### Read tests (`tsst:ReadTest`): conforming consumers

**Input:** a metadata document and its data file, if it has one. **Do:** read the snippet's points. **Pass when** the points are those of `expected.csv`, or, for `mf:result tsst:Error`, reading reports an error.

`expected.csv` has a column `time` and one column per field, named by the field's IRI. Each row is one point, and an empty cell is no value. Points and rows are compared like this:

- **times** as moments: `2024-05-12T10:00:00+02:00` is the row `2024-05-12T08:00:00Z`;
- **numbers** by value, since the specification does not fix the text of a decoded value: `4810.5`, `4810.50` and `4.8105E3` are the same `xsd:double`;
- **other values** by their exact text: `" good "` keeps its spaces;
- the order of the points does not matter: both sides are sorted by time.

| Test | The rule it checks |
|---|---|
| `rdf-points` | RDF points: one node per point, the field IRIs as predicates |
| `json-empty-values` | `""`, `null` and a missing key are empty values |
| `csv-delimiter`, `csv-no-header` | `tss:delimiter`; `tss:header false` with columns by `tss:columnIndex` |
| `csv-time-window` | only the rows in [`tss:from`, `tss:until`) are points; times compare as moments |
| `csv-time-offsets` | times with offsets |
| `time-grid` | the time of a row is `tss:timeBase` + step × `tss:timeStep` |
| `time-grid-calendar-months` | a step of `P1M` from 31 January gives 29 February and 31 March: the n months are added at once, and the day is clamped to the month |
| `delta-from-baseline` | the specification's own example: 4750 + 60.5 = 4810.5 and 4750 + (-27.6) = 4722.4 |
| `texts-kept` | a text keeps its spaces; a text of only spaces is a value |
| `parquet`, `arrow` | UTC timestamp and DOUBLE columns |
| `arrow-stream` | a growing Arrow snippet's data file is an IPC stream, told apart from an IPC file by its first bytes |
| `rdf-message-log` | a growing snippet's RDF Message Log is read as the merge of its messages |
| `rdf-message-log-unfinished-message` | text after the last delimiter is a message still being written, and is not read |
| `rdf-message-log-blank-node-labels` | a blank node label belongs to its message: `_:p` in two messages is two points |
| `csv-growing-unfinished-record` | in a growing CSV snippet, a last record without its line break is still being written, and is not read |
| `csv-immutable-last-record-without-line-break` | in an immutable CSV snippet, the same record is read |
| `error-csv-point-without-time`, `error-json-point-without-time` | a point without a time is an error |
| `error-csv-value-not-in-lexical-form`, `error-json-value-not-in-lexical-form` | a value not in the lexical form of its datatype is an error |

The specification names exactly two errors, the last two rows. Other problems in data, such as two rows at the same time, make the data non-conforming, but the specification does not say a consumer must stop at them, so the suite does not test them yet ([section 8](#8-what-the-suite-does-not-cover-yet)).

### Summary tests (`tsst:SummaryTest`): stored summaries

**Input:** a metadata document with stored summaries, and its data file. **Do:** recompute every summary from the data in the window. **Pass when** all summaries are up to date if and only if `tsst:upToDate` is true, and the numbers that differ are exactly those of `tsst:outOfDate`, such as `"tss:count"`.

| Test | What it shows |
|---|---|
| `up-to-date` | the summary of the embedded base document matches its three points |
| `out-of-date` | `tss:count` and `tss:mean` were not recomputed after a point was added |
| `out-of-date-first-time` | `tss:firstTime` is not the first point with a value |
| `empty-values` | `tss:ExcludeEmptyValues`: an empty value counts in `tss:missing`, and is left out of the numbers |
| `same-moment` | a `tss:lastTime` written with an offset is the same moment |
| `decoded-values` | the numbers are those of the decoded values, not of the differences in the file |
| `window` | rows of the data file outside the window don't count |

### Expansion tests (`tsst:ExpansionTest`): conforming expanders

**Input:** a metadata document and its data file. **Do:** expand the snippet into observations. **Pass when** the observations are those of `expected.csv`, one row each.

The specification leaves the RDF vocabulary of observations to the implementer (SOSA is its informative suggestion). So the test compares what an observation *holds*, not its triples: the columns are `snippet`, `observed_property`, `sensor`, `feature_of_interest`, `unit` (IRIs, empty when the field does not state one), `time`, `result` and `datatype`. The column `match` says how to compare the result: `text` when the specification says the value keeps its text as written (a field without a value encoding), and `value` for a decoded value.

| Test | What it shows |
|---|---|
| `rdf-points` | one observation per value, with what its field says |
| `measured-fields-only` | an empty value gives no observation, and neither does a field without `tss:observedProperty` |
| `time-grid-and-delta` | the time of each step, and the decoded value |

## 6. Running the suite

With rdftss, from the library's main folder:

```bash
python -m pytest tests/test_conformance.py          # every test of the manifest, about 40 seconds
python -m pytest tests/test_conformance.py -k S23   # the tests of one check
```

[`tests/test_conformance.py`](../tests/test_conformance.py) reads the manifest and runs each entry as one pytest test, named after it. It shows how to run the four kinds of tests in about 150 lines. The metadata tests need `pyshacl`, and the Parquet and Arrow tests `pyarrow`.

With another implementation:

1. Read `manifest.ttl` with any RDF library. `mf:entries` lists the tests in order, and relative IRIs resolve against the manifest's own location.
2. For each test, take its kind from its `rdf:type`, its input from `mf:action`, and its expected result from the properties of [section 5](#5-the-four-kinds-of-tests).
3. Run your software on the input, and compare as section 5 says.

An implementation of only some classes runs only some kinds of tests. For example, a SHACL-based checker runs the metadata tests, and a reader runs the read tests.

## 7. Changing the suite

The test files are written by [`conformance/make_suite.py`](../conformance/make_suite.py) from tables in that file:

- `EMBEDDED_TESTS`, `EXTERNAL_TESTS` and `MIXED_TESTS` hold the metadata tests: a name, a base document, the edits, a comment, the rule, and the expected violations and warnings. By default, an invalid test expects a violation of the check its name starts with.
- `READ_TESTS`, `SUMMARY_TESTS` and `EXPANSION_TESTS` hold the other tests, with their files and expected results written out by hand.

To add or change a test, edit a table and run:

```bash
python conformance/make_suite.py        # writes the files and manifest.ttl again
python -m pytest tests/test_conformance.py
```

An edit must match its base document exactly once, or the script stops, so a base document can't change without its tests noticing. A test also checks that the committed files are exactly what the script writes.

When the specification changes, update the shapes and the suite together:

1. Copy the new shapes files into `src/rdftss/shapes/`.
2. Change the base documents or the tests that the change affects.
3. Run the suite. A failing test is either a mistake in the test or a gap in the shapes, and both are worth knowing about.

## 8. What the suite does not cover yet

- **Checks of the data beyond SHACL.** The content of JSON points and of data files is only tested where the specification names an error. A full data check, which would report two rows at the same time, a time-grid step that is not a whole number, or a Parquet column type that doesn't match `tss:datatype`, is planned. rdftss already refuses most of these while reading, and its own unit tests cover them.
- **Conforming writers.** rdftss does not add points yet. Writer tests would give a document and a new point, and check the document after the write: one more message in an RDF Message Log, or one more object at the end of `tss:points`, and nothing else changed. Tests of ending a snippet would check that `tss:immutable true` and the summaries are added.
- **Following a growing snippet.** The specification's `Range: bytes=N-` requests and their answers (206, 416, 200) need a server, so they are not in this file-based suite.
- **CSV dialects** (quoting, encodings, a byte-order mark) and **the Parquet and Arrow type tables.** rdftss's unit tests cover them; they could become read tests.
- **One test per check per file.** Some checks have a single test. More cases per check, such as each way a field can be malformed, would make the suite stronger.
