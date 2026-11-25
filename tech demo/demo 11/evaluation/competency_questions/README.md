# RDFTSS competency questions

These eight competency questions evaluate **semantic source discovery and retrieval of access and interpretation metadata**. They do not query sensory readings, open external payloads, decode values, or measure client conformance. SHACL validation remains separate in `../../SHACL/`.

## Files

| File | Purpose |
| --- | --- |
| `competency_questions.yaml` | Categorized natural-language questions, concrete examples, complete SPARQL SELECT queries, and interpretation limits |
| `sample_instances.trig` | Synthetic metadata catalog with separate named graphs for publication documents |
| `evaluation_runner.py` | Local RDFLib runner that prints actual results, optionally exports JSON, and optionally compares the results with expected answers |
| `requirements.txt` | Versions used to verify the queries |
| `evaluation_runner.md` | Installation, execution, and adaptation instructions |
| `waterinfo_questions.yaml` | The same eight questions, with the domain resources, interval and sources of the Waterinfo metadata documents |
| `waterinfo_expected.yaml` | The expected answers on the Waterinfo documents, written from the documents |
| `results/waterinfo_results.json` | The answers on the Waterinfo documents, with their comparison with the expected answers |
| `experimental_log.md` | A record of one run on the synthetic catalog (2026-11-20) |

## Questions

| ID | Category | Information retrieved |
| --- | --- | --- |
| CQ1 | Semantic discovery | Sources matching an observed property and feature of interest |
| CQ2 | Semantic discovery | Candidates whose declared windows overlap a requested interval |
| CQ3 | Source access | External file references and profiles, or embedded access predicates |
| CQ4 | Source access | Time/value field identities and column bindings |
| CQ5 | Interpretation metadata | Datatype, observed property, unit, sensor, and feature |
| CQ6 | Interpretation metadata | Declared time-grid and baseline-encoding parameters |
| CQ7 | Source assessment | Final (immutable) or still growing, and followed with Range requests |
| CQ8 | Source assessment | Available declared summaries and their source windows |

Each question follows the SemTS question/example/query pattern. The `id` and `interpretation` attributes are RDFTSS additions. Every query declares its own prefixes and uses stable ordering. Domain matching uses exact IRIs; no inference, label matching, or ontology alignment is assumed.

## Sample catalog and its limits

The catalog is synthetic, not a Waterinfo dataset or evidence of real-world adoption. Its domain resources and `example.org` file URLs are placeholders. No external files are supplied or fetched. The embedded sources are empty: metadata discovery does not imply that a window contains readings.

The catalog includes CSV, Parquet, Arrow, embedded JSON and embedded RDF descriptions, time-grid and baseline-difference descriptions, a different observed property, a different monitored feature, an open upper bound, and windows touching either edge of the requested interval. The different property/feature cases distinguish semantic matching from listing all sources; the touching windows distinguish overlap from boundary contact. These are representative cases, not an exhaustive conformance suite.

Each named graph represents a separate publication document. The runner queries their union as a discovery catalog. The mixed union is **not** asserted to be one conforming RDFTSS publication document: a document describes only embedded sources or only external sources, never both (§the-source of the specification). TriG is a catalog packaging choice, not an additional RDFTSS payload profile.

Queries use the current Demo 11 names `tss:EmbeddedJSONSource` and `tss:EmbeddedRDFSource`. They do not use the older class names still found in some manuscript drafts.

CQ2 retrieves candidate windows, not guaranteed data coverage. CQ3 identifies embedded access through the source IRI and `tss:points` or `tss:hasPoint`; it does not return those predicates' payload values. CQ4's absent bindings can be intentional for RDF predicates or derived time. CQ6 retrieves optional parameters independently so partial metadata remains visible; it does not determine conformance. CQ8 returns publisher-declared statistics, not verified calculations.

## SemTS reference and static answers

Inspected the public [SemTS competency-question package](https://github.com/semts-ontology/SemTS/tree/823dd58471fc95b9ead9d4b825812b75d1417f00/evaluation/competency_questions), at commit `823dd58471fc95b9ead9d4b825812b75d1417f00`.

It contains:

- `competency_questions.yaml`, grouped by category, with `question`, `example`, and `query` entries;
- `sample_instances.rdf`;
- `evaluation_runner.py`, which loads instances, executes queries, and prints results;
- `evaluation_runner.md` and `requirements.txt`.

**No static expected-answer files or expected-answer fields were found in that package.** The runner does not compare results to stored answers. The paper's formatted response is an illustration, not a static-answer fixture in the inspected package.

Accordingly, the answers on the synthetic catalog are generated at runtime rather than stored as snapshots. Optional JSON exports are actual execution outputs, not independent expected answers or pass/fail verdicts. The runner and RDFTSS questions were written for this repository; SemTS supplies the organizational precedent, not copied ontology content.

Successful execution shows that these queries can retrieve the described metadata from this fixture. It does not by itself establish source reachability, source fidelity, full specification conformance, or general applicability. The Waterinfo run below adds real sources and compares expected with retrieved answers.

## Waterinfo catalog: expected versus retrieved

`waterinfo_questions.yaml` asks the same eight questions of the eight Waterinfo metadata documents in `../../metadata endpoint/` (`waterinfo_*.ttl`), each loaded as a named graph. The growing RDF Message Log is read as the merge of its messages (`../tss_reader.py`), since RDFLib cannot parse RDF Messages. The factory document in that folder is not part of the evaluation, and the runner refuses to load it.

The questions keep their SPARQL patterns, and only their parameters change: the feature of interest of Zevenaarshaven (CQ1); the interval [2025-10-07T07:45:00Z, 2025-12-18T23:15:00Z), at whose start the Autrichehaven window ends (CQ2); and the Waterinfo source and field IRIs (CQ5–CQ6).

`waterinfo_expected.yaml` holds the expected rows of each question. They were written from the metadata documents, not copied from query results. `--expected` compares them with the retrieved rows. IRIs are compared as prefixed names, a data file by its file name (it resolves against the location of the document), and numbers and times by value. An unbound variable is `null`.

```bash
python evaluation_runner.py --waterinfo --questions waterinfo_questions.yaml \
    --expected waterinfo_expected.yaml --output results/waterinfo_results.json
```

All eight questions retrieve exactly the expected rows: 46 expected rows, 46 retrieved, with no missing or unexpected row. The expected answers rest on the documents' declarations. That the declarations describe the data correctly is checked separately, against the Waterinfo records, by `../waterinfo/check_waterinfo.py`.
