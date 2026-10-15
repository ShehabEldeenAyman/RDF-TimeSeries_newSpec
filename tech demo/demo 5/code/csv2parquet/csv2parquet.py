"""Turn a CSV file into a Parquet file.

DuckDB reads the CSV: it detects the delimiter, the header and the type of
every column (numbers, timestamps, text) by itself, and writes them to Parquet.
Needs duckdb (pip install duckdb).

Examples:
    python csv2parquet.py ../../data/waterinfo_289423042.csv output/waterinfo_289423042.parquet
    python csv2parquet.py ../../data/factory_conductivity.csv output/
"""
import argparse
from pathlib import Path

import duckdb


def main():
    parser = argparse.ArgumentParser(description="Turn a CSV file into a Parquet file.")
    parser.add_argument("csv_file", help="the CSV file to read")
    parser.add_argument("output", help="the Parquet file to write, or a folder (then: <csv name>.parquet in that folder)")
    args = parser.parse_args()

    csv_path = Path(args.csv_file)
    if not csv_path.is_file():
        parser.error(f"No CSV file at {csv_path}")

    # A folder (an existing one, or a path ending in / or \) gets <csv name>.parquet inside it
    out_path = Path(args.output)
    if out_path.is_dir() or args.output.endswith(("/", "\\")):
        out_path = out_path / f"{csv_path.stem}.parquet"
    if out_path.resolve() == csv_path.resolve():
        parser.error("The output can't be the CSV file itself")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with duckdb.connect() as con:
            table = con.read_csv(str(csv_path))
            table.write_parquet(str(out_path))
            rows = table.shape[0]
            columns = table.columns
            types = table.types
    except duckdb.Error as error:
        parser.error(f"Can't convert {csv_path}: {error}")

    print(f"{rows} rows, {len(columns)} columns: {csv_path} -> {out_path}")
    for index in range(len(columns)):
        print(f"  {columns[index]}: {types[index]}")


if __name__ == "__main__":
    main()
