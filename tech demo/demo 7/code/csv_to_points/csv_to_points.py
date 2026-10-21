"""Turn a CSV file into TSS points: one JSON array per value column.

Every row becomes one point in each column's array:

    {"time": "<the row's timestamp>", "value": "<the cell>"}

An empty cell gives no point. The timestamps and values stay text, exactly as
they are in the CSV (as in the TSS v1 examples).

Examples:
    python csv_to_points.py ../../data/factory_conductivity_altered.csv --time datetime
    python csv_to_points.py ../../data/waterinfo_289423042.csv --time Timestamp --columns Value
    python csv_to_points.py data.tsv --time time --delimiter tab
"""
import argparse
import csv
import json
import re
from pathlib import Path


def read_csv(path, delimiter):
    """The header and the rows of a CSV file.
    Without a delimiter, it is guessed from the header line (, ; tab or |).
    A file that can't be used gives a ValueError with the reason."""
    with open(path, encoding="utf-8-sig", newline="") as f:  # utf-8-sig: skips a BOM if there is one
        first_line = f.readline()
        if first_line.strip() == "":
            raise ValueError("the file is empty (no header line)")
        if delimiter is None:
            try:
                delimiter = csv.Sniffer().sniff(first_line, delimiters=",;\t|").delimiter
            except csv.Error:
                raise ValueError("could not guess the delimiter from the header line, give it with --delimiter")
        f.seek(0)
        rows = list(csv.reader(f, delimiter=delimiter))
    return rows[0], rows[1:]


def make_points(rows, time_index, value_index):
    """The JSON array for one column: one point per row that has a time and a value"""
    points = []
    for row in rows:
        time = row[time_index] if time_index < len(row) else ""
        value = row[value_index] if value_index < len(row) else ""
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


def file_name(column, taken):
    """A safe file name for a column that no other column uses yet,
    e.g. "Quality Code" -> Quality_Code.json, and a second "Quality Code" -> Quality_Code_2.json.
    taken holds the names in lower case: on Windows, a.json and A.json are the same file."""
    base = re.sub(r"[^\w.-]+", "_", column)
    name = base + ".json"
    number = 2
    while name.lower() in taken:
        name = f"{base}_{number}.json"
        number += 1
    return name


def main():
    parser = argparse.ArgumentParser(description="Make one JSON array of points per value column of a CSV file.")
    parser.add_argument("csv_file", help="the CSV file to read")
    parser.add_argument("--time", required=True, help="the name of the timestamp column (as in the CSV header)")
    parser.add_argument("--columns", nargs="+", help="only these value columns (default: every column except the time column)")
    parser.add_argument("--delimiter", help='the CSV delimiter, "tab" for a tab (default: guessed from the header line)')
    parser.add_argument("--out", help="the folder for the JSON files (default: <csv name>_points next to the CSV)")
    args = parser.parse_args()

    # A tab is hard to type on the command line, so "tab" and "\t" both mean a tab
    delimiter = args.delimiter
    if delimiter in ("tab", "\\t"):
        delimiter = "\t"
    if delimiter is not None and len(delimiter) != 1:
        parser.error(f"The delimiter must be one character (or \"tab\"), not '{args.delimiter}'")

    csv_path = Path(args.csv_file)
    try:
        header, rows = read_csv(csv_path, delimiter)
    except (OSError, ValueError) as error:  # OSError: e.g. the file does not exist
        parser.error(f"Can't read {csv_path}: {error}")

    if args.time not in header:
        parser.error(f"No column '{args.time}' in {csv_path}. The columns are: {', '.join(header)}")
    time_index = header.index(args.time)

    # The value columns, by position: two columns with the same name are still two columns
    value_indexes = []
    if args.columns is None:
        for index in range(len(header)):
            if index != time_index:
                value_indexes.append(index)
    else:
        for column in args.columns:
            if column not in header:
                parser.error(f"No column '{column}' in {csv_path}. The columns are: {', '.join(header)}")
            if column == args.time:
                parser.error(f"'{column}' is the time column, so it can't be a value column too")
            value_indexes.append(header.index(column))
    if len(value_indexes) == 0:
        parser.error(f"{csv_path} has no value columns, only the time column '{args.time}'")

    out_dir = Path(args.out) if args.out else csv_path.parent / f"{csv_path.stem}_points"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"{len(rows)} rows in {csv_path}, time column '{args.time}'")
    taken = []  # the file names used so far (lower case), so no column overwrites another one's file
    for index in value_indexes:
        column = header[index]
        name = file_name(column, taken)
        taken.append(name.lower())
        points = make_points(rows, time_index, index)
        write_points(points, out_dir / name)
        print(f"  {column}: {len(points)} points -> {out_dir / name}")


if __name__ == "__main__":
    main()
