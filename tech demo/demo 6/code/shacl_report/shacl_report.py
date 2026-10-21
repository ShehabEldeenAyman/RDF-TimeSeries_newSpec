"""Validate a TSS2 metadata endpoint against a SHACL shapes file and write the report.

The report is printed in the terminal and saved as a text file.

Examples:
    python shacl_report.py "../../metadata endpoint/waterinfo_289423042_tss2.ttl" ../../SHACL/external_shapes.ttl
    python shacl_report.py "../../metadata endpoint/waterinfo_289429042_tss2_parquet.ttl" ../../SHACL/external_shapes.ttl
    python shacl_report.py endpoint.ttl shapes.ttl --out my_report.txt

Without --out, the report goes to reports/<endpoint>__<shapes>.txt next to this script.
"""
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from pyshacl import validate
from rdflib import Graph, Namespace, RDF

SH = Namespace("http://www.w3.org/ns/shacl#")


def summary(report_graph):
    """How many results of each severity (Violation, Warning, Info) the report holds"""
    report = report_graph.value(predicate=RDF.type, object=SH.ValidationReport)
    counts = Counter()
    for result in report_graph.objects(report, SH.result):
        severity = report_graph.value(result, SH.resultSeverity)
        counts[str(severity).split("#")[-1]] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description="Validate a TSS2 file against SHACL shapes and write the report.")
    parser.add_argument("tss2_file", help="the TSS2 metadata endpoint (Turtle)")
    parser.add_argument("shapes_file", help="the SHACL shapes (Turtle)")
    parser.add_argument("--out", help="the text file for the report (default: reports/<endpoint>__<shapes>.txt)")
    args = parser.parse_args()

    tss2_path = Path(args.tss2_file)
    shapes_path = Path(args.shapes_file)
    for path in (tss2_path, shapes_path):
        if not path.is_file():
            parser.error(f"File not found: {path}")

    data = Graph().parse(tss2_path, format="turtle")
    shapes = Graph().parse(shapes_path, format="turtle")

    # allow_warnings: warnings alone don't make the endpoint fail; only violations do
    conforms, report_graph, report_text = validate(data, shacl_graph=shapes, allow_warnings=True)

    counts = summary(report_graph)
    header = (
        f"SHACL report, {datetime.now():%Y-%m-%d %H:%M}\n"
        f"Endpoint: {tss2_path}\n"
        f"Shapes:   {shapes_path}\n"
        f"Result:   {'CONFORMS' if conforms else 'DOES NOT CONFORM'}"
        f" ({counts['Violation']} violations, {counts['Warning']} warnings, {counts['Info']} infos)\n"
        + "=" * 70 + "\n"
    )
    text = header + report_text

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = Path(__file__).parent / "reports" / f"{tss2_path.stem}__{shapes_path.stem}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    print(text)
    print(f"Report saved to {out_path}")


if __name__ == "__main__":
    main()
