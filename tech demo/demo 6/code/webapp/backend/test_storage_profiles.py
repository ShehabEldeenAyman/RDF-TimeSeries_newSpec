"""Run from backend/: python -B -m unittest -v test_storage_profiles."""
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from rdflib import Literal, RDF, URIRef
import main

EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL = "waterinfo_289423042_tss2"
PARQUET = "waterinfo_289429042_tss2_parquet"
VALUE = "https://example.org/ex#zevenaarshaven-ec20-value-field"
TEXT = "https://example.org/ex#zevenaarshaven-quality-color-value-field"
CSV_VALUE = "https://example.org/ex#grootdok-ec20-field"
PARQUET_VALUE = "https://example.org/ex#rodenhuizedok-ec20-field"


class StorageProfiles(unittest.TestCase):
    def test_inspector_reads_first_rows_of_every_format(self):
        for endpoint, field, count in [(EMBEDDED, VALUE, 5), (EXTERNAL, CSV_VALUE, 10), (PARQUET, PARQUET_VALUE, 10)]:
            with self.subTest(endpoint=endpoint):
                rows = main.series_metadata(endpoint, field)["files"]["rows"]
                self.assertIsNone(rows["error"])
                self.assertEqual(len(rows["rows"]), count)
                self.assertEqual(rows["notFound"], [])
                bound = [column["bound"] for column in rows["columns"]]
                self.assertEqual(bound.count("field"), 1)
                self.assertEqual(bound.count("time"), 1)

    def test_embedded_numeric_filters_and_limits(self):
        result = main.query_series(EMBEDDED, VALUE, start="2021-03-03T23:30:00Z", max_value="4750", limit=1)
        self.assertEqual(result["summary"]["count"], 3)
        self.assertTrue(result["limitReached"])
        self.assertEqual(result["readings"], [{"time": "2021-03-03T23:30:00Z", "value": 4717.8}])

    def test_embedded_half_open_window(self):
        graph = main.load_graph(EMBEDDED)
        source = graph.value(predicate=main.TSS.hasField, object=URIRef(VALUE))
        graph.set((source, main.TSS.until, Literal("2021-03-03T23:30:00Z", datatype=URIRef("http://www.w3.org/2001/XMLSchema#dateTime"))))
        with patch.object(main, "load_graph", return_value=graph):
            self.assertEqual(main.query_series(EMBEDDED, VALUE)["summary"]["count"], 1)

    def test_embedded_resampling(self):
        result = main.query_series(EMBEDDED, VALUE, resample="day")
        self.assertEqual([row["count"] for row in result["readings"]], [3, 2])
        self.assertAlmostEqual(result["readings"][1]["value"], 4746.03)

    def test_embedded_text_preserved_and_numeric_operations_rejected(self):
        result = main.query_series(EMBEDDED, TEXT)
        self.assertFalse(result["numeric"])
        self.assertEqual(result["summary"]["count"], 5)
        self.assertEqual(result["readings"][0]["value"], "rgba(0, 128, 0, 1.0)")
        for options in ({"resample": "day"}, {"min_value": "1"}, {"filter_column": "quality_code"}):
            with self.assertRaises(HTTPException) as caught:
                main.query_series(EMBEDDED, TEXT, **options)
            self.assertEqual(caught.exception.status_code, 400)

    def test_external_queries_and_quality_filters(self):
        result = main.query_series(EXTERNAL, CSV_VALUE, start="2021-03-03T23:45:00Z", end="2021-03-04T00:00:00Z", filter_column="Quality Code", filter_value="180")
        self.assertFalse(result["embedded"])
        self.assertEqual(result["readings"][0]["value"], 902.27)
        self.assertEqual(result["timeColumn"], "Timestamp")
        self.assertEqual(result["valueColumn"], "Value")
        names = [column["name"] for column in main.filter_columns(EXTERNAL, CSV_VALUE)]
        self.assertIn("Quality Code", names)

    def test_parquet_queries(self):
        result = main.query_series(PARQUET, PARQUET_VALUE, end="2021-03-04T00:00:00Z")
        self.assertEqual(result["format"], "tss:Parquet")
        self.assertEqual(result["readings"][0], {"time": "2021-03-03T23:15:00Z", "value": 1605.77})
        self.assertEqual(result["summary"]["count"], 3)  # 23:15, 23:45 and 00:00 (no reading at 23:30)

    def test_embedded_sql_and_read_only_guard(self):
        table = main.sql_schema(EMBEDDED)["tables"][0]
        self.assertEqual(table["name"], "zevenaarshaven_source")
        self.assertEqual([(column["name"], column["type"]) for column in table["columns"]],
                         [("time", "TIMESTAMPTZ"), ("ec20", "DOUBLE"), ("qualityColor", "VARCHAR")])
        result = main.run_sql(EMBEDDED, main.SqlQuery(sql="SELECT count(*), avg(ec20), min(qualityColor) FROM zevenaarshaven_source"))
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["rows"][0][0], 5)
        self.assertAlmostEqual(result["rows"][0][1], 4750.588)
        self.assertEqual(result["rows"][0][2], "rgba(0, 128, 0, 1.0)")
        for sql in ("DELETE FROM fields", "SELECT 1; SELECT 2", "SELECT * FROM read_csv('data.csv')"):
            with self.subTest(sql=sql):
                with self.assertRaises(HTTPException):
                    main.run_sql(EMBEDDED, main.SqlQuery(sql=sql))

    def test_external_sql(self):
        result = main.run_sql(EXTERNAL, main.SqlQuery(sql='SELECT count(*), count(DISTINCT "Quality Code") FROM waterinfo_289423042'))
        self.assertEqual(result["rows"], [[94853, 2]])
        self.assertEqual(result["skipped"], [])
        result = main.run_sql(EXTERNAL, main.SqlQuery(sql="SELECT column_name, role FROM fields WHERE describes = 'Value' ORDER BY column_name LIMIT 1"))
        self.assertEqual(result["rows"], [["Interpolation Type", "describes Value"]])

    def test_parquet_sql_keeps_the_file_types(self):
        table = main.sql_schema(PARQUET)["tables"][0]
        types = {column["name"]: column["type"] for column in table["columns"]}
        self.assertEqual(types["Value"], "DOUBLE")
        self.assertEqual(types["Timestamp"], "TIMESTAMP WITH TIME ZONE")
        result = main.run_sql(PARQUET, main.SqlQuery(
            sql="SELECT count(*) FROM waterinfo_289429042 AS d, source AS s WHERE d.Timestamp >= s.from_time AND d.Timestamp < s.until_time"))
        self.assertEqual(result["rows"], [[158022]])

    def test_every_sql_example_runs(self):
        for endpoint in (EMBEDDED, EXTERNAL, PARQUET):
            for example in main.sql_schema(endpoint)["examples"]:
                with self.subTest(endpoint=endpoint, example=example["title"]):
                    result = main.run_sql(endpoint, main.SqlQuery(sql=example["sql"]))
                    self.assertEqual(result["skipped"], [])
                    self.assertGreater(len(result["rows"]), 0)

if __name__ == "__main__":
    unittest.main()
