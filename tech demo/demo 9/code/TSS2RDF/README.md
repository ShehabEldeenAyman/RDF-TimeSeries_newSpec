# TSS2RDF

`tss2rdf.py` turns a TSS metadata file (one `tss:Source` with its `tss:Field` nodes) and its data into SOSA observations, written as N-Triples (one triple per line) by rdflib. It runs on its own: you can copy it elsewhere and run it with `rdflib` 7 or newer (and `duckdb` for Parquet data). The dashboard's **RDF generation** tab imports its `generate()` function through `code/webapp/backend/rdf_generation.py`, so both make the same RDF. The backend reads embedded points with the same `point_graph()`.

## Install and run

From the repository root, in PowerShell:

```powershell
cd "tech demo/demo 7/code/TSS2RDF"
python -m pip install -r requirements.txt
```

Embedded points:

```powershell
python .\tss2rdf.py --tss "..\..\metadata endpoint\waterinfo_289441042_tss2_points_embedded.ttl" --output ".\output\embedded.nt"
```

A CSV or Parquet source (the data file is the source's `tss:dataFile`, read relative to the TSS file):

```powershell
python .\tss2rdf.py --tss "..\..\metadata endpoint\waterinfo_289423042_tss2.ttl" --output ".\output\external.nt"
```

The output folder is created when needed. `output/` is ignored by Git, so full generated datasets stay local. A successful run prints statistics: the data that was read, the output path, the number of observations and triples, the file size, and the observations per field (by the field's IRI). For the embedded file:

```text
Data:         the points embedded in the TSS file (tss:pointOf)
Output:       ...\output\embedded.nt
Observations: 10
Triples:      65
Size:         6,588 bytes
Observations per field:
  https://example.org/ex#zevenaarshaven-ec20-value-field: 5
  https://example.org/ex#zevenaarshaven-quality-color-value-field: 5
```

## Arguments

| Argument | Meaning |
|---|---|
| `--tss PATH` | Required: the TSS Turtle file, with exactly one `tss:Source`. |
| `--output PATH`, `-o PATH` | The N-Triples file to write; `observations.nt` in the current folder by default. |
| `--help` | Show the command-line help. |

## What it makes

Every row of the data gives one `sosa:Observation` for each field that has a `tss:observedProperty`, except the time field (here in Turtle, to read it more easily):

```turtle
_:o1 rdf:type sosa:Observation ;
    dcterms:source ex:grootdok-source ;                          # the tss:Source (when it has an IRI)
    sosa:observedProperty quantitykind:ElectricConductivity ;   # tss:observedProperty
    sosa:madeBySensor ex:sensor-grootdok-ec20 ;                 # tss:sensor (when stated)
    sosa:hasFeatureOfInterest ex:water-grootdok ;               # tss:featureOfInterest (when stated)
    qudt:hasUnit unit:MicroS-PER-CentiM ;                       # tss:unit (when stated)
    sosa:resultTime "2021-03-03T23:45:00Z"^^xsd:dateTime ;      # the row's tss:timeField value
    sosa:hasSimpleResult "902.27"^^xsd:double .                 # the value, with the field's tss:datatype
```

In the output file, the same observation is eight lines of N-Triples, every IRI in full:

```text
_:o1 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/sosa/Observation> .
...
_:o1 <http://www.w3.org/ns/sosa/hasSimpleResult> "902.27"^^<http://www.w3.org/2001/XMLSchema#double> .
```

- A field that describes another one (`tss:describesField`) but has its own `tss:observedProperty`, such as the quality code colour, becomes its own observation at the same time. Fields without `tss:observedProperty` (an id column, a quality code number, ...) are not in the RDF.
- Only rows inside the source's time window are used: `tss:from` included, `tss:until` not. Both must be `xsd:dateTime` literals.
- The time field's values must be `xsd:dateTime` texts like `2021-03-03T23:45:00Z` (a time without a timezone is taken as UTC). A time field typed `xsd:dateTimeStamp` is read the same way, but every time must have a timezone. `sosa:resultTime` is always an `xsd:dateTime`.
- An empty value (an empty cell, a NULL, or an empty, `null` or missing value in an embedded point) gives no observation.
- A value that does not fit its field's `tss:datatype` (XSD numbers, booleans, dates and date-times) stops the conversion and says where it is: the CSV line, the Parquet row, or the point's time. `rdf:langString` fields use `tss:language`.
- CSV values keep their text (`"3.0E1"` stays `"3.0E1"`). Parquet values are written in the XSD form rdflib gives their type: `4797.72`, `true`, `1.50` for a DECIMAL, `12:30:00` for a TIME, hexadecimal for a BLOB, and times with a timezone in UTC as `...Z`. Values of embedded points written as JSON strings keep their text too (see below).
- rdflib writes all the RDF syntax; nothing is glued together as text. The observations go into a small rdflib graph, and every 10,000 observations (`BATCH_SIZE`) rdflib's N-Triples serializer appends that graph to the file (`write_batch`) and a new one starts. N-Triples has no prefixes and nothing that carries over from one line to the next, so the batches simply follow each other, and a big data file never has to fit in memory.
- Observations are blank nodes (`_:o1`, `_:o2`, ...), numbered over the whole file, so two observations never merge, also not across batches. Blank nodes copied from the metadata's descriptions get new labels (rdflib's `BNode()`, like `_:N3f2a...`), so they never merge with an observation either, even when the metadata gave them a label like `o1`. N-Triples writes every literal in full (`"3.0E1"^^<...#double>`), so its text stays exactly as it is.
- `generate()` (used by the dashboard) also gives a preview: the first 200 observations and the copied descriptions, as one small graph written by rdflib's Turtle serializer (`exact_turtle`). rdflib writes the observations there as anonymous blank nodes (`[] a sosa:Observation ; ...`).
- Why N-Triples and not Turtle: rdflib's Turtle serializer, run once per batch, took about 110 s instead of about 22 s for `factory_conductivity_tss2.ttl` (its Turtle file is half the size). Before this change the converter wrote the Turtle syntax itself, as text (about 15 s, 153 MB). The table below has the N-Triples sizes.
- Every observation links back to its source with `dcterms:source`: the IRI of the `tss:Source` (a source that is a blank node gets no link). Only the link: what the metadata says about the source is not copied.
- The TSS vocabulary and the source and field nodes are not in the output. What the metadata says about the IRIs the observations use (a sensor, a feature, ...) is copied along, without TSS terms: each IRI's concise bounded description (`graph.cbd`), so blank nodes inside a description come along too.
- The data is read as `tss:format` says: `tss:CSV` with `tss:delimiter` (optional, default `,`), `tss:header` (optional, default `true`; without a header line, columns are matched by `tss:columnIndex`) and `tss:encoding` (optional, default `utf-8`); `tss:Parquet` with its own column types; or, with `tss:embedded true`, the points in the TSS file itself (the nodes with `tss:pointOf`).
- The output is written to a temporary file and only replaces the output file when the whole conversion worked, so errors never leave half a graph or overwrite an earlier result. The output cannot be the TSS file or the data file.

Exit codes: `0` for success, `1` for conversion or input errors, `2` for wrong command-line arguments. No SHACL validation is done before converting.

## Embedded points (`tss:pointOf`)

Embedded points are RDF in the TSS file itself, at its end: each point is a node of its own, linked to its source with `tss:pointOf`, and each value is a triple with the field's IRI as predicate and a literal typed with the field's `tss:datatype`. One of the demo's embedded points:

```turtle
[] tss:pointOf ex:zevenaarshaven-source ;
    ex:zevenaarshaven-time-field "2021-03-03T23:15:00Z"^^xsd:dateTime ;
    ex:zevenaarshaven-ec20-value-field "4797.72"^^xsd:double ;
    ex:zevenaarshaven-quality-color-value-field "rgba(0, 128, 0, 1.0)"^^xsd:string .
```

Each point is one Turtle statement, so a new reading is appended as one more statement at the end of the file. `point_graph(graph, source)` gives the points of a source as an `rdflib.Graph`: the triples of every node with `tss:pointOf <source>`, without the `tss:pointOf` link itself (for the demo's file: 15 triples, one blank node per point). Then:

- A missing value has no triple; an empty string (`""`) gives no triple either, like an empty CSV cell.
- A point's time is its value for the source's `tss:timeField`. Blank nodes have no order, so the converter puts the points in time order. A point without a time stops the conversion, like a CSV row with an empty time cell. So do a node as a value (not a literal) and two values for one field.
- A property of a point that is no field of the source is ignored (the SHACL shapes warn about it: P6). A point with no value for any field (only such properties, or only empty values) has nothing to convert and is skipped, as the dashboard and the browser skip it. A node of another source, or a node without `tss:pointOf`, is no point of the source.
- Every literal keeps its text exactly as the file writes it, also when it is wrong: `"...Z"` stays `...Z` (not `+00:00`), and `" 12 "` or `"1_000"` as `xsd:double`, or `"yes"` as `xsd:boolean`, stay as they are, so the SHACL check P4 can report them (and the browser, whose parser keeps them too, gives the same texts). rdflib normally rewrites a literal's text while it parses (`"yes"^^xsd:boolean` would become `"false"`), so the converter reads the TSS file with `parse_exact(graph, ...)`: `graph.parse(...)` with `rdflib.NORMALIZE_LITERALS = False`, put back afterwards (also when the parse fails). rdflib still reads each literal's value (`toPython()`). That switch is one for the whole program, and pyshacl's `validate()` switches it off and on again too, so a lock (`NORMALIZE_LOCK`) makes them take turns: `parse_exact` holds it, and the dashboard's backend and `shacl_report.py` hold it around `validate()`. A literal that another thread makes during those milliseconds only keeps its text as written.
- rdflib's Turtle writer would write such texts in a short form made from their value (a bare `yes`, which is broken Turtle, or `1.2e+01` for `" 12 "`). `exact_turtle(graph)` writes numbers and booleans in full instead (`"yes"^^xsd:boolean`); the SHACL report and the preview of `generate()` use it.

The web app's browser code does the same with N3.js (`pointStore(store, source)` in `code/webapp/frontend/src/tss.js`).

## Tests

```powershell
python -B -m unittest -v test_tss2rdf
```

The tests build their metadata with rdflib (or, with embedded points, as the text of a TSS file), write everything in a temporary folder, and parse the output back with rdflib to check its triples and values. They cover:

- a CSV source: the link back to the source on every observation, `,` and `utf-8` when `tss:delimiter` and `tss:encoding` are not stated, the time window, empty cells, the describing field with its own observed property, a field without one, kept value text, the copied sensor description with nested blank nodes (also blank nodes labelled `o1` or `o001` in the metadata, which must not merge with observations), and a headerless CSV;
- the N-Triples output: exactly the expected triples (texts as in the CSV file), one triple per line, the same triples with one observation per batch (no blank nodes merged across batches), and the Turtle preview;
- Parquet files, also with DECIMAL, TIME and BLOB columns;
- a bad value, a bad time and an untyped `tss:from` (the old output stays);
- embedded points: the demo's 15 point triples, time order, an appended point, only the points of the source (not those of another node, nor nodes without `tss:pointOf`), a property that is no field, empty values, a source without points, a point with no value for any field (skipped), a point without a time, a time field typed `xsd:dateTimeStamp` (a timezone is required), a bad value, and a node or two values for one field;
- the exact texts of `parse_exact` (`" 12 "`, `"1_000"`, `"yes"`, `...Z`, also in the output), rdflib's switch put back (also after a parse error, and with four parses at the same time), and `exact_turtle`;
- the demo's embedded file and CSV file, and the command line.

Demo conversions (the N-Triples output; the Turtle the converter wrote before was about half the size, with the same triples):

| Input | Observations | RDF triples | Output size (N-Triples) |
|---|---:|---:|---:|
| `waterinfo_289441042_tss2_points_embedded.ttl` | 10 | 65 | 7 kB |
| `waterinfo_289423042_tss2.ttl` (CSV) | 189,706 | 1,233,089 | 128 MB |
| `waterinfo_289429042_tss2_parquet.ttl` (Parquet) | 316,044 | 2,054,286 | 217 MB |
| `waterinfo_289435042_tss2.ttl` (CSV) | 279,856 | 1,819,064 | 192 MB |
| `factory_conductivity_tss2.ttl` (CSV) | 441,923 | 3,433,406 | 358 MB |
