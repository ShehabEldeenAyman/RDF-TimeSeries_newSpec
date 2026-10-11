"""Turn a CSV file into TSS points: one JSON array per value column.

Every row becomes one point in each column's array:

    {"time": "<the row's timestamp>", "value": "<the cell>"}

An empty cell gives no point. The timestamps and values stay text, exactly as
they are in the CSV (as in the TSS v1 examples).

Examples:
    python csv_to_points.py ../../data/factory_conductivity_altered.csv --time datetime
    python csv_to_points.py ../../data/waterinfo_289423042.csv --time Timestamp --columns Value
"""
import argparse
import csv
import json
import re
from pathlib import Path


def read_csv(path, delimiter):
    """The header and the rows of a CSV file.
    Without a delimiter, it is guessed from the header line (, ; tab or |)."""
    with open(path, encoding="utf-8-sig", newline="") as f:  # utf-8-sig: skips a BOM if there is one
        if delimiter is None:
            delimiter = csv.Sniffer().sniff(f.readline(), delimiters=",;\t|").delimiter
            f.seek(0)
        rows = list(csv.reader(f, delimiter=delimiter))
    return rows[0], rows[1:]


def make_points(header, rows, time_column, column):
    """The JSON array for one column: one point per row that has a value"""
    t = header.index(time_column)
    v = header.index(column)
    points = []
    for row in rows:
        time = row[t] if t < len(row) else ""
        value = row[v] if v < len(row) else ""
        if time == "" or value == "":
            continue  # no time or no value: no reading in this row
        points.append({"time": time, "value": value})
    return points


def write_points(points, path):
    """Write the array with one point per line, like the TSS examples"""
    with open(path, "w", encoding="utf-8") as f:
        if len(points) == 0:
            f.write("[]\n")
            return
        lines = []
        for point in points:
            lines.append("    " + json.dumps(point, ensure_ascii=False))
        f.write("[\n" + ",\n".join(lines) + "\n]\n")


def file_name(column):
    """A safe file name for a column, e.g. "Quality Code" -> Quality_Code.json"""
    return re.sub(r"[^\w.-]+", "_", column) + ".json"


def main():
    parser = argparse.ArgumentParser(description="Make one JSON array of points per value column of a CSV file.")
    parser.add_argument("csv_file", help="the CSV file to read")
    parser.add_argument("--time", required=True, help="the name of the timestamp column (as in the CSV header)")
    parser.add_argument("--columns", nargs="+", help="only these value columns (default: every column except the time column)")
    parser.add_argument("--delimiter", help="the CSV delimiter (default: guessed from the header line)")
    parser.add_argument("--out", help="the folder for the JSON files (default: <csv name>_points next to the CSV)")
    args = parser.parse_args()

    csv_path = Path(args.csv_file)
    header, rows = read_csv(csv_path, args.delimiter)

    if args.time not in header:
        parser.error(f"No column '{args.time}' in {csv_path}. The columns are: {', '.join(header)}")
    if args.columns is None:
        columns = []
        for column in header:
            if column != args.time:
                columns.append(column)
    else:
        columns = args.columns
        for column in columns:
            if column not in header:
                parser.error(f"No column '{column}' in {csv_path}. The columns are: {', '.join(header)}")

    out_dir = Path(args.out) if args.out else csv_path.parent / f"{csv_path.stem}_points"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"{len(rows)} rows in {csv_path}, time column '{args.time}'")
    for column in columns:
        points = make_points(header, rows, args.time, column)
        path = out_dir / file_name(column)
        write_points(points, path)
        print(f"  {column}: {len(points)} points -> {path}")


if __name__ == "__main__":
    main()
