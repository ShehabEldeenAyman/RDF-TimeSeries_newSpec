"""Check the Waterinfo metadata documents of demo 11 against the retrieved data.

    python fetch_waterinfo.py     # first: retrieve the selection (see manifest.json)
    python check_waterinfo.py     # writes results/waterinfo_report.json and .md

For each Waterinfo metadata document, the readings are read *through the description*, as the
specification says (tss_reader.py), and compared with the records of the Waterinfo service
(the raw KiWIS responses in data/raw/):

  D1 binding     the data file is found, and every field's column is in it, with a native type
                 that the specification allows for the field's datatype (Parquet)
  D2 values      every value is in the lexical form of its field's datatype; every time has a timezone
  D3 key         no two rows in the window have the same time
  D4 readings    every reading, and every describing value, equals the Waterinfo record of that
                 time; the times are the Waterinfo times, in UTC
  D5 coverage    every Waterinfo record in the window is a row (external sources); an embedded
                 source holds a sample, so its rows are only checked against the records (D4)
  D6 summaries   every stored summary equals the summary recomputed from the rows in the window

The factory document of the metadata endpoint is NDA-derived: it is never read here.
"""
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tss_reader as tr   # noqa: E402
from tss_reader import TSS, XSD   # noqa: E402

DEMO = HERE.parent.parent
ENDPOINT = DEMO / "metadata endpoint"
RAW = DEMO / "data" / "raw"
RESULTS = HERE / "results"
EXCLUDED = {"factory_conductivity_tss2.ttl"}   # NDA-derived: never part of the public evaluation
DOCUMENTS = sorted(p for p in ENDPOINT.glob("waterinfo_*.ttl") if p.name not in EXCLUDED)
# Native Parquet/Arrow types (pyarrow) and the datatypes the specification allows for them.
ALLOWED = {"int64": {XSD.integer, XSD.long}, "int32": {XSD.integer, XSD.int, XSD.long},
           "double": {XSD.double}, "float": {XSD.float, XSD.double},
           "timestamp[us, tz=UTC]": {XSD.dateTime, XSD.dateTimeStamp},
           "timestamp[ms, tz=UTC]": {XSD.dateTime, XSD.dateTimeStamp},
           "string": None, "large_string": None}       # None: any datatype that reads a text
# The Waterinfo fields that the describing fields hold, by column name.
RECORD = {"Value": 1, "Interpolation Type": 2, "Quality Code": 3, "Quality Code Name": 4,
          "Quality Code Description": 5, "Quality Code Color": 6}
TOLERANCE = Decimal("1e-12")     # relative, for means and medians stored as xsd:double


def waterinfo_records(ts_id: str) -> dict:
    """The records of the Waterinfo service for a series, by time (UTC)."""
    records = {}
    for path in sorted(RAW.glob(f"{ts_id}_*.json")):
        for record in json.loads(path.read_text())[0]["data"]:
            records[tr.parse_time(record[0])] = record
    return records


def waterinfo_field(source, f, prop):
    """The Waterinfo field that a describing field means, from its label (rdfs:label), not from
    the column it reads: a field bound to the wrong column is then compared with the Waterinfo
    field it claims to hold, and the binding error shows."""
    from rdflib.namespace import RDFS
    label = str(source.value(RDFS.label, f) or "").lower()
    for prefix, key in (("quality code colo", "Quality Code Color"), ("quality code description", "Quality Code Description"),
                        ("quality code name", "Quality Code Name"), ("quality code", "Quality Code"),
                        ("interpolation type", "Interpolation Type")):
        if label.startswith(prefix):
            return key
    return None


def same_number(a, b) -> bool:
    return a == b or (b != 0 and abs((a - b) / b) <= TOLERANCE)


def check_document(path: Path) -> dict:
    graph, messages = tr.parse_document(path)
    ts_id = re.match(r"waterinfo_(\d+)_", path.name).group(1)
    records = waterinfo_records(ts_id)
    report = {"document": path.name, "ts_id": ts_id, "message_log": messages is not None,
              "messages": len(messages) if messages else None, "sources": []}
    for source in tr.sources(graph, path):
        checks = {k: [] for k in ("D1 binding", "D2 values", "D3 key", "D4 readings", "D5 coverage", "D6 summaries")}
        result = {"source": str(source.iri).split("#")[-1], "class": str(source.kind).split("#")[-1],
                  "data_file": None if source.data_path is None else
                  (str(source.data_path.relative_to(DEMO)) if source.data_path.is_relative_to(DEMO) else str(source.data_path)),
                  "checks": checks}
        # D1: the data file and its columns
        if source.kind == TSS.ParquetSource:
            import pyarrow.parquet as pq
            schema = pq.read_schema(source.data_path)
            for f in source.fields:
                name = source.column(f)
                if name is None:
                    continue
                if name not in schema.names:
                    checks["D1 binding"].append(f"no column {name!r}")
                    continue
                allowed = ALLOWED.get(str(schema.field(name).type), set())
                if allowed is not None and source.datatype(f) not in allowed:
                    checks["D1 binding"].append(f"column {name!r} is {schema.field(name).type}, "
                                                f"which does not allow {source.datatype(f)}")
        try:
            rows = tr.read_rows(source)
        except tr.ReadError as error:
            checks["D1 binding"].append(str(error))
            result["rows_read"] = 0
            report["sources"].append(result)
            continue
        window = tr.in_window(source, rows)
        result["rows_read"], result["rows_in_window"] = len(rows), len(window)
        # D2: lexical forms and timezones
        bad = [(r.time, str(f).split("#")[-1], v) for r in window for f, v in r.values.items()
               if v is not None and not tr.valid_lexical(v, source.datatype(f))]
        bad += [(None, "time", "row without a time with a timezone") for r in rows if r.time is None]
        checks["D2 values"] = [f"{t} {f}: {v!r}" for t, f, v in bad[:10]] + ([f"... {len(bad)} in all"] if len(bad) > 10 else [])
        # D3: the key
        times = [r.time for r in window]
        if len(times) != len(set(times)):
            checks["D3 key"].append(f"{len(times) - len(set(times))} duplicate times")
        # D4: every reading and describing value equals the Waterinfo record of its time
        describing = source.describing()
        mismatches = []
        for row in window:
            record = records.get(row.time)
            if record is None:
                mismatches.append(f"{row.time}: no Waterinfo record at this time")
                continue
            for f in source.value_fields:
                v = row.values.get(f)
                if v is not None and Decimal(v) != Decimal(repr(float(record[1]))):
                    mismatches.append(f"{row.time}: reading {v} != Waterinfo {record[1]}")
            for f, prop in describing.items():
                v, key = row.values.get(f), waterinfo_field(source, f, prop)
                if v is not None and key is not None and str(record[RECORD[key]]) != v:
                    mismatches.append(f"{row.time}: {key} {v!r} != Waterinfo {record[RECORD[key]]!r}")
        checks["D4 readings"] = mismatches[:10] + ([f"... {len(mismatches)} in all"] if len(mismatches) > 10 else [])
        result["readings_compared"] = sum(1 for r in window for f in source.value_fields if r.values.get(f) is not None)
        # D5: coverage of the Waterinfo records in the window (external sources)
        if source.kind in (TSS.CSVSource, TSS.ParquetSource, TSS.ArrowSource):
            start, end = source.window
            expected = {t for t in records if (start is None or t >= start) and (end is None or t < end)}
            missing = sorted(expected - set(times))
            if missing:
                checks["D5 coverage"].append(f"{len(missing)} Waterinfo records are not rows, first {missing[0]}")
            result["waterinfo_records_in_window"] = len(expected)
        # D6: stored summaries
        compared = 0
        for f in source.fields:
            stored = tr.stored_summary(source, f)
            if stored is None:
                continue
            computed = tr.summarise(source, window, f)
            for key, value in stored.items():
                if key == "generatedAtTime":
                    continue
                compared += 1
                got = computed.get(key)
                ok = got == value if key in ("count", "missing", "firstTime", "lastTime", "observedMinimum",
                                             "observedMaximum") else (got is not None and same_number(got, value))
                if not ok:
                    checks["D6 summaries"].append(f"{str(f).split('#')[-1]} {key}: stored {value}, computed {got}")
        result["summary_values_compared"] = compared
        result["passed"] = not any(checks.values())
        report["sources"].append(result)
    return report


def main() -> None:
    if not RAW.is_dir():
        raise SystemExit("run fetch_waterinfo.py first")
    reports = [check_document(p) for p in DOCUMENTS]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "waterinfo_report.json").write_text(json.dumps(reports, indent=2, default=str) + "\n")
    lines = ["# Waterinfo metadata documents against the retrieved data", "",
             "Generated by `check_waterinfo.py`. Checks D1-D6 are described in that script.", "",
             "| Document | Class | Rows in window | Readings compared | Summary values compared | Result |",
             "|---|---|---|---|---|---|"]
    for report in reports:
        for s in report["sources"]:
            failed = [k for k, v in s["checks"].items() if v]
            lines.append(f"| `{report['document']}` | {s['class']} | {s.get('rows_in_window', 0)} | "
                         f"{s.get('readings_compared', 0)} | {s.get('summary_values_compared', 0)} | "
                         f"{'pass' if s['passed'] else 'fail: ' + ', '.join(failed)} |")
    problems = [(r["document"], k, m) for r in reports for s in r["sources"] for k, v in s["checks"].items() for m in v]
    lines += ["", "## Discrepancies", ""] + ([f"- `{d}` {k}: {m}" for d, k, m in problems] or ["None."])
    (RESULTS / "waterinfo_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
