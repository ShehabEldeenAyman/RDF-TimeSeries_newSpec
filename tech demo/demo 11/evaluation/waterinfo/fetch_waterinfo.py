"""Retrieve the Waterinfo selection of the evaluation and build the data files of the demo 11
metadata documents.

    python fetch_waterinfo.py            # writes ../../data/ and manifest.json

The readings are public EC20 (electrical conductivity at 20 °C, µS/cm) series of four monitoring
locations along the Ghent-Terneuzen Canal, retrieved from the Waterinfo.be KiWIS web service
without authentication. The selection is the time window that each metadata document declares.

The data files are written to `tech demo/demo 11/data/`, which git does not track. The manifest
(`manifest.json`, tracked) records every request, the retrieval time, the station metadata, and
the number of rows and the SHA-256 of every file, so that a later retrieval can be compared.

Only the public Waterinfo series are retrieved. The factory document of the metadata endpoint is
NDA-derived and is never part of this evaluation.
"""
import csv
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

HERE = Path(__file__).resolve().parent
DATA = HERE.parent.parent / "data"
RAW = DATA / "raw"
API = "https://download.waterinfo.be/tsmdownload/KiWIS/KiWIS"
FIELDS = ["Timestamp", "Value", "Interpolation Type", "Quality Code", "Quality Code Name",
          "Quality Code Description", "Quality Code Color"]
HEADERS = {"User-Agent": "rdf-tss-evaluation/1.0"}

# The selection: the window [from, until) that each metadata document declares.
SERIES = [
    {"ts_id": "289423042", "name": "Grootdok (BE)", "from": "2021-03-03T23:45:00Z",
     "until": "2025-12-18T23:15:00Z", "file": "waterinfo_289423042.csv", "format": "csv",
     "documents": ["waterinfo_289423042_tss2.ttl"]},
    {"ts_id": "289429042", "name": "Rodenhuizedok (BE)", "from": "2021-03-03T23:15:00Z",
     "until": "2026-01-01T00:00:00Z", "file": "waterinfo_289429042.parquet", "format": "parquet",
     "documents": ["waterinfo_289429042_tss2_parquet.ttl"]},
    {"ts_id": "289435042", "name": "Autrichehaven (NL)", "from": "2021-03-03T23:15:00Z",
     "until": "2025-10-07T07:45:00Z", "file": "waterinfo_289435042.csv", "format": "csv",
     "documents": ["waterinfo_289435042_tss2.ttl"]},
    {"ts_id": "289441042", "name": "Zevenaarshaven (NL)", "from": "2021-03-03T23:15:00Z",
     "until": "2021-03-05T00:00:00Z", "file": "waterinfo_289441042.csv", "format": "csv",
     "documents": ["waterinfo_289441042_tss2_points_embedded.ttl",
                   "waterinfo_289441042_tss2_points_embedded_json.ttl",
                   "waterinfo_289441042_tss2_points_time_value.ttl"]},
]
# Derived representations of one day (2021-03-04, UTC) of the Zevenaarshaven series.
DAY = ("2021-03-04T00:00:00Z", "2021-03-05T00:00:00Z")
GRID_BASE, GRID_STEP = DAY[0], timedelta(minutes=15)
BASELINE = Decimal("4750")


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def utc_text(value: datetime) -> str:
    """The XSD canonical form of a UTC time: no fraction when there is none, and Z."""
    text = value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        text += f".{value.microsecond:06d}".rstrip("0")
    return text + "Z"


def double_text(value) -> str:
    """A JSON number as an xsd:double lexical form (the shortest that reads back the same)."""
    return "" if value is None else repr(float(value))


def fetch(series: dict, requests_log: list) -> list:
    """All rows of a series in its window, in UTC, one request per calendar year."""
    start, end = moment(series["from"]), moment(series["until"])
    rows, year = {}, start.year
    while datetime(year, 1, 1, tzinfo=timezone.utc) < end:
        chunk_from = max(start, datetime(year, 1, 1, tzinfo=timezone.utc))
        chunk_to = min(end, datetime(year + 1, 1, 1, tzinfo=timezone.utc))
        params = {"service": "kisters", "type": "queryServices", "request": "getTimeseriesValues",
                  "datasource": "1", "format": "json", "timezone": "UTC", "ts_id": series["ts_id"],
                  "from": utc_text(chunk_from), "to": utc_text(chunk_to), "returnfields": ",".join(FIELDS)}
        response = requests.get(API, params=params, headers=HEADERS, timeout=120)
        response.raise_for_status()
        raw = RAW / f"{series['ts_id']}_{year}.json"
        raw.write_bytes(response.content)
        body = response.json()[0]
        if body["columns"].split(",") != FIELDS:
            raise SystemExit(f"unexpected columns for {series['ts_id']}: {body['columns']}")
        for record in body["data"]:
            when = moment(record[0])
            if start <= when < end:             # the window: [from, until); KiWIS includes `to`
                rows[when] = record
        requests_log.append({"url": response.url, "rows_returned": len(body["data"]),
                             "raw_file": str(raw.relative_to(DATA.parent)), "sha256": sha256(raw)})
        year += 1
        time.sleep(1)                           # be gentle with the public service
    return [rows[when] for when in sorted(rows)]


def table_rows(series: dict, records: list) -> list:
    """The rows of the data file: ts_id first, then the KiWIS fields, as text."""
    out = []
    for timestamp, value, interpolation, code, name, description, color in records:
        out.append([series["ts_id"], utc_text(moment(timestamp)), double_text(value),
                    "" if interpolation is None else str(interpolation),
                    "" if code is None else str(code), name or "", description or "", color or ""])
    return out


def write_csv(path: Path, header: list, rows: list) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_parquet(path: Path, series: dict, records: list) -> None:
    """Native Parquet types: INT64, TIMESTAMP(isAdjustedToUTC=true, MICROS), DOUBLE, STRING."""
    schema = pa.schema([("ts_id", pa.int64()), ("Timestamp", pa.timestamp("us", tz="UTC")),
                        ("Value", pa.float64()), ("Interpolation Type", pa.int64()),
                        ("Quality Code", pa.int64()), ("Quality Code Name", pa.string()),
                        ("Quality Code Description", pa.string()), ("Quality Code Color", pa.string())])
    columns = list(zip(*records)) if records else [[]] * 7
    table = pa.table([
        pa.array([int(series["ts_id"])] * len(records), pa.int64()),
        pa.array([moment(t) for t in columns[0]], pa.timestamp("us", tz="UTC")),
        pa.array([None if v is None else float(v) for v in columns[1]], pa.float64()),
        pa.array(columns[2], pa.int64()), pa.array(columns[3], pa.int64()),
        pa.array(columns[4], pa.string()), pa.array(columns[5], pa.string()),
        pa.array(columns[6], pa.string())], schema=schema)
    pq.write_table(table, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def station_metadata() -> list:
    params = {"service": "kisters", "type": "queryServices", "request": "getTimeseriesList",
              "datasource": "1", "format": "json", "ts_id": ",".join(s["ts_id"] for s in SERIES),
              "returnfields": "ts_id,ts_name,station_name,station_no,stationparameter_name,"
                              "ts_unitsymbol,station_latitude,station_longitude,coverage"}
    response = requests.get(API, params=params, headers=HEADERS, timeout=60)
    response.raise_for_status()
    header, *rows = response.json()
    return [dict(zip(header, row)) | {"request": response.url} for row in rows]


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = {"retrieved": utc_text(datetime.now(timezone.utc).replace(microsecond=0)),
                "service": API, "access": "public web service, no authentication",
                "selection": "the time window [from, until) declared by each metadata document",
                "stations": station_metadata(), "series": [], "derived": []}
    header = ["ts_id"] + FIELDS
    by_id = {}
    for series in SERIES:
        log = []
        records = fetch(series, log)
        by_id[series["ts_id"]] = records
        path = DATA / series["file"]
        if series["format"] == "csv":
            write_csv(path, header, table_rows(series, records))
        else:
            write_parquet(path, series, records)
        manifest["series"].append({k: series[k] for k in ("ts_id", "name", "from", "until", "documents")}
                                  | {"file": f"data/{series['file']}", "rows": len(records),
                                     "sha256": sha256(path), "requests": log})
        print(f"{series['ts_id']} {series['name']}: {len(records)} rows -> data/{series['file']}")

    # One day of Zevenaarshaven, as a time grid with a step column, and as differences from a baseline.
    day = [r for r in by_id["289441042"] if moment(DAY[0]) <= moment(r[0]) < moment(DAY[1])]
    base = moment(GRID_BASE)
    grid = DATA / "waterinfo_289441042_time_step.csv"
    write_csv(grid, ["step", "value"],
              [[str((moment(r[0]) - base) // GRID_STEP), double_text(r[1])] for r in day])
    delta = DATA / "waterinfo_289441042_value_delta.csv"
    write_csv(delta, ["time", "value_delta"],
              [[utc_text(moment(r[0])), str(Decimal(double_text(r[1])) - BASELINE)] for r in day])
    for path, how in ((grid, f"rows of {DAY[0]}..{DAY[1]}; step = (time - {GRID_BASE}) / PT15M"),
                      (delta, f"rows of {DAY[0]}..{DAY[1]}; value_delta = value - {BASELINE}, exact decimal")):
        manifest["derived"].append({"file": f"data/{path.name}", "derived_from": "data/waterinfo_289441042.csv",
                                    "transformation": how, "rows": len(day), "sha256": sha256(path)})
        print(f"derived: {len(day)} rows -> data/{path.name}")
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print("manifest.json written")


if __name__ == "__main__":
    main()
