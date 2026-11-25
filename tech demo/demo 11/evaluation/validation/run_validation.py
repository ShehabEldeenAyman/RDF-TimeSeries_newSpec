"""Structural validation of demo 11: the SHACL shapes on valid descriptions and on deliberately
invalid variants (cases.py).

    python run_validation.py      # writes results/validation_summary.md, .json and results/reports/

Valid descriptions: the Waterinfo metadata documents, and every example of the specification
that describes a whole source. Each must conform: no sh:Violation (warnings are listed).
Invalid variants: each must be reported with the check it targets, at the expected severity.
Every other check that a variant also triggers is listed too.

Validator: pyshacl with inference "none". Every document is read with its literals as written
(tss_reader.shacl_validate keeps rdflib's literal normalisation off after each validation). An RDF
Message Log is validated as the merge of its messages (tss_reader.parse_document): pyshacl cannot
read RDF Messages. A document with embedded
sources is checked with SHACL/embedded_shapes.ttl, one with external sources with
SHACL/external_shapes.ttl, and one with both with both shape files.

The factory document of the metadata endpoint is NDA-derived and is never validated here.
"""
import json
import platform
import re
import sys
import tempfile
from pathlib import Path

import pyshacl
import rdflib
from rdflib import Graph
from rdflib.namespace import RDF, SH

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tss_reader as tr   # noqa: E402
from cases import INVALID, VALID   # noqa: E402

DEMO = HERE.parent.parent
ENDPOINT = DEMO / "metadata endpoint"
SPEC = DEMO / "spec" / "spec.bs"
EXCLUDED = {"factory_conductivity_tss2.ttl"}   # NDA-derived: never part of the public evaluation
RESULTS = HERE / "results"
SHAPES = {k: Graph().parse(DEMO / "SHACL" / f"{k}_shapes.ttl") for k in ("embedded", "external")}
EMBEDDED = {tr.TSS.EmbeddedRDFSource, tr.TSS.EmbeddedJSONSource}
EXTERNAL = {tr.TSS.CSVSource, tr.TSS.ParquetSource, tr.TSS.ArrowSource}
SPEC_PREFIXES = """@prefix tss: <https://w3id.org/tss#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
@prefix quantitykind: <http://qudt.org/vocab/quantitykind/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix ex: <https://example.org/ex#> .
"""


def check_id(text: str) -> str:
    m = re.match(r"\s*([SFDPQ]\d+)\b", text or "")
    return m.group(1) if m else (text or "?")[:40]


def validate(graph: Graph) -> dict:
    classes = set(graph.objects(None, RDF.type))
    kinds = [k for k, present in (("embedded", classes & EMBEDDED), ("external", classes & EXTERNAL)) if present]
    observed = set()
    reports = Graph()
    for kind in kinds or ["external"]:
        conforms, report, _ = tr.shacl_validate(graph, SHAPES[kind], inference="none",
                                                allow_warnings=True, abort_on_first=False)
        reports += report
        for result in report.objects(None, SH.result):
            shape = report.value(result, SH.sourceShape)
            name = SHAPES[kind].value(shape, SH.name) or report.value(result, SH.resultMessage)
            severity = str(report.value(result, SH.resultSeverity)).split("#")[-1]
            observed.add((check_id(str(name)), "V" if severity == "Violation" else "W"))
    return {"shapes": kinds, "violations": sorted(i for i, s in observed if s == "V"),
            "warnings": sorted(i for i, s in observed if s == "W"), "report": reports}


def graph_of(text: str, name: str) -> Graph:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_text(text, encoding="utf-8")
        graph, _ = tr.parse_document(path)
    return graph


def spec_examples() -> list:
    """The examples of the specification that describe a whole source (a source class and fields)."""
    spec = SPEC.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r"```turtle\n(.*?)```", spec, re.S):
        example = re.findall(r'<div class="example" id="([^"]+)"', spec[:m.start()])
        code = m.group(1)
        if re.search(r"^MESSAGE$", code, re.M) and "VERSION" not in code:
            code = 'VERSION "1.2-messages"\n' + code
        text = (code.replace('VERSION "1.2-messages"\n', 'VERSION "1.2-messages"\n' + SPEC_PREFIXES, 1)
                if "VERSION" in code else SPEC_PREFIXES + code)
        graph = graph_of(text, "example.ttl")
        if set(graph.objects(None, RDF.type)) & (EMBEDDED | EXTERNAL) and any(graph.objects(None, tr.TSS.hasField)):
            out.append((example[-1] if example else "?", graph))
    return out


def apply(case: dict) -> str:
    text = (ENDPOINT / case["base"]).read_text(encoding="utf-8")
    for old, new in case.get("edits", []):
        if old not in text:
            raise SystemExit(f"{case['id']}: {old!r} is not in {case['base']}")
        text = text.replace(old, new) if case.get("replace_all") else text.replace(old, new, 1)
    if "append_document" in case:
        text += "\n" + (ENDPOINT / case["append_document"]).read_text(encoding="utf-8")
    return text + case.get("append", "")


def main() -> None:
    (RESULTS / "reports").mkdir(parents=True, exist_ok=True)
    rows = []
    for name in VALID:
        if name in EXCLUDED:
            continue
        graph, _ = tr.parse_document(ENDPOINT / name)
        r = validate(graph)
        rows.append({"case": name, "kind": "valid document", "target": [], **{k: r[k] for k in ("shapes", "violations", "warnings")},
                     "passed": not r["violations"]})
        r["report"].serialize(RESULTS / "reports" / f"{Path(name).stem}.ttl", format="turtle")
    for example, graph in spec_examples():
        r = validate(graph)
        rows.append({"case": f"spec: {example}", "kind": "valid example", "target": [], **{k: r[k] for k in ("shapes", "violations", "warnings")},
                     "passed": not r["violations"]})
        r["report"].serialize(RESULTS / "reports" / f"spec-{example}.ttl", format="turtle")
    for case in INVALID:
        r = validate(graph_of(apply(case), case["base"]))
        observed = set(r["warnings"] if case.get("warning") else r["violations"])
        rows.append({"case": case["id"], "kind": "warning variant" if case.get("warning") else "invalid variant",
                     "base": case["base"], "category": case["category"], "what": case["what"],
                     "target": sorted(case["target"]), **{k: r[k] for k in ("shapes", "violations", "warnings")},
                     "passed": case["target"] <= observed and (not case.get("warning") or not r["violations"])})
        r["report"].serialize(RESULTS / "reports" / f"{case['id']}.ttl", format="turtle")

    versions = {"python": platform.python_version(), "pyshacl": pyshacl.__version__, "rdflib": rdflib.__version__,
                "configuration": "inference=none, allow_warnings=True, abort_on_first=False; RDF Message Logs as the merge of their messages"}
    (RESULTS / "validation_results.json").write_text(json.dumps({"versions": versions, "cases": rows}, indent=2) + "\n")
    passed = sum(r["passed"] for r in rows)
    lines = ["# Structural validation", "", f"Generated by `run_validation.py`. Validator: pyshacl {versions['pyshacl']} "
             f"(rdflib {versions['rdflib']}, Python {versions['python']}); {versions['configuration']}.", "",
             f"**{passed} of {len(rows)} cases behaved as expected.**", "",
             "| Case | Kind | Category | Error | Target | Violations | Warnings | Result |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['case']} | {r['kind']} | {r.get('category', '')} | {r.get('what', '')} | {', '.join(r['target']) or '-'} | "
                     f"{', '.join(r['violations']) or '-'} | {', '.join(r['warnings']) or '-'} | {'as expected' if r['passed'] else 'UNEXPECTED'} |")
    (RESULTS / "validation_summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
