# TSS2 Dashboard (Demo 6)

The dashboard loads local Turtle files from `demo 6/metadata endpoint/`, or online Turtle URLs.

## Storage profiles

Each metadata endpoint describes one `tss:Source` with its columns as `tss:Field` nodes (column name, datatype, unit, observed property, ...).

- `tss:embedded false`: the data is in an external file, `tss:dataFile`, read as `tss:format` says: `tss:CSV` (with `tss:delimiter`, default `,`; `tss:header`, default `true`; `tss:encoding`, default `utf-8`) or `tss:Parquet`.
- `tss:embedded true`: the data is a JSON array of objects in `tss:points`, one object per row, keyed by the fields' `tss:columnName`.

The source's `tss:timeField` names the time column. Its time window includes `tss:from` and excludes `tss:until`. There is no delimiter guessing; raw file previews and downloads do not parse CSV cells.

| Tab | Purpose |
|---|---|
| Overview | The source, its fields (role, datatype, unit, ...) and the raw Turtle |
| Series inspector | Pick a field: everything known about it, marked as stated on the field or on its source, plus the marked Turtle and the first rows of the data |
| SHACL | Conformance test of the metadata with `external_shapes.ttl` or `embedded_shapes.ttl` (picked from `tss:embedded`), or with uploaded custom Turtle shapes, with the full text report |
| RDF generation | One `sosa:Observation` per row for each field with a `tss:observedProperty` (CSV, Parquet or embedded data); preview the Turtle and download the whole graph |
| Files | External sources: preview of `tss:dataFile` (CSV as is, Parquet converted to CSV) and a download of the original file. Embedded sources: the source's `tss:points`, one point per line. Both: the metadata Turtle file |
| Query | Pick a field: filtering, summaries, gaps and hourly/daily aggregation over CSV, Parquet or embedded data, read with the source's tss:timeField, tss:from/until and CSV settings; text fields support time and column filtering |
| SQL (DuckDB) | One table for the source's data (CSV, Parquet or embedded points) with a column per field, plus `source` and `fields` metadata tables; example queries are made from the fields |

In the Files tab, a source declared as `tss:format tss:Parquet` is previewed as CSV text with a header and the first 19 data rows. Only that sample is converted; the original Parquet file is unchanged, and the download icon saves the original `.parquet` file. CSV previews retain their original text and delimiter.

The SHACL tab picks its default shapes from the source's `tss:embedded`:

| TSS declaration | Default shapes |
|---|---|
| `tss:embedded false` (a CSV or Parquet `tss:dataFile`) | `demo 6/SHACL/external_shapes.ttl` |
| `tss:embedded true` (the readings in `tss:points`) | `demo 6/SHACL/embedded_shapes.ttl` |
| missing or not a boolean | none: the tab asks for custom shapes |

`external_shapes.ttl` checks the source (exactly one; required `tss:from`, `tss:mutable`, `tss:timeField` and `tss:key` among its own fields, at least one `tss:hasField`, `tss:embedded false`, `tss:dataFile`, `tss:format` `tss:CSV` or `tss:Parquet`, and for `tss:CSV` a one-character `tss:delimiter` and a `tss:encoding`; optional label, `tss:producedBy` and `tss:until` after `tss:from`) and each field (typed `tss:Field` and a field of exactly one source, a unique `tss:columnName`, a `tss:datatype`; optional unique `tss:columnIndex`, label, comment, numeric `tss:minimum`/`tss:maximum`). Other properties are allowed and not checked. A delimiter or encoding on a `tss:Parquet` source is only a warning. The file header lists every check with its number (S1-S15, F1-F8). The shapes only look at the metadata, not at the data file.

`embedded_shapes.ttl` checks the source (exactly one; required `tss:mutable`, `tss:embedded true`, a `tss:timeField` among its own fields, at least one `tss:hasField`, and one `tss:points` typed `rdf:JSON` whose text looks like a JSON array of objects; optional label, `tss:producedBy`, `tss:key` among its fields, `tss:from`, and `tss:until` after `tss:from`). `tss:dataFile`, `tss:format`, `tss:delimiter`, `tss:encoding` and `tss:header` are not allowed on an embedded source. The fields get the same checks as in the external shapes, plus a warning when a field's column name is not a key in the points (S1-S12, F1-F9). SHACL can't parse JSON, so the points checks are text patterns, not a full JSON check.

Violations fail conformance; warnings are shown without failing it.

In the SHACL tab, choose a custom `.ttl` file (up to 5 MB) to validate with your own shapes instead of the bundled profile. The selected filename is shown before running and in the report. **Use default shapes** clears the upload and restores automatic selection. Uploaded shapes are used only for that validation and are not saved over any project files. Malformed Turtle or invalid shapes produce an error.

The embedded Zevenaarshaven example has only five readings per series. Its queries read these samples, not the complete CSV stored elsewhere in the demo. Numeric ranges and mean/min/max aggregation apply only to numeric series.

## RDF generation

Open **RDF generation** and click **Generate RDF**. The backend runs the shared converter `code/TSS2RDF/tss2rdf.py` (through `backend/rdf_generation.py`) on the source's data: the CSV or Parquet `tss:dataFile`, or the embedded `tss:points`. Every row gives one `sosa:Observation` for each field with a `tss:observedProperty` (except the time field), with the field's observed property, sensor, feature of interest and unit, the row's time as `sosa:resultTime` and the cell as `sosa:hasSimpleResult`, typed with the field's `tss:datatype`. Only rows inside `[tss:from, tss:until)` are used; empty cells give no observation; a value that does not fit its datatype stops generation with the row. See `code/TSS2RDF/README.md` for all rules.

The whole graph is streamed to a temporary file, which the **Download Turtle** link serves; the page shows the counts per field and a preview of the first 200 observations. No SHACL validation is done before generating.

## Running

From the repository root:

```powershell
cd "tech demo/demo 6/code/webapp/backend"
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

In another terminal:

```powershell
cd "tech demo/demo 6/code/webapp/frontend"
npm install
npm run dev
```

Open <http://localhost:5174>. Vite proxies `/api` to port 8001. Alternatively, run `npm run build` in the frontend folder and open <http://localhost:8001>; the backend serves `frontend/dist` when present. These ports are shared with Demo 3.

## Code and API

`main.py` loads endpoints, resolves metadata, reads both storage profiles, runs SHACL and provides the API. `sql_box.py` loads data into a temporary DuckDB database and runs read-only queries. The data table has one column per data column. CSV cells and embedded JSON values get the type of their field's `tss:datatype` (XSD numbers, date-times, booleans; anything else stays text), and a value that does not fit stops loading with a message. Parquet columns keep the types stored in the file. SQL permits one reading statement, up to 1000 returned rows, about 5 MB of values and 30 seconds of query execution. External access is disabled after loading.

All routes use `/api`; API documentation is at <http://localhost:8001/docs>. New routes:

- `GET /api/endpoints/{id}/shacl`: conformance status, selected shapes, full report, timing and severity counts.
- `POST /api/endpoints/{id}/shacl`: custom validation with a JSON body containing `filename` and `turtle`; returns the same report fields plus `custom: true`.
- `POST /api/endpoints/{id}/rdf`: generate the observations; returns a `turtle` preview, `observations`, `triples`, `series` (observations per field), `previewObservations`, `previewTruncated`, `bytes`, `filename` and `downloadUrl`. Embedded downloads use the complete generated response in the browser.
- `GET /api/endpoints/{id}/rdf/download/{token}`: download a completed export as `observations.ttl` without repeating the conversion.
- `GET /api/endpoints/{id}/data` and `GET /api/endpoints/{id}/data/download`: preview and download of an external source's `tss:dataFile`.
- `GET /api/endpoints/{id}/points`: the embedded source's `tss:points`, one point per line.

Online endpoint URLs are saved in backend/online_endpoints.json. Relative file links resolve against the endpoint URL. Embedded endpoints need no external data file. Each endpoint is expected to describe one `tss:Source`; the SHACL tab does not guess a profile when `tss:embedded` is missing or invalid.

## Verification

```powershell
# From backend/
python -B -m unittest -v test_storage_profiles test_external_shacl test_file_previews test_rdf_generation
# From frontend/
npm run lint
npm run build
```
