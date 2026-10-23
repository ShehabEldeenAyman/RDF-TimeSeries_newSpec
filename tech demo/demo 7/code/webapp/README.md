# TSS2 Dashboard (Demo 7)

The dashboard shows TSS2 metadata endpoints: the local Turtle files in `demo 7/metadata endpoint/`, and online RDF documents added by URL. The browser fetches each RDF document and parses it with [rdf-parse](https://github.com/rubensworks/rdf-parse.js) into an N3.js Store (`frontend/src/rdf.js`). The helpers in `frontend/src/tss.js` ask that store questions by full IRI, and every metadata fact a tab shows comes from it. The FastAPI backend serves the documents, reads the data files and does the computing: rdflib for RDF, pyshacl for SHACL, DuckDB for SQL and Parquet.

## Storage profiles

Each metadata endpoint describes one `tss:Source` with its columns as `tss:Field` nodes (column name, datatype, unit, observed property, ...).

- `tss:embedded false`: the data is in an external file, `tss:dataFile`, read as `tss:format` says: `tss:CSV` (with `tss:delimiter`, default `,`; `tss:header`, default `true`; `tss:encoding`, default `utf-8`) or `tss:Parquet`.
- `tss:embedded true`: the data is in `tss:points`, an `rdf:JSON` literal holding a JSON array of objects, one per point, keyed by the fields' `tss:columnName`. It is read as RDF (see [Embedded points](#embedded-points-tsspoints)).

The source's `tss:timeField` names the time field. Its time window includes `tss:from` and excludes `tss:until`. There is no delimiter guessing. Terms are compared by their full IRI (`https://w3id.org/tss#CSV`), never by a prefixed name, so a document may use any prefixes.

## What is RDF and what is not

| What | Syntax | Read with |
|---|---|---|
| Metadata endpoints (`/files/metadata%20endpoint/<id>.ttl`, online documents) | Turtle (online also RDF/XML, N-Triples, N3; not JSON-LD, see [Security](#security)) | rdf-parse in the browser, rdflib in the backend, both with the same base IRI |
| Catalogue (`GET /api/catalog`) | Turtle (DCAT) | rdf-parse (`readCatalog` in `tss.js`) |
| Online registry (`backend/online_endpoints.ttl`, gitignored) | Turtle (DCAT) | rdflib |
| SHACL shapes (`/files/SHACL/*.ttl`) and validation reports | Turtle | pyshacl and rdflib; the browser parses the report with rdf-parse |
| Generated observations | N-Triples download and Turtle preview, both written by rdflib | the browser parses the preview with rdf-parse |
| Embedded points (`tss:points`) | an `rdf:JSON` literal inside the metadata | a JSON-LD parser (rdflib, rdf-parse) with a context made from the fields |
| Data files (`tss:dataFile`) | CSV or Parquet: raw data, described by the RDF | the backend (Python's csv module, DuckDB), as the `tss:format` IRI says |
| Computed results and errors | JSON | `response.json()` |

JSON is used only for computed tabular results (rows, previews, filter values, readings, summaries, SQL results, counts) and for error messages (`{"detail": "..."}`). No code reads RDF as JSON (no `JSON.parse` or `json.loads` of RDF). The JSON answers don't repeat what the metadata says (labels, units, datatypes, formats, column names, paths): they name fields by their full IRI, and the browser looks those up in the store.

## Tabs

| Tab | What it shows | Where it comes from |
|---|---|---|
| Overview | The source and how to read its data (literals written as in Turtle), when the document was last edited (`tss:modifiedAt`), the fields table (role, datatype, unit, observed property, range, ...), the stored summaries of the fields, every other statement in the document, the number of embedded points, and the document as it was sent | The store; the point count from `rdf.points` (App.jsx reads the points once with `readPoints`, which calls `liftPoints`) |
| Series inspector | Pick a field: each of its properties, marked as stated on the field, on its source, or missing; its stored summary; the source's own statements; the Turtle of the field and its source; the first 10 rows of the data | The store. Rows: `GET /rows` for CSV and Parquet; for embedded points, `rdf.points` (read in the browser, no request) |
| SHACL | Conformance with the default shapes (picked by the backend from `tss:embedded`) or uploaded shapes: conforms or not, counts per severity, one row per result, and the report as Turtle | The backend's report (Turtle), parsed in the browser; labels of focus nodes from the store |
| RDF generation | Observations and triples per field, a preview of the first 200 observations, and a download of the whole graph | `POST /rdf` (counts and links, JSON); the preview fetched and parsed in the browser; field names from the store |
| Files | External source: the first lines of `tss:dataFile` (Parquet as CSV text) and a download of the original file. Embedded source: the `tss:points` literal, the points read as RDF (a table, and the triples as Turtle), and a warning for undeclared keys. Both: the metadata document as it was sent | `GET /data` (JSON) for data files; the store and `rdf.points` for points |
| Query | Pick a field: time, value and column filters, summary, gaps, and hourly/daily aggregation for numeric fields; "Check the stored summary" puts the field's `tss:summary` next to the same numbers computed from the data now, marked same or different | `/filter-columns` and `/query` (JSON); time window, unit, datatype and the stored summary from the store |
| SQL (DuckDB) | One data table with a column per field, plus the `source`, `fields` and `summaries` metadata tables (IRIs in full), example queries and a list of the tables | `/sql/tables` and `POST /sql` (JSON); what each column means, and the labels in the example names, from the store |

The `tss:dataFile` IRI is resolved against the document's IRI and shown as text. `data/` is not served to the browser (the factory data is NDA-derived), so data previews and downloads go through the `/api` routes.

## Stored summaries (`tss:summary`)

A field may carry a `tss:summary` node with numbers computed from the data when the metadata was made: the window (`tss:from`, `tss:until`), `tss:missingValuePolicy`, `tss:count`, `tss:missing`, `tss:firstTime`, `tss:lastTime`, for numeric fields `tss:observedMinimum`, `tss:observedMaximum`, `tss:mean` and `tss:median`, and `prov:generatedAtTime`. Every attribute is optional. The document says when it was last edited with `<> tss:modifiedAt`.

The summaries are not refreshed by themselves, so the app can check them against the data:

- The Query tab's "Check the stored summary" asks `/query` for the field's readings in the summary's window (no filters) and compares count, empty values, first and last time, and for numbers minimum, maximum, mean (to 3 decimals) and median.
- The SQL box has a `summaries` table (one row per field with a summary; `source` has a `modified_at` column) and three example queries: the stored summaries; one series' stored summary next to the same numbers computed with SQL; and "Is every stored summary still up to date?", which recomputes count, empty values and first/last time for every column in its summary's window.

## Embedded points (`tss:points`)

The points are never read as plain JSON. Python (`lift_points` in `code/TSS2RDF/tss2rdf.py`, used by the backend, the converter and the SHACL command line) and JavaScript (`liftPoints` in `frontend/src/tss.js`) follow the same rule:

- The JSON-LD context is derived from the fields: each field of the source with a `tss:columnName` maps that key to `{"@id": <field IRI>, "@type": <its tss:datatype>}`. An `rdf:langString` field gets `"@language": <its tss:language>` instead of `"@type"`, and a field without `tss:datatype` only `"@id"`. The context also has `"@vocab": "urn:tss:unmapped:"`.
- The literal's text is parsed by a JSON-LD parser (rdflib, rdf-parse) with that context given out of band. Each point becomes a blank node, each value a triple with the field's IRI as predicate and a typed literal. Blank nodes have no order, so points are put in order by their `tss:timeField` value.
- Values should be JSON strings (`"ec20": "4797.72"`). `""`, `null` or a missing key means no value (no triple), like an empty CSV cell.
- No key may start with `@` (`"@context"`, `"@id"`, also written `"@id"`): such points are refused before parsing (SHACL S6, 400 on the data routes, an error message in the browser).
- A key that is no field's column name (an undeclared key) becomes a predicate under `urn:tss:unmapped:`. Tables and the converter ignore it; SHACL reports it as a warning (P6), and the Files tab counts it.
- Both lifts keep the exact text of a JSON string: `"...Z"` stays `...Z`, and a wrong text such as `" 12 "` for an `xsd:double` stays as it is, so SHACL can report it (rdflib's literal normalisation is switched off during the lift). JSON numbers lose their text: rdflib gives `"4797.72"`, rdf-parse `"4.79772E3"`. A key that can't be an IRI (`"Some Key"`) is percent-encoded by Python and dropped by rdf-parse; only P6 sees the difference.
- Known limit: JSON-LD skips array items that are not objects (`[ {...}, 42 ]`), so no check sees them.

## SHACL

The backend picks the default shapes from the source's `tss:embedded` (`default_shapes` in `code/shacl_report/shacl_report.py`, the one validation that both the SHACL tab and the command line run). After a run, the SHACL tab names the file from the report's `dcterms:source`:

| TSS declaration | Default shapes |
|---|---|
| `tss:embedded false` (a CSV or Parquet `tss:dataFile`) | `demo 7/SHACL/external_shapes.ttl` |
| `tss:embedded true` (the readings in `tss:points`) | `demo 7/SHACL/embedded_shapes.ttl` |
| missing or not a boolean | none: the tab asks for custom shapes |

`external_shapes.ttl` checks the metadata only (S1-S15, F1-F8), not the data file. `embedded_shapes.ttl` checks the metadata (S1-S12, F1-F8, and the F9 warning: some point has a value for the field) together with the lifted points. Its SPARQL checks join each point with the field metadata:

- P1: every point has a time (a value for the `tss:timeField`);
- P2: every time is in `[tss:from, tss:until)`;
- P3: no two points have the same `tss:key` value(s);
- P4: every value is a literal with its field's `tss:datatype`, in a valid lexical form (`xsd:dateTime`, `xsd:dateTimeStamp` (with a timezone), `xsd:date` (a timezone is allowed), `xsd:double`, `xsd:float`, `xsd:decimal`, the integer types and `xsd:boolean` are checked);
- P5: numbers are within the field's `tss:minimum` and `tss:maximum`;
- P6 (warning): every key of a point is a field's `tss:columnName`.

When `tss:points` can't be read as JSON-LD at all, the backend adds an S6 violation to the report itself, because SHACL can't look inside the literal. Violations fail conformance; warnings are shown without failing it. Known limit: P3 compares RDF terms, so one instant written as `...Z` and as `...+00:00` is not a duplicate. The header of each shapes file lists every check.

To use your own shapes, choose a `.ttl` file (up to 5 MB). The browser parses it first, so a Turtle error shows at once, then sends the file as the request body with `Content-Type: text/turtle`. Uploaded shapes are used for that run only and are not saved. **Use default shapes** goes back to the bundled ones.

The same validation, with the same lift and S6 result, runs from the command line (from `demo 7/`):

```powershell
python code/shacl_report/shacl_report.py "metadata endpoint/waterinfo_289441042_tss2_points_embedded.ttl" --out report.ttl
```

## RDF generation

**Generate RDF** runs the shared converter `code/TSS2RDF/tss2rdf.py` (through `backend/rdf_generation.py`) on the CSV or Parquet `tss:dataFile`, or on the lifted points. Every row gives one `sosa:Observation` for each field with a `tss:observedProperty` (except the time field), with the field's observed property, sensor, feature of interest and unit, the row's time as `sosa:resultTime` and the value as `sosa:hasSimpleResult`, typed with the field's `tss:datatype`. Only rows inside `[tss:from, tss:until)` are used; an empty value gives no observation; a value that does not fit its datatype stops generation.

rdflib writes all of it: the whole graph as N-Triples (`observations.nt`, in batches of 10,000 observations, so memory stays bounded) and the first 200 observations as a Turtle preview. N-Triples is about twice the size of Turtle: the grootdok CSV gives 189,706 observations in 112 MB. No SHACL validation is done before generating. See `code/TSS2RDF/README.md` for all rules.

## API

All routes are served on port 8001; Vite proxies `/api` and `/files` to it. The interactive API documentation is at <http://localhost:8001/docs>. `{id}` is a catalogue id (`dcterms:identifier`): a local file name without `.ttl`, or `online-1`, `online-2`, ...

The code, in `backend/` (the docstring of `main.py` lists every module with one line each):

- `main.py` makes the FastAPI app and adds the routes of the five `*_routes.py` modules (one `APIRouter` each): `catalog_routes.py`, `rdf_routes.py`, `shacl_routes.py`, `data_routes.py` and `sql_routes.py`.
- What the routes share: `paths.py` (folders and addresses), `files.py` (local or online files), `online_registry.py` (the online endpoints), `documents.py` (reading the RDF documents with the browser's base IRI), `source_reading.py`, `value_reading.py` and `data_reading.py` (the source, one value, the data), `query_helpers.py` (the query form's summary, gaps and hourly/daily values) and `sql_box.py`.
- `rdf_generation.py` is the only door to the shared code in `code/`: the converter (`TSS2RDF/tss2rdf.py`) and the SHACL command line (`shacl_report/shacl_report.py`), whose validation the SHACL tab runs.

RDF answers (Turtle, `text/turtle; charset=utf-8`, unless noted):

| Route | Answer |
|---|---|
| `GET /files/metadata%20endpoint/<id>.ttl` | A local metadata endpoint, exactly as the file is |
| `GET /files/SHACL/<name>.ttl` | The bundled shapes |
| `GET /api/catalog` | The DCAT catalogue: per endpoint a `dcat:Dataset` with `dcterms:identifier`, the source's `rdfs:label`, and a `dcat:distribution` with `dcat:downloadURL` and `dcat:mediaType`. A local dataset's IRI is its download URL; an online dataset's IRI is its registered URL, and it is downloaded through `/document`. Online labels come from the registry, so the catalogue downloads nothing |
| `POST /api/endpoints/online?url=<URL>` | Fetches and parses the URL, checks that it has exactly one `tss:Source`, and registers it as the next `online-N`; answers with the updated catalogue |
| `DELETE /api/endpoints/{id}` | Removes an online endpoint; answers with the updated catalogue |
| `GET /api/endpoints/{id}/document` | The endpoint's document as it is, with its own media type (an online one is fetched by the backend, so the browser needs no CORS; `Content-Location` gives its registered URL, the base IRI the browser and the backend read it with) |
| `GET /api/endpoints/{id}/shacl` | The `sh:ValidationReport` for the default shapes, with `dcterms:source` naming the shapes file |
| `POST /api/endpoints/{id}/shacl` | The same, for the shapes file sent as the body (its RDF media type as `Content-Type`, not JSON-LD; untyped is read as Turtle) |
| `GET /api/endpoints/{id}/rdf/preview/{token}` | The Turtle preview of generated observations |
| `GET /api/endpoints/{id}/rdf/download/{token}` | The whole generated graph, `application/n-triples`, saved as `observations.nt` |

JSON answers:

| Route | Answer |
|---|---|
| `POST /api/endpoints/{id}/rdf` | `observations`, `triples`, `series` (`{field, observations}` per field IRI), `previewUrl`, `previewObservations`, `previewTruncated`, `bytes`, `downloadUrl`, `filename` |
| `GET /api/endpoints/{id}/rows?limit=10` | `{columns: [field IRIs], rows: [[text or null]]}`: the first rows of the data |
| `GET /api/endpoints/{id}/data?lines=20` | `{found, note, text}`: the first lines of the data file (Parquet as CSV text) |
| `GET /api/endpoints/{id}/data/download` | The original CSV or Parquet file (an attachment, not JSON) |
| `GET /api/endpoints/{id}/filter-columns?field=<IRI>` | `[{field, values}]`: the other fields with 2 to 30 different values |
| `GET /api/endpoints/{id}/query?field=<IRI>` | `resample`, `aggregate`, `filterValue`, `summary` (`count`, `empty`, `mean`, `median`, `min`, `max`, `firstTime`, `lastTime`), `gaps`, `totalRows`, `limitReached`, `readings`. Parameters: `start`, `end`, `min_value`, `max_value`, `filterField` (an IRI), `filter_value`, `resample` (`hour`, `day`), `aggregate` (`mean`, `min`, `max`), `limit` (up to 5000) |
| `GET /api/endpoints/{id}/sql/tables` | The data table (`name`, `error`, `columns` as `{name, type, field}`), `metadataTables`, `examples` (`{title, sql, field}`), `maxRows`, `maxSeconds` |
| `POST /api/endpoints/{id}/sql` | Body `{"sql": "SELECT ..."}`; answers `columns`, `rows`, `moreRows`, `seconds`, `skipped` |

Errors are always JSON `{"detail": "..."}` (FastAPI's own 422 has a list as `detail`); a crash answers 500 with the error. Data cells must have the XSD form of their field's datatype, as SHACL's P4 wants (a time like `2021-03-03T23:45:00Z`, a number like `4797.72`): `/query` refuses other texts, and the SQL box skips a data table that has them, with the reason. SQL permits one reading statement, up to 1000 returned rows, about 5 MB of values and 30 seconds; external access is disabled after loading.

Removed in demo 7, because the browser reads the RDF instead: `GET /api/`, `GET /api/endpoints`, `GET /api/endpoints/{id}`, `GET /api/endpoints/{id}/series/metadata`, `GET /api/endpoints/{id}/points`, and the JSON body and answers of the SHACL routes. The online registry moved from `online_endpoints.json` to `online_endpoints.ttl`.

## Running

From the repository root:

```powershell
cd "tech demo/demo 7/code/webapp/backend"
python -m pip install -r requirements.txt
python -m uvicorn main:app --port 8001
```

In another terminal:

```powershell
cd "tech demo/demo 7/code/webapp/frontend"
npm install
npm run dev
```

Open <http://localhost:5174>. Vite proxies `/api` and `/files` to port 8001 without changing the `Host` header, so the IRIs in the catalogue use `localhost:5174`. Alternatively, run `npm run build` in the frontend folder and open <http://localhost:8001>; the backend serves `frontend/dist` when present. Demo 6 uses the same two ports, so run one demo at a time.

## Security

Run the backend for yourself only, on your own computer. It is not safe to share:

- **It fetches any address it is given.** The backend downloads every http(s) address that a registered online endpoint names (the document, and its `tss:dataFile`), including `localhost`, `127.0.0.1` and addresses on your private network, and it passes the answer on through `/document`, `/data`, `/data/download`, `/rows`, `/query` and the SQL box.
- **Anyone who can reach port 8001 can register an online endpoint** (`POST /api/endpoints/online` has no login), and so make the backend fetch such addresses for them.
- **Never use `backend/tunnel.py` with this backend.** It puts port 8001 on the public internet (ngrok); then anyone could read pages and files that only your computer can reach.

What the backend does guard against: an online document can't make it read a local file. Its `tss:dataFile` must be a web address, and only files in `demo 7/data/` are read for local endpoints (after `..` is resolved). Online documents and uploaded shapes can't be JSON-LD, because a JSON-LD `@context` can name another address or a local file, which rdflib would download or read while parsing. (The embedded points are still read as JSON-LD, but with a context made from the fields, and a key that starts with `@` is refused.)

## Verification

```powershell
# From backend/ (109 tests, a few minutes)
python -B -m unittest -v
# From code/TSS2RDF/
python -B -m unittest -v test_tss2rdf
# From frontend/
npm run lint
npm run build
```

The backend tests cover the catalogue, `/files` and the online registry (`test_rdf_documents`), SHACL with both shapes files (`test_embedded_shacl`, `test_external_shacl`), RDF generation, the file previews, both storage profiles with their query and SQL routes (`test_storage_profiles`), answers that stay the same when the metadata uses other prefixes or none (`test_prefix_independence`), field IRIs that are the same as the browser's for a document with relative IRIs, local and online (`test_base_iri`), and what online documents and uploads may not do: read a local file through `tss:dataFile` or `..`, or be JSON-LD (`test_untrusted_documents`). `npm run build` prints Vite's warning about a chunk over 500 kB (rdf-parse and its parsers); that is expected.

## ldfetch

[ldfetch](https://github.com/pietercolpaert/ldfetch), which follows links between RDF documents, was evaluated next to rdf-parse. The app uses rdf-parse: it is the production-grade parser, it bundles with Vite and runs in the browser without polyfills, and `loadRdf` checks each answer's status itself. ldfetch 1.3.6 is a 180 MB install (616 packages), ends a 404 with exit code 0 and no triples (only `404` on stderr), and, used as a library in a long-running process, keeps a page without `Cache-Control` for about 69 days (it gives milliseconds to a cache that counts seconds). It was not tried with Vite.

It is still a handy command-line tool to look at the RDF. These commands were checked against this backend from a scratch install; following `dcat:dataset` from the catalogue gives the same 497 triples as rdflib (the catalogue plus the five local documents):

```powershell
# in an empty folder
npm install ldfetch@1.3.6
# one metadata endpoint (TriG)
npx ldfetch "http://localhost:8001/files/metadata%20endpoint/waterinfo_289441042_tss2_points_embedded.ttl"
# the catalogue and every document it lists (N-Quads; an online one from its own URL)
npx ldfetch --format nquads -p "http://www.w3.org/ns/dcat#dataset" "http://localhost:8001/api/catalog"
```
