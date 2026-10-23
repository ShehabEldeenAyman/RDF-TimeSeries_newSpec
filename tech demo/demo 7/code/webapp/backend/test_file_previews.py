"""Files-tab previews and downloads, and the first rows of the inspector:
python -B -m unittest -v test_file_previews."""
import csv
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
from fastapi import HTTPException
from rdflib import Graph

import paths
import documents
import data_routes

EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL_CSV = "waterinfo_289423042_tss2"
EXTERNAL_PARQUET = "waterinfo_289429042_tss2_parquet"

# A small metadata file for the fixtures, in a "metadata endpoint" folder: a source with a data
# file in the "data" folder next to it, as in demo 7 (the backend only reads files in data/).
# {format} is the local name of a tss:format (Parquet, CSV, ...), {data_file} a file name.
FIXTURE = """
@prefix tss: <https://w3id.org/tss#> .

<https://example.org/fixture#source> a tss:Source ;
    tss:embedded false ;
    tss:format tss:{format} ;
    tss:dataFile <../data/{data_file}> .
"""

# A CSV file without a header line (tss:header false): each field says where its column is
# (tss:columnIndex, zero-based). Column 1 has no field.
NO_HEADER = """
@prefix tss: <https://w3id.org/tss#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix f: <https://example.org/fixture#> .

f:source a tss:Source ;
    tss:embedded false ;
    tss:format tss:CSV ;
    tss:dataFile <../data/readings.csv> ;
    tss:delimiter ";" ;
    tss:header false ;
    tss:timeField f:time ;
    tss:hasField f:time, f:value .

f:time a tss:Field ; tss:columnName "time" ; tss:columnIndex 0 ; tss:datatype xsd:dateTime .
f:value a tss:Field ; tss:columnName "value" ; tss:columnIndex 2 ; tss:datatype xsd:double .
"""


class FilePreviews(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)  # plays the demo 7 folder (DEMO_DIR, below)
        self.data = self.root / "data"
        self.data.mkdir()
        self.parquet = self.data / "readings.parquet"
        with duckdb.connect() as connection:
            connection.execute('CREATE TABLE readings ("Timestamp" TIMESTAMPTZ, "Value" DOUBLE, "Note" VARCHAR)')
            connection.executemany("INSERT INTO readings VALUES (?, ?, ?)", [
                ("2025-01-01T00:00:00Z", 1.25, "rgba(0, 128, 0, 1.0)"),
                ("2025-01-01T00:15:00Z", None, 'a "quote"\nand a newline'),
                ("2025-01-01T00:30:00Z", 3.5, "last row"),
            ])
            connection.sql("SELECT * FROM readings").write_parquet(str(self.parquet))
        patcher = patch.object(paths, "DEMO_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def metadata(self, data_file="readings.parquet", format="Parquet", text=None):
        """Use a fixture metadata file instead of the endpoint's (a patched load_graph).
        It is parsed with its own file:/// address as base IRI, so <../data/readings.parquet>
        is a file in the fixture's data folder. text: other Turtle than FIXTURE."""
        if text is None:
            text = FIXTURE.format(format=format, data_file=data_file)
        turtle = self.root / "metadata endpoint" / "fixture.ttl"
        turtle.parent.mkdir(exist_ok=True)
        turtle.write_text(text, encoding="utf-8")
        graph = Graph().parse(turtle, format="turtle", publicID=turtle.as_uri())
        return patch.object(documents, "load_graph", return_value=graph)

    def test_parquet_preview_preserves_values_and_csv_escaping_without_changing_source(self):
        original = hashlib.sha256(self.parquet.read_bytes()).digest()
        with self.metadata():
            preview = data_routes.get_data_file("fixture", lines=4)
        rows = list(csv.reader(io.StringIO(preview["text"])))
        self.assertTrue(preview["found"])
        self.assertEqual(rows, [
            ["Timestamp", "Value", "Note"],
            ["2025-01-01T00:00:00Z", "1.25", "rgba(0, 128, 0, 1.0)"],
            ["2025-01-01T00:15:00Z", "", 'a "quote"\nand a newline'],
            ["2025-01-01T00:30:00Z", "3.5", "last row"],
        ])
        self.assertIn("original Parquet file", preview["note"])
        self.assertEqual(hashlib.sha256(self.parquet.read_bytes()).digest(), original)

    def test_parquet_preview_limit_includes_header_and_is_at_least_one(self):
        with self.metadata():
            for requested, expected in [(0, 1), (1, 1), (2, 2)]:
                with self.subTest(lines=requested):
                    preview = data_routes.get_data_file("fixture", lines=requested)
                    self.assertEqual(len(list(csv.reader(io.StringIO(preview["text"])))), expected)

    def test_parquet_decimal_preview_retains_full_precision(self):
        value = "12345678901234567890.1234567890"
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE decimals AS SELECT CAST(? AS DECIMAL(38, 10)) AS amount", [value])
            connection.sql("SELECT * FROM decimals").write_parquet(str(self.parquet))
        with self.metadata():
            preview = data_routes.get_data_file("fixture")
        self.assertEqual(list(csv.reader(io.StringIO(preview["text"]))), [["amount"], [value]])

    def test_invalid_parquet_returns_readable_error(self):
        self.parquet.write_text("not a Parquet file", encoding="utf-8")
        with self.metadata():
            with self.assertRaises(HTTPException) as caught:
                data_routes.get_data_file("fixture")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("Could not preview Parquet", caught.exception.detail)

    def test_parquet_download_keeps_original_file_and_media_type(self):
        with self.metadata():
            response = data_routes.download_data_file("fixture")
        self.assertEqual(response.path, self.parquet)
        self.assertEqual(response.filename, self.parquet.name)
        self.assertEqual(response.media_type, "application/vnd.apache.parquet")

    def test_csv_preview_keeps_the_original_text(self):
        text = "time;value\n2025-01-01T00:00:00Z;1.25\n"
        (self.data / "readings.csv").write_text(text, encoding="utf-8")
        with self.metadata(data_file="readings.csv", format="CSV"):
            preview = data_routes.get_data_file("fixture")
            response = data_routes.download_data_file("fixture")
        self.assertEqual(preview["text"], text.rstrip("\n"))
        self.assertEqual(response.media_type, "text/csv")

    def test_missing_data_file_is_reported(self):
        with self.metadata(data_file="missing.csv", format="CSV"):
            preview = data_routes.get_data_file("fixture")
        self.assertFalse(preview["found"])
        self.assertIsNone(preview["text"])
        # The answer has no path: the browser shows the tss:dataFile IRI from the RDF
        self.assertEqual(sorted(preview), ["found", "note", "text"])

    def test_first_rows_of_a_csv_file_without_a_header_line(self):
        (self.data / "readings.csv").write_text("2025-01-01T00:00:00Z;x;1.25\n2025-01-01T00:15:00Z;y;\n", encoding="utf-8")
        with self.metadata(text=NO_HEADER):
            result = data_routes.get_first_rows("fixture")
        # One column per field, by its IRI, in the order of the file's columns; "" is no value (None)
        self.assertEqual(result["columns"], ["https://example.org/fixture#time", "https://example.org/fixture#value"])
        self.assertEqual(result["rows"], [["2025-01-01T00:00:00Z", "1.25"], ["2025-01-01T00:15:00Z", None]])

    def test_other_formats_have_no_preview(self):
        with self.metadata(format="JSON"):
            with self.assertRaises(HTTPException) as caught:
                data_routes.get_data_file("fixture")
        self.assertEqual(caught.exception.status_code, 400)


class RealEndpoints(unittest.TestCase):
    """The Files tab on the demo's own metadata files: one embedded, two external.
    (The browser shows embedded points from the tss:points literal in the RDF itself.)"""

    def test_embedded_source_has_no_data_file(self):
        for route in (data_routes.get_data_file, data_routes.download_data_file):
            with self.subTest(route=route.__name__):
                with self.assertRaises(HTTPException) as caught:
                    route(EMBEDDED)
                self.assertEqual(caught.exception.status_code, 400)

    def test_external_csv_source_shows_and_downloads_its_data_file(self):
        preview = data_routes.get_data_file(EXTERNAL_CSV, lines=3)
        self.assertTrue(preview["found"])
        lines = preview["text"].splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("ts_id,Timestamp,Value"))
        response = data_routes.download_data_file(EXTERNAL_CSV)
        self.assertEqual(response.media_type, "text/csv")
        self.assertEqual(response.filename, "waterinfo_289423042.csv")

    def test_external_parquet_source_shows_and_downloads_its_data_file(self):
        preview = data_routes.get_data_file(EXTERNAL_PARQUET, lines=3)
        rows = list(csv.reader(io.StringIO(preview["text"])))
        self.assertEqual(rows[0][:3], ["ts_id", "Timestamp", "Value"])
        self.assertEqual(rows[1][1:3], ["2021-03-03T23:15:00Z", "1605.77"])
        response = data_routes.download_data_file(EXTERNAL_PARQUET)
        self.assertEqual(response.media_type, "application/vnd.apache.parquet")
        self.assertEqual(response.filename, "waterinfo_289429042.parquet")


if __name__ == "__main__":
    unittest.main()
