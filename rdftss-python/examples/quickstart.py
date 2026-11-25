"""A tour of rdftss with the example documents. Run it from anywhere:

    python examples/quickstart.py

It reads a source, analyses it, queries it with SPARQL, finds sources across documents, expands
one into points (and the SOSA observations they align with), converts it to Parquet, and checks
documents against the specification.
Files it writes go to a temporary folder.
"""
import tempfile
from pathlib import Path

import rdftss

HERE = Path(__file__).resolve().parent


def title(text: str) -> None:
    print(f"\n=== {text} " + "=" * max(0, 70 - len(text)))


def main() -> None:
    out = Path(tempfile.mkdtemp(prefix="rdftss-quickstart-"))

    title("1. Load a document and describe its source")
    document = rdftss.load(HERE / "readings_csv.ttl")
    source = document.source()
    print(source.describe())

    title("2. Read the rows")
    rows = source.read()
    print(rows)
    for row in rows[:3]:
        print(" ", row)

    title("3. Analyse them")
    print(rows.statistics("value"))
    print(rows.gaps())
    print("Mean per 6 hours:")
    for item in rows.resample("6h", "value"):
        print(" ", item)
    print("Readings marked 'suspect':", len(rows.where("quality", equals="suspect")))

    title("4. The stored summary of the specification's example is up to date")
    embedded = rdftss.load(HERE / "embedded_rdf.ttl").source()
    print(embedded.field("value-field").summary)
    print("Differences from the data:", embedded.statistics().differences() or "none")

    title("5. A DataFrame")
    try:
        print(source.to_pandas().head())
    except rdftss.MissingDependencyError as error:
        print(error)

    title("6. SPARQL")
    print(source.sparql("""
        SELECT ?quality (COUNT(?point) AS ?n) (ROUND(AVG(?value) * 100) / 100 AS ?mean)
        WHERE {
            ex:csv-quality-field tss:property ?quality_property .
            ?point tss:field ex:csv-value-field ; tss:value ?value ; ?quality_property ?quality .
        }
        GROUP BY ?quality ORDER BY DESC(?n)
    """))

    title("7. Find sources across documents")
    documents = [rdftss.load(path) for path in sorted(HERE.glob("*.ttl"))]
    for match in rdftss.find(documents, observed_property="quantitykind:ElectricConductivity",
                             overlapping=("2024-05-12T08:00:00Z", "2024-05-12T08:30:00Z"), kind="rdf"):
        print(" ", match)

    title("8. Expand into points, with the SOSA observations they align with")
    result = embedded.expand(out / "points.nt", derive=True)
    print(result)
    first = next(embedded.points())
    print(f"The first point: {first.field.name} at {first.time.text}: {first.value}")

    title("9. Convert to Parquet, on a time grid")
    try:
        converted = rdftss.convert(source, "parquet", document=out / "readings.ttl", time_grid="auto")
        print(converted.source().describe())
        print("Same rows:", [row["value"] for row in converted.source().read()] == [row["value"] for row in rows])
    except rdftss.MissingDependencyError as error:
        print(error)
    title("10. Check the documents against the specification")
    try:
        print(rdftss.load(HERE / "readings_grid.ttl").validate())
    except rdftss.MissingDependencyError as error:
        print(error)
    print(embedded.verify_summaries())
    print(f"\nFiles written to {out}")


if __name__ == "__main__":
    main()
