"""A tour of rdftss with the example documents. Run it from anywhere:

    python examples/quickstart.py

It reads a snippet, analyses it, queries it with SQL, finds snippets across documents, expands
one into observations and converts it to Parquet. Files it writes go to a temporary folder.
"""
import tempfile
from pathlib import Path

import rdftss

HERE = Path(__file__).resolve().parent


def title(text: str) -> None:
    print(f"\n=== {text} " + "=" * max(0, 70 - len(text)))


def main() -> None:
    out = Path(tempfile.mkdtemp(prefix="rdftss-quickstart-"))

    title("1. Load a document and describe its snippet")
    document = rdftss.load(HERE / "readings_csv.ttl")
    snippet = document.snippet()
    print(snippet.describe())

    title("2. Read the points")
    points = snippet.read()
    print(points)
    for point in points[:3]:
        print(" ", point)

    title("3. Analyse them")
    print(points.statistics("value"))
    print(points.gaps())
    print("Mean per 6 hours:")
    for item in points.resample("6h", "value"):
        print(" ", item)
    print("Readings marked 'suspect':", len(points.where("quality", equals="suspect")))

    title("4. The stored summary of the specification's example is up to date")
    embedded = rdftss.load(HERE / "embedded_rdf.ttl").snippet()
    print(embedded.field("value-field").summary)
    print("Differences from the data:", embedded.statistics().differences() or "none")

    title("5. A DataFrame")
    try:
        print(snippet.to_pandas().head())
    except rdftss.MissingDependencyError as error:
        print(error)

    title("6. SQL")
    try:
        print(snippet.sql("SELECT quality, count(*) AS n, round(avg(value), 2) AS mean FROM points GROUP BY quality"))
    except rdftss.MissingDependencyError as error:
        print(error)

    title("7. Find snippets across documents")
    documents = [rdftss.load(path) for path in sorted(HERE.glob("*.ttl"))]
    for match in rdftss.find(documents, observed_property="quantitykind:ElectricConductivity",
                             overlapping=("2024-05-12T08:00:00Z", "2024-05-12T08:30:00Z"), kind="rdf"):
        print(" ", match)

    title("8. Expand into SOSA observations")
    result = embedded.expand(out / "observations.nt")
    print(result)
    print(result.preview.split("\n\n")[-2])

    title("9. Convert to Parquet, on a time grid")
    try:
        converted = rdftss.convert(snippet, "parquet", document=out / "readings.ttl", time_grid="auto")
        print(converted.snippet().describe())
        print("Same points:", [p["value"] for p in converted.snippet().read()] == [p["value"] for p in points])
    except rdftss.MissingDependencyError as error:
        print(error)
    print(f"\nFiles written to {out}")


if __name__ == "__main__":
    main()
