# TSS2 Dashboard (Demo 7)

The dashboard shows TSS2 metadata endpoints: the bundled Turtle files in `demo 7/metadata endpoint/`, and your own endpoints, uploaded from your computer or added by web address (see [Your own endpoints](#your-own-endpoints)). The browser parses each RDF document with [rdf-parse](https://github.com/rubensworks/rdf-parse.js) into an N3.js Store (`frontend/src/rdf.js`). The helpers in `frontend/src/tss.js` ask that store questions by full IRI, and every metadata fact a tab shows comes from it. For the bundled endpoints, the FastAPI backend serves the documents, reads the data files and does the computing: rdflib for RDF, pyshacl for SHACL, DuckDB for SQL and Parquet. Your own endpoints never reach the backend: the browser does the same computing in the tab (DuckDB-WASM, pyshacl in Pyodide, ports of the data routes), so their documents and data stay in the browser.

## Storage profiles

Each metadata endpoint describes one `tss:Source` with its columns as `tss:Field` nodes (column name, datatype, unit, observed property, ...).

- `tss:embedded false`: the data is in an external file, `tss:dataFile`, read as `tss:format` says: `tss:CSV` (with `tss:delimiter`, default `,`; `tss:header`, default `true`; `tss:encoding`, default `utf-8`) or `tss:Parquet`.
- `tss:embedded true`: the data is in `tss:points`, an `rdf:JSON` literal holding a JSON array of objects, one per point, keyed by the fields' `tss:columnName`. It is read as RDF (see [Embedded points](#embedded-points-tsspoints)).

The source's `tss:timeField` names the time field. Its time window includes `tss:from` and excludes `tss:until`. Every source states `tss:committedUntil`: the time of the last write to the data (a write time, not a bound of the window). There is no delimiter guessing. Terms are compared by their full IRI (`https://w3id.org/tss#CSV`), never by a prefixed name, so a document may use any prefixes.

## What is RDF and what is not

| What | Syntax | Read with |
|---|---|---|
| Bundled metadata endpoints (`/files/metadata%20endpoint/<id>.ttl`) | Turtle | rdf-parse in the browser, rdflib in the backend, both with the same base IRI |
| Your own metadata endpoints (uploaded, or fetched by the browser) | Turtle, N-Triples, N3 or RDF/XML (not JSON-LD, see [Security](#security)) | rdf-parse in the browser; for SHACL, rdflib inside the browser (Pyodide). Never the backend |
| Catalogue (`GET /api/catalog`) | Turtle (DCAT) | rdf-parse (`readCatalog` in `tss.js`) |
| SHACL shapes (`/files/SHACL/*.ttl`) and validation reports | Turtle | pyshacl and rdflib; the browser parses the report with rdf-parse |
| Generated observations | N-Triples download and Turtle preview, written by rdflib (bundled endpoints) or by n3 in a Web Worker (your own endpoints) | the browser parses the preview with rdf-parse |
| Embedded points (`tss:points`) | an `rdf:JSON` literal inside the metadata | a JSON-LD parser (rdflib, rdf-parse) with a context made from the fields |
| Data files (`tss:dataFile`) | CSV or Parquet: raw data, described by the RDF | as the `tss:format` IRI says: the backend (Python's csv module, DuckDB) for bundled endpoints; the browser (`src/browser/csvFile.js` and `dataReading.js` for CSV, DuckDB-WASM for Parquet and SQL) for your own |
| Computed results and errors | JSON | `response.json()` |

JSON is used only for computed tabular results (rows, previews, filter values, readings, summaries, SQL results, counts) and for error messages (`{"detail": "..."}`). No code reads RDF as JSON (no `JSON.parse` or `json.loads` of RDF). The JSON answers don't repeat what the metadata says (labels, units, datatypes, formats, column names, paths): they name fields by their full IRI, and the browser looks those up in the store.

## Tabs

| Tab | What it shows | Where it comes from |
|---|---|---|
| Overview | The source and how to read its data (literals written as in Turtle), when the source's metadata was last edited (`tss:modifiedAt` on the `tss:Source`; for an older document that states it on itself, `<> tss:modifiedAt`), the fields table (role, datatype, unit, observed property, range, ...), the stored summaries of the fields, every other statement in the document, the number of embedded points, and the document as it was sent | The store; the point count from `rdf.points` (App.jsx reads the points once with `readPoints`, which calls `liftPoints`) |
| Series inspector | Pick a field: each of its properties, marked as stated on the field, on its source, or missing; its stored summary; the source's own statements; the Turtle of the field and its source; the first 10 rows of the data | The store. Rows: `GET /rows` for CSV and Parquet; for embedded points, `rdf.points` (read in the browser, no request) |
| SHACL | Conformance with the default shapes (picked by the backend from `tss:embedded`) or uploaded shapes: conforms or not, counts per severity, one row per result, and the report as Turtle | The backend's report (Turtle), parsed in the browser; labels of focus nodes from the store |
| RDF generation | Observations and triples per field, a preview of the first 200 observations, and a download of the whole graph | `POST /rdf` (counts and links, JSON); the preview fetched and parsed in the browser; field names from the store |
| Files | External source: the first lines of `tss:dataFile` (Parquet as CSV text) and a download of the original file. Embedded source: the `tss:points` literal, the points read as RDF (a table, and the triples as Turtle), and a warning for undeclared keys. Both: the metadata document as it was sent | `GET /data` (JSON) for data files; the store and `rdf.points` for points |
| Query | Pick a field: time, value and column filters, summary, gaps, and hourly/daily aggregation for numeric fields; "Check the stored summary" puts the field's `tss:summary` next to the same numbers computed from the data now, marked same or different | `/filter-columns` and `/query` (JSON); time window, unit, datatype and the stored summary from the store |
| SQL (DuckDB) | One data table with a column per field, plus the `source`, `fields` and `summaries` metadata tables (IRIs in full), example queries and a list of the tables | `/sql/tables` and `POST /sql` (JSON); what each column means, and the labels in the example names, from the store |
| Add data | Only for a bundled endpoint whose source states `tss:embedded true` and `tss:mutable true`: a form with one input per field (the time first), the current window and number of points; after a submit, the SPARQL updates that ran, the SHACL verdict and report, and the new window and count (see [Adding a point](#adding-a-point-to-an-embedded-endpoint)) | The store (fields, labels, datatypes, window); `POST /points` (JSON, with the SHACL report as Turtle, parsed in the browser) |

The `tss:dataFile` IRI is resolved against the document's IRI and shown as text. `data/` is not served to the browser (the factory data is NDA-derived), so for the bundled endpoints data previews and downloads go through the `/api` routes. Every tab asks for data through one object, `makeDataApi` in `frontend/src/dataApi.js`: for a bundled endpoint its methods call the routes above, for your own endpoint they compute the same JSON answers in the browser (next section). A tab is drawn the first time you open it and then kept (hidden), so its results stay, and nothing that needs the data starts before you open a tab that needs it.

## Your own endpoints

Under the drop down, **Add your own endpoint** adds a metadata document of your own (exactly one `tss:Source`), from your computer or by its web address. Everything about it runs in your browser tab: the browser reads the document, finds the data, and computes every tab's answers itself. The rule: its document and its data (the data file, the embedded points, and everything computed from them) are never sent to, stored on, or passed through the demo's server.

**How it fits together.** Start reading at `frontend/src/dataApi.js`, the switch between the two kinds of endpoint:

1. `AddEndpointForm.jsx`: the form, **By web address** or **From your computer** (the metadata file, and if you like its data files).
2. `userEndpoints.js`: reads the document (`fetchRdfDocument` or `readRdfFile` in `rdf.js`), checks that it has one `tss:Source`, and makes the endpoint (`user-1`, `user-2`, ...).
3. `App.jsx`: keeps your endpoints in its state (and only their web addresses in `sessionStorage`), lists them after the bundled ones, marked "(in this tab)", with **Remove**, and shows the note "This endpoint and its data stay in this browser tab. Nothing is sent to the demo's server."
4. `dataApi.js`: `makeDataApi` gives every tab the same object; `endpoint.user` picks the browser, so its methods call the modules in `src/browser/` instead of the `/api` routes.
5. `src/browser/`: `dataSource.js` finds the data file (`DataFileCard.jsx`, above the data tabs, asks you for it when it is missing); the other modules compute the answers:

| Tab | Computed by |
|---|---|
| Files, Series inspector (first rows), Query | `query.js`: ports of `GET /data`, `/rows`, `/filter-columns` and `/query` (with "Check the stored summary") |
| SQL (DuckDB) | `duck.js` and `sqlBox.js`: DuckDB-WASM in a Web Worker, with the same tables, examples, limits and lock-down as the backend |
| RDF generation | `observations.js` and `observationsWorker.js`: the rules of `tss2rdf.py`, in a Web Worker with n3; the download is a `blob:` address |
| SHACL | `shacl.js` and `shaclWorker.js`: pyshacl in Pyodide (Python in WebAssembly) in a Web Worker, running the server's own `shacl_report.py` and `tss2rdf.py`; both default shapes files are part of the app (imported with `?raw`), so nothing is fetched for them |

No `/api` route is ever called for your endpoint. The only requests to the demo's server are for the app's static files (the page and its scripts, DuckDB's WebAssembly and extensions and Pyodide's files under `/vendor/`); none of them carries your metadata or data. (The default SHACL shapes are inside the SHACL worker's script, so no request tells the server which shapes your metadata needs; the report still names them by their address, `/files/SHACL/<file>`, as the server's report does.) The dev server does not receive the page's console output either (`forwardConsole: false` in `vite.config.js`; Vite turns it on by itself when a coding agent starts it), because an error message could quote your metadata or data.

**The same rules in two languages.** Except for SHACL, the browser modules are JavaScript ports of the backend's Python, written to give the same JSON, character for character. Nothing in the repository checks that: when you change one side, change the other too.

| Browser file (`frontend/src/browser/`) | The Python it ports (change both together) |
|---|---|
| `query.js` | `backend/data_routes.py` (`GET /data`, `/rows`, `/filter-columns`, `/query`) |
| `queryHelpers.js` | `backend/query_helpers.py` (the summary, the gaps, the hourly and daily values) |
| `dataReading.js` | `backend/data_reading.py` (the rows of a CSV file, a Parquet file or the embedded points) |
| `sourceReading.js` | `backend/source_reading.py`, with `column_index` and `embedded_mode` of `tss2rdf.py` |
| `values.js` | `backend/value_reading.py`, with `parse_time`, `parse_number` and `time_text` of `query_helpers.py`, `LEXICAL_FORMS` of `tss2rdf.py` and `XSD_TO_DUCKDB` of `sql_box.py` |
| `csvFile.js` | `backend/files.py` (`read_lines`) and Python's own `csv` reader |
| `python.js` | no backend module: how Python and rdflib write, round, add, read and sort values (`repr`, `round`, `sum`, `int`, rdflib's names in messages), so the answers have the same text |
| `duck.js` | `backend/sql_box.py` (`open_database`, `column_expression`, `load_table`, `run_query`) and `sql_routes.py` (`sql_table`, `run_sql`) |
| `arrowValues.js` | `backend/sql_box.py` (`to_json_value`), `data_reading.py` (`cell_text`) and `tss2rdf.py` (`cell_text`) |
| `sqlCheck.js` | `backend/sql_box.py` (`run_query`: exactly one reading query) |
| `sqlBox.js` | `backend/sql_box.py` (`example_queries`, the limits) and `sql_routes.py` (`GET /sql/tables`) |
| `observationsWorker.js` | `code/TSS2RDF/tss2rdf.py` (`generate`) |
| `shacl.js`, `shaclWorker.js` | none: they run the server's own `shacl_report.py` and `tss2rdf.py` in Pyodide, with the server's own `SHACL/*_shapes.ttl` (all imported with `?raw`), so there is only one copy |

(`liftPoints` in `src/tss.js` and `lift_points` in `tss2rdf.py` are such a pair too, see [Embedded points](#embedded-points-tsspoints).)

**The data file** (for `tss:embedded false`; embedded points need none), found by `frontend/src/browser/dataSource.js`:

1. a file you picked in a data tab (**Pick <file name> from your computer**, or **Pick another file** to replace the one in use);
2. else a data file you uploaded with the metadata, matched by name: the last part of the `tss:dataFile` IRI, decoded (`waterinfo_289423042.csv` for `<../data/waterinfo_289423042.csv>`). Upper and lower case do not matter, and when the last part is a word like `content`, `download`, `raw`, `file` or `data` (Zenodo's API: `.../files/x.csv/content`), the part before it is the name;
3. else, for an `http://` or `https://` IRI, your browser downloads it, once per endpoint;
4. else (a `file:` IRI, or a download that failed: a host without CORS, a 404, an `http://` file on an `https://` page) the data tabs say why and show **Pick <file name> from your computer**. When you uploaded exactly one data file and its name matched nothing, they also offer **Use <that file>**. After you choose a file, the tabs work.

A file from your computer (1 and 2) is checked before it is used: for a CSV file with `tss:header true` (the default), its first line must name every field's `tss:columnName` (only the first 64 kB of the file are read for this). When it does not, the data tabs name the missing columns and offer **Pick another file** or **Use <file> anyway**; the file is never used without a word. A Parquet file's columns are not checked beforehand (only DuckDB reads Parquet here); a picked file with another name than the metadata's gets a warning.

The data tabs and the Files tab say where the data came from ("Data file downloaded by your browser from ..." or "Data file picked from your computer: ..."), and the download icon gives the browser's own copy.

**Storage: this tab only.** Your endpoints, their files and everything computed from them stay in the tab's memory. Only the web addresses of the endpoints you added by address are kept, in `sessionStorage` (the tab's own storage, emptied when the tab closes), each address once, so a reload of the page adds them again, downloading them again (an address you add with the form while that is still under way is not added twice). Uploaded files are not kept anywhere: a reload forgets them, as the form says. Nothing goes into `localStorage` or IndexedDB. Leaving an endpoint stops its DuckDB and its RDF generation and frees its `blob:` addresses, also when a download or a start was still under way (what finishes later is thrown away); **Remove** also forgets its downloaded data file.

**Details.**

- *By web address*: your browser downloads the document itself: `fetch` with `cache: 'no-store'` and only one header, `Accept: text/turtle, application/rdf+xml;q=0.8, application/n-triples;q=0.8, text/n3;q=0.5, */*;q=0.1` (short enough to need no CORS preflight). Redirects are followed, and the address you typed is the document's base IRI. The `Content-Type` gives the syntax: Turtle (also `application/x-turtle`), N-Triples, N3 or RDF/XML; of a doubled value such as Zenodo's `application/octet-stream, application/octet-stream` the first one counts. When the server does not say (no type, `text/plain` or `application/octet-stream`, as raw.githubusercontent.com sends every file), the end of the address decides, with the same rules as for an upload (`.ttl`, `.nt`, `.n3`, `.rdf`/`.owl`/`.xml`; `.jsonld`/`.json` are refused), and Turtle when it ends in none of them. JSON-LD and HTML pages are refused.
- *From your computer*: the metadata file may be `.ttl`, `.nt`, `.n3`, `.rdf`, `.owl` or `.xml` (not JSON-LD). "Published at" (optional) is the document's address on the web, used as its base IRI; without it the document is read as `file:///uploaded/<file name>`.
- *CORS*: a browser may read a file from another host only when the host allows it (`Access-Control-Allow-Origin`). raw.githubusercontent.com, GitHub Pages, gists, Hugging Face, Zenodo's API, GitLab's API, figshare and dl.dropboxusercontent.com do; github.com `/raw/` and `?raw=true` links, GitHub releases, GitLab `/-/raw/` links, Zenodo `?download=1`, Google Drive and www.dropbox.com share links do not. When a host refuses, the app says so ("This host does not let a browser download the file ..."): download the file and add it from your computer (a data file: pick it in the data tab). There is no server relay.
- *Costs*: DuckDB-WASM starts in about 3 seconds and uses about 400 MB of memory while an endpoint is open. The first SHACL check in a tab loads Python (about 9 MB downloaded from the demo's own server, a few seconds; `public/vendor/pyodide/` holds 16 MB on disk; the tab says "Loading Python for the SHACL check (first time in this tab)…"); later checks take under a second. Generated RDF is a Blob in the tab's memory (the 10 MB grootdok CSV gives 112 MB of N-Triples).

DuckDB's extensions and Pyodide's packages do not come with `npm install`: run `npm run fetch-browser-assets` once after it (see [Running](#running)).

## Stored summaries (`tss:summary`)

A field may carry a `tss:summary` node with numbers computed from the data when the metadata was made: the window (`tss:from`, `tss:until`), `tss:missingValuePolicy`, `tss:count`, `tss:missing`, `tss:firstTime`, `tss:lastTime`, for numeric fields `tss:observedMinimum`, `tss:observedMaximum`, `tss:mean` and `tss:median`, and `prov:generatedAtTime`. Every attribute is optional. The source says when its metadata was last edited with `tss:modifiedAt` (on the `tss:Source`).

The summaries are not refreshed by themselves (only the **Add data** tab recomputes them, when it adds a point: see [Adding a point](#adding-a-point-to-an-embedded-endpoint)), so the app can check them against the data:

- The Query tab's "Check the stored summary" asks `/query` for the field's readings in the summary's window (no filters) and compares count, empty values, first and last time, and for numbers minimum, maximum, mean (to 3 decimals) and median.
- The SQL box has a `summaries` table (one row per field with a summary; `source` has a `modified_at` column, the source's `tss:modifiedAt`) and three example queries: the stored summaries; one series' stored summary next to the same numbers computed with SQL; and "Is every stored summary still up to date?", which recomputes count, empty values and first/last time for every column in its summary's window.

## Embedded points (`tss:points`)

The points are never read as plain JSON. Python (`lift_points` in `code/TSS2RDF/tss2rdf.py`, used by the backend, the converter and the SHACL command line) and JavaScript (`liftPoints` in `frontend/src/tss.js`) follow the same rule:

- The JSON-LD context is derived from the fields: each field of the source with a `tss:columnName` maps that key to `{"@id": <field IRI>, "@type": <its tss:datatype>}`. An `rdf:langString` field gets `"@language": <its tss:language>` instead of `"@type"`, and a field without `tss:datatype` only `"@id"`. The context also has `"@vocab": "urn:tss:unmapped:"`.
- The literal's text is parsed by a JSON-LD parser (rdflib, rdf-parse) with that context given out of band. Each point becomes a blank node, each value a triple with the field's IRI as predicate and a typed literal. Blank nodes have no order, so points are put in order by their `tss:timeField` value.
- Values should be JSON strings (`"ec20": "4797.72"`). `""`, `null` or a missing key means no value (no triple), like an empty CSV cell.
- No key may start with `@` (`"@context"`, `"@id"`, also written `"@id"`): such points are refused before parsing (SHACL S6, 400 on the data routes, an error message in the browser).
- A key that is no field's column name (an undeclared key) becomes a predicate under `urn:tss:unmapped:`. Tables and the converter ignore it; SHACL reports it as a warning (P6), and the Files tab counts it.
- Both lifts keep the exact text of a JSON string: `"...Z"` stays `...Z`, and a wrong text such as `" 12 "` for an `xsd:double` stays as it is, so SHACL can report it (rdflib's literal normalisation is switched off during the lift). JSON numbers lose their text: rdflib gives `"4797.72"`, rdf-parse `"4.79772E3"`. A key that can't be an IRI (`"Some Key"`) is percent-encoded by Python and dropped by rdf-parse; only P6 sees the difference.
- Known limit: JSON-LD skips array items that are not objects (`[ {...}, 42 ]`), so no check sees them.

## Adding a point to an embedded endpoint

The **Add data** tab adds one point to the `tss:points` of a bundled endpoint, through `POST /api/endpoints/{id}/points` (`backend/points_routes.py`). It is the only route that changes a bundled file.

- **Which endpoints.** Only bundled endpoints whose `tss:Source` states `tss:embedded true` and `tss:mutable true`. `tss:mutable false` (or no `tss:mutable`) means the data never changes: the backend refuses (409), and the tab is not shown. An external source (CSV, Parquet) is refused too (400). Your own endpoints are never changed (their `api.addPoint` refuses).
- **The form.** One input per field of the source that has a `tss:columnName`, the time field first, each with its label, column name and datatype: numeric fields get a number box, the others a text box. The time is typed as an `xsd:dateTime` with a timezone (`2021-03-04T01:15:00Z`; the **now** button fills in the current UTC time). An empty input means no value: the point gets no key for it.
- **Checks before anything changes.** Every value names a field of the source by its IRI (else 400); the time is a valid `xsd:dateTime` with a timezone; every value has the XSD form of its field's datatype (`abc` for an `xsd:double`: 400); no other point has the same `tss:key` (the time, compared as a moment, so `...Z` and `...+00:00` are the same): 409.
- **The window.** `tss:from` becomes the earlier of the current `tss:from` and the earliest point; `tss:until` the later of the current `tss:until` and the newest point plus one second (`tss:until` is not in the window). So the new point always fits and the window never shrinks. A side the source does not state stays open. `tss:committedUntil` is not moved to the new point's time: like `tss:modifiedAt`, it becomes the time of the write.
- **The summaries.** Every field that has a `tss:summary` gets it recomputed from the points in the new window: its `tss:from`/`tss:until` become the source's window, `prov:generatedAtTime` becomes now, and of `tss:count`, `tss:missing`, `tss:firstTime`, `tss:lastTime` and, for numeric fields, `tss:observedMinimum`, `tss:observedMaximum`, `tss:mean` (not rounded) and `tss:median`, only those it already states are recomputed; `tss:missingValuePolicy` is kept. The source's `tss:modifiedAt` and `tss:committedUntil` become now (UTC, `...Z`).
- **SPARQL Update.** The file is parsed with rdflib with every literal's text kept exactly (`rdflib.NORMALIZE_LITERALS` off during the parse, under `NORMALIZE_LOCK`). The existing points are read as RDF (`lift_points`), the new point is added, and the new `tss:points` text is written: one JSON object per line, in time order, every value a JSON string with its text as it was. Then `graph.update` runs a `DELETE`/`INSERT` for `tss:points`, one for the source's `tss:from`, `tss:until`, `tss:modifiedAt` and `tss:committedUntil`, and one per summary; the values come with `initBindings`, so the SPARQL parser never rewrites them. The answer lists each update's text exactly as it ran, with its bindings (the `tss:points` literal shortened), and the tab shows them.
- **SHACL is the gate.** The changed graph is validated with `SHACL/embedded_shapes.ttl` by `shacl_report.validation_report` (what the SHACL tab runs: P2 window, P3 key, P4 datatypes, P5 range, ...). Not conforming (a value above `tss:maximum`, say): 422 with the report, and nothing is written; the tab shows the violations and says that nothing was saved.
- **Writing the file.** Conforming: the graph is written as Turtle by rdflib (`exact_turtle`) to a temporary file in the same folder, read back and compared with the validated graph (isomorphic), and then put in place of the file (`os.replace`, so a reader sees the old or the new file, never half of it). **The file is written again: its comments and layout are lost** (the triples stay; booleans and numbers are written in full, e.g. `"true"^^xsd:boolean`). One append runs at a time (a lock), so two at once both end up in the file. The tab then has App load the document again (`cache: 'no-store'`, as every bundled document is loaded), and every tab shows the new data.
- **Known limits.** rdflib writes absolute IRIs only, so a document with relative IRIs (`<#value>`, `<../data/x.csv>`) would change when written back: the backend reads the document a second time with another base IRI, and when any IRI differs it refuses (400). Write such a document's IRIs in full first. Points with a key that is no field's `tss:columnName` (SHACL's P6 warning) are refused too, because writing the points again would lose that key.

## SHACL

The backend picks the default shapes from the source's `tss:embedded` (`default_shapes` in `code/shacl_report/shacl_report.py`, the one validation that both the SHACL tab and the command line run; for your own endpoint the same Python runs in the browser, see [Your own endpoints](#your-own-endpoints)). After a run, the SHACL tab names the file from the report's `dcterms:source`:

| TSS declaration | Default shapes |
|---|---|
| `tss:embedded false` (a CSV or Parquet `tss:dataFile`) | `demo 7/SHACL/external_shapes.ttl` |
| `tss:embedded true` (the readings in `tss:points`) | `demo 7/SHACL/embedded_shapes.ttl` |
| missing or not a boolean | none: the tab asks for custom shapes |

For your own endpoint the browser uses the same two files, built into the app (`?raw` imports in `frontend/src/browser/shaclWorker.js`), so checking it fetches no shapes: after editing a shapes file, build the frontend again (the dev server picks up the change by itself).

`external_shapes.ttl` checks the metadata only (S1-S17, F1-F8), not the data file. `embedded_shapes.ttl` checks the metadata (S1-S12, S16-S17, F1-F8, and the F9 warning: some point has a value for the field) together with the lifted points. In both, S16 requires one `tss:modifiedAt` and S17 one `tss:committedUntil` (each an `xsd:dateTime` with a timezone). The embedded file's SPARQL checks join each point with the field metadata:

- P1: every point has a time (a value for the `tss:timeField`);
- P2: every time is in `[tss:from, tss:until)`;
- P3: no two points have the same `tss:key` value(s);
- P4: every value is a literal with its field's `tss:datatype`, in a valid lexical form (`xsd:dateTime`, `xsd:dateTimeStamp` (with a timezone), `xsd:date` (a timezone is allowed), `xsd:double`, `xsd:float`, `xsd:decimal`, the integer types and `xsd:boolean` are checked);
- P5: numbers are within the field's `tss:minimum` and `tss:maximum`;
- P6 (warning): every key of a point is a field's `tss:columnName`.

When `tss:points` can't be read as JSON-LD at all, the backend adds an S6 violation to the report itself, because SHACL can't look inside the literal. Violations fail conformance; warnings are shown without failing it. Known limit: P3 compares RDF terms, so one instant written as `...Z` and as `...+00:00` is not a duplicate. The header of each shapes file lists every check.

To use your own shapes, choose a `.ttl` file (up to 5 MB). The browser parses it first, so a Turtle error shows at once, then sends the file as the request body with `Content-Type: text/turtle` (for your own endpoint, the shapes stay in the browser too: pyshacl runs in the tab). Uploaded shapes are used for that run only and are not saved. **Use default shapes** goes back to the bundled ones.

The same validation, with the same lift and S6 result, runs from the command line (from `demo 7/`):

```powershell
python code/shacl_report/shacl_report.py "metadata endpoint/waterinfo_289441042_tss2_points_embedded.ttl" --out report.ttl
```

## RDF generation

**Generate RDF** runs the shared converter `code/TSS2RDF/tss2rdf.py` (through `backend/rdf_generation.py`) on the CSV or Parquet `tss:dataFile`, or on the lifted points. Every row gives one `sosa:Observation` for each field with a `tss:observedProperty` (except the time field), with the field's observed property, sensor, feature of interest and unit, the row's time as `sosa:resultTime` and the value as `sosa:hasSimpleResult`, typed with the field's `tss:datatype`. Only rows inside `[tss:from, tss:until)` are used; an empty value gives no observation; a value that does not fit its datatype stops generation.

rdflib writes all of it: the whole graph as N-Triples (`observations.nt`, in batches of 10,000 observations, so memory stays bounded) and the first 200 observations as a Turtle preview. N-Triples is about twice the size of Turtle: the grootdok CSV gives 189,706 observations in 112 MB. No SHACL validation is done before generating. See `code/TSS2RDF/README.md` for all rules.

The backend keeps the two files in one folder, `demo7-rdf-exports` in the system's temporary folder (`paths.RDF_EXPORT_DIR`), so no generated data is left behind: each endpoint keeps only its latest generation (a new one deletes the files of the one before, whose links then answer 404), a link only works for its own endpoint, and the folder is emptied when the backend starts and when it stops.

## API

All routes are served on port 8001; Vite proxies `/api` and `/files` to it. The interactive API documentation is at <http://localhost:8001/docs>. `{id}` is a catalogue id (`dcterms:identifier`): the file name of a bundled endpoint without `.ttl`. Any other id answers 404 `Unknown metadata endpoint: <id>`. The backend only knows the bundled endpoints: the user's own endpoints (uploaded or online) are read by the browser, and neither their documents nor their data reach the backend.

The code, in `backend/` (the docstring of `main.py` lists every module with one line each):

- `main.py` makes the FastAPI app (its `lifespan` empties the RDF export folder when the backend starts and stops) and adds the routes of the six `*_routes.py` modules (one `APIRouter` each): `catalog_routes.py`, `rdf_routes.py`, `shacl_routes.py`, `data_routes.py`, `sql_routes.py` and `points_routes.py`.
- What the routes share: `paths.py` (folders, addresses and the RDF export folder), `files.py` (the data files in `demo 7/data/`), `documents.py` (reading the bundled RDF documents, always from `paths.METADATA_DIR`, with the browser's base IRI), `source_reading.py`, `value_reading.py` and `data_reading.py` (the source, one value, the data), `query_helpers.py` (the query form's summary, gaps and hourly/daily values) and `sql_box.py`.
- `rdf_generation.py` is the only door to the shared code in `code/`: the converter (`TSS2RDF/tss2rdf.py`) and the SHACL command line (`shacl_report/shacl_report.py`), whose validation the SHACL tab runs.

RDF answers (Turtle, `text/turtle; charset=utf-8`, unless noted):

| Route | Answer |
|---|---|
| `GET /files/metadata%20endpoint/<id>.ttl` | A local metadata endpoint, exactly as the file is |
| `GET /files/SHACL/<name>.ttl` | The bundled shapes |
| `GET /api/catalog` | The DCAT catalogue of the bundled endpoints only (the `.ttl` files in `metadata endpoint/`): per endpoint a `dcat:Dataset` with `dcterms:identifier`, the source's `rdfs:label`, and a `dcat:distribution` with `dcat:downloadURL` and `dcat:mediaType` (Turtle). A dataset's IRI is its download URL, `/files/metadata%20endpoint/<id>.ttl` |
| `GET /api/endpoints/{id}/shacl` | The `sh:ValidationReport` for the default shapes, with `dcterms:source` naming the shapes file |
| `POST /api/endpoints/{id}/shacl` | The same, for the shapes file sent as the body (its RDF media type as `Content-Type`, not JSON-LD; untyped is read as Turtle) |
| `GET /api/endpoints/{id}/rdf/preview/{token}` | The Turtle preview of generated observations (404 for any token but the endpoint's latest generation) |
| `GET /api/endpoints/{id}/rdf/download/{token}` | The whole generated graph, `application/n-triples`, saved as `observations.nt` (404 as for the preview) |

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
| `POST /api/endpoints/{id}/points` | Body `{"values": {"<field IRI>": "<text>", ...}}` (the time field's IRI is needed; `""` or a missing field means no value). 200: `conforms` (true), `points`, `from`, `until`, `modifiedAt`, `sparql`, `report` (Turtle); the file was written. 422: `conforms` (false), `report`, `sparql`; nothing was written. 400, 404, 409: `{"detail": ...}`, nothing was written. Each `sparql` item is `{update, bindings: [{variable, value}]}` (see [Adding a point](#adding-a-point-to-an-embedded-endpoint)) |

Errors are always JSON `{"detail": "..."}` (FastAPI's own 422 has a list as `detail`); a crash answers 500 with the error. A `tss:dataFile` must be a file in `demo 7/data/`: a web address answers 400 (`... is a web address: this backend reads only the demo's own data files; user endpoints are read by the browser.`). Data cells must have the XSD form of their field's datatype, as SHACL's P4 wants (a time like `2021-03-03T23:45:00Z`, a number like `4797.72`): `/query` refuses other texts, and the SQL box skips a data table that has them, with the reason. SQL permits one reading statement, up to 1000 returned rows, about 5 MB of values and 30 seconds; external access is disabled after loading.

Removed in demo 7, because the browser reads the RDF instead: `GET /api/`, `GET /api/endpoints`, `GET /api/endpoints/{id}`, `GET /api/endpoints/{id}/series/metadata`, `GET /api/endpoints/{id}/points`, and the JSON body and answers of the SHACL routes.

Removed when the user's own endpoints moved to the browser: the online registry (`backend/online_endpoints.ttl`), `POST /api/endpoints/online`, `DELETE /api/endpoints/{id}` and `GET /api/endpoints/{id}/document` (they answer 404, or 405 when the built frontend is served), and every download by the backend (online documents and online data files).

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
npm run fetch-browser-assets
npm run dev
```

`npm run fetch-browser-assets` downloads what your own endpoints need in the browser and `npm install` does not bring: DuckDB's `icu`, `parquet` and `json` extensions and Pyodide's Python packages (pyshacl, rdflib, ...), into `frontend/public/vendor/`, each checked against a pinned sha256 (the folder is not in git). Run it once after `npm install`, and again after an update of `@duckdb/duckdb-wasm` or `pyodide`. The app serves these files itself, so the browser never loads code from a CDN.

Open <http://localhost:5174>. Vite proxies `/api` and `/files` to port 8001 without changing the `Host` header, so the IRIs in the catalogue use `localhost:5174`. Alternatively, run `npm run build` in the frontend folder and open <http://localhost:8001>; the backend serves `frontend/dist` when present. Demo 6 uses the same two ports, so run one demo at a time.

## Security

Run the backend for yourself only, on your own computer. It is not safe to share:

- **It has no login.** Anyone who can reach port 8001 can read every data file of the bundled endpoints through the `/api` routes, including the NDA-derived factory data, and make the backend run long SQL queries and RDF generations.
- **Never use `backend/tunnel.py` with this backend.** It puts port 8001 on the public internet (ngrok).

The backend no longer fetches any address. It serves only the bundled endpoints, reads only files in `demo 7/data/` (after `..` is resolved), and answers 400 for a `tss:dataFile` that is a web address. The user's own endpoints (uploaded or online) are read by the browser; their documents and data never reach the backend. Uploaded shapes can't be JSON-LD, because a JSON-LD `@context` can name another address or a local file, which rdflib would download or read while parsing. For the same reason the browser refuses a JSON-LD document as your own endpoint. (The embedded points are still read as JSON-LD, but with a context made from the fields, and a key that starts with `@` is refused.)

The only data the backend writes to disk is the generated RDF, in `demo7-rdf-exports` in the system's temporary folder (see [RDF generation](#rdf-generation)): one generation per endpoint, and the folder is emptied when the backend starts and stops. Besides that, it writes the file of a bundled endpoint with `tss:embedded true` and `tss:mutable true`, when a point is added to it (the **Add data** tab; anyone who can reach port 8001 can do that too). DuckDB runs in memory without a scratch folder (`temp_directory = ''`).

## Verification

```powershell
# From backend/ (135 tests, about a minute and a half)
python -B -m unittest -v
# From code/TSS2RDF/
python -B -m unittest -v test_tss2rdf
# From frontend/
npm run lint
npm run build
```

The backend tests cover the catalogue of bundled endpoints, `/files` and the removed online-endpoint routes (`test_rdf_documents`), SHACL with both shapes files (`test_embedded_shacl`, `test_external_shacl`), RDF generation and its export folder (one export per endpoint, tokens per endpoint, emptied at start and stop: `test_rdf_generation`), the file previews, both storage profiles with their query and SQL routes (`test_storage_profiles`), stored summaries and `modified_at` (`test_summaries`), adding a point to embedded points, on copies of the files in a folder of their own (`test_points`: the SPARQL Update, the window, the summaries, SHACL as the gate, the refusals, two appends at once; the real `metadata endpoint/` folder is checked to be unchanged), answers that stay the same when the metadata uses other prefixes or none (`test_prefix_independence`), field IRIs that are the same as the browser's for a document with relative IRIs (`test_base_iri`), and what the backend may not do: read a file outside `demo 7/data/` through `tss:dataFile` or `..`, download a `tss:dataFile` that is a web address, or read JSON-LD shapes (`test_untrusted_documents`). `npm run build` prints Vite's warning about a chunk over 500 kB (rdf-parse and its parsers); that is expected. These checks do not compare the browser's answers for your own endpoints with the backend's: after a change to a Python module that has a JavaScript twin (see the table in [Your own endpoints](#your-own-endpoints)), change the twin too and compare the two by hand, for example by adding a bundled endpoint's files as your own endpoint.

## ldfetch

[ldfetch](https://github.com/pietercolpaert/ldfetch), which follows links between RDF documents, was evaluated next to rdf-parse. The app uses rdf-parse: it is the production-grade parser, it bundles with Vite and runs in the browser without polyfills, and `loadRdf` checks each answer's status itself. ldfetch 1.3.6 is a 180 MB install (616 packages), ends a 404 with exit code 0 and no triples (only `404` on stderr), and, used as a library in a long-running process, keeps a page without `Cache-Control` for about 69 days (it gives milliseconds to a cache that counts seconds). It was not tried with Vite.

It is still a handy command-line tool to look at the RDF. These commands were checked against this backend from a scratch install; following `dcat:dataset` from the catalogue gives the same triples as rdflib (the catalogue plus every local document):

```powershell
# in an empty folder
npm install ldfetch@1.3.6
# one metadata endpoint (TriG)
npx ldfetch "http://localhost:8001/files/metadata%20endpoint/waterinfo_289441042_tss2_points_embedded.ttl"
# the catalogue and every document it lists (N-Quads)
npx ldfetch --format nquads -p "http://www.w3.org/ns/dcat#dataset" "http://localhost:8001/api/catalog"
```
