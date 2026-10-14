# TSS2 Dashboard (Demo 4)

The dashboard loads local Turtle files from `demo 4/metadata endpoint/`, or online Turtle URLs.

## Storage profiles

- `tss:embedded false`: groups link to external CSV data (`tss:dataFile`) and CSVW descriptions (`tss:describedby`). Snippets select CSVW time/value column names.
- `tss:embedded true`: each snippet stores a JSON point array in `tss:points`. Its effective JSON-LD `tss:context` identifies numeric or text values.

Every dashboard CSV-reading path requires an explicit, single-character `dialect.delimiter` in the corresponding CSVW file. Missing or invalid delimiters stop parsing with an error; there is no delimiter guessing or comma fallback. This applies to the inspector preview, query/filter forms, SQL, CSVW validation and RDF generation. Raw file previews and downloads do not parse CSV cells.

Snippets inherit defaults from their group's `tss:snippetTemplate`, unless they state their own value. `tss:about` is replaced as a whole. The snippet window includes `tss:from` and excludes `tss:until`.

| Tab | Purpose |
|---|---|
| Overview | Group, template, series and raw Turtle |
| Series inspector | Metadata provenance and source files, including embedded points |
| SHACL | Automatic profile detection or uploaded custom Turtle shapes, conformance test and full text report |
| RDF generation | Expand embedded points or CSVW-mapped external readings, preview Turtle, and download the generated graph |
| Files | CSV/CSVW previews and downloads, or embedded points with a series picker |
| CSVW validation | External CSV validation; embedded endpoints direct users to SHACL |
| Query | Filtering, summaries, gaps and hourly/daily aggregation; text series support time filtering |
| SQL (DuckDB) | One table per external CSV or embedded snippet, plus a snippets metadata table |

SHACL selects `demo 4/SHACL/embedded_shapes.ttl` or `external_csv_shapes.ttl` from the group's boolean `tss:embedded`. Violations fail conformance; warnings are shown without failing it. Tests use the original metadata graph. The existing embedded shapes check JSON structure with regular expressions; they do not replace a full JSON parser. CSV contents are validated separately in CSVW validation.

In the SHACL tab, choose a custom `.ttl` file (up to 5 MB) to validate with your own shapes instead of the bundled profile. The selected filename is shown before running and in the report. **Use default shapes** clears the upload and restores automatic selection. Uploaded shapes are used only for that validation and are not saved over any project files. Malformed Turtle or invalid shapes produce an error.

The embedded Zevenaarshaven example has only five readings per series. Its queries read these samples, not the complete CSV stored elsewhere in the demo. Numeric ranges and mean/min/max aggregation apply only to numeric series.

## RDF generation

For either storage profile, open **RDF generation** and click **Generate RDF**. The Turtle preview contains domain observations and their referenced domain resources, with no TSS vocabulary or source container nodes. Each reading is independent; no collections or membership links are created. Group attributes, including `tss:producedBy`, are omitted. **Download Turtle (.ttl)** saves the generated graph without regenerating it. Embedded exports show the entire graph. External exports preview the first 200 readings; the download includes every generated reading and remains available until the backend restarts.

`backend/rdf_generation.py` applies each snippet's effective JSON-LD context to point keys by name, regardless of key order. It resolves snippet overrides before template defaults, adds the effective point type and about properties (except the about node's rdf:type), without adding links back to source snippets. Only top-level points receive this metadata; nested result nodes remain separate. Existing point identifiers are preserved; points without identifiers become blank nodes. Group metadata, including `tss:producedBy`, raw point arrays and JSON-LD context literals, is not exported. Sensor, unit and feature metadata attached to the observations is preserved. Referenced sensors/features and nested about metadata retain their domain descriptions. Each reading is exported as a standalone RDF node. No collections or membership links are synthesized. References whose objects are source snippets or other TSS container nodes are omitted; this includes the quality-colour series' source-snippet feature-of-interest reference. Other domain predicates and objects are retained. The output uses no TSS namespace. A time/value mapping or point type that explicitly uses TSS is rejected rather than silently dropping readings.

Embedded generation requires valid JSON points with time/value keys, a context mapping those keys to RDF properties, and an effective point type/about node. The context determines how RDF is constructed; timePath/valuePath and the declared time window are input metadata only and are not used to invent or filter embedded readings.

`backend/csv_rdf_generation.py` follows each external group's `tss:describedby` to its CSVW file and `tss:dataFile` to its CSV, resolving local or online relative links against the endpoint. It parses only with the explicit CSVW delimiter and applies its header, encoding, quoting, null/default and datatype settings. Headers are matched to column titles in schema order; `header: false` or `headerRowCount: 0` supports headerless CSVs. Column names must be distinct because TSS time/value bindings select those names.

CSVW `propertyUrl` supplies the predicate and `datatype` (a string, or an object with `base`) supplies the literal type. `valueUrl` supplies IRI objects, including the virtual `row_type` column's `rdf:type` mapping. URI templates may use row values and CSVW row/column variables. The generator preserves these mappings rather than replacing value predicates with `tss:valuePath`. Each snippet produces anonymous readings in its half-open time window, using its own value column, the shared timestamp, mapped auxiliary columns and effective about metadata. Values belonging to other snippets in the same CSV are excluded from that snippet's reading. Null measured values produce no reading; other null cells produce no triple. Referenced domain resources keep their descriptions.

External generation streams the complete Turtle export to a temporary file instead of building the entire graph in memory. Missing time/value mappings, invalid values, wrong column counts or mismatched headers stop generation with an error. This export supports zero or one header row, no skipped columns, anonymous subjects (no `aboutUrl` subject mapping), and unordered separator values. Use SHACL and CSVW validation separately to check source conformance.

The original CLI script in `code/expand_snippets` is separate; the new dashboard uses `backend/rdf_generation.py`.

## Running

From the repository root:

```powershell
cd "tech demo/demo 4/code/webapp/backend"
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

In another terminal:

```powershell
cd "tech demo/demo 4/code/webapp/frontend"
npm install
npm run dev
```

Open <http://localhost:5174>. Vite proxies `/api` to port 8001. Alternatively, run `npm run build` in the frontend folder and open <http://localhost:8001>; the backend serves `frontend/dist` when present. These ports are shared with Demo 3.

## Code and API

`main.py` loads endpoints, resolves metadata, reads both storage profiles, runs SHACL and provides the API. `tss2.py` defines inheritance and provenance. `sql_box.py` loads data into a temporary DuckDB database and runs read-only queries. Embedded SQL tables have time and value columns; numeric XSD value mappings become numbers and other values remain text. SQL permits one reading statement, up to 1000 returned rows, about 5 MB of values and 30 seconds of query execution. External access is disabled after loading.

All routes use `/api`; API documentation is at <http://localhost:8001/docs>. New routes:

- `GET /api/endpoints/{id}/shacl`: conformance status, selected shapes, full report, timing and severity counts.
- `POST /api/endpoints/{id}/shacl`: custom validation with a JSON body containing `filename` and `turtle`; returns the same report fields plus `custom: true`.
- `POST /api/endpoints/{id}/rdf`: generate embedded or external RDF; returns `turtle`, `filename`, `triples`, `points` and per-series counts. External results also include `downloadUrl`, `previewPoints`, `previewTruncated` and full export `bytes`. Embedded downloads use the complete generated response in the browser.
- `GET /api/endpoints/{id}/rdf/download/{token}`: download a completed external export as `observations.ttl` without repeating conversion.
- `GET /api/endpoints/{id}/points?snippet=...`: embedded JSON preview.

Online endpoint URLs are saved in backend/online_endpoints.json. Relative file links resolve against the endpoint URL. Embedded endpoints need no external data file. Each endpoint is expected to describe one group; SHACL rejects missing, invalid or conflicting storage flags rather than guessing.

## Verification

```powershell
# From backend/
python -B -m unittest -v test_storage_profiles test_csvw_delimiter test_rdf_generation test_csv_rdf_generation
# From frontend/
npm run lint
npm run build
```
