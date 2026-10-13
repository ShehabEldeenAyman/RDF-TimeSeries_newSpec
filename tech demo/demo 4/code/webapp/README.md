# TSS2 Dashboard (Demo 4)

The dashboard loads local Turtle files from `demo 4/metadata endpoint/`, or online Turtle URLs.

## Storage profiles

- `tss:embedded false`: groups link to external CSV data (`tss:dataFile`) and CSVW descriptions (`tss:describedby`). Snippets select CSVW time/value column names.
- `tss:embedded true`: each snippet stores a JSON point array in `tss:points`. Its effective JSON-LD `tss:context` identifies numeric or text values.

Every dashboard CSV-reading path requires an explicit, single-character `dialect.delimiter` in the corresponding CSVW file. Missing or invalid delimiters stop parsing with an error; there is no delimiter guessing or comma fallback. This applies to the inspector preview, query/filter forms, SQL and CSVW validation. Raw file previews and downloads do not parse CSV cells.

Snippets inherit defaults from their group's `tss:snippetTemplate`, unless they state their own value. `tss:about` is replaced as a whole. The snippet window includes `tss:from` and excludes `tss:until`.

| Tab | Purpose |
|---|---|
| Overview | Group, template, series and raw Turtle |
| Series inspector | Metadata provenance and source files, including embedded points |
| SHACL | Automatic profile detection or uploaded custom Turtle shapes, conformance test and full text report |
| Files | CSV/CSVW previews and downloads, or embedded points with a series picker |
| CSVW validation | External CSV validation; embedded endpoints direct users to SHACL |
| Query | Filtering, summaries, gaps and hourly/daily aggregation; text series support time filtering |
| SQL (DuckDB) | One table per external CSV or embedded snippet, plus a snippets metadata table |

SHACL selects `demo 4/SHACL/embedded_shapes.ttl` or `external_csv_shapes.ttl` from the group's boolean `tss:embedded`. Violations fail conformance; warnings are shown without failing it. Tests use the original metadata graph. The existing embedded shapes check JSON structure with regular expressions; they do not replace a full JSON parser. CSV contents are validated separately in CSVW validation.

In the SHACL tab, choose a custom `.ttl` file (up to 5 MB) to validate with your own shapes instead of the bundled profile. The selected filename is shown before running and in the report. **Use default shapes** clears the upload and restores automatic selection. Uploaded shapes are used only for that validation and are not saved over any project files. Malformed Turtle or invalid shapes produce an error.

The embedded Zevenaarshaven example has only five readings per series. Its queries read these samples, not the complete CSV stored elsewhere in the demo. Numeric ranges and mean/min/max aggregation apply only to numeric series.

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
- `GET /api/endpoints/{id}/points?snippet=...`: embedded JSON preview.

Online endpoint URLs are saved in backend/online_endpoints.json. Relative file links resolve against the endpoint URL. Embedded endpoints need no external data file. Each endpoint is expected to describe one group; SHACL rejects missing, invalid or conflicting storage flags rather than guessing.

## Verification

```powershell
# From backend/
python -B -m unittest -v test_storage_profiles test_csvw_delimiter
# From frontend/
npm run lint
npm run build
```
