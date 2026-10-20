"""Files-tab previews and downloads: python -B -m unittest -v test_file_previews."""
import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
from fastapi import HTTPException

import main

EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL_CSV = "waterinfo_289423042_tss2"
EXTERNAL_PARQUET = "waterinfo_289429042_tss2_parquet"


class FilePreviews(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parquet = self.root / "readings.parquet"
        with duckdb.connect() as connection:
            connection.execute('CREATE TABLE readings ("Timestamp" TIMESTAMPTZ, "Value" DOUBLE, "Note" VARCHAR)')
            connection.executemany("INSERT INTO readings VALUES (?, ?, ?)", [
                ("2025-01-01T00:00:00Z", 1.25, "rgba(0, 128, 0, 1.0)"),
                ("2025-01-01T00:15:00Z", None, 'a "quote"\nand a newline'),
                ("2025-01-01T00:30:00Z", 3.5, "last row"),
            ])
            connection.sql("SELECT * FROM readings").write_parquet(str(self.parquet))
        self.info = {
            "embedded": False,
            "dataFile": self.parquet.name,
            "format": "tss:Parquet",
        }
        patcher = patch.object(main, "DEMO_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parquet_preview_preserves_values_and_csv_escaping_without_changing_source(self):
        original = hashlib.sha256(self.parquet.read_bytes()).digest()
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            preview = main.get_data_file("fixture", lines=4)
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
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            for requested, expected in [(0, 1), (1, 1), (2, 2)]:
                with self.subTest(lines=requested):
                    preview = main.get_data_file("fixture", lines=requested)
                    self.assertEqual(len(list(csv.reader(io.StringIO(preview["text"])))), expected)

    def test_parquet_decimal_preview_retains_full_precision(self):
        value = "12345678901234567890.1234567890"
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE decimals AS SELECT CAST(? AS DECIMAL(38, 10)) AS amount", [value])
            connection.sql("SELECT * FROM decimals").write_parquet(str(self.parquet))
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            preview = main.get_data_file("fixture")
        self.assertEqual(list(csv.reader(io.StringIO(preview["text"]))), [["amount"], [value]])

    def test_invalid_parquet_returns_readable_error(self):
        self.parquet.write_text("not a Parquet file", encoding="utf-8")
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            with self.assertRaises(HTTPException) as caught:
                main.get_data_file("fixture")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("Could not preview Parquet", caught.exception.detail)

    def test_parquet_download_keeps_original_file_and_media_type(self):
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            response = main.download_data_file("fixture")
        self.assertEqual(response.path, self.parquet)
        self.assertEqual(response.filename, self.parquet.name)
        self.assertEqual(response.media_type, "application/vnd.apache.parquet")

    def test_csv_preview_keeps_the_original_text(self):
        text = "time;value\n2025-01-01T00:00:00Z;1.25\n"
        (self.root / "readings.csv").write_text(text, encoding="utf-8")
        info = dict(self.info, dataFile="readings.csv", format="tss:CSV")
        with patch.object(main, "get_endpoint_info", return_value=info):
            preview = main.get_data_file("fixture")
            response = main.download_data_file("fixture")
        self.assertEqual(preview["text"], text.rstrip("\n"))
        self.assertEqual(response.media_type, "text/csv")

    def test_missing_data_file_is_reported(self):
        info = dict(self.info, dataFile="missing.csv", format="tss:CSV")
        with patch.object(main, "get_endpoint_info", return_value=info):
            preview = main.get_data_file("fixture")
        self.assertFalse(preview["found"])
        self.assertEqual(preview["path"], "missing.csv")

    def test_other_formats_have_no_preview(self):
        info = dict(self.info, format="tss:JSON")
        with patch.object(main, "get_endpoint_info", return_value=info):
            with self.assertRaises(HTTPException) as caught:
                main.get_data_file("fixture")
        self.assertEqual(caught.exception.status_code, 400)


class RealEndpoints(unittest.TestCase):
    """The Files tab on the demo's own metadata files: one embedded, two external"""

    def test_embedded_source_shows_its_points(self):
        result = main.get_points_file(EMBEDDED)
        points = json.loads(result["text"])
        self.assertTrue(result["found"])
        self.assertEqual(len(points), 5)
        self.assertEqual(points[0], {"time": "2021-03-03T23:15:00Z", "ec20": "4797.72", "qualityColor": "rgba(0, 128, 0, 1.0)"})
        self.assertEqual(len(result["text"].splitlines()), 7)  # "[", one line per point, "]"
        self.assertIn("5 points", result["note"])

    def test_embedded_source_has_no_data_file(self):
        for route in (main.get_data_file, main.download_data_file):
            with self.subTest(route=route.__name__):
                with self.assertRaises(HTTPException) as caught:
                    route(EMBEDDED)
                self.assertEqual(caught.exception.status_code, 400)

    def test_external_csv_source_shows_and_downloads_its_data_file(self):
        preview = main.get_data_file(EXTERNAL_CSV, lines=3)
        self.assertTrue(preview["found"])
        self.assertEqual(preview["path"], "data/waterinfo_289423042.csv")
        lines = preview["text"].splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("ts_id,Timestamp,Value"))
        response = main.download_data_file(EXTERNAL_CSV)
        self.assertEqual(response.media_type, "text/csv")
        self.assertEqual(response.filename, "waterinfo_289423042.csv")

    def test_external_parquet_source_shows_and_downloads_its_data_file(self):
        preview = main.get_data_file(EXTERNAL_PARQUET, lines=3)
        rows = list(csv.reader(io.StringIO(preview["text"])))
        self.assertEqual(rows[0][:3], ["ts_id", "Timestamp", "Value"])
        self.assertEqual(rows[1][1:3], ["2021-03-03T23:15:00Z", "1605.77"])
        response = main.download_data_file(EXTERNAL_PARQUET)
        self.assertEqual(response.media_type, "application/vnd.apache.parquet")
        self.assertEqual(response.filename, "waterinfo_289429042.parquet")

    def test_external_sources_have_no_embedded_points(self):
        for endpoint in (EXTERNAL_CSV, EXTERNAL_PARQUET):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(HTTPException) as caught:
                    main.get_points_file(endpoint)
                self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
