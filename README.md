# RDF Time Series — Demo 4

Demo 4 implements RDFTSS: a way to describe time series, their storage, and their meaning using RDF metadata. It connects individual readings to information such as the sensor, observed property, feature of interest, and unit of measurement.

The code provides a web dashboard for inspecting, validating, querying, and exporting time series, plus a standalone Python converter for generating domain RDF. The examples use water conductivity measurements and associated quality information.

## Supported data models

The storage profile is declared on a `tss:SnippetGroup` using `tss:embedded`.

| Profile | Where the readings live | How readings are interpreted |
|---|---|---|
| **Embedded** — `tss:embedded true` | JSON point arrays in each snippet's `tss:points` | The effective JSON-LD `tss:context` maps point keys to RDF predicates and datatypes. |
| **External CSV** — `tss:embedded false` | A CSV linked through `tss:dataFile`, with CSVW metadata linked through `tss:describedby` | Snippets select time/value columns by their CSVW names; CSVW describes CSV parsing and RDF mappings. |

A group contains snippets, each describing a time series, and a `tss:snippetTemplate` with shared defaults. A snippet's own attributes override template defaults. `tss:about` supplies the domain metadata attached to readings; an override replaces the entire about node.

## Dashboard features

Select a local Turtle endpoint or add an online endpoint by URL. Online endpoint addresses are saved between backend runs, and relative resource links resolve against the endpoint's location.

| Tab | Features |
|---|---|
| **Overview** | Inspect the group, template, available series, sensors, units, observed properties, and raw Turtle. |
| **Series inspector** | Examine one series and see which metadata is stated directly, inherited, or overridden. Inspect the corresponding source definitions and data previews. |
| **SHACL** | Detect the storage profile automatically, select the bundled shapes, run a conformance test, and display the full report with severity counts. Upload a custom SHACL Turtle file to use instead of the bundled shapes, or switch back to the defaults. |
| **RDF generation** | Generate domain RDF from embedded points or external CSV readings, view a Turtle preview, and download the generated graph. |
| **Files** | Preview and download external CSV/CSVW files, or select a series and inspect its embedded JSON points. |
| **CSVW validation** | Validate external CSV contents against their CSVW descriptions, including datatypes, required values, headers, and declared constraints. |
| **Query** | Filter a series by time, numeric value range, and available auxiliary columns such as quality codes. Inspect readings, summary statistics, missing-data gaps, and hourly/daily mean, minimum, or maximum aggregation. Text series support time filtering. |
| **SQL (DuckDB)** | Inspect tables and columns, use example queries, and run read-only SQL over readings and snippet metadata. External CSV files and embedded snippets are exposed as queryable tables. |

SHACL validates the original endpoint graph; CSVW validation checks the external CSV contents. SHACL violations fail conformance, while warnings remain visible without failing it. Custom shapes are used for that validation run and do not replace project files. Embedded JSON shape checks use patterns; the RDF converter also parses and checks the JSON during conversion.

## RDF generation

The dashboard and the standalone **TSS2RDF** script share the same conversion functions.

- Each reading is exported as an independent RDF node. Readings without identifiers become anonymous nodes; explicit identifiers in embedded points are preserved.
- Effective point types and about metadata are applied to readings, including sensor, unit, observed property, and feature-of-interest relationships where supplied. Referenced domain resources retain their descriptions.
- The generated graph excludes TSS vocabulary, source snippet/group nodes, and group attributes such as `tss:producedBy`. It does not synthesize collections or membership links.
- Embedded conversion expands point keys using their effective JSON-LD context, including declared literal types and nested result structures.
- External conversion uses CSVW `propertyUrl` for predicates, `datatype` or `datatype.base` for literal types, and `valueUrl` for IRI objects. Virtual columns, such as a row type mapping, are supported.
- In a CSV with multiple measured series, each series' reading receives its own value column and shared row metadata, together with that series' domain metadata.
- External conversion applies each snippet's inclusive `tss:from` and exclusive `tss:until` bounds. Embedded RDF expansion materializes the supplied point arrays without filtering them by that window.

CSVW-driven parsing uses `dialect.delimiter` when provided. If `delimiter` or the entire `dialect` is missing, it defaults to **comma**. Explicit delimiters are respected, invalid values produce errors, and delimiters are not guessed. The external RDF converter supports headerless CSVs when declared in CSVW; column names are still required for the TSS bindings.

External RDF generation streams the complete graph to disk. The dashboard previews the first **200 readings**, while its download contains every generated reading. Generated external downloads remain available until the backend restarts. Embedded exports show the entire generated graph.

The external RDF converter currently supports zero or one header row, no skipped columns, anonymous subjects rather than CSVW `aboutUrl` subject mappings, and unordered separator values. Further implementation details are in the [dashboard documentation](<tech demo/demo 4/code/webapp/README.md>) and [TSS2RDF documentation](<tech demo/demo 4/code/TSS2RDF/README.md>).

## Run the dashboard

Requirements:

- Python and pip; the implementation has been tested with Python 3.13.
- Node.js and npm. The current frontend lockfile requires Node.js `^20.19.0 || >=22.12.0`.

The commands below use PowerShell. Start each terminal at the repository root. A Python virtual environment can be used for the dependencies.

**Terminal 1 — backend:**

```powershell
cd "tech demo/demo 4/code/webapp/backend"
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8001
```

**Terminal 2 — frontend:**

```powershell
cd "tech demo/demo 4/code/webapp/frontend"
npm install
npm run dev
```

Open [http://localhost:5174](http://localhost:5174). The development server forwards `/api` requests to the backend on port **8001**. Interactive API documentation is available at [http://localhost:8001/docs](http://localhost:8001/docs).

To serve a built frontend through the backend, run `npm run build` in the frontend folder, then start or restart the backend and open [http://localhost:8001](http://localhost:8001).

### Load the examples or your own data

Local endpoint files are discovered in `tech demo/demo 4/metadata endpoint/`. Place a Turtle endpoint there and refresh the dashboard, or use **Add an online endpoint** to supply a Turtle URL.

The embedded example, `waterinfo_289441042_tss2_points_embedded.ttl`, works without external CSV files. It contains five conductivity readings and five quality-colour readings.

External examples describe factory conductivity and Waterinfo measurements at Grootdok, Rodenhuizedok, and Autrichehaven. Their CSVW descriptions are in `tech demo/demo 4/csvw/`. Supply the corresponding CSV files at the paths referenced by each endpoint, normally `tech demo/demo 4/data/`.

**CSV data files are excluded from Git by the repository's `data/` ignore rule.** A fresh clone includes the endpoint and CSVW descriptions, but the external CSV files must be supplied separately.

## Standalone TSS2RDF converter

The converter is a self-contained Python script and can run without the dashboard or a web server. From the repository root:

```powershell
cd "tech demo/demo 4/code/TSS2RDF"
python -m pip install -r requirements.txt
```

Convert the embedded example:

```powershell
python .\tss2rdf.py --mode embedded `
  --tss "..\..\metadata endpoint\waterinfo_289441042_tss2_points_embedded.ttl" `
  --output ".\output\embedded.ttl"
```

Convert an external example after supplying its CSV:

```powershell
python .\tss2rdf.py --mode external `
  --tss "..\..\metadata endpoint\waterinfo_289423042_tss2.ttl" `
  --csv "..\..\data\waterinfo_289423042.csv" `
  --csvw "..\..\csvw\waterinfo_289423042_csvw.json" `
  --output ".\output\external.ttl"
```

`--mode` must agree with the endpoint's `tss:embedded` flag. External mode requires both `--csv` and `--csvw`; all input arguments are local paths. One external CSV/CSVW pair is supported per invocation. The script writes the full Turtle graph, creates the output directory when needed, and prints JSON statistics with reading counts, triple counts, per-series counts, and file size. Existing output is replaced only after successful conversion.

See the [TSS2RDF README](<tech demo/demo 4/code/TSS2RDF/README.md>) for argument details and verified example results.

## Other Python utilities

| Script | Purpose |
|---|---|
| [csv_to_points.py](<tech demo/demo 4/code/csv_to_points/csv_to_points.py>) | Convert selected CSV columns into separate JSON point arrays for preparing embedded data. Select the timestamp with `--time`, optional value columns with `--columns`, and a delimiter with `--delimiter`. This preparation utility has its own CSV reader and can detect the delimiter from the header when the argument is omitted. |
| [shacl_report.py](<tech demo/demo 4/code/shacl_report/shacl_report.py>) | Validate a Turtle endpoint against a supplied SHACL shapes file, print the report and severity counts, and save the report as text. |
| [expand_snippets.py](<tech demo/demo 4/code/expand_snippets/expand_snippets.py>) | Expand embedded snippets, optionally selecting a single snippet by IRI. This helper has its own expansion logic and uses TSS time/value defaults when no context is supplied; the shared dashboard export rules are implemented in TSS2RDF. |

## Demo 4 layout

```text
tech demo/demo 4/
├── metadata endpoint/       RDFTSS Turtle endpoints
├── csvw/                    External CSV descriptions and RDF mappings
├── data/                    Local CSV datasets, excluded from Git
├── SHACL/                   Embedded and external profile shapes
└── code/
    ├── webapp/
    │   ├── backend/         FastAPI, RDF metadata, validation, query and SQL APIs
    │   └── frontend/        React dashboard built with Vite
    ├── TSS2RDF/             Standalone converter and shared RDF generation logic
    ├── csv_to_points/       CSV-to-JSON preparation utility
    ├── shacl_report/        Command-line SHACL reporting
    └── expand_snippets/     Embedded expansion helper
```

The backend uses RDFLib for RDF processing, pySHACL for SHACL validation, the `csvw` library for CSVW handling, and DuckDB for SQL. Its `tss2.py` module implements metadata inheritance and provenance; `main.py` provides the API; `sql_box.py` handles SQL. The backend's RDF generation modules call the shared converter in `TSS2RDF/tss2rdf.py`.

## Verification

After installing the dependencies, run these commands from the repository root:

```powershell
python -B -m unittest discover -s "tech demo/demo 4/code/webapp/backend" -p "test_*.py" -v
python -B -m unittest discover -s "tech demo/demo 4/code/TSS2RDF" -p "test_*.py" -v
npm --prefix "tech demo/demo 4/code/webapp/frontend" run lint
npm --prefix "tech demo/demo 4/code/webapp/frontend" run build
```

Tests that read external demo endpoints require the corresponding local CSV files described above.

The tests cover both storage profiles, inheritance, numeric and text readings, queries and SQL, default and custom SHACL shapes, explicit delimiters and comma defaults, domain RDF generation, complete downloads, and standalone CLI behavior.

## License

The source code is distributed under the [MIT License](LICENSE).
