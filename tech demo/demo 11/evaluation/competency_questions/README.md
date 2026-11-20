# RDFTSS competency questions

These eight competency questions evaluate **semantic source discovery and retrieval of access and interpretation metadata**. They do not query sensory readings, open external payloads, decode values, or measure client conformance. SHACL validation remains separate in `../../SHACL/`.

## Files

| File | Purpose |
| --- | --- |
| `competency_questions.yaml` | Categorized natural-language questions, concrete examples, complete SPARQL SELECT queries, and interpretation limits |
| `sample_instances.trig` | Synthetic metadata catalog with separate named graphs for publication documents |
| `evaluation_runner.py` | Local RDFLib runner that prints actual results and optionally exports JSON |
| `requirements.txt` | Versions used to verify the queries |
| `evaluation_runner.md` | Installation, execution, and adaptation instructions |

## Questions

| ID | Category | Information retrieved |
| --- | --- | --- |
| CQ1 | Semantic discovery | Sources matching an observed property and feature of interest |
| CQ2 | Semantic discovery | Candidates whose declared windows overlap a requested interval |
| CQ3 | Source access | External file references and profiles, or embedded access predicates |
| CQ4 | Source access | Time/value field identities and column bindings |
| CQ5 | Interpretation metadata | Datatype, observed property, unit, sensor, and feature |
| CQ6 | Interpretation metadata | Declared time-grid and baseline-encoding parameters |
| CQ7 | Source assessment | Final (immutable) or still growing |
| CQ8 | Source assessment | Available declared summaries and their source windows |

Each question follows the SemTS question/example/query pattern. The `id` and `interpretation` attributes are RDFTSS additions. Every query declares its own prefixes and uses stable ordering. Domain matching uses exact IRIs; no inference, label matching, or ontology alignment is assumed.

## Sample catalog and its limits

The catalog is synthetic, not a Waterinfo dataset or evidence of real-world adoption. Its domain resources and `example.org` file URLs are placeholders. No external files are supplied or fetched. The embedded sources are empty: metadata discovery does not imply that a window contains readings.

The catalog includes CSV, Parquet, Arrow, embedded JSON and embedded RDF descriptions, time-grid and baseline-difference descriptions, a different observed property, a different monitored feature, an open upper bound, and windows touching either edge of the requested interval. The different property/feature cases distinguish semantic matching from listing all sources; the touching windows distinguish overlap from boundary contact. These are representative cases, not an exhaustive conformance suite.

Each named graph represents a separate publication document. The runner queries their union as a discovery catalog. The mixed union is **not** asserted to be one conforming RDFTSS publication document: Demo 11 disallows mixing embedded and external sources within such a document. TriG is a catalog packaging choice, not an additional RDFTSS payload profile.

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

Accordingly, this folder also generates answers at runtime rather than storing answer snapshots. Optional JSON exports are actual execution outputs, not independent expected answers or pass/fail verdicts. The runner and RDFTSS questions were written for this repository; SemTS supplies the organizational precedent, not copied ontology content.

Successful execution shows that these queries can retrieve the described metadata from this fixture. It does not by itself establish source reachability, source fidelity, full specification conformance, or general applicability. Publication evaluation should add independently documented real sources and assess expected versus retrieved sources and access information.
