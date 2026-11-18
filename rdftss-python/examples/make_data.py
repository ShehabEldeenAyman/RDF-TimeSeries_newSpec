"""Write the example data files in examples/sample-data/ (run: python make_data.py).

One synthetic day (2024-05-12, UTC) of conductivity readings every 15 minutes, with gaps, and a
quality code per reading. The same readings are written in every way the examples use:

- readings.csv            CSV with a header line and ";" as delimiter
- readings_no_header.csv  CSV without a header line
- readings_grid.csv       CSV on a time grid: a "step" column instead of a time column
- readings_delta.csv      CSV whose values are differences from a baseline of 4750
- readings.parquet        Parquet: a UTC timestamp, a DOUBLE and a STRING column
- readings.arrow          Arrow IPC file: nanosecond UTC timestamps, a dictionary-encoded quality

The data is made up: it is only there to show and test rdftss.
"""
import csv
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "sample-data"
START = datetime(2024, 5, 12, tzinfo=timezone.utc)
MISSING_STEPS = set(range(20, 24)) | {50} | set(range(70, 76))
BASELINE = Decimal("4750")


def readings():
    """(step, time text, value text, quality) for each reading."""
    rows = []
    for step in range(96):
        if step in MISSING_STEPS:
            continue
        moment = START + timedelta(minutes=15 * step)
        value = 4750 + 60 * math.sin(2 * math.pi * step / 96) + ((step * 7) % 13 - 6) / 2
        quality = "suspect" if step % 29 == 0 else "good"
        rows.append((step, moment.strftime("%Y-%m-%dT%H:%M:%SZ"), f"{value:.2f}", quality))
    return rows


def write_csv(name, header, rows, delimiter=","):
    with open(DATA / name, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=delimiter, lineterminator="\n")
        if header:
            writer.writerow(header)
        writer.writerows(rows)


def main():
    DATA.mkdir(exist_ok=True)
    rows = readings()
    write_csv("readings.csv", ["time", "value", "quality"], [(t, v, q) for _, t, v, q in rows], delimiter=";")
    write_csv("readings_no_header.csv", None, [(t, v, q) for _, t, v, q in rows])
    write_csv("readings_grid.csv", ["step", "value", "quality"], [(s, v, q) for s, _, v, q in rows])
    write_csv("readings_delta.csv", ["time", "value_delta", "quality"],
              [(t, str(Decimal(v) - BASELINE), q) for _, t, v, q in rows])

    import pyarrow as pa
    import pyarrow.ipc
    import pyarrow.parquet as pq

    times = [datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for _, t, _, _ in rows]
    values = [float(v) for _, _, v, _ in rows]
    qualities = [q for _, _, _, q in rows]
    table = pa.table({
        "time": pa.array(times, type=pa.timestamp("us", tz="UTC")),
        "value": pa.array(values, type=pa.float64()),
        "quality": pa.array(qualities, type=pa.string()),
    })
    pq.write_table(table, DATA / "readings.parquet")

    arrow_table = pa.table({
        "time": pa.array(times, type=pa.timestamp("ns", tz="UTC")),
        "value": pa.array(values, type=pa.float64()),
        "quality": pa.array(qualities, type=pa.string()).dictionary_encode(),
    })
    with pa.OSFile(str(DATA / "readings.arrow"), "wb") as sink:
        with pyarrow.ipc.new_file(sink, arrow_table.schema) as writer:
            writer.write_table(arrow_table)
    print(f"Wrote {len(rows)} readings in 6 files to {DATA}")


if __name__ == "__main__":
    main()
