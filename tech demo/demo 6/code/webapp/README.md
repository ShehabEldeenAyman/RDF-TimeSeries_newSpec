# TSS2 Dashboard (Demo 5)

The dashboard loads local Turtle files from `demo 5/metadata endpoint/`, or online Turtle URLs.

## Storage profiles

- `tss:embedded false`: groups link to external data with `tss:dataFile`. CSV sources use CSVW descriptions (`tss:describedby`); Parquet sources use the snippet's effective JSON-LD `tss:context` for RDF generation.
- `tss:embedded true`: each snippet stores a JSON point array in `tss:points`. Its effective JSON-LD `tss:context` identifies numeric or text values.

Every dashboard CSV-reading path uses the corresponding CSVW file's `dialect.delimiter`. If `delimiter` or the entire `dialect` is omitted, it uses the CSVW default comma (`","`). An explicitly supplied delimiter must be a single character; invalid values stop parsing with an error. There is no delimiter guessing. This applies to RDF generation. Raw file previews and downloads do not parse CSV cells.

Snippets inherit defaults from their group's `tss:snippetTemplate`, unless they state their own value. `tss:about` is replaced as a whole. The snippet window includes `tss:from` and excludes `tss:until`.

| Tab | Purpose |
|---|---|
| Overview | Group, template, series and raw Turtle |
| Series inspector | Metadata provenance and source files, including embedded points |
| SHACL | Automatic profile detection or uploaded custom Turtle shapes, conformance test and full text report |
| RDF generation | Expand embedded points, CSVW-mapped CSV readings, or context-mapped Parquet readings; preview Turtle and download the generated graph |
| Files | External sources: preview of `tss:dataFile` (CSV as is, Parquet converted to CSV) and a download of the original file. Embedded sources: the source's `tss:points`, one point per line. Both: the metadata Turtle file |
| Query | Pick a field: filtering, summaries, gaps and hourly/daily aggregation over CSV, Parquet or embedded data, read with the source's tss:timeField, tss:from/until and CSV settings; text fields support time and column filtering |
| SQL (DuckDB) | One table for the source's data (CSV, Parquet or embedded points) with a column per field, plus `source` and `fields` metadata tables; example queries are made from the fields |

In the Files tab, a source declared as `tss:format tss:Parquet` is previewed as CSV text with a header and the first 19 data rows. Only that sample is converted; the original Parquet file is unchanged, and the download icon saves the original `.parquet` file. CSV previews retain their original text and delimiter.

SHACL automatically selects one of three files in `demo 5/SHACL/` using the original TSS graph:

| TSS declaration | Default shapes | Mapping requirements |
|---|---|---|
| `tss:embedded true` | `embedded_shapes.ttl` | Embedded JSON points and their context |
| `tss:embedded false` with `dcterms:format filetype:CSV` | `external_csv_shapes.ttl` | CSVW via `tss:describedby` |
| `tss:embedded false` with another format | `external_non_csv_shapes.ttl` | Mandatory `tss:context` on each snippet or inherited from its template; no CSVW requirement |

Both external profiles require exactly one format IRI. The CSV profile requires the EU `filetype:CSV` IRI; the non-CSV profile excludes it and allows formats such as `filetype:PARQUET`. Non-CSV context values must be `rdf:JSON` objects, and column bindings may contain spaces or punctuation. These checks establish the presence and form of the context; they do not verify its complete mapping against the external file. Missing format declarations are reported as nonconformance by the non-CSV shapes. Mixed CSV/non-CSV groups require custom shapes for one combined test.

The Parquet example is `metadata endpoint/waterinfo_289429042_tss2_parquet.ttl`; its dashboard/API ID is `waterinfo_289429042_tss2_parquet`. Its data file remains `data/waterinfo_289429042.parquet`.

Violations fail conformance; warnings are shown without failing it. Tests use the original metadata graph. JSON structure checks use regular expressions and do not replace a full JSON parser.

In the SHACL tab, choose a custom `.ttl` file (up to 5 MB) to validate with your own shapes instead of the bundled profile. The selected filename is shown before running and in the report. **Use default shapes** clears the upload and restores automatic selection. Uploaded shapes are used only for that validation and are not saved over any project files. Malformed Turtle or invalid shapes produce an error.

The embedded Zevenaarshaven example has only five readings per series. Its queries read these samples, not the complete CSV stored elsewhere in the demo. Numeric ranges and mean/min/max aggregation apply only to numeric series.

## RDF generation

For either storage profile, open **RDF generation** and click **Generate RDF**. The Turtle preview contains domain observations and their referenced domain resources, with no TSS vocabulary or source container nodes. Each reading is independent; no collections or membership links are created. Group attributes, including `tss:producedBy`, are omitted. **Download Turtle (.ttl)** saves the generated graph without regenerating it. Embedded exports show the entire graph. External exports preview the first 200 readings; the download includes every generated reading and remains available until the backend restarts.

`backend/rdf_generation.py` applies each snippet's effective JSON-LD context to point keys by name, regardless of key order. It resolves snippet overrides before template defaults, adds the effective point type and about properties (except the about node's rdf:type), without adding links back to source snippets. Only top-level points receive this metadata; nested result nodes remain separate. Existing point identifiers are preserved; points without identifiers become blank nodes. Group metadata, including `tss:producedBy`, raw point arrays and JSON-LD context literals, is not exported. Sensor, unit and feature metadata attached to the observations is preserved. Referenced sensors/features and nested about metadata retain their domain descriptions. Each reading is exported as a standalone RDF node. No collections or membership links are synthesized. References whose objects are source snippets or other TSS container nodes are omitted; this includes the quality-colour series' source-snippet feature-of-interest reference. Other domain predicates and objects are retained. The output uses no TSS namespace. A time/value mapping or point type that explicitly uses TSS is rejected rather than silently dropping readings.

Embedded generation requires valid JSON points with time/value keys, a context mapping those keys to RDF properties, and an effective point type/about node. The context determines how RDF is constructed; timePath/valuePath and the declared time window are input metadata only and are not used to invent or filter embedded readings.

For external sources, RDF generation selects CSV or Parquet using the group's `dcterms:format`. Unsupported formats and mixed CSV/Parquet groups return a clear error. An absent format retains the original CSV conversion behavior.

`backend/parquet_rdf_generation.py` reads the Parquet file linked by `tss:dataFile` directly, without CSVW or an intermediate CSV. It expands each snippet's selected rows using its effective JSON-LD `tss:context`, including predicate and datatype mappings, language tags, IRI values and nested results. Column bindings match exact Parquet field names first, then an unambiguous case-insensitive match (so the sample's `value` binding finds `Value`). The context itself maps actual field names. Decimal values with context datatype mappings retain their precision.

Parquet observations receive the effective point type and about metadata, including descriptions of referenced domain resources. Each series includes its own measured column, the shared time column and mapped auxiliary columns; measured columns belonging to other series are excluded. Null measured values are skipped, and each snippet's inclusive `from` and exclusive `until` bounds apply. Missing timestamps, missing time/value mappings and invalid typed literals stop conversion. Rows are read in batches of at most 1,000: simple scalar contexts are written directly as RDF, while complex contexts and nested results use JSON-LD expansion. The complete graph is written to disk, with a preview of at most 200 readings. The output has no TSS vocabulary, source containers, producer attributes or synthesized observation collections.

`backend/csv_rdf_generation.py` follows each CSV group's `tss:describedby` to its CSVW file and `tss:dataFile` to its CSV, resolving local or online relative links against the endpoint. It parses with the CSVW delimiter (defaulting to comma when omitted) and applies its header, encoding, quoting, null/default and datatype settings. Headers are matched to column titles in schema order; `header: false` or `headerRowCount: 0` supports headerless CSVs. Column names must be distinct because TSS time/value bindings select those names.

CSVW `propertyUrl` supplies the predicate and `datatype` (a string, or an object with `base`) supplies the literal type. `valueUrl` supplies IRI objects, including the virtual `row_type` column's `rdf:type` mapping. URI templates may use row values and CSVW row/column variables. The generator preserves these mappings rather than replacing value predicates with `tss:valuePath`. Each snippet produces anonymous readings in its half-open time window, using its own value column, the shared timestamp, mapped auxiliary columns and effective about metadata. Values belonging to other snippets in the same CSV are excluded from that snippet's reading. Null measured values produce no reading; other null cells produce no triple. Referenced domain resources keep their descriptions.

External generation streams the complete Turtle export to a temporary file instead of building the entire graph in memory. Missing time/value mappings, invalid values, wrong column counts or mismatched headers stop generation with an error. This export supports zero or one header row, no skipped columns, anonymous subjects (no `aboutUrl` subject mapping), and unordered separator values. Use SHACL and CSVW validation separately to check source conformance.

The original CLI script in `code/expand_snippets` is separate; the new dashboard uses `backend/rdf_generation.py`.

The standalone converter in `code/TSS2RDF/tss2rdf.py` supports embedded and CSV conversions through its CLI with `--mode embedded` or `--mode external`, a TSS path, and explicit CSV/CSVW paths for external data. See `code/TSS2RDF/README.md` for commands. The dashboard imports all three conversion functions from that module, including `generate_parquet`; the Parquet dashboard path additionally uses DuckDB from the backend dependencies.

## Running

From the repository root:

```powershell
cd "tech demo/demo 5/code/webapp/backend"
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

In another terminal:

```powershell
cd "tech demo/demo 5/code/webapp/frontend"
npm install
npm run dev
```

Open <http://localhost:5174>. Vite proxies `/api` to port 8001. Alternatively, run `npm run build` in the frontend folder and open <http://localhost:8001>; the backend serves `frontend/dist` when present. These ports are shared with Demo 3.

## Code and API

`main.py` loads endpoints, resolves metadata, reads both storage profiles, runs SHACL and provides the API. `tss2.py` defines inheritance and provenance. `sql_box.py` loads data into a temporary DuckDB database and runs read-only queries. The data table has one column per data column. CSV cells and embedded JSON values get the type of their field's `tss:datatype` (XSD numbers, date-times, booleans; anything else stays text), and a value that does not fit stops loading with a message. Parquet columns keep the types stored in the file. SQL permits one reading statement, up to 1000 returned rows, about 5 MB of values and 30 seconds of query execution. External access is disabled after loading.

All routes use `/api`; API documentation is at <http://localhost:8001/docs>. New routes:

- `GET /api/endpoints/{id}/shacl`: conformance status, selected shapes, full report, timing and severity counts.
- `POST /api/endpoints/{id}/shacl`: custom validation with a JSON body containing `filename` and `turtle`; returns the same report fields plus `custom: true`.
- `POST /api/endpoints/{id}/rdf`: generate embedded or external RDF; returns `turtle`, `filename`, `triples`, `points` and per-series counts. External results also include `downloadUrl`, `previewPoints`, `previewTruncated` and full export `bytes`. Embedded downloads use the complete generated response in the browser.
- `GET /api/endpoints/{id}/rdf/download/{token}`: download a completed external export as `observations.ttl` without repeating conversion.
- `GET /api/endpoints/{id}/data` and `GET /api/endpoints/{id}/data/download`: preview and download of an external source's `tss:dataFile`.
- `GET /api/endpoints/{id}/points`: the embedded source's `tss:points`, one point per line.

Online endpoint URLs are saved in backend/online_endpoints.json. Relative file links resolve against the endpoint URL. Embedded endpoints need no external data file. Each endpoint is expected to describe one group; SHACL rejects missing, invalid or conflicting storage flags rather than guessing.

## Verification

```powershell
# From backend/
python -B -m unittest -v test_storage_profiles test_external_shacl test_file_previews test_rdf_generation test_csv_rdf_generation test_parquet_rdf_generation
# From frontend/
npm run lint
npm run build
```
