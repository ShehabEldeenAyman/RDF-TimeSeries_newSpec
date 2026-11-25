"""Check the rdftss library against the evaluation reader on the Waterinfo metadata documents.

    python -m pip install ../../../../rdftss-python    # the rdftss library (requirements.txt has pyarrow and pyshacl)
    python check_rdftss.py                             # writes results/rdftss_report.json and .md

Run it after waterinfo/fetch_waterinfo.py: it reads the retrieved data files in data/.

For each Waterinfo metadata document, rdftss loads the description, reads the rows of its source,
validates the document with the SHACL shapes it bundles, and recomputes the stored summaries.
tss_reader.py, the reader of the other evaluations, reads the same rows. The two must give the same
rows, at the same times, with the same value for every field: decoded values (a baseline
difference is compared after decoding), numbers by value, other values by their text. rdftss must
report no reading problem, no SHACL violation or warning, and no out-of-date summary.

The factory document of the metadata endpoint is NDA-derived: it is never read here.
"""
import json
import platform
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE.parents[1]
RESULTS = HERE / "results"
sys.path.insert(0, str(HERE.parent))
import tss_reader as tr   # noqa: E402

import pyarrow   # noqa: E402
import pyshacl   # noqa: E402
import rdflib    # noqa: E402
import rdftss    # noqa: E402

DOCUMENTS = sorted((DEMO / "metadata endpoint").glob("waterinfo_*.ttl"))


def same(a: str, b: str) -> bool:
    """The same value: the same text, or the same number."""
    if a == b:
        return True
    try:
        return Decimal(a) == Decimal(b)
    except (InvalidOperation, TypeError, ValueError):
        return False


def check_document(path: Path) -> dict:
    rdflib.NORMALIZE_LITERALS = False   # pyshacl turns it back on after each validation
    graph, _ = tr.parse_document(path)
    (reference,) = tr.sources(graph, path)
    expected = tr.in_window(reference, tr.read_rows(reference))

    document = rdftss.load(path, allow_network=False)
    source = document.source()
    rows = source.read()
    validation = document.validate()
    summaries = document.verify_summaries()

    found = {row.time: {str(f.iri): row.text(f) for f in row.values if not f.is_time} for row in rows}
    differences, compared = [], 0
    for row in expected:
        values = found.get(row.time)
        if values is None:
            differences.append(f"{row.time}: no rdftss row at this time")
            continue
        for f, text in row.values.items():
            if f == reference.time_field:
                continue
            other = values.get(str(f))
            if text is None and other is None:
                continue
            compared += 1
            if not same(text, other):
                differences.append(f"{row.time} {str(f).split('#')[-1]}: tss_reader {text!r}, rdftss {other!r}")
    only_rdftss = set(found) - {row.time for row in expected}
    differences += [f"{time}: a row that only rdftss reads" for time in sorted(only_rdftss)]

    return {
        "document": path.name,
        "class": str(reference.kind).split("#")[-1],
        "rows_rdftss": len(rows),
        "rows_tss_reader": len(expected),
        "values_compared": compared,
        "differences": differences,
        "reading_problems": [str(issue) for issue in document.all_issues] + [str(issue) for issue in rows.issues],
        "shapes": "embedded" if source.kind.name.startswith("EMBEDDED") else "external",
        "violations": sum(1 for result in validation.results if result.severity == "Violation"),
        "warnings": sum(1 for result in validation.results if result.severity == "Warning"),
        "summaries": len(summaries.checks),
        "summary_numbers": sum(len(check.compared) for check in summaries.checks),
        "summaries_out_of_date": [str(check) for check in summaries.out_of_date + summaries.not_checked],
    }


def passed(result: dict) -> bool:
    return (result["rows_rdftss"] == result["rows_tss_reader"] and not result["differences"]
            and not result["reading_problems"] and not result["violations"] and not result["warnings"]
            and not result["summaries_out_of_date"])


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    results = [check_document(path) for path in DOCUMENTS]
    versions = {"rdftss": rdftss.__version__, "rdftss specification version": rdftss.SPEC_VERSION,
                "python": platform.python_version(), "rdflib": rdflib.__version__,
                "pyshacl": pyshacl.__version__, "pyarrow": pyarrow.__version__}
    (RESULTS / "rdftss_report.json").write_text(json.dumps({"versions": versions, "documents": results},
                                                           indent=2) + "\n")
    total = {key: sum(r[key] for r in results) for key in
             ("rows_rdftss", "rows_tss_reader", "values_compared", "violations", "warnings", "summaries",
              "summary_numbers")}
    lines = ["# The rdftss library on the Waterinfo metadata documents", "",
             "Generated by `check_rdftss.py`, which describes the comparison.", "",
             "Versions: " + ", ".join(f"{name} {version}" for name, version in versions.items()) + ".", "",
             "| Document | Class | Rows (rdftss) | Rows (tss_reader) | Values compared | Differences "
             "| Shapes | Violations | Warnings | Summaries (numbers) | Result |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| `{r['document']}` | {r['class']} | {r['rows_rdftss']} | {r['rows_tss_reader']} "
                     f"| {r['values_compared']} | {len(r['differences'])} | {r['shapes']} | {r['violations']} "
                     f"| {r['warnings']} | {r['summaries']} ({r['summary_numbers']}) "
                     f"| {'pass' if passed(r) else 'FAIL'} |")
    lines.append(f"| **Total** | | **{total['rows_rdftss']}** | **{total['rows_tss_reader']}** "
                 f"| **{total['values_compared']}** | **{sum(len(r['differences']) for r in results)}** | "
                 f"| **{total['violations']}** | **{total['warnings']}** "
                 f"| **{total['summaries']} ({total['summary_numbers']})** "
                 f"| **{sum(passed(r) for r in results)} of {len(results)}** |")
    lines += ["", "## Discrepancies", ""]
    problems = [f"- `{r['document']}`: {problem}" for r in results
                for problem in r["differences"][:10] + r["reading_problems"] + r["summaries_out_of_date"]]
    lines += problems or ["None."]
    (RESULTS / "rdftss_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    if not all(passed(r) for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
