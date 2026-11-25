# Evaluation results

The results of the demo 11 evaluation, run on 2026-11-23; the rdftss check of §5 was run on 2026-11-24. They are for section 5 (Evaluation) of the RDF-TSS resource paper. Each numbered section answers one placeholder of that section, with:

- the facts;
- the files that hold the evidence;
- a suggested text for the paper.

[README.md](README.md) says how to reproduce the runs.

## Versions

| Item | Version |
| --- | --- |
| Specification | `../spec/spec.bs`: [`KNowledgeOnWebScale/rdf-tss`](https://github.com/KNowledgeOnWebScale/rdf-tss) `main` at `c6338dc` (2026-11-21), with the demo 11 differences listed in `../CHANGELOG.md` ("Demo 11 and the repository today") |
| Vocabulary | `../vocabulary/tss.ttl`, with the same reference |
| SHACL shapes | `../SHACL/embedded_shapes.ttl`, `../SHACL/external_shapes.ttl` |
| Metadata descriptions | `../metadata endpoint/waterinfo_*.ttl` (8 documents) |
| Evaluation code and results | this folder. **Not committed yet**: the working tree is on commit `c9aa410` of this repository. |
| rdftss library | 0.2.0, in `../../../rdftss-python/` of this repository. It bundles the vocabulary and the shapes above, byte for byte. **Not committed yet.** |
| Data | Waterinfo.be KiWIS, retrieved 2026-11-23T14:26:22Z (`waterinfo/manifest.json`) |
| Software | Python 3.14.6, RDFLib 7.6.0, pySHACL 0.40.2, PyArrow 23.0.1, PyYAML 6.0.3, Requests 2.34.2 |

## 1. Waterinfo evaluation material (§5.1)

The placeholder asks for the original source references, the monitoring locations, the selection period, the number of readings and source descriptions, the preparation steps, the access conditions, and the location of the reproducible subset.

### Source and access

- **Service.** The KiWIS web service of Waterinfo.be, `https://download.waterinfo.be/tsmdownload/KiWIS/KiWIS`, datasource 1. It is public and needs no account or key.
- **Requests.** One `getTimeseriesValues` request per time series and calendar year, in JSON and in UTC. Each request asks for these fields:
  - Timestamp and Value;
  - Interpolation Type;
  - Quality Code, Quality Code Name, Quality Code Description and Quality Code Color.

  Station metadata comes from one `getTimeseriesList` request. `waterinfo/manifest.json` lists every request URL.
- **Licence: to confirm.** The Flemish open-data catalogue lists Waterinfo measurement series under the *Modellicentie Gratis Hergebruik*: free reuse with attribution, compatible with CC BY ([licence](https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/open-data/voorwaarden-voor-het-hergebruik-van-overheidsinformatie/modellicentie-gratis-hergebruik), [catalogue entry for the discharge series](https://www.vlaanderen.be/datavindplaats/catalogus/meetreeksen-afvoer)). But the licence is set per dataset. The usage rights of these EC20 series, which come from VITO stations, have not been checked.

### Monitoring locations and selection

The data come from four VITO monitoring stations along the Ghent–Terneuzen Canal. Each provides EC20 (electrical conductivity at 20 °C, in µS/cm), every 15 minutes (time series P.15). The selection of each series is the time window [from, until) that its RDF-TSS description declares:

- For Grootdok, Rodenhuizedok and Autrichehaven, it is the whole series, from its start in March 2021 to the end of 2025. Grootdok and Autrichehaven end earlier, in December and October 2025.
- For Zevenaarshaven, it is the first 46 readings.

| ts_id | Station | Station number | Latitude, longitude | Selection [from, until) (UTC) | Readings | Data file |
| --- | --- | --- | --- | --- | --- | --- |
| 289423042 | Kanaal Gent-Terneuzen Grootdok (BE) | HIS_BWO_VITO_IOW48 | 51.0880014, 3.7336759 | 2021-03-03T23:45Z – 2025-12-18T23:15Z | 94,853 | CSV |
| 289429042 | Kanaal Gent-Terneuzen Rodenhuizedok (BE) | BWO_VITO_IOW49 | 51.1458785, 3.7961788 | 2021-03-03T23:15Z – 2026-01-01T00:00Z | 158,022 | Parquet |
| 289435042 | Kanaal Gent-Terneuzen Autrichehaven (NL) | HIS_BWO_VITO_IOW50 | 51.2643487, 3.8380514 | 2021-03-03T23:15Z – 2025-10-07T07:45Z | 139,928 | CSV |
| 289441042 | Kanaal Gent-Terneuzen Zevenaarshaven (NL) | BWO_VITO_IOW51 | 51.3048161, 3.8332267 | 2021-03-03T23:15Z – 2021-03-05T00:00Z | 46 | CSV |
| | | | | **Total** | **392,849** | |

Eight RDF-TSS descriptions describe the selection. They cover four of the five source classes; none is a `tss:ArrowSource`, which the lifecycle checks (§4) exercise instead.

| Description | Class | Rows | What it holds |
| --- | --- | --- | --- |
| `waterinfo_289423042_tss2.ttl` | `tss:CSVSource` | 94,853 | Grootdok, with five describing quality fields |
| `waterinfo_289429042_tss2_parquet.ttl` | `tss:ParquetSource` | 158,022 | Rodenhuizedok, with five describing quality fields |
| `waterinfo_289435042_tss2.ttl` | `tss:CSVSource` | 139,928 | Autrichehaven, with five describing quality fields |
| `waterinfo_289441042_tss2_points_embedded.ttl` | `tss:EmbeddedRDFSource` | 5 | the first five Zevenaarshaven readings as RDF points, with the quality-code colour |
| `waterinfo_289441042_tss2_points_embedded_json.ttl` | `tss:EmbeddedJSONSource` | 5 | the same readings as JSON rows |
| `waterinfo_289441042_tss2_points_time_value.ttl` | `tss:EmbeddedRDFSource` (growing) | 5 | an RDF Message Log of time and value points, `tss:rangeRequests true` |
| `waterinfo_289441042_tss2_time_step.ttl` | `tss:CSVSource` | 43 | the readings of 2021-03-04 on a time grid (`PT15M`) with a step field: **derived** |
| `waterinfo_289441042_tss2_value_delta.ttl` | `tss:CSVSource` | 43 | the readings of 2021-03-04 as differences from a baseline of 4750: **derived** |

### Preparation (`waterinfo/fetch_waterinfo.py`)

1. Each series is requested per calendar year, and the raw responses are kept (`../data/raw/`).
2. The records in the declared window are kept, with their times in UTC.
3. The data files are written:
   - CSV files have the KiWIS fields as columns: `ts_id`, `Timestamp`, `Value`, `Interpolation Type`, `Quality Code`, `Quality Code Name`, `Quality Code Description`, `Quality Code Color`.
   - The Parquet file (Rodenhuizedok) holds the same fields in native types: int64, timestamp[µs, UTC], float64, int64 and string.
4. Two files are derived from the 43 Zevenaarshaven readings of 2021-03-04. The manifest identifies them as derived representations:
   - `waterinfo_289441042_time_step.csv`, where step = (time − 2021-03-04T00:00Z) / PT15M;
   - `waterinfo_289441042_value_delta.csv`, where value_delta = value − 4750, in exact decimal arithmetic.
5. The manifest records the row count and the SHA-256 checksum of every file.

### The descriptions against the data (`waterinfo/check_waterinfo.py`)

Each source is read *through its description* (`tss_reader.py`) and compared with the Waterinfo records. There are six checks:

- **D1 binding:** the data file and every field's column are found, with an allowed native type.
- **D2 values:** lexical forms are valid, and every time has a timezone.
- **D3 key:** no two rows have the same time.
- **D4 readings:** every reading and every describing value equals the Waterinfo record.
- **D5 coverage:** every record in the window is a row.
- **D6 summaries:** every stored summary equals the summary recomputed from the rows.

| Result | Value |
| --- | --- |
| Descriptions that pass | **8 of 8**, no discrepancy (`waterinfo/results/waterinfo_report.md`) |
| Rows read and compared | 392,904 (five Zevenaarshaven readings appear in three descriptions, and 43 in two derived ones) |
| Stored summary values compared | 168 |
| Self-test: deliberate errors detected | **12 of 12** (`waterinfo/results/selftest_report.md`) |

### Location of the reproducible subset

The retrieved data are not in the repository: `../data/` is excluded from version control. `fetch_waterinfo.py` retrieves them again, and `waterinfo/manifest.json` gives the request URLs and the checksums. Waterinfo may revise its records, so a later retrieval can differ. If the licence allows it, archiving `../data/` with the release would freeze the subset.

### Suggested text (replaces the future-tense paragraph and the placeholder)

```latex
The Waterinfo evaluation uses publicly available water-conductivity
measurements of four monitoring locations along the Ghent--Terneuzen
Canal: Grootdok and Rodenhuizedok in Belgium, and Autrichehaven and
Zevenaarshaven in the Netherlands. Each station reports electrical
conductivity at 20\,\textdegree C (EC20) every 15 minutes. The
readings were retrieved on 23 November 2026, with their quality
codes and in UTC, from the public KiWIS web service of Waterinfo.be,
which requires no authentication. The selection of each series is
the temporal window declared by its description. It comprises the
complete Grootdok, Rodenhuizedok, and Autrichehaven series, from
March 2021 to the end of 2025 (94,853, 158,022, and 139,928
readings), and the first 46 Zevenaarshaven readings, 392,849
readings in total.

Eight RDF-TSS descriptions cover four storage profiles. The three
long series are CSV and Parquet sources with five describing quality
fields. Five Zevenaarshaven readings are embedded as RDF points, as
JSON rows, and in a growing RDF Message Log. Two CSV files derived
from the 43 readings of 4 March 2021 exercise a time grid with a step
field and a baseline-difference encoding; they are identified as
derived representations. A retrieval script records each request,
the selection, the row counts, and SHA-256 checksums.

Read through its description, every source reproduced the Waterinfo
records in its window, including the quality fields, and every
stored summary: all eight descriptions passed checks of column
bindings, lexical forms, keys, readings, coverage, and summaries,
over 392,904 rows and 168 summary values. A self-test confirmed that
these checks detect 12 deliberately introduced errors.
```

## 2. Competency questions on the Waterinfo descriptions (§5.2)

The placeholder asks for expected versus retrieved sources and metadata, and for any discrepancies identified and resolved.

### What was run

The same eight questions keep their SPARQL patterns, with the parameters of the Waterinfo descriptions (`competency_questions/waterinfo_questions.yaml`). They run over the eight Waterinfo descriptions, each in its own named graph.

The expected rows were written from the descriptions, not from query results (`competency_questions/waterinfo_expected.yaml`). They are compared with the retrieved bindings: IRIs as names, data files by file name, and numbers and times by value.

| CQ | Waterinfo parameters | Expected rows | Retrieved rows | Match |
| --- | --- | --- | --- | --- |
| CQ1 | EC20 of the water at Zevenaarshaven | 5 | 5 | yes |
| CQ2 | EC20 windows overlapping [2025-10-07T07:45Z, 2025-12-18T23:15Z) | 5 | 5 | yes |
| CQ3 | all EC20 sources | 8 | 8 | yes |
| CQ4 | all EC20 sources | 8 | 8 | yes |
| CQ5 | the Grootdok EC20 field | 1 | 1 | yes |
| CQ6 | Grootdok, the time-grid source and the baseline-difference source | 3 | 3 | yes |
| CQ7 | all EC20 sources | 8 | 8 | yes |
| CQ8 | all EC20 sources | 8 | 8 | yes |
| | **Total** | **46** | **46** | **8 of 8** |

Evidence: `competency_questions/results/waterinfo_results.json`, which holds each answer with its comparison.

### What the answers show

- **CQ1** returns the five Zevenaarshaven sources. The three other locations have their own features of interest.
- **CQ2** keeps the growing log, whose window has no upper bound. It excludes:
  - the Autrichehaven source, whose window ends where the interval begins (boundary contact, not overlap);
  - the two single-day sources.

  It keeps the embedded Zevenaarshaven sources: their declared windows run to 2026, though they hold five readings from 2021. A window describes candidates, not data coverage.
- **CQ3** returns five data files (four CSV, one Parquet) and three embedded access predicates (`tss:points` once, `tss:hasPoint` twice).
- **CQ4** leaves some column bindings unbound, as the descriptions intend:
  - those of the RDF points;
  - the time column of the time-grid source, which has a step field instead.
- **CQ6** returns the time-grid parameters:
  - base 2021-03-04T00:00Z;
  - step `PT15M`;
  - the step field, column `step`, index 0.

  It also returns the baseline of 4750 with `tss:DeltaFromBaseline`, and leaves both unbound for Grootdok.
- **CQ7** finds seven immutable sources, and one growing log that alone states `tss:rangeRequests true`.
- **CQ8** returns the stored counts, minima and maxima of seven sources. It leaves the growing log unbound: a growing source has no summary.

### Discrepancies identified and resolved

No row differed in the reported run. Two discrepancies in the package were resolved before it:

1. The runner could not load the growing source: RDFLib cannot parse an RDF Message Log (`VERSION "1.2-messages"`, `MESSAGE` delimiters). It now reads a log as the merge of its messages.
2. The interpretation of CQ4 in `competency_questions.yaml` still described the model from before the final specification, in which field IRIs were the predicates of RDF points. It now says that RDF points state their time and reading with `tss:time` and `tss:value`.

### Suggested text (replaces the placeholder)

```latex
The same eight questions were applied to the eight Waterinfo
descriptions, with the Waterinfo feature of interest, interval, and
sources as parameters. Their expected answers were written from the
descriptions and compared with the retrieved bindings: all eight
questions retrieved exactly the expected rows (46 expected, 46
retrieved). CQ1 returned the five Zevenaarshaven sources and excluded
the three other locations, which have their own features of interest.
CQ2 excluded the Autrichehaven source, whose window ends where the
requested interval begins, and the two single-day sources, while
retaining the growing log with its open upper bound. It also retained
the embedded Zevenaarshaven sources, whose declared windows extend to
2026 although they hold five readings from 2021: declared windows
identify candidates, not data coverage. CQ4 left the column bindings
of the RDF points and of the derived time field unbound, CQ7
identified the growing log as the only source declaring incremental
access, and CQ8 left its summary unbound. Two discrepancies were
resolved before this run: the runner could not parse the RDF Message
Log of the growing source and now reads a log as the merge of its
messages, and the interpretation of CQ4 still described an earlier
model in which field IRIs were the predicates of RDF points.
```

## 3. Structural validation (§5.3)

The placeholder asks for:

- the validator and its version;
- the configuration;
- the tested descriptions and invalid variants;
- the expected and observed violations, with links to the reports;
- the number of cases;
- any unexpected outcomes.

### Validator and configuration

- **Validator.** pySHACL 0.40.2, with RDFLib 7.6.0 and Python 3.14.6.
- **Configuration.** `inference="none"`, `allow_warnings=True` (a warning does not make a graph non-conforming), `abort_on_first=False`.
- **Shapes.** `embedded_shapes.ttl` for a description with embedded sources, and `external_shapes.ttl` for one with external sources. Both apply when both kinds are present, which the shapes forbid (S24).
- **Message logs.** An RDF Message Log is validated as the merge of its messages, since pySHACL does not parse RDF Messages.
- **Literals.** Every description is read with its literals as written (see "Fixed during the evaluation").

### Cases

| Kind | Cases | Expected | Observed |
| --- | --- | --- | --- |
| Waterinfo descriptions | 8 | conform, no violation | 8 conform, no warning |
| Examples of the specification that describe a whole source | 10 | conform | 10 conform, no warning |
| Invalid variants of the Waterinfo descriptions, one error each | 54 | a violation of the targeted constraint | 54 as expected |
| Warning variants | 2 | the targeted warning, no violation | 2 as expected |
| **Total** | **74** | | **74 of 74 as expected; no unexpected outcome** |

The ten specification examples are:

- `example-embedded-source`, `example-multi-sensor`, `example-describing-field`;
- `example-json-points`, `example-csv-source`, `example-csv-no-header`;
- `example-time-grid`, `example-time-grid-no-step`, `example-delta-from-baseline`;
- `example-message-stream`.

The variants fall into these categories:

| Category | Cases | Category | Cases |
| --- | --- | --- | --- |
| points | 10 | duplicate points | 2 |
| field roles | 8 | lifecycle declaration | 2 |
| representation | 6 | missing time field | 2 |
| encoding | 5 | source class | 2 |
| field declarations | 4 | summaries | 2 |
| time grid | 4 | timestamps | 2 |
| out-of-window timestamps | 3 | time window | 1 |
| warning | 2 | summary on a growing source | 1 |

Eleven variants also triggered other constraints that follow from the same error. All are listed in `validation/results/validation_summary.md`:

| Case | Error | Targeted | Also reported |
| --- | --- | --- | --- |
| V01 | the source states no `tss:timeField` | S4 | S5 |
| V02 | the embedded source states no `tss:timeField` | S4 | S9 |
| V03 | the source states no source class | S7 | S1 |
| V04 | the source states two source classes | S7 | warning S15 |
| V13 | one document describes an external and an embedded source | S24 | F2, warning F9 |
| V15 | a step field without a time grid | S21 | F2 |
| V24 | the property of a describing field is a TSS term | F13 | P4, warning P6 |
| V33 | the time field states a value encoding | D3 | D1 |
| V46 | two value fields, and points without `tss:field` | P8 | warning F9 |
| V47 | a point whose `tss:field` is a context field | P8 | P10, warning F9 |
| V53 | a summary count that is not an integer | Q4 | F10 |

Evidence:

- `validation/results/validation_summary.md`: the table of the 74 cases;
- `validation/results/validation_results.json`;
- `validation/results/reports/`: the SHACL report of every case, in Turtle (74 files);
- `validation/cases.py`: the edit that makes each variant.

### Suggested text (replaces the placeholder)

```latex
The shapes were applied with pySHACL~0.40.2 (RDFLib~7.6.0,
Python~3.14.6), without inference and without stopping at the first
result. The shape set was selected from the source classes of each
description, and an RDF Message Log was validated as the merge of
its messages, since pySHACL does not parse RDF Messages. We assessed
74 cases: the eight Waterinfo descriptions, the ten examples of the
specification that describe a complete source, 54 invalid variants
of the Waterinfo descriptions that each introduce one error, and two
variants expected to raise only a warning. The invalid variants
cover 15 categories, including missing time fields, field roles,
encoding declarations, duplicate points, out-of-window timestamps,
and a summary on a growing source. All 74 cases behaved as expected:
the valid descriptions conformed without warnings, each invalid
variant was reported as a violation by the constraint it targets,
and each warning variant raised its warning without a violation.
Eleven variants also triggered constraints that follow from the same
error; a source without a class, for instance, also leaves its
description without a recognisable source. No other violation was
reported. The reports of all cases are included in the repository.
```

## 4. Lifecycle and message-sequence checks (§5.4)

The placeholder asks:

- which representations were exercised;
- the expected and observed outcomes;
- whether HTTP incremental retrieval was tested.

### Representations and progression (`lifecycle/check_lifecycle.py`)

The 46 Zevenaarshaven readings are written one Write at a time to three growing sources, all stating `tss:rangeRequests true`. These are the three representations for which the specification allows it. JSON and Parquet sources cannot state it: a Write rewrites their literal or file.

| Representation | States | Progression |
| --- | --- | --- |
| RDF Message Log (`tss:EmbeddedRDFSource`) | 48 | The description message, 46 row messages, and the ending message (`tss:immutable true` and the stored summary). The first 6 states are the **published** description `waterinfo_289441042_tss2_points_time_value.ttl`, message by message: state 5 is that document, byte for byte, and its five rows are the first five readings. |
| CSV data file (`tss:CSVSource`) | 48 | The header line, then one record per reading. The writer then ends the source in its metadata document (`tss:immutable true` and the summary). |
| Arrow IPC stream (`tss:ArrowSource`) | 49 | The schema message, then one record batch per reading. The writer then ends the source in its metadata document, and appends the end-of-stream marker. |

### Checks (Table 5 of the paper)

| Check | Table 5 requirement | What is checked on every state |
| --- | --- | --- |
| L0 structure | (structural checks on the resulting states) | SHACL. On the last state, it also checks that the readings, read through the description, are those written, and that the stored summary equals the recomputed one. |
| L1 interpretation | Preserve source interpretation | The description is unchanged: class, fields, window, data file, grid and `tss:rangeRequests`. So are the CSV header columns and the Arrow schema. The ending adds only `tss:immutable true` and summaries. |
| L2 dependencies | Establish message dependencies | (log) A row or ending message comes after the description of its source, and is read with the prefixes before it. |
| L3 units | Preserve row-message structure | (log) Every message ends with a delimiter, and a row message holds one row of one source, at one time, and nothing else. (CSV) The file ends with a line break. (Arrow) The streaming format, complete messages, and no end-of-stream marker while growing. |
| L4 finalisation | Enforce finalisation | Summaries and `tss:immutable true` appear only at the end, `tss:immutable false` never appears in a log, and no row comes after the end. |
| L5 byte prefix | Honour incremental-access declarations | With `tss:rangeRequests true`, every state starts with the bytes of the previous one. |

### Results

- **The valid sequences** pass every check that applies: L0–L5 for the log, and L0, L1 and L3–L5 for the CSV file and the Arrow stream (L2 is about messages).
- **19 of 19 variants behave as expected.** Each of the 17 variants with an error is detected by the check it targets. The 2 permitted variants pass every check.

| Variant | Source | Change | Expected | Failed checks |
| --- | --- | --- | --- | --- |
| LV1 | log | the window moves after 10 rows; the log states no `tss:rangeRequests`, so it may be rewritten | L1 | L1 |
| LV2 | log | a row message before the description message | L2 | L1, L2 |
| LV3 | log | a row message after the ending message | L4 | L4 |
| LV4 | log | a summary message while the source grows | L4 | L0, L4 |
| LV5 | log | two rows in one row message | L3 | L3 |
| LV6 | log | the ending message has no delimiter | L3 | L3 |
| LV7 | log | the description states `tss:immutable false` | L4 | L0, L4 |
| LV8 | log | the log is rewritten after 10 rows (re-indented), despite `tss:rangeRequests true` | L5 | L5 |
| LV9 | log | a Write is repeated: the same row twice | L0 (SHACL P3) | L0 |
| VV1 | log | rows written out of time order | none | none |
| CV1 | CSV | ending the source also adds `tss:until` | L1 | L1 |
| CV2 | CSV | the value column is renamed after 10 records (no `tss:rangeRequests`) | L1 | L0, L1 |
| CV3 | CSV | the file is re-sorted after 10 records, despite `tss:rangeRequests true` | L5 | L5 |
| CV4 | CSV | a state whose last record has no line break | L3 | L3 |
| CV5 | CSV | the source is ended after 45 records, then the 46th is appended | L4 | L0, L4 |
| VV2 | CSV | no `tss:rangeRequests`, and the file is rewritten (newest first) at every Write | none | none |
| AV1 | Arrow | the IPC file format, rewritten at every Write | L3 | L3, L5 |
| AV2 | Arrow | the end-of-stream marker after 10 batches, while the source grows | L3 | L0, L3 |
| AV3 | Arrow | the stream is rewritten with a float32 value column (no `tss:rangeRequests`) | L1 | L0, L1 |

The extra failures follow from the same error. For example:

- LV2's description appears only after the first state (L1).
- LV4's early summary is a second summary in the final merge (SHACL F10).
- CV5's stored summary no longer matches the 46 rows (L0).

LV1, LV2, LV3, LV5, LV6 and LV8 leave a final merged graph that conforms to the shapes and holds the right readings. Only the sequence of states reveals their error: merging the messages of a log loses their order.

### HTTP incremental retrieval: tested, against a local server

A local server (Python `http.server`) answers requests as RFC 9110 says:

- Range requests, with 206, or 416 and `Content-Range: bytes */L`;
- `Accept-Ranges`;
- strong ETags and `If-None-Match`, with 304;
- plain requests, with 200.

A consumer follows §following and §range-requests of the specification:

1. It reads the whole representation once.
2. It then sends `Range: bytes=N-` with `Accept-Encoding: identity`, and reads complete units only.
3. It falls back to a full read when the new bytes cannot be read.
4. It never emits a row twice.

It polls after every Write, and once more in the middle of each sequence, when nothing was added.

| Case | Scenario | Requests (statuses) | Bytes received | Bytes, whole representation at every poll | Fall-backs | Rows correct |
| --- | --- | --- | --- | --- | --- | --- |
| H1 | log, Range requests | 49 (200: 1, 206: 47, 416: 1) | 14,786 | 484,174 | 0 | 46, yes |
| H2 | CSV, Range requests | 48 (200: 1, 206: 46, 416: 1) | 1,339 | 32,400 | 0 | 46, yes |
| H3 | Arrow, Range requests, until the end-of-stream marker | 49 (200: 1, 206: 47, 416: 1) | 9,768 | 248,824 | 0 | 46, yes |
| H4 | log rewritten after 10 rows (LV8); the consumer checks the last 32 bytes it read | 50 (200: 2, 206: 48) | 23,160 | 472,342 | 1 | 46, yes |
| H5 | log, polled also in the middle of every Write (half a message) | 96 (200: 1, 206: 94, 416: 1) | 19,755 | 948,552 | 0 | 46, yes |
| H6 | CSV, the server ignores Range (200 with the whole file) | 48 (200: 48) | 32,400 | 32,400 | 47 | 46, yes |
| H7 | CSV without `tss:rangeRequests` (VV2), whole file again when its ETag changed | 48 (200: 47, 304: 1) | 31,724 | 32,400 | 0 | 46, yes |

With Range requests, the consumer received these shares of the bytes that re-reading the whole representation at every poll costs:

- the log: 3.1 %;
- the CSV file: 4.1 %;
- the Arrow stream: 3.9 %.

**Not tested:**

- deployed servers and CDNs;
- intermediaries and caches;
- HTTP content coding;
- open-ended live ranges (RFC 8673);
- Arrow dictionary batches;
- concurrent writers (Writes are simulated as successive states, and as half-written states in H5);
- 404 and 410 answers.

### Suggested text (replaces the placeholder)

```latex
The checks were executed on three growing sources built from the 46
Zevenaarshaven readings, added one write at a time: the RDF Message
Log of an embedded RDF source, whose first six states are the
published description message by message, a CSV data file, and an
Arrow IPC stream. These are the representations for which a source
may declare incremental access, and all three declared it. Every
state after a write was retained and checked in sequence against the
requirements of Table~\ref{tab:lifecycle-checks}, together with the
structural validity of each state and the agreement of the final
state and its summary with the readings written. The valid sequences
passed every applicable check. Seventeen variants introduced a single
error, such as a row before its description, a row after the ending
message, a summary while the source grows, two rows in one message,
a missing delimiter, a repeated write, a changed window or header, a
rewritten log or re-sorted CSV file despite declared incremental
access, and an early end-of-stream marker; each was detected by the
check it targets. Six of the log variants leave a final merged log
that conforms to the shapes and were detected only from the sequence
of states. Two permitted variants, rows written out of timestamp
order and a CSV file rewritten at every write without declaring
incremental access, passed every check.

HTTP incremental retrieval was tested against a local server that
implements RFC~9110 range and conditional requests, with a consumer
that follows the procedure of the specification. In all seven
scenarios the consumer reconstructed the 46 readings without emitting
any twice. Following the log, the CSV file, and the Arrow stream with
range requests transferred 3.1--4.1\,\% of the bytes that re-reading
each representation at every poll would have required. The consumer
detected a rewritten log through its overlap check and fell back to a
full read, did not read a half-written message, and fell back to full
reads when the server ignored range requests. Deployed servers,
intermediaries, and content coding were not tested.
```

## 5. Reproducibility and supporting software (§5.5)

The placeholder asks for:

- permanent resource links;
- specification and artifact versions;
- an archive DOI;
- the compatible software release, with its implemented and tested capabilities;
- the commands for the remaining evaluations.

### Available now

- **Commands.** [README.md](README.md), section "Reproducing". Each evaluation is one script, and runs offline except the retrieval.
- **Versions.** The table at the top of this file.
- **Specification.** `https://knowledgeonwebscale.github.io/rdf-tss/`, built from `KNowledgeOnWebScale/rdf-tss`.
- **Vocabulary.** `https://w3id.org/tss` resolves (checked 2026-11-24) to `https://knowledgeonwebscale.github.io/rdf-tss/tss.ttl`, in Turtle. That is the upstream vocabulary. It differs from the evaluated `../vocabulary/tss.ttl` by the demo 11 differences:
  - the licence statement;
  - `dcterms:creator` instead of `dcterms:contributor`;
  - two comments: a summary only on an immutable source, and how a source of another class is streamed.
- **Supporting software.** The `rdftss` library, version 0.2.0, follows the final specification (next section).

### Supporting software: rdftss 0.2.0

`rdftss` is a Python library in `../../../rdftss-python/` of this repository, under the MIT licence of the repository. It bundles the vocabulary and the two shape files of this evaluation, byte for byte.

What it implements, by conformance class of the specification:

| Class | rdftss 0.2.0 |
| --- | --- |
| Metadata documents | `validate()`: the bundled SHACL shapes, chosen by the source classes. Its report gives the check ID of every result. |
| Data | Reading stops at a data error and says where it is; `read(strict=False)` collects the errors instead. `verify_summaries()` recomputes the stored summaries. |
| Consumers | Reads the five source classes: RDF points, JSON rows, CSV, Parquet, and Arrow IPC files and streams. It handles time grids with or without a step field, `tss:DeltaFromBaseline`, describing fields with their field property, the time window, and RDF Message Logs. A growing source is read as it is at that moment. **Not implemented:** following a growing source over HTTP (Range requests, ETags). |
| Expanders | `expand()`: the points of a source, with the default field property. `derive=True`, or `rdftss.derive()`, adds the SOSA, SAREF and QUDT statements of the vocabulary alignment, without a reasoner. |
| Writers | **Not implemented:** adding rows to a source, and ending it. It does write documents (a growing RDF source as an RDF Message Log) and converts sources between classes. |

It also runs SPARQL over the metadata and the points, finds sources across documents, and gives DataFrames.

How it is tested:

- **Its conformance suite:** 221 tests, in `../../../rdftss-python/conformance/`. Every check of both shape files has at least one test.
  - 180 metadata documents: 30 valid, 7 with an expected warning, 143 invalid;
  - 29 reading tests: 23 with expected rows, 6 with an expected error;
  - 7 summary tests;
  - 5 expansion tests.
- **Its test suite:** 461 tests, the conformance suite included. All passed on 2026-11-23.
- **On the Waterinfo descriptions:** `software/check_rdftss.py`, run on 2026-11-24 with the software versions above. **8 of 8 pass** (`software/results/rdftss_report.md`):
  - the same 392,904 rows as `tss_reader.py`, with no difference in 2,749,775 values;
  - no SHACL violation or warning;
  - 35 stored summaries (168 numbers), all up to date.

The HTTP consumer of §4 is the evaluation's own code (`lifecycle/check_lifecycle.py`), not part of rdftss.

### Still open, for the authors

1. **NDA-derived files in the public repository.** `factory_conductivity_tss2.ttl` (demos 2–11) and `factory_conductivity_csvw.json` (demos 2–5) are tracked in git since 2026-09-29 (commit `113b10c`). They are on the public GitHub repository that the paper would link. The evaluation never reads them. Decide how to handle them before linking the repository, or archive only the evaluation material.
2. **Commit and tag.** Nothing of this evaluation is committed yet. The paper needs the commit or tag that holds the code, descriptions, shapes and results.
3. **Archive DOI.** For example, a Zenodo release of that tag. Include `../data/` if the licence allows (see §1).
4. **Software release.** rdftss 0.2.0 is not committed or released yet. The paper needs its tag, or a package release, and the archive should include it. Its writer features and following over HTTP are not in this release (see "Supporting software").
5. **Licence of the Waterinfo series** (see §1).
6. **Vocabulary version.** `https://w3id.org/tss` serves the upstream vocabulary, without the demo 11 differences. Until they are merged upstream, cite the evaluated vocabulary by its commit.

### Suggested text (replaces the placeholder; the bracketed parts are still open)

```latex
The specification is published at
\url{https://knowledgeonwebscale.github.io/rdf-tss/} and its
vocabulary at \url{https://w3id.org/tss}. The reported runs used the
specification and vocabulary of [commit], and the SHACL shapes,
descriptions, competency questions, validation cases, and lifecycle
checks of [repository and tag], archived at [DOI], with
Python~3.14.6, RDFLib~7.6.0, pySHACL~0.40.2, and PyArrow~23.0.1.
From the evaluation directory, after installing its requirements and
the library, each remaining evaluation is reproduced by a single
script (Listing~\ref{lst:run-evaluation}). Only the retrieval needs
network access; its manifest records each request and the checksum
of each file.

\begin{lstlisting}[style=tsslisting,
    caption={Reproducing the remaining evaluations.},
    label={lst:run-evaluation}]
python waterinfo/fetch_waterinfo.py
python waterinfo/check_waterinfo.py
python waterinfo/selftest_waterinfo.py
cd competency_questions
python evaluation_runner.py --waterinfo \
    --questions waterinfo_questions.yaml \
    --expected waterinfo_expected.yaml
cd ..
python validation/run_validation.py
python lifecycle/check_lifecycle.py
python software/check_rdftss.py
\end{lstlisting}

The supporting software is the rdftss Python library, release 0.2.0
[link], which bundles the vocabulary and shapes used here. It reads
all five storage profiles, including time grids, baseline-difference
encodings, describing fields, and RDF Message Logs; it validates
descriptions with the SHACL shapes, recomputes stored summaries,
expands sources into points, and optionally derives the SOSA, SAREF,
and QUDT statements of the vocabulary alignment. It passes a
conformance suite of 221 tests, 180 metadata documents and 41
reading, summary, and expansion cases, which exercises every check
of both shape files. On the eight Waterinfo descriptions, it read
the same 392,904 rows as the evaluation reader, with no difference
in any of their 2,749,775 values, reported no violation or warning,
and confirmed all 35 stored summaries. Adding rows to a growing
source, ending it, and following it over HTTP are not implemented in
this release; the lifecycle and HTTP checks use the evaluation's own
consumer.
```

## Fixed during the evaluation

- **pySHACL changes how later documents are read.** pySHACL turns RDFLib's literal normalisation back on after every validation. Every description parsed after the first validation was therefore read with normalised literals: `"1"^^xsd:boolean` became `"true"`, and `"…Z"` became `"…+00:00"`. `tss_reader.shacl_validate` now turns normalisation off again, so every description is read as written, whatever was validated before it. The validation was re-run: all 74 cases have the same violations, warnings and verdicts as before.
- **Stale comment lines in the Waterinfo descriptions.** Six descriptions began with two comment lines from an earlier demo ("experimental … schema with field-level source locators", "proposal vocabulary"). They are replaced by one line that points to the specification. Only the comments changed, not the graphs, and every evaluation was re-run afterwards.
- **The competency-question package.** The runner now reads RDF Message Logs, `--expected` compares answers with expected rows, and the CQ4 interpretation follows the final model.
