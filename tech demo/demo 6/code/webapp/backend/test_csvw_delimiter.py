"""Check explicit CSVW delimiters and the comma default across CSV parsers."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import main


class CsvwDelimiter(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.metadata = {
            "@context": "http://www.w3.org/ns/csvw", "url": "data.csv",
            "dialect": {"delimiter": ";", "header": True},
            "tableSchema": {"columns": [
                {"name": "timestamp", "titles": "Timestamp", "datatype": "dateTime"},
                {"name": "reading", "titles": "Reading", "datatype": "double"},
                {"name": "category", "titles": "Category", "datatype": "string"},
            ]},
        }
        self.write_metadata()
        (self.root / "data.csv").write_text(
            'Timestamp;Reading;Category\n2025-01-01T00:00:00Z;1.5;good,review\n2025-01-01T00:15:00Z;2.5;bad,review\n',
            encoding="utf-8",
        )
        snippet = {
            "id": "urn:demo", "label": "Demo", "group": "urn:group", "embedded": False,
            "csvUrl": "data.csv", "describedBy": "schema.json", "timeColumn": "timestamp",
            "valueColumn": "reading", "from": None, "until": None, "unit": None,
            "observedProperty": None, "sensor": None, "featureOfInterest": None,
        }
        self.info = {"id": "fixture", "embedded": False, "describedBy": "schema.json", "snippets": [snippet],
                     "dataFormat": str(main.CSV_FILE_TYPE), "csvwApplicable": True}
        self.addCleanup(patch.stopall)
        patch.object(main, "DEMO_DIR", self.root).start()
        patch.object(main, "get_endpoint_info", return_value=self.info).start()

    def write_metadata(self):
        (self.root / "schema.json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def test_query_filters_and_sql_use_semicolon(self):
        self.assert_csv_parsers(";", "good,review", "bad,review")

    def assert_csv_parsers(self, delimiter, good, bad):
        filters = main.filter_columns("fixture", "urn:demo")
        self.assertEqual(filters[0]["values"], [bad, good])
        result = main.query_series("fixture", "urn:demo", filter_column="category", filter_value=good)
        self.assertEqual(result["delimiter"], delimiter)
        self.assertEqual(result["summary"]["count"], 1)
        self.assertEqual(result["readings"][0]["value"], 1.5)
        result = main.run_sql("fixture", main.SqlQuery(sql="SELECT reading, category FROM data ORDER BY timestamp"))
        self.assertEqual(result["rows"], [[1.5, good], [2.5, bad]])
        self.assertEqual(result["skipped"], [])

    def test_missing_delimiter_or_dialect_defaults_to_comma_everywhere(self):
        (self.root / "data.csv").write_text(
            'Timestamp,Reading,Category\n2025-01-01T00:00:00Z,1.5,good;review\n2025-01-01T00:15:00Z,2.5,bad;review\n',
            encoding="utf-8",
        )
        for dialect in ({}, {"header": True}, None):
            with self.subTest(dialect=dialect):
                if dialect is None:
                    self.metadata.pop("dialect", None)
                else:
                    self.metadata["dialect"] = dialect
                self.write_metadata()
                self.assert_csv_parsers(",", "good;review", "bad;review")

    def test_invalid_delimiters_stop_before_csv_parsing(self):
        for dialect in ({"delimiter": None}, {"delimiter": ""}, {"delimiter": ";;"},
                        {"delimiter": 1}, {"delimiter": "\n"}, "dialect.json", None):
            with self.subTest(dialect=dialect):
                self.metadata["dialect"] = dialect
                self.write_metadata()
                with patch.object(main, "read_lines") as reader:
                    with self.assertRaises(HTTPException) as caught:
                        main.query_series("fixture", "urn:demo")
                    self.assertEqual(caught.exception.status_code, 400)
                    with self.assertRaises(HTTPException):
                        main.filter_columns("fixture", "urn:demo")
                    self.assertIn("dialect", main.csvw_columns("schema.json")["error"])
                    reader.assert_not_called()

    def test_wrong_declared_delimiter_is_not_guessed_by_sql(self):
        self.metadata["dialect"]["delimiter"] = ","
        self.write_metadata()
        result = main.run_sql("fixture", main.SqlQuery(sql="SELECT count(*) FROM snippets"))
        self.assertEqual(result["rows"], [[1]])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("header", result["skipped"][0]["error"])


if __name__ == "__main__":
    unittest.main()
