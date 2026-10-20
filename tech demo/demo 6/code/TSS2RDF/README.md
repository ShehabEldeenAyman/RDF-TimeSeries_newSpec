# TSS2RDF

`tss2rdf.py` turns a TSS metadata file (one `tss:Source` with its `tss:Field` nodes) and its data into SOSA observations in Turtle. It runs on its own: you can copy it elsewhere and run it with `rdflib` (and `duckdb` for Parquet data). The dashboard's **RDF generation** tab imports its `generate()` function, so both make the same RDF.

## Install and run

From the repository root, in PowerShell:

```powershell
cd "tech demo/demo 6/code/TSS2RDF"
python -m pip install -r requirements.txt
```

Embedded points:

```powershell
python .\tss2rdf.py --tss "..\..\metadata endpoint\waterinfo_289441042_tss2_points_embedded.ttl" --output ".\output\embedded.ttl"
```

A CSV or Parquet source (the data file is the source's `tss:dataFile`, read relative to the TSS file):

```powershell
python .\tss2rdf.py --tss "..\..\metadata endpoint\waterinfo_289423042_tss2.ttl" --output ".\output\external.ttl"
```

The output folder is created when needed. A successful run prints JSON statistics: the output path, the data that was read, the number of observations and triples, the observations per field, and the file size. `output/` is ignored by Git, so full generated datasets stay local.

## Arguments

| Argument | Meaning |
|---|---|
| `--tss PATH` | Required: the TSS Turtle file, with exactly one `tss:Source`. |
| `--output PATH`, `-o PATH` | The Turtle file to write; `observations.ttl` in the current folder by default. |
| `--help` | Show the command-line help. |

## What it makes

Every row of the data gives one `sosa:Observation` for each field that has a `tss:observedProperty`, except the time field:

```turtle
[] a sosa:Observation ;
    sosa:observedProperty quantitykind:ElectricConductivity ;   # tss:observedProperty
    sosa:madeBySensor ex:sensor-grootdok-ec20 ;                 # tss:sensor (when stated)
    sosa:hasFeatureOfInterest ex:water-grootdok ;               # tss:featureOfInterest (when stated)
    qudt:hasUnit unit:MicroS-PER-CentiM ;                       # tss:unit (when stated)
    sosa:resultTime "2021-03-03T23:45:00Z"^^xsd:dateTime ;      # the row's tss:timeField column
    sosa:hasSimpleResult "902.27"^^xsd:double .                 # the cell, with the field's tss:datatype
```

- A field that describes another one (`tss:describesField`) but has its own `tss:observedProperty`, such as the quality code colour, becomes its own observation at the same time. Fields without `tss:observedProperty` (an id column, a quality code number, ...) are not in the RDF.
- Only rows inside the source's time window are used: `tss:from` included, `tss:until` not.
- An empty cell (or a missing key in an embedded point) gives no observation.
- Values keep their text from the data (`"3.0E1"` stays `"3.0E1"`). A value that does not fit its field's `tss:datatype` (XSD numbers, booleans, dates and date-times) stops the conversion and names the row. `rdf:langString` fields use `tss:language`.
- Observations are blank nodes. The TSS vocabulary and the source and field nodes are not in the output. What the metadata says about the IRIs the observations use (a sensor, a feature, ...) is copied along, without TSS terms.
- The data is read as `tss:format` says: `tss:CSV` with `tss:delimiter` (default `,`), `tss:header` (default `true`; without a header line, columns are matched by `tss:columnIndex`) and `tss:encoding` (default `utf-8`); `tss:Parquet` with its own column types; or, with `tss:embedded true`, the JSON objects in `tss:points`, keyed by `tss:columnName`.
- The output is written to a temporary file and only replaces the output file when the whole conversion worked, so errors never leave half a graph or overwrite an earlier result. The output cannot be the TSS file or the data file.

Exit codes: `0` for success, `1` for conversion or input errors, `2` for wrong command-line arguments. No SHACL validation is done before converting.

## Tests

```powershell
python -B -m unittest -v test_tss2rdf
```

The tests cover a CSV source (time window, empty cells, the describing field with its own observed property, a field without one, kept value text, copied sensor description), a headerless CSV, a Parquet file, a bad value and a bad time (the old output stays), the command line, and the demo's embedded and CSV files.

Demo conversions:

| Input | Observations | RDF triples | Output size |
|---|---:|---:|---:|
| `waterinfo_289441042_tss2_points_embedded.ttl` | 10 | 55 | 3 kB |
| `waterinfo_289423042_tss2.ttl` (CSV) | 189,706 | 1,043,383 | 50 MB |
| `waterinfo_289429042_tss2_parquet.ttl` (Parquet) | 316,044 | 1,738,242 | 85 MB |
| `waterinfo_289435042_tss2.ttl` (CSV) | 279,856 | 1,539,208 | 75 MB |
| `factory_conductivity_tss2.ttl` (CSV) | 441,923 | 2,991,483 | 147 MB |
