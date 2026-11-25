"""Check that check_waterinfo.py finds errors: run it on copies of the Waterinfo metadata
documents with one deliberate error each, and compare the failed checks with the expected ones.

    python selftest_waterinfo.py      # writes results/selftest_report.md

The copies are written to a temporary directory, next to a link to the data directory, so their
relative tss:dataFile references still resolve.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_waterinfo as cw   # noqa: E402

ENDPOINT = cw.ENDPOINT
CASES = [
    # (id, document, text in the document, replacement, the check that must fail)
    ("W1", "waterinfo_289423042_tss2.ttl", 'tss:columnName "Value"', 'tss:columnName "Valeur"', "D1",
     "the value field names a column that is not in the CSV file"),
    ("W2", "waterinfo_289423042_tss2.ttl", 'tss:datatype xsd:double', 'tss:datatype xsd:integer', "D2",
     "the value field is typed xsd:integer, but the readings have decimals"),
    ("W3", "waterinfo_289441042_tss2_points_embedded_json.ttl", '"time": "2021-03-03T23:30:00Z"',
     '"time": "2021-03-03T23:15:00Z"', "D3", "two JSON rows have the same time"),
    ("W4", "waterinfo_289441042_tss2_points_embedded.ttl", 'tss:value "4797.72"^^xsd:double',
     'tss:value "4797.73"^^xsd:double', "D4", "an RDF point states a reading that Waterinfo does not have"),
    ("W5", "waterinfo_289441042_tss2_points_embedded.ttl", 'ex:qualityCodeColor "rgba(0, 128, 0, 1.0)"^^xsd:string',
     'ex:qualityCodeColor "red"^^xsd:string', "D4", "an RDF point states another quality colour"),
    ("W6", "waterinfo_289441042_tss2_points_embedded_json.ttl", '"qualityColor": "rgba(0, 128, 0, 1.0)"',
     '"qualityColor": "red"', "D4", "a JSON row states another quality colour"),
    ("W7", "waterinfo_289423042_tss2.ttl", 'tss:columnName "Quality Code"', 'tss:columnName "Interpolation Type"', "D4",
     "the quality code field reads the column of the interpolation type (CSV)"),
    ("W8", "waterinfo_289429042_tss2_parquet.ttl", 'tss:columnName "Quality Code Name"', 'tss:columnName "Quality Code Color"', "D4",
     "the quality code name field reads the column of the quality colour (Parquet)"),
    ("W9", "waterinfo_289441042_tss2_value_delta.ttl", 'tss:baseline "4750"^^xsd:double', 'tss:baseline "4751"^^xsd:double', "D4",
     "the baseline is off by one, so every decoded reading is"),
    ("W10", "waterinfo_289441042_tss2_time_step.ttl", 'tss:timeStep "PT15M"^^xsd:duration', 'tss:timeStep "PT30M"^^xsd:duration', "D4",
     "the time step is doubled, so the times of the rows are wrong"),
    ("W11", "waterinfo_289423042_tss2.ttl", 'tss:count "94853"^^xsd:integer', 'tss:count "94852"^^xsd:integer', "D6",
     "a stored count is off by one"),
    ("W12", "waterinfo_289429042_tss2_parquet.ttl", 'tss:from "2021-03-03T23:15:00Z"', 'tss:from "2022-01-01T00:00:00Z"', "D6",
     "the window starts later, so the stored summaries no longer describe it"),
]


def main() -> None:
    rows, ok = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        meta = Path(tmp) / "meta"
        meta.mkdir()
        (Path(tmp) / "data").symlink_to(cw.DEMO / "data")
        for case, name, old, new, expected, what in CASES:
            text = (ENDPOINT / name).read_text(encoding="utf-8")
            if old not in text:
                raise SystemExit(f"{case}: {old!r} not in {name}")
            path = meta / name
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            report = cw.check_document(path)
            failed = sorted({k.split()[0] for s in report["sources"] for k, v in s["checks"].items() if v})
            found = expected in failed
            ok += found
            rows.append(f"| {case} | `{name}` | {what} | {expected} | {', '.join(failed) or '-'} | {'found' if found else 'MISSED'} |")
    lines = ["# Self-test of check_waterinfo.py", "",
             f"{ok} of {len(CASES)} deliberate errors were found.", "",
             "| Case | Document | Error | Expected | Failed checks | Result |", "|---|---|---|---|---|---|"] + rows
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "selftest_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    if ok != len(CASES):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
