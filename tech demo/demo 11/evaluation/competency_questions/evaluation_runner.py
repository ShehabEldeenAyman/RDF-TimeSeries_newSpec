"""Run metadata discovery CQs locally; never dereference tss:dataFile URLs.

    python evaluation_runner.py                       # the synthetic catalog (sample_instances.trig)
    python evaluation_runner.py --waterinfo --questions waterinfo_questions.yaml \\
        --expected waterinfo_expected.yaml --output results/waterinfo_results.json
"""

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml
from rdflib import Dataset, URIRef

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tss_reader  # noqa: E402  (reads RDF Message Logs as the merge of their messages)

ENDPOINT = HERE.parent.parent / "metadata endpoint"
EXCLUDED = {"factory_conductivity_tss2.ttl"}   # NDA-derived: never part of the public evaluation


def waterinfo_documents():
    return sorted(p for p in ENDPOINT.glob("waterinfo_*.ttl") if p.name not in EXCLUDED)


def load_catalog(graph_paths):
    catalog = Dataset(default_union=True)
    # Each document is a graph of its own. Queries intentionally use the union as a discovery
    # catalog, not as a single RDFTSS publication document.
    for path in graph_paths:
        if not path.is_file():
            raise ValueError(f"Expected a local RDF file: {path}")
        if path.name in EXCLUDED:
            raise ValueError(f"{path.name} is NDA-derived and is not part of the evaluation")
        if path.suffix == ".ttl" and tss_reader.is_log(path.read_text(encoding="utf-8")):
            graph, _ = tss_reader.parse_document(path)
            named = catalog.graph(URIRef(path.resolve().as_uri()))
            for triple in graph:
                named.add(triple)
        else:
            catalog.parse(path.resolve().as_uri())
    return catalog


def run(graph_paths, selected, questions_path):
    questions = yaml.safe_load(questions_path.read_text())
    entries = [
        (category, cq)
        for category, group in questions["competency questions"].items()
        for cq in group
    ]
    unknown = set(selected) - {cq["id"] for _, cq in entries}
    if unknown:
        raise ValueError(f"Unknown question IDs: {', '.join(sorted(unknown))}")
    catalog = load_catalog(graph_paths)
    results = []
    for category, cq in entries:
        if selected and cq["id"] not in selected:
            continue
        answer = catalog.query(cq["query"])
        if answer.type != "SELECT":
            raise ValueError(f"{cq['id']} must be a SELECT query")
        results.append({
            "id": cq["id"], "category": category,
            "question": cq["question"], "example": cq["example"],
            "interpretation": cq["interpretation"],
            "answer": json.loads(answer.serialize(format="json")),
        })
    return results


# --- Comparing with expected answers -----------------------------------------------------------

def _normal(value, prefixes):
    """A comparable form of a value: IRIs as prefixed names, data files by file name, numbers
    and times by value."""
    if value is None:
        return None
    for prefix, namespace in prefixes.items():
        if value.startswith(namespace):
            value = f"{prefix}:{value[len(namespace):]}"
    if value.startswith("file:"):
        return value.rsplit("/", 1)[-1]
    try:
        return ("time", datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        return ("number", Decimal(value))
    except InvalidOperation:
        return value


def compare(results, expected):
    prefixes = expected.get("prefixes", {})
    report = {}
    for result in results:
        spec = expected.get(result["id"])
        if spec is None:
            continue
        variables = spec["variables"]
        got = sorted((tuple(_normal(row.get(v, {}).get("value"), prefixes) for v in variables)
                      for row in result["answer"]["results"]["bindings"]), key=repr)
        want = sorted((tuple(_normal(None if x is None else str(x), {}) for x in row) for row in spec["rows"]),
                      key=repr)
        missing = [w for w in want if w not in got]
        extra = [g for g in got if g not in want]
        report[result["id"]] = {"expected": len(want), "retrieved": len(got),
                                "match": not missing and not extra and len(got) == len(want),
                                "missing": [repr(m) for m in missing], "unexpected": [repr(e) for e in extra]}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--graph", type=Path, action="append",
                        help="Local RDF file; repeat to build a catalog. Default: bundled TriG fixture.")
    parser.add_argument("--waterinfo", action="store_true",
                        help="Use the Waterinfo metadata documents of demo 11 as the catalog.")
    parser.add_argument("--questions", type=Path, default=HERE / "competency_questions.yaml",
                        help="The questions (default: competency_questions.yaml).")
    parser.add_argument("--expected", type=Path, help="Compare the answers with these expected rows.")
    parser.add_argument("--question", action="append", default=[],
                        help="Question ID, e.g. CQ3; repeat to select several.")
    parser.add_argument("--output", type=Path,
                        help="Optionally write actual answers as SPARQL-results JSON inside a report.")
    args = parser.parse_args()
    graphs = (args.graph or []) + (waterinfo_documents() if args.waterinfo else [])
    try:
        results = run(graphs or [HERE / "sample_instances.trig"], args.question, args.questions)
    except Exception as exc:
        parser.exit(1, f"Evaluation failed: {exc}\n")
    comparison = compare(results, yaml.safe_load(args.expected.read_text())) if args.expected else {}
    for result in results:
        variables = result["answer"]["head"]["vars"]
        rows = result["answer"]["results"]["bindings"]
        print(f"\n{result['id']} — {result['question']}")
        print(f"Example: {result['example']}")
        print("\t".join(variables))
        for row in rows:
            print("\t".join(row.get(v, {}).get("value", "—") for v in variables))
        print(f"Rows: {len(rows)}")
        print(f"Interpretation: {result['interpretation']}")
        if result["id"] in comparison:
            c = comparison[result["id"]]
            result["comparison"] = c
            print(f"Expected rows: {c['expected']}, retrieved: {c['retrieved']}, "
                  f"{'all match' if c['match'] else 'DIFFERENT'}")
            for m in c["missing"]:
                print(f"  missing: {m}")
            for e in c["unexpected"]:
                print(f"  unexpected: {e}")
    if comparison:
        matched = sum(c["match"] for c in comparison.values())
        print(f"\n{matched} of {len(comparison)} questions retrieved exactly the expected rows.")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
        print(f"\nActual query results written to {args.output}")


if __name__ == "__main__":
    main()
