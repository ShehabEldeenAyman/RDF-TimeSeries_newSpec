"""Run metadata discovery CQs locally; never dereference tss:dataFile URLs."""

import argparse
import json
from pathlib import Path

import yaml
from rdflib import Dataset


HERE = Path(__file__).resolve().parent


def run(graph_paths, selected):
    questions = yaml.safe_load((HERE / "competency_questions.yaml").read_text())
    entries = [
        (category, cq)
        for category, group in questions["competency questions"].items()
        for cq in group
    ]
    unknown = set(selected) - {cq["id"] for _, cq in entries}
    if unknown:
        raise ValueError(f"Unknown question IDs: {', '.join(sorted(unknown))}")
    catalog = Dataset(default_union=True)
    # TriG preserves publication boundaries. Queries intentionally use the union
    # as a discovery catalog, not as a single RDFTSS publication document.
    for path in graph_paths:
        if not path.is_file():
            raise ValueError(f"Expected a local RDF file: {path}")
        catalog.parse(path.resolve().as_uri())
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, action="append",
                        help="Local RDF file; repeat to build a catalog. Default: bundled TriG fixture.")
    parser.add_argument("--question", action="append", default=[],
                        help="Question ID, e.g. CQ3; repeat to select several.")
    parser.add_argument("--output", type=Path,
                        help="Optionally write actual answers as SPARQL-results JSON inside a report.")
    args = parser.parse_args()
    try:
        results = run(args.graph or [HERE / "sample_instances.trig"], args.question)
    except Exception as exc:
        parser.exit(1, f"Evaluation failed: {exc}\n")
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
    if args.output:
        args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
        print(f"\nActual query results written to {args.output}")


if __name__ == "__main__":
    main()
