"""The stored summaries (tss:summary on a field) and the document's tss:modifiedAt: the SQL box's
"summaries" table and example queries, and the query form's summary numbers that the Query tab
compares with them. Run from backend/: python -B -m unittest -v test_summaries."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from rdflib import Literal, URIRef
from rdflib.namespace import XSD
import documents
import data_routes
import query_helpers
import sql_routes
from rdf_generation import TSS

EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL = "waterinfo_289423042_tss2"
VALUE = "https://example.org/ex#zevenaarshaven-ec20-value-field"
TEXT = "https://example.org/ex#zevenaarshaven-quality-color-value-field"
CSV_VALUE = "https://example.org/ex#grootdok-ec20-field"

UP_TO_DATE = "Is every stored summary still up to date? (count, missing, first and last time)"
SIDE_BY_SIDE = "Stored summary next to the same numbers computed from the data"
STORED = "The stored summaries (tss:summary in the metadata)"


def run_example(endpoint, title):
    """Run the SQL box's example query with this title; returns (columns, rows)"""
    for example in sql_routes.sql_schema(endpoint)["examples"]:
        if example["title"] == title:
            result = sql_routes.run_sql(endpoint, sql_routes.SqlQuery(sql=example["sql"]))
            return result["columns"], result["rows"]
    raise AssertionError(f"No example '{title}' for {endpoint}")


class QuerySummary(unittest.TestCase):
    """/query computes the numbers a stored summary states"""

    def test_median_and_first_and_last_time(self):
        def at(hour, value):
            return {"moment": datetime(2021, 3, 3, hour, tzinfo=timezone.utc), "value": value}
        summary = query_helpers.summarize([at(1, 4.0), at(2, None), at(3, 1.0), at(4, 3.0), at(5, 2.0)])
        self.assertEqual((summary["count"], summary["empty"]), (4, 1))
        self.assertEqual(summary["median"], 2.5)  # an even count: the mean of the middle two
        self.assertEqual((summary["firstTime"], summary["lastTime"]), ("2021-03-03T01:00:00Z", "2021-03-03T05:00:00Z"))

    def test_the_embedded_summary_matches_query(self):
        summary = data_routes.query_series(EMBEDDED, VALUE)["summary"]
        # The numbers the metadata states in tss:summary (see the embedded .ttl file)
        self.assertEqual((summary["count"], summary["empty"]), (5, 0))
        self.assertEqual((summary["min"]["value"], summary["max"]["value"]), (4716.1, 4797.72))
        self.assertEqual((summary["mean"], summary["median"]), (4750.588, 4745.36))
        self.assertEqual((summary["firstTime"], summary["lastTime"]), ("2021-03-03T23:15:00Z", "2021-03-04T01:00:00Z"))

    def test_a_text_field_has_counts_and_times_but_no_numbers(self):
        summary = data_routes.query_series(EMBEDDED, TEXT)["summary"]
        self.assertEqual((summary["count"], summary["empty"]), (5, 0))
        self.assertEqual(summary["firstTime"], "2021-03-03T23:15:00Z")
        self.assertIsNone(summary["median"])


class SqlSummaries(unittest.TestCase):
    """The SQL box's "summaries" table and its example queries"""

    def test_summaries_is_a_metadata_table(self):
        names = [table["name"] for table in sql_routes.sql_schema(EXTERNAL)["metadataTables"]]
        self.assertEqual(names, ["source", "fields", "summaries"])

    def test_the_summaries_table_holds_what_the_metadata_states(self):
        columns, rows = run_example(EMBEDDED, STORED)
        by_column = {}
        for row in rows:
            by_column[row[columns.index("column_name")]] = dict(zip(columns, row))
        self.assertEqual(sorted(by_column), ["ec20", "qualityColor", "time"])
        ec20 = by_column["ec20"]
        self.assertEqual((ec20["value_count"], ec20["missing"], ec20["median"]), (5, 0, 4745.36))
        self.assertEqual(ec20["generated_at"], "2026-10-22T14:02:30Z")
        self.assertIsNone(by_column["qualityColor"]["mean"])  # no numbers for a text field

    def test_the_source_table_has_the_document_s_modified_at(self):
        result = sql_routes.run_sql(EMBEDDED, sql_routes.SqlQuery(sql="SELECT modified_at FROM source"))
        self.assertEqual(result["rows"], [["2026-10-22T14:13:56Z"]])

    def test_every_summary_of_the_demo_is_up_to_date(self):
        for endpoint in (EMBEDDED, EXTERNAL):
            with self.subTest(endpoint=endpoint):
                columns, rows = run_example(endpoint, UP_TO_DATE)
                self.assertGreater(len(rows), 0)
                for row in rows:
                    self.assertTrue(row[columns.index("up_to_date")], row)

    def test_side_by_side_gives_the_same_numbers(self):
        columns, rows = run_example(EXTERNAL, SIDE_BY_SIDE)
        stored, computed = rows
        self.assertEqual(stored[1:], computed[1:])  # all but the first column, the row's name

    def test_a_stale_summary_is_reported(self):
        # The same endpoint, with a summary that counts one value too many
        graph = documents.load_graph(EMBEDDED)
        summary = graph.value(URIRef(VALUE), TSS.summary)
        graph.set((summary, TSS["count"], Literal("6", datatype=XSD.integer)))
        with patch.object(documents, "load_graph", return_value=graph):
            columns, rows = run_example(EMBEDDED, UP_TO_DATE)
        up_to_date = {}
        for row in rows:
            up_to_date[row[columns.index("column_name")]] = row[columns.index("up_to_date")]
        self.assertEqual(up_to_date, {"ec20": False, "qualityColor": True, "time": True})

    def test_example_titles_name_no_field(self):
        titles = [example["title"] for example in sql_routes.sql_schema(EXTERNAL)["examples"]]
        for title in (STORED, UP_TO_DATE, SIDE_BY_SIDE):
            self.assertIn(title, titles)
            for name in ("Value", "Grootdok", "Timestamp", "Electrical conductivity"):
                self.assertNotIn(name, title)


if __name__ == "__main__":
    unittest.main()
