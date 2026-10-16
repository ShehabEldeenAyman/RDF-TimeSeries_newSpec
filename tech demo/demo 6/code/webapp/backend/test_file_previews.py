"""Files-tab previews and CSVW applicability: python -B -m unittest -v test_file_previews."""
import csv
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
from fastapi import HTTPException
from rdflib import Literal, RDF, URIRef
from rdflib.namespace import DCTERMS

import main


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
        (self.root / "schema.json").write_text('{"tableSchema": {"columns": []}}', encoding="utf-8")
        self.info = {
            "csvUrl": self.parquet.name,
            "dataFormat": str(main.PARQUET_FILE_TYPE),
            "csvwApplicable": False,
            "describedBy": "schema.json",
        }
        patcher = patch.object(main, "DEMO_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parquet_preview_preserves_values_and_csv_escaping_without_changing_source(self):
        original = hashlib.sha256(self.parquet.read_bytes()).digest()
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            preview = main.get_csv_file("fixture", lines=4)
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
                    preview = main.get_csv_file("fixture", lines=requested)
                    self.assertEqual(len(list(csv.reader(io.StringIO(preview["text"])))), expected)

    def test_parquet_decimal_preview_retains_full_precision(self):
        value = "12345678901234567890.1234567890"
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE decimals AS SELECT CAST(? AS DECIMAL(38, 10)) AS amount", [value])
            connection.sql("SELECT * FROM decimals").write_parquet(str(self.parquet))
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            preview = main.get_csv_file("fixture")
        self.assertEqual(list(csv.reader(io.StringIO(preview["text"]))), [["amount"], [value]])

    def test_invalid_parquet_returns_readable_error(self):
        self.parquet.write_text("not a Parquet file", encoding="utf-8")
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            with self.assertRaises(HTTPException) as caught:
                main.get_csv_file("fixture")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("Could not preview Parquet", caught.exception.detail)

    def test_parquet_download_keeps_original_file_and_media_type(self):
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            response = main.download_csv_file("fixture")
        self.assertEqual(response.path, self.parquet)
        self.assertEqual(response.filename, self.parquet.name)
        self.assertEqual(response.media_type, "application/vnd.apache.parquet")

    def test_csv_preview_and_csvw_remain_available_for_declared_csv(self):
        text = "time;value\n2025-01-01T00:00:00Z;1.25\n"
        (self.root / "readings.csv").write_text(text, encoding="utf-8")
        info = dict(self.info, csvUrl="readings.csv", dataFormat=str(main.CSV_FILE_TYPE), csvwApplicable=True)
        with patch.object(main, "get_endpoint_info", return_value=info):
            preview = main.get_csv_file("fixture")
            metadata = main.get_csvw_file("fixture")
            response = main.download_csv_file("fixture")
            csvw_response = main.download_csvw_file("fixture")
        self.assertEqual(preview["text"], text.rstrip("\n"))
        self.assertTrue(metadata["found"])
        self.assertIn("tableSchema", metadata["text"])
        self.assertEqual(response.media_type, "text/csv")
        self.assertEqual(csvw_response.media_type, "application/json")

    def test_csvw_is_inapplicable_for_non_csv_even_if_metadata_exists(self):
        with patch.object(main, "get_endpoint_info", return_value=self.info):
            metadata = main.get_csvw_file("fixture")
            with self.assertRaises(HTTPException) as caught:
                main.download_csvw_file("fixture")
        self.assertFalse(metadata["applicable"])
        self.assertIn("Not applicable", metadata["note"])
        self.assertNotIn("text", metadata)
        self.assertEqual(caught.exception.status_code, 400)

    def test_missing_csvw_is_still_missing_for_declared_csv(self):
        info = dict(self.info, describedBy="missing.json", dataFormat=str(main.CSV_FILE_TYPE), csvwApplicable=True)
        with patch.object(main, "get_endpoint_info", return_value=info):
            metadata = main.get_csvw_file("fixture")
        self.assertFalse(metadata["found"])
        self.assertIsNot(metadata.get("applicable"), False)


class DeclaredFormat(unittest.TestCase):
    def test_csvw_requires_the_exact_csv_format_iri(self):
        endpoint = "waterinfo_289423042_tss2"
        graph = main.load_graph(endpoint)
        group = next(graph.subjects(RDF.type, main.TSS.SnippetGroup))
        for value, applicable in [
            (main.CSV_FILE_TYPE, True),
            (main.PARQUET_FILE_TYPE, False),
            (URIRef("https://example.org/OTHER"), False),
            (None, False),
            (Literal(str(main.CSV_FILE_TYPE)), False),
        ]:
            with self.subTest(format=value):
                graph.remove((group, DCTERMS.format, None))
                if value is not None:
                    graph.add((group, DCTERMS.format, value))
                with patch.object(main, "load_graph", return_value=graph):
                    info = main.get_endpoint_info(endpoint)
                self.assertIs(info["csvwApplicable"], applicable)


class CsvwValidation(unittest.TestCase):
    def test_non_csv_formats_skip_file_access_and_validation(self):
        endpoint = "waterinfo_289423042_tss2"
        graph = main.load_graph(endpoint)
        group = next(graph.subjects(RDF.type, main.TSS.SnippetGroup))
        for value in (main.PARQUET_FILE_TYPE, URIRef("https://example.org/OTHER"), None):
            with self.subTest(format=value):
                graph.remove((group, DCTERMS.format, None))
                if value is not None:
                    graph.add((group, DCTERMS.format, value))
                with patch.object(main, "load_graph", return_value=graph), \
                        patch.object(main, "file_exists") as file_exists, \
                        patch.object(main, "load_csvw_json") as loader, \
                        patch.object(main, "CSVW") as validator:
                    result = main.validate(endpoint)
                    self.assertFalse(result["applicable"])
                    self.assertIn("Not applicable", result["note"])
                    file_exists.assert_not_called()
                    loader.assert_not_called()
                    validator.assert_not_called()

    def test_embedded_data_is_not_applicable(self):
        result = main.validate("waterinfo_289441042_tss2_points_embedded")
        self.assertFalse(result["applicable"])
        self.assertNotIn("valid", result)

    def test_missing_csvw_for_declared_csv_is_still_a_validation_error(self):
        with patch.object(main, "file_exists", return_value=False):
            result = main.validate("waterinfo_289423042_tss2")
        self.assertFalse(result["valid"])
        self.assertIn("CSVW file not found", result["messages"][0])


if __name__ == "__main__":
    unittest.main()
