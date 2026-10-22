"""The data routes on the three storage profiles (embedded points, CSV, Parquet):
the inspector's first rows, the query form and the SQL box.
Run from backend/: python -B -m unittest -v test_storage_profiles.
Fields are named by their full IRI, in the requests and in the answers."""
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import XSD
import main

EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL = "waterinfo_289423042_tss2"
PARQUET = "waterinfo_289429042_tss2_parquet"
VALUE = "https://example.org/ex#zevenaarshaven-ec20-value-field"
TEXT = "https://example.org/ex#zevenaarshaven-quality-color-value-field"
TIME = "https://example.org/ex#zevenaarshaven-time-field"
CSV_VALUE = "https://example.org/ex#grootdok-ec20-field"
CSV_QUALITY_CODE = "https://example.org/ex#grootdok-quality-code-field"
CSV_INTERPOLATION_TYPE = "https://example.org/ex#grootdok-interpolation-type-field"
PARQUET_VALUE = "https://example.org/ex#rodenhuizedok-ec20-field"

# A small CSV source for the SQL tests: a time, a number, a whole number and a boolean.
# Its data file (readings.csv) is written by each test, in the data folder next to its folder.
CSV_FIXTURE = """
@prefix tss: <https://w3id.org/tss#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix f: <https://example.org/fixture#> .

f:source a tss:Source ;
    tss:embedded false ;
    tss:format tss:CSV ;
    tss:dataFile <../data/readings.csv> ;
    tss:timeField f:time ;
    tss:hasField f:time, f:value, f:code, f:flag .

f:time a tss:Field ; tss:columnName "time" ; tss:datatype xsd:dateTime .
f:value a tss:Field ; tss:columnName "value" ; tss:datatype xsd:double ; tss:observedProperty f:temperature .
f:code a tss:Field ; tss:columnName "code" ; tss:datatype xsd:integer .
f:flag a tss:Field ; tss:columnName "flag" ; tss:datatype xsd:boolean .
"""


class StorageProfiles(unittest.TestCase):
    def test_inspector_reads_first_rows_of_every_format(self):
        for endpoint, field, count in [(EMBEDDED, VALUE, 5), (EXTERNAL, CSV_VALUE, 10), (PARQUET, PARQUET_VALUE, 10)]:
            with self.subTest(endpoint=endpoint):
                result = main.get_first_rows(endpoint)
                self.assertEqual(len(result["rows"]), count)
                # One column per field of the source, by its IRI (every field has a column here)
                graph = main.load_graph(endpoint)
                source = main.find_source(graph)
                fields = set()
                for node in graph.objects(source, main.TSS.hasField):
                    fields.add(str(node))
                self.assertEqual(set(result["columns"]), fields)
                self.assertIn(field, result["columns"])
                for row in result["rows"]:
                    self.assertEqual(len(row), len(result["columns"]))

    def test_first_rows_follow_the_columns_of_the_data(self):
        result = main.get_first_rows(EXTERNAL, limit=2)
        # The CSV file starts with ts_id, Timestamp, Value
        self.assertEqual(result["columns"][:3], ["https://example.org/ex#grootdok-ts_id-field",
                                                 "https://example.org/ex#grootdok-time-field", CSV_VALUE])
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0][1:3], ["2021-03-03T23:45:00Z", "902.27"])
        # Embedded points: the fields' own order (the time first), and the points in time order
        result = main.get_first_rows(EMBEDDED)
        self.assertEqual(result["columns"], [TIME, VALUE, TEXT])
        self.assertEqual(result["rows"][0][1:], ["4797.72", "rgba(0, 128, 0, 1.0)"])
        # The texts of the points, as they are written (...Z, not rdflib's ...+00:00)
        self.assertEqual(result["rows"][0][0], "2021-03-03T23:15:00Z")

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
        self.assertEqual(result["summary"]["count"], 5)
        self.assertIsNone(result["summary"]["mean"])
        self.assertEqual(result["readings"][0]["value"], "rgba(0, 128, 0, 1.0)")
        for options in ({"resample": "day"}, {"min_value": "1"},
                        {"filter_field": "https://example.org/ex#no-such-field", "filter_value": "x"},
                        {"filter_field": VALUE}):
            with self.subTest(options=options):
                with self.assertRaises(HTTPException) as caught:
                    main.query_series(EMBEDDED, TEXT, **options)
                self.assertEqual(caught.exception.status_code, 400)

    def test_the_query_answer_holds_no_metadata(self):
        result = main.query_series(EMBEDDED, VALUE)
        # The data and what was computed from it, plus what the request asked for:
        # the label, unit, datatype, format, ... of the field the browser reads from the RDF
        self.assertEqual(sorted(result), ["aggregate", "filterValue", "gaps", "limitReached", "readings",
                                          "resample", "summary", "totalRows"])

    def test_external_queries_and_quality_filters(self):
        result = main.query_series(EXTERNAL, CSV_VALUE, start="2021-03-03T23:45:00Z", end="2021-03-04T00:00:00Z",
                                   filter_field=CSV_QUALITY_CODE, filter_value="180")
        self.assertEqual(result["readings"][0]["value"], 902.27)
        self.assertEqual(result["filterValue"], "180")
        fields = [column["field"] for column in main.filter_columns(EXTERNAL, CSV_VALUE)]
        self.assertIn(CSV_QUALITY_CODE, fields)

    def test_the_filter_field_is_called_filterField_in_the_address(self):
        client = TestClient(main.app)
        address = f"/api/endpoints/{EXTERNAL}/query"
        filtered = client.get(address, params={"field": CSV_VALUE, "end": "2021-03-04T02:00:00Z",
                                               "filterField": CSV_QUALITY_CODE, "filter_value": "180"})
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual(filtered.json()["filterValue"], "180")
        everything = client.get(address, params={"field": CSV_VALUE, "end": "2021-03-04T02:00:00Z"})
        self.assertLess(filtered.json()["summary"]["count"], everything.json()["summary"]["count"])

    def test_parquet_queries(self):
        result = main.query_series(PARQUET, PARQUET_VALUE, end="2021-03-04T00:00:00Z")
        self.assertEqual(result["readings"][0], {"time": "2021-03-03T23:15:00Z", "value": 1605.77})
        self.assertEqual(result["summary"]["count"], 3)  # 23:15, 23:45 and 00:00 (no reading at 23:30)

    def test_embedded_sql_and_read_only_guard(self):
        table = main.sql_schema(EMBEDDED)["tables"][0]
        self.assertEqual(table["name"], "zevenaarshaven_source")
        # Each column says which field it holds, by the field's IRI
        self.assertEqual([(column["name"], column["type"], column["field"]) for column in table["columns"]],
                         [("time", "TIMESTAMPTZ", TIME), ("ec20", "DOUBLE", VALUE), ("qualityColor", "VARCHAR", TEXT)])
        self.assertEqual(sorted(table), ["columns", "error", "name"])
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
        # The metadata tables hold full IRIs: describes is the IRI of the field it describes
        sql = f"SELECT column_name, role FROM fields WHERE describes = '{CSV_VALUE}' ORDER BY column_name LIMIT 1"
        result = main.run_sql(EXTERNAL, main.SqlQuery(sql=sql))
        self.assertEqual(result["rows"], [["Interpolation Type", "describes"]])
        sql = ("SELECT s.format, f.field, f.datatype, f.unit FROM source AS s, fields AS f "
               "WHERE f.field = s.time_field OR f.role = 'value' ORDER BY f.role")
        result = main.run_sql(EXTERNAL, main.SqlQuery(sql=sql))
        self.assertEqual(result["rows"], [
            ["https://w3id.org/tss#CSV", "https://example.org/ex#grootdok-time-field",
             "http://www.w3.org/2001/XMLSchema#dateTime", None],
            ["https://w3id.org/tss#CSV", CSV_VALUE,
             "http://www.w3.org/2001/XMLSchema#double", "http://qudt.org/vocab/unit/MicroS-PER-CentiM"],
        ])

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

    def test_sql_examples_name_their_field_by_iri_not_by_label(self):
        graph = main.load_graph(EXTERNAL)
        examples = main.sql_schema(EXTERNAL)["examples"]
        by_title = {}
        for example in examples:
            self.assertEqual(sorted(example), ["field", "sql", "title"])
            by_title[example["title"]] = example
            # A fixed English title: no label and no column name of the metadata (the browser adds the label)
            for name in ("Electrical conductivity", "Grootdok", "Value", "Quality Code", "Interpolation Type", "Timestamp"):
                self.assertNotIn(name, example["title"])
        self.assertEqual(by_title["Daily mean, min and max"]["field"], CSV_VALUE)
        self.assertIsNone(by_title["The first 10 rows of the data"]["field"])
        # The readings per code: about the field that describes the value (tss:describesField)
        code_field = URIRef(by_title["Readings per value"]["field"])
        self.assertEqual(graph.value(code_field, main.TSS.describesField), URIRef(CSV_VALUE))
        self.assertEqual(code_field, URIRef(CSV_INTERPOLATION_TYPE))  # the first describing field


class EmbeddedPointsAsRdf(unittest.TestCase):
    """The embedded points are read as RDF (JSON-LD with a context made from the fields),
    so they follow the RDF rules: no order, "" is no value, other keys are not fields."""

    def with_points(self, points):
        """The embedded endpoint, with other tss:points (an rdf:JSON literal)"""
        graph = main.load_graph(EMBEDDED)
        source = graph.value(predicate=RDF.type, object=main.TSS.Source)
        graph.set((source, main.TSS.points, Literal(points, datatype=RDF.JSON)))
        return patch.object(main, "load_graph", return_value=graph)

    def quiet_rdflib(self):
        """rdflib logs a long warning for a literal that does not fit its datatype ("abc" as xsd:double)"""
        return patch.object(logging.getLogger("rdflib.term"), "disabled", True)

    def test_points_are_sorted_by_time_and_empty_or_unknown_values_are_left_out(self):
        # Out of time order, an empty value, a missing key, and a key that is no field's column name
        points = """[
            {"time": "2021-03-03T23:45:00Z", "ec20": "30.5", "qualityColor": "green", "extra": "not a field"},
            {"time": "2021-03-03T23:15:00Z", "ec20": "10.5", "qualityColor": ""},
            {"time": "2021-03-03T23:30:00Z", "qualityColor": "red"}
        ]"""
        with self.with_points(points):
            result = main.query_series(EMBEDDED, VALUE)
            self.assertEqual(result["readings"], [{"time": "2021-03-03T23:15:00Z", "value": 10.5},
                                                  {"time": "2021-03-03T23:30:00Z", "value": None},
                                                  {"time": "2021-03-03T23:45:00Z", "value": 30.5}])
            self.assertEqual((result["summary"]["count"], result["summary"]["empty"]), (2, 1))

            table = main.sql_schema(EMBEDDED)["tables"][0]
            self.assertIsNone(table["error"])
            self.assertEqual([column["name"] for column in table["columns"]], ["time", "ec20", "qualityColor"])
            sql = "SELECT time, ec20, qualityColor FROM zevenaarshaven_source ORDER BY time"
            self.assertEqual(main.run_sql(EMBEDDED, main.SqlQuery(sql=sql))["rows"],
                             [["2021-03-03T23:15:00Z", 10.5, None],
                              ["2021-03-03T23:30:00Z", None, "red"],
                              ["2021-03-03T23:45:00Z", 30.5, "green"]])

            # The inspector's table: one column per field (by IRI), the literals' texts, in time order
            result = main.get_first_rows(EMBEDDED)
            self.assertEqual(result["columns"], [TIME, VALUE, TEXT])
            self.assertEqual([row[1:] for row in result["rows"]], [["10.5", None], [None, "red"], ["30.5", "green"]])

    def test_a_value_that_does_not_fit_its_datatype_is_reported(self):
        points = '[{"time": "2021-03-03T23:15:00Z", "ec20": "abc", "qualityColor": "red"}]'
        with self.with_points(points), self.quiet_rdflib():
            with self.assertRaises(HTTPException) as caught:
                main.query_series(EMBEDDED, VALUE)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertIn("'abc'", caught.exception.detail)
            table = main.sql_schema(EMBEDDED)["tables"][0]
            self.assertIn("ec20 should be a number", table["error"])

    def test_texts_that_are_not_in_the_xsd_form_are_reported(self):
        # Python (and rdflib) would read " 12 " and "1_000" as numbers, and "2021-03-03" as a time;
        # like SHACL, the data routes want the XSD form of the field's datatype
        for ec20, time in ((" 12 ", "2021-03-03T23:15:00Z"), ("1_000", "2021-03-03T23:15:00Z"), ("12", "2021-03-03")):
            points = f'[{{"time": "{time}", "ec20": "{ec20}", "qualityColor": "red"}}]'
            with self.subTest(points=points), self.with_points(points), self.quiet_rdflib():
                with self.assertRaises(HTTPException) as caught:
                    main.query_series(EMBEDDED, VALUE)
                self.assertEqual(caught.exception.status_code, 400)
                if ec20 != "12":
                    self.assertIn(repr(ec20), caught.exception.detail)
                    self.assertIn("ec20 should be a number", main.sql_schema(EMBEDDED)["tables"][0]["error"])
                else:
                    self.assertIn("not an xsd:dateTime", caught.exception.detail)

    def test_embedded_values_are_read_from_their_literals(self):
        # The rows of embedded points hold the lifted literals themselves, not their texts
        graph, source = main.load_source(EMBEDDED)
        rows = list(main.data_rows(graph, source))
        self.assertIsInstance(rows[0][URIRef(VALUE)], Literal)
        self.assertEqual(rows[0][URIRef(VALUE)].datatype, URIRef("http://www.w3.org/2001/XMLSchema#double"))
        # rdflib reads them (Literal.toPython()): another time zone, an exponent
        points = """[{"time": "2021-03-04T00:15:00+01:00", "ec20": "3.0E1", "qualityColor": "red"},
                     {"time": "2021-03-03T23:30:00Z", "ec20": "+12.50", "qualityColor": "green"}]"""
        with self.with_points(points):
            result = main.query_series(EMBEDDED, VALUE)
        self.assertEqual(result["readings"], [{"time": "2021-03-03T23:15:00Z", "value": 30.0},
                                              {"time": "2021-03-03T23:30:00Z", "value": 12.5}])
        # A number in the XSD form that is no finite number, and a day that does not exist
        for ec20, time, message in (("INF", "2021-03-03T23:15:00Z", "non-finite"),
                                    ("1", "2021-02-30T23:15:00Z", "not an xsd:dateTime")):
            points = f'[{{"time": "{time}", "ec20": "{ec20}", "qualityColor": "red"}}]'
            with self.subTest(points=points), self.with_points(points), self.quiet_rdflib():
                with self.assertRaises(HTTPException) as caught:
                    main.query_series(EMBEDDED, VALUE)
                self.assertEqual(caught.exception.status_code, 400)
                self.assertIn(message, caught.exception.detail)

    def test_embedded_filters_compare_the_texts_of_the_literals(self):
        points = """[{"time": "2021-03-03T23:15:00Z", "ec20": "10.5", "qualityColor": "green"},
                     {"time": "2021-03-03T23:30:00Z", "ec20": "20.5", "qualityColor": "red"},
                     {"time": "2021-03-03T23:45:00Z", "ec20": "30.5"}]"""
        with self.with_points(points):
            self.assertEqual(main.filter_columns(EMBEDDED, VALUE), [{"field": TEXT, "values": ["", "green", "red"]}])
            result = main.query_series(EMBEDDED, VALUE, filter_field=TEXT, filter_value="red")
            self.assertEqual(result["readings"], [{"time": "2021-03-03T23:30:00Z", "value": 20.5}])
            result = main.query_series(EMBEDDED, VALUE, filter_field=TEXT, filter_value="")
            self.assertEqual(result["readings"], [{"time": "2021-03-03T23:45:00Z", "value": 30.5}])

    def test_a_point_without_any_field_value_is_skipped(self):
        # Like the browser: a point with only keys that are no column name, or with only empty
        # values, has nothing to show (SHACL's P6 still warns about its keys: test_embedded_shacl)
        points = """[{"time": "2021-03-03T23:15:00Z", "ec20": "10.5", "qualityColor": "green"},
                     {"extra": "x"},
                     {"time": "", "ec20": "", "note": "y"}]"""
        with self.with_points(points):
            self.assertEqual(main.query_series(EMBEDDED, VALUE)["readings"], [{"time": "2021-03-03T23:15:00Z", "value": 10.5}])
            self.assertEqual(main.get_first_rows(EMBEDDED)["rows"], [["2021-03-03T23:15:00Z", "10.5", "green"]])
            sql = "SELECT count(*) FROM zevenaarshaven_source"
            self.assertEqual(main.run_sql(EMBEDDED, main.SqlQuery(sql=sql))["rows"], [[1]])
            self.assertEqual(main.generate_rdf(EMBEDDED)["observations"], 2)

    def test_a_time_typed_xsd_date_time_stamp_is_read_as_a_date_time(self):
        # xsd:dateTimeStamp is an xsd:dateTime that must have a timezone. rdflib's toPython()
        # leaves it unread, so the backend reads it as an xsd:dateTime.
        graph = main.load_graph(EMBEDDED)
        source = graph.value(predicate=RDF.type, object=main.TSS.Source)
        graph.set((URIRef(TIME), main.TSS.datatype, XSD.dateTimeStamp))
        graph.set((source, main.TSS.points, Literal('[{"time": "2021-03-04T00:30:00+01:00", "ec20": "20.5", "qualityColor": "red"},'
                                                    ' {"time": "2021-03-03T23:15:00Z", "ec20": "10.5", "qualityColor": "green"}]',
                                                    datatype=RDF.JSON)))
        with patch.object(main, "load_graph", return_value=graph):
            self.assertEqual(main.query_series(EMBEDDED, VALUE)["readings"], [{"time": "2021-03-03T23:15:00Z", "value": 10.5},
                                                                              {"time": "2021-03-03T23:30:00Z", "value": 20.5}])
            # In time order, each time with its text as written
            self.assertEqual([row[0] for row in main.get_first_rows(EMBEDDED)["rows"]],
                             ["2021-03-03T23:15:00Z", "2021-03-04T00:30:00+01:00"])
            table = main.sql_schema(EMBEDDED)["tables"][0]
            self.assertIsNone(table["error"])
            self.assertEqual(table["columns"][0], {"name": "time", "type": "TIMESTAMPTZ", "field": TIME})
            sql = "SELECT time, ec20 FROM zevenaarshaven_source ORDER BY time"
            self.assertEqual(main.run_sql(EMBEDDED, main.SqlQuery(sql=sql))["rows"],
                             [["2021-03-03T23:15:00Z", 10.5], ["2021-03-03T23:30:00Z", 20.5]])
            self.assertEqual(main.generate_rdf(EMBEDDED)["observations"], 4)

            # Without a timezone it is no xsd:dateTimeStamp: refused everywhere
            graph.set((source, main.TSS.points, Literal('[{"time": "2021-03-03T23:15:00", "ec20": "1", "qualityColor": "red"}]',
                                                        datatype=RDF.JSON)))
            for route in (lambda: main.query_series(EMBEDDED, VALUE), lambda: main.generate_rdf(EMBEDDED)):
                with self.assertRaises(HTTPException) as caught:
                    route()
                self.assertEqual(caught.exception.status_code, 400)
                self.assertIn("xsd:dateTimeStamp", caught.exception.detail)
            self.assertIn("Column time should be a date-time", main.sql_schema(EMBEDDED)["tables"][0]["error"])

    def test_points_without_a_valid_time_go_last(self):
        # rdflib alone reads "2021-03-03" (a date, typed xsd:dateTime) as midnight. Like /query, SHACL
        # and the browser, the order wants an xsd:dateTime in the XSD form, so that point goes last.
        points = """[{"time": "2021-03-03", "ec20": "1", "qualityColor": "red"},
                     {"time": "2021-03-03T23:15:00Z", "ec20": "2", "qualityColor": "green"}]"""
        with self.with_points(points), self.quiet_rdflib():
            result = main.get_first_rows(EMBEDDED)
        self.assertEqual([row[0] for row in result["rows"]], ["2021-03-03T23:15:00Z", "2021-03-03"])

    def test_a_data_table_duckdb_can_not_make_is_skipped(self):
        # For DuckDB, "ec20" and "EC20" are the same column name, so the table can't be made.
        # POST /sql still answers (with the metadata tables) and lists the table as skipped.
        graph = main.load_graph(EMBEDDED)
        graph.set((URIRef(TEXT), main.TSS.columnName, Literal("EC20")))
        with patch.object(main, "load_graph", return_value=graph):
            response = TestClient(main.app).post(f"/api/endpoints/{EMBEDDED}/sql", json={"sql": "SELECT count(*) FROM fields"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rows"], [[3]])
        skipped = response.json()["skipped"]
        self.assertEqual([table["name"] for table in skipped], ["zevenaarshaven_source"])
        self.assertIn("Could not load table zevenaarshaven_source: ", skipped[0]["error"])
        self.assertIn("already exists", skipped[0]["error"])  # DuckDB's own words

    def test_points_that_are_not_json_ld_are_reported(self):
        with self.with_points("this is not JSON"):
            for route, arguments in ((main.query_series, (EMBEDDED, VALUE)), (main.filter_columns, (EMBEDDED, VALUE)),
                                     (main.get_first_rows, (EMBEDDED,))):
                with self.subTest(route=route.__name__):
                    with self.assertRaises(HTTPException) as caught:
                        route(*arguments)
                    self.assertEqual(caught.exception.status_code, 400)
                    self.assertIn("JSON-LD", caught.exception.detail)
            self.assertIn("JSON-LD", main.sql_schema(EMBEDDED)["tables"][0]["error"])


class CsvSqlTable(unittest.TestCase):
    """The SQL box on a small CSV source (CSV_FIXTURE): its cells must have the XSD form of
    their field's datatype, as /query and SHACL want, or the table is skipped with the reason."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # The data file must be in demo 7's data folder, so the fixture's folder plays demo 7
        patcher = patch.object(main, "DEMO_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.root / "data").mkdir()
        (self.root / "metadata endpoint").mkdir()
        turtle = self.root / "metadata endpoint" / "fixture.ttl"
        turtle.write_text(CSV_FIXTURE, encoding="utf-8")
        self.graph = Graph().parse(turtle, format="turtle", publicID=turtle.as_uri())
        patcher = patch.object(main, "load_graph", return_value=self.graph)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def write_rows(self, *rows):
        lines = ["time,value,code,flag"]
        for row in rows:
            lines.append(row)
        (self.root / "data" / "readings.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def sql(self, sql):
        """POST /sql through the app (so a crash would be a 500 answer, not an exception)"""
        return self.client.post("/api/endpoints/fixture/sql", json={"sql": sql})

    def test_cells_in_the_xsd_form_are_loaded(self):
        self.write_rows("2025-01-01T00:00:00Z,3.0E1,+5,true", "2025-01-01T00:15:00,,-7,0")
        response = self.sql("SELECT time, value, code, flag FROM readings ORDER BY time")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["skipped"], [])
        # A time without a timezone is UTC; an empty cell is NULL
        self.assertEqual(response.json()["rows"], [["2025-01-01T00:00:00Z", 30.0, 5, True],
                                                   ["2025-01-01T00:15:00Z", None, -7, False]])

    def test_a_table_that_can_not_be_loaded_is_skipped_with_the_reason(self):
        self.write_rows("2025-01-01T00:00:00Z,1.5,1,true", "2025-01-01T00:15:00Z,abc,2,false")
        response = self.sql("SELECT count(*) FROM fields")
        # Not a crash (500): the metadata tables still work, and the data table is listed as skipped
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rows"], [[4]])
        skipped = response.json()["skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["name"], "readings")
        self.assertIn("Column value should be a number, but the data has: abc", skipped[0]["error"])
        # A query on the data table says why the table is not there
        response = self.sql("SELECT * FROM readings")
        self.assertEqual(response.status_code, 400)
        self.assertIn("(Table readings was not loaded: ", response.json()["detail"])

    def test_cells_that_are_not_in_the_xsd_form_are_refused(self):
        # DuckDB alone would read " 12 " as 12, "1_000" as 1000, "yes" as true and a date as a time
        good = {"time": "2025-01-01T00:00:00Z", "value": "1.5", "code": "1", "flag": "true"}
        a_time = "a date-time like 2021-03-03T23:45:00Z"  # (sql_box.TYPE_WORDS)
        for column, cell, words in (("value", " 12 ", "a number"), ("value", "1_000", "a number"),
                                    ("value", "4,5", "a number"), ("code", "1.5", "a whole number"),
                                    ("flag", "yes", "true, false, 1 or 0"), ("flag", "TRUE", "true, false, 1 or 0"),
                                    ("time", "2025-01-01", a_time), ("time", "2025-01-01 00:15", a_time),
                                    ("time", "2025-02-30T00:00:00Z", a_time)):
            row = dict(good)
            row[column] = cell
            cells = []
            for name in ("time", "value", "code", "flag"):
                text = row[name]
                if "," in text:
                    text = '"' + text + '"'  # in a CSV file, a cell with a comma in it is quoted
                cells.append(text)
            self.write_rows(",".join(cells))
            with self.subTest(column=column, cell=cell):
                response = self.sql("SELECT count(*) FROM source")
                self.assertEqual(response.status_code, 200, response.text)
                skipped = response.json()["skipped"]
                self.assertEqual(len(skipped), 1)
                self.assertIn(f"Column {column} should be {words}, but the data has: {cell}", skipped[0]["error"])
                # /query refuses the same cells (only the time and the number are read there)
                if column in ("time", "value"):
                    with self.assertRaises(HTTPException) as caught:
                        main.query_series("fixture", "https://example.org/fixture#value")
                    self.assertEqual(caught.exception.status_code, 400)


    def test_a_time_typed_xsd_date_time_stamp_needs_a_timezone(self):
        # xsd:dateTimeStamp is an xsd:dateTime that must have a timezone (as SHACL's P4 says)
        self.graph.set((URIRef("https://example.org/fixture#time"), main.TSS.datatype, XSD.dateTimeStamp))
        self.write_rows("2025-01-01T00:00:00Z,1.5,1,true", "2025-01-01T01:15:00+01:00,2.5,2,false")
        response = self.sql("SELECT time, value FROM readings ORDER BY time")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rows"], [["2025-01-01T00:00:00Z", 1.5], ["2025-01-01T00:15:00Z", 2.5]])
        result = main.query_series("fixture", "https://example.org/fixture#value")
        self.assertEqual(result["readings"], [{"time": "2025-01-01T00:00:00Z", "value": 1.5},
                                              {"time": "2025-01-01T00:15:00Z", "value": 2.5}])
        # A time without a timezone: fine for xsd:dateTime (it is UTC), not for xsd:dateTimeStamp
        self.write_rows("2025-01-01T00:00:00,1.5,1,true")
        skipped = self.sql("SELECT count(*) FROM source").json()["skipped"]
        self.assertIn("Column time should be a date-time like 2021-03-03T23:45:00Z, but the data has: 2025-01-01T00:00:00",
                      skipped[0]["error"])
        with self.assertRaises(HTTPException) as caught:
            main.query_series("fixture", "https://example.org/fixture#value")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("not an xsd:dateTimeStamp", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
