# Running the competency questions

Use Python 3.10 or newer. Run these commands from this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python evaluation_runner.py
```

Run selected questions or export the actual answers:

```bash
python evaluation_runner.py --question CQ2 --question CQ3
python evaluation_runner.py --output results.json
```

The default fixture and YAML paths resolve relative to the script, so the script can also be invoked from another working directory. User-supplied input/output paths resolve relative to the current working directory.

`--output` writes a JSON report containing each question and a standard SPARQL-results JSON answer. RDF term types and literal datatypes are preserved. Unbound variables are absent from JSON bindings and displayed as `—` in console output. Existing output files at the chosen path are replaced. No output file is written unless requested.

## Apply the questions to other metadata

```bash
python evaluation_runner.py --graph /path/to/metadata.ttl --question CQ1
python evaluation_runner.py --graph /path/to/first.ttl --graph /path/to/second.ttl
```

Only supply trusted local RDF inputs. Turtle and TriG avoid remote JSON-LD context processing. The runner does not dereference `tss:dataFile`; it evaluates the graph descriptions only. Multiple input files form a discovery catalog, not a single publication document.

The queries contain explicit example IRIs and dates. Before using real metadata, edit the YAML examples and corresponding SPARQL values together:

- CQ1–CQ3: replace `ex:ElectricalConductivity`, `ex:WaterBodyA`, and the CQ2 interval with the intended domain resources and time window.
- CQ4–CQ8: replace the selected snippet IRIs and observed property with the real ones.
- Prefix changes alone are insufficient if resource local names also differ.

Do not merge alternative documents that reuse the same snippet/field IRIs as if they described different resources. Use separate runs or properly distinguish those resources when constructing a catalog. The supplied fixture gives every snippet and field a distinct IRI.

## Interpreting execution

Exit status 0 means query execution completed; it does **not** mean answers are correct or metadata conforms. Empty results are printed normally and require interpretation. Invalid question IDs, missing files, and parsing/query errors return a nonzero exit status. No SHACL validation or automatic reasoning is run.

For a paper table, choose a question, run it on the documented evaluation metadata, and include the question, example, SPARQL query and actual formatted response. Report any source-selection discrepancies against independently established expectations. Never present a synthetic fixture result as an observation about Waterinfo.
