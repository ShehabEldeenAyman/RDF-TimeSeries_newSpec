# Demo 11 competency-question experimental log

- Started: 2026-11-20T10:57:08.639168+01:00 (Europe/Brussels)
- Finished: 2026-11-20T10:57:08.858924+01:00
- Duration: 0.220 seconds
- Python: 3.14.6
- Dependencies: RDFLib 7.6.0, PyYAML 6.0.3, pyparsing 3.3.3
- Working directory: `/home/shehab/Desktop/RDF-TimeSeries_newSpec/tech demo/demo 11/evaluation/competency_questions`
- Input: `sample_instances.trig` (bundled synthetic metadata catalog)
- Questions: all eight (CQ1–CQ8), from `competency_questions.yaml`
- Exit status: 0

## Command

```bash
/tmp/demo11-cq-evaluation-venv/bin/python evaluation_runner.py
```

## Evaluation output (stdout and stderr)

```text

CQ1 — Which sources describe a specified observed property for a specified feature of interest?
Example: Find conductivity series for Water Body A.
source	field
https://example.org/rdftss-cq/afterSource	https://example.org/rdftss-cq/afterValue
https://example.org/rdftss-cq/arrowSource	https://example.org/rdftss-cq/arrowValue
https://example.org/rdftss-cq/beforeSource	https://example.org/rdftss-cq/beforeValue
https://example.org/rdftss-cq/csvSource	https://example.org/rdftss-cq/csvValue
https://example.org/rdftss-cq/deltaSource	https://example.org/rdftss-cq/deltaValue
https://example.org/rdftss-cq/gridSource	https://example.org/rdftss-cq/gridValue
https://example.org/rdftss-cq/jsonSource	https://example.org/rdftss-cq/jsonValue
https://example.org/rdftss-cq/parquetSource	https://example.org/rdftss-cq/parquetValue
https://example.org/rdftss-cq/rdfSource	https://example.org/rdftss-cq/rdfValue
Rows: 9
Interpretation: Exact domain IRI matching; no inference or vocabulary alignment is performed. Matching metadata does not establish payload availability.

CQ2 — Which relevant sources have declared temporal windows overlapping a requested interval?
Example: Find conductivity series for Water Body A overlapping [2026-01-01T00:00:00Z, 2026-01-02T00:00:00Z).
source	from	until
https://example.org/rdftss-cq/arrowSource	2026-01-01T00:00:00Z	2026-01-02T00:00:00Z
https://example.org/rdftss-cq/csvSource	2026-01-01T00:00:00Z	2026-01-02T00:00:00Z
https://example.org/rdftss-cq/deltaSource	2026-01-01T00:00:00Z	2026-01-02T00:00:00Z
https://example.org/rdftss-cq/gridSource	2026-01-01T00:00:00Z	2026-01-02T00:00:00Z
https://example.org/rdftss-cq/jsonSource	2026-01-01T00:00:00Z	2026-01-02T00:00:00Z
https://example.org/rdftss-cq/parquetSource	2025-12-01T00:00:00Z	—
https://example.org/rdftss-cq/rdfSource	2026-01-01T00:00:00Z	2026-01-02T00:00:00Z
Rows: 7
Interpretation: Absent bounds are open. Assumes conforming timestamps and nonempty declared windows. Boundary-touching windows do not overlap. A matching window does not guarantee readings exist in it.

CQ3 — Where are the readings of relevant sources located, and how are they represented?
Example: Locate external CSV, Parquet and Arrow references or embedded access predicates for conductivity series concerning Water Body A.
source	profile	dataFile	accessPredicate
https://example.org/rdftss-cq/afterSource	https://w3id.org/tss#CSVSource	https://example.org/rdftss-cq/data/after.csv	https://w3id.org/tss#dataFile
https://example.org/rdftss-cq/arrowSource	https://w3id.org/tss#ArrowSource	https://example.org/rdftss-cq/data/arrow.arrows	https://w3id.org/tss#dataFile
https://example.org/rdftss-cq/beforeSource	https://w3id.org/tss#CSVSource	https://example.org/rdftss-cq/data/before.csv	https://w3id.org/tss#dataFile
https://example.org/rdftss-cq/csvSource	https://w3id.org/tss#CSVSource	https://example.org/rdftss-cq/data/csv.csv	https://w3id.org/tss#dataFile
https://example.org/rdftss-cq/deltaSource	https://w3id.org/tss#CSVSource	https://example.org/rdftss-cq/data/delta.csv	https://w3id.org/tss#dataFile
https://example.org/rdftss-cq/gridSource	https://w3id.org/tss#CSVSource	https://example.org/rdftss-cq/data/grid.csv	https://w3id.org/tss#dataFile
https://example.org/rdftss-cq/jsonSource	https://w3id.org/tss#EmbeddedJSONSource	—	https://w3id.org/tss#points
https://example.org/rdftss-cq/parquetSource	https://w3id.org/tss#ParquetSource	https://example.org/rdftss-cq/data/parquet.parquet	https://w3id.org/tss#dataFile
https://example.org/rdftss-cq/rdfSource	https://w3id.org/tss#EmbeddedRDFSource	—	https://w3id.org/tss#hasPoint
Rows: 9
Interpretation: Embedded results identify the source and predicate in the queried catalog, not a new file URL. No JSON literals or point readings are selected. External URLs are not dereferenced.

CQ4 — Which fields identify timestamps and the requested measurement, and how are they bound to the representation?
Example: Inspect bindings for direct CSV, Parquet, Arrow, JSON, RDF and time-grid representations.
source	profile	timeField	timeColumn	timeIndex	valueField	valueColumn	valueIndex
https://example.org/rdftss-cq/arrowSource	https://w3id.org/tss#ArrowSource	https://example.org/rdftss-cq/arrowTime	timestamp	—	https://example.org/rdftss-cq/arrowValue	value	—
https://example.org/rdftss-cq/csvSource	https://w3id.org/tss#CSVSource	https://example.org/rdftss-cq/csvTime	timestamp	—	https://example.org/rdftss-cq/csvValue	value	—
https://example.org/rdftss-cq/gridSource	https://w3id.org/tss#CSVSource	https://example.org/rdftss-cq/gridTime	—	—	https://example.org/rdftss-cq/gridValue	value	—
https://example.org/rdftss-cq/jsonSource	https://w3id.org/tss#EmbeddedJSONSource	https://example.org/rdftss-cq/jsonTime	timestamp	—	https://example.org/rdftss-cq/jsonValue	value	—
https://example.org/rdftss-cq/parquetSource	https://w3id.org/tss#ParquetSource	https://example.org/rdftss-cq/parquetTime	recorded_at	—	https://example.org/rdftss-cq/parquetValue	conductivity	—
https://example.org/rdftss-cq/rdfSource	https://w3id.org/tss#EmbeddedRDFSource	https://example.org/rdftss-cq/rdfTime	—	—	https://example.org/rdftss-cq/rdfValue	—	—
Rows: 6
Interpretation: For RDF points, field IRIs are the predicates. JSON uses column names as keys. Derived time fields have no column; CQ6 retrieves grid parameters. Unbound values are not automatically errors.

CQ5 — What datatype and semantic context describe the selected measurement field?
Example: Retrieve measurement context for the selected CSV conductivity series.
field	datatype	property	unit	sensor	feature
https://example.org/rdftss-cq/csvValue	http://www.w3.org/2001/XMLSchema#double	https://example.org/rdftss-cq/ElectricalConductivity	http://qudt.org/vocab/unit/MicroS-PER-CentiM	https://example.org/rdftss-cq/sensorA	https://example.org/rdftss-cq/WaterBodyA
Rows: 1
Interpretation: Reports supplied context, not independently verified domain truth. Missing optional descriptions remain unbound.

CQ6 — What declared parameters are needed to reconstruct timestamps or measurement values from a selected external source?
Example: Compare direct, time-grid and baseline-difference descriptions without decoding their payloads.
source	dataFile	field	timeBase	timeStep	stepField	stepColumn	stepIndex	encoding	baseline
https://example.org/rdftss-cq/csvSource	https://example.org/rdftss-cq/data/csv.csv	https://example.org/rdftss-cq/csvValue	—	—	—	—	—	—	—
https://example.org/rdftss-cq/deltaSource	https://example.org/rdftss-cq/data/delta.csv	https://example.org/rdftss-cq/deltaValue	—	—	—	—	—	https://w3id.org/tss#DeltaFromBaseline	4750.0
https://example.org/rdftss-cq/gridSource	https://example.org/rdftss-cq/data/grid.csv	https://example.org/rdftss-cq/gridValue	2026-01-01T08:00:00Z	PT15M	https://example.org/rdftss-cq/gridStep	step	—	—	—
Rows: 3
Interpretation: Parameters are optional independently so incomplete declarations remain visible. This is retrieval, not validation. No timestamp or measurement reconstruction is performed.

CQ7 — Which selected sources are final, and which are still growing?
Example: Compare a final CSV source with a growing Arrow stream.
source	profile	immutable
https://example.org/rdftss-cq/arrowSource	https://w3id.org/tss#ArrowSource	false
https://example.org/rdftss-cq/csvSource	https://w3id.org/tss#CSVSource	true
Rows: 2
Interpretation: A source that does not state tss:immutable is growing (the default is false). The metadata does not say when a growing source last changed; a consumer finds that out with HTTP validators (ETag, Last-Modified). The query does not verify publisher behavior.

CQ8 — Which declared summaries are available for selected measurement fields, and what source windows do they describe?
Example: Retrieve available CSV statistics and preserve a Parquet result with no summary.
source	field	from	until	summary	count	missing	minimum	maximum	generatedAt
https://example.org/rdftss-cq/csvSource	https://example.org/rdftss-cq/csvValue	2026-01-01T00:00:00Z	2026-01-02T00:00:00Z	https://example.org/rdftss-cq/csvSummary	3	0	4722.4	4810.5	2026-01-02T10:00:00Z
https://example.org/rdftss-cq/parquetSource	https://example.org/rdftss-cq/parquetValue	2025-12-01T00:00:00Z	—	—	—	—	—	—	—
Rows: 2
Interpretation: Summary numbers are synthetic publisher declarations. They are not recomputed or verified. An absent summary is retained as an unbound result; missing counts do not count absent timestamps.
```

## Interpretation

Exit status 0 indicates successful query execution, not independent verification of answer correctness or specification conformance. This run uses synthetic metadata; it does not retrieve external payloads or perform SHACL validation. No static expected-answer comparison is provided by this evaluation runner.
