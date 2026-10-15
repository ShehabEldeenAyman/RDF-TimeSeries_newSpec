# TSS2RDF

`tss2rdf.py` converts RDFTSS Turtle files into the same domain RDF as the Demo 4 dashboard. It is a self-contained script: you can copy it elsewhere and run it with `rdflib` and `csvw` installed. It does not require the dashboard, a web server, or FastAPI. The dashboard imports this script's conversion functions so the two entry points share the same rules.

## Install and run

From the repository root, in PowerShell:

```powershell
cd "tech demo/demo 4/code/TSS2RDF"
python -m pip install -r requirements.txt
```

Embedded example:

```powershell
python .\tss2rdf.py --mode embedded --tss "..\..\metadata endpoint\waterinfo_289441042_tss2_points_embedded.ttl" --output ".\output\embedded.ttl"
```

External CSV example:

```powershell
python .\tss2rdf.py --mode external --tss "..\..\metadata endpoint\waterinfo_289423042_tss2.ttl" --csv "..\..\data\waterinfo_289423042.csv" --csvw "..\..\csvw\waterinfo_289423042_csvw.json" --output ".\output\external.ttl"
```

The output directory is created automatically. Each successful run prints JSON statistics including the output path, reading count, triple count, per-series counts, and file size. `output/` is ignored by Git so full generated datasets stay local.

## Arguments

| Argument | Meaning |
|---|---|
| `--mode embedded` or `--mode external` | Required storage profile; must agree with the file's boolean `tss:embedded`. |
| `--tss PATH` | Required input Turtle file. |
| `--csv PATH` | Required in external mode: the actual CSV file to read. |
| `--csvw PATH` | Required in external mode: the CSVW JSON description to use. |
| `--output PATH`, `-o PATH` | Complete output graph in Turtle; defaults to `observations.ttl` in the current working directory. |
| `--help` | Show command-line help. |

All input arguments are local file paths, absolute or relative to the working directory. Embedded mode rejects CSV/CSVW arguments. In external mode, the explicit paths locate the physical inputs; the group's `tss:dataFile` IRI remains the base for relative CSVW URL mappings, matching dashboard behavior. The TSS must still declare `tss:dataFile` and `tss:describedby` IRIs. One external CSV/CSVW pair is supported per invocation; groups referring to different pairs require separate conversions.

## Conversion behavior

- Each reading becomes a standalone domain RDF node. Embedded point identifiers are preserved when present; other readings are anonymous nodes.
- Effective point types and `tss:about` metadata follow the same snippet overrides and template inheritance as the dashboard. Referenced domain resources retain their descriptions.
- TSS vocabulary, source snippet/group nodes, group attributes such as `tss:producedBy`, and synthesized collections are excluded.
- Embedded values are expanded using the effective JSON-LD context. Their original time/value mappings and nested result structure are preserved.
- External values use CSVW `propertyUrl`, `datatype` strings or `datatype.base`, and `valueUrl` mappings, including virtual columns. Each series receives its own measured value, shared timestamp, mapped auxiliary columns, and its domain metadata.
- External readings respect each snippet's inclusive `from` and exclusive `until` bounds. Null measured values produce no reading. As in the dashboard, embedded expansion materializes the supplied point arrays without window filtering.
- CSV parsing requires the CSVW's explicit single-character `dialect.delimiter`; there is no guessing or fallback. It supports headers or headerless CSVs, column names, encoding, quoting, null/default settings, and datatype constraints. The same dashboard restrictions apply: zero or one header row, no skipped columns, no `aboutUrl` subject mappings, and no ordered separator lists.
- External output streams to disk and contains **all** generated readings; it is not restricted to the dashboard's 200-reading preview.
- Existing output is replaced only after successful conversion. Errors preserve the previous output and source files. The output cannot be an input file.

Exit codes: `0` for success, `1` for conversion/input errors, and `2` for incorrect command-line arguments. SHACL validation remains a separate operation.

## Tests

```powershell
python -B -m unittest -v test_tss2rdf
```

The tests run a copy of the script outside the repository to check that it works independently. They cover the real embedded example, an external CSV with multiple series and CSVW mappings, argument/profile errors, and preservation of existing output after a failed conversion. The two example commands above run against the full embedded and external demo datasets.

Verified demo conversions:

| Input | Readings | RDF triples | Output |
|---|---:|---:|---|
| `waterinfo_289441042_tss2_points_embedded.ttl` | 10 | 55 | `output/embedded.ttl` |
| `waterinfo_289423042_tss2.ttl` and its CSV/CSVW | 94,853 | 1,233,089 | `output/external.ttl` |

The generated files and verification report are available locally in `output/`. Six standalone CLI tests and all 32 dashboard regression tests passed.
