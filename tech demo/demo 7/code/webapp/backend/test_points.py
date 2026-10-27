"""The "Add data" tab's route, POST /api/endpoints/{id}/points (points_routes.py): python -B -m unittest -v test_points.

The route WRITES the endpoint's file, so these tests never use the real "metadata endpoint/" folder:
each test makes a folder of its own, puts copies of the files it needs in it, and points
paths.METADATA_DIR there (every reader and the route find the files through it, see
documents.document_path). The tests also check that the real folder's files are byte for byte the
same after all of them.

The time/value document is not copied from "metadata endpoint/": that file changes every time someone
adds a point to it with the dashboard. TIME_VALUE below is a copy of it with its 5 points (as it was
when these tests were written, with tss:mutable true), so the expected numbers stay right."""
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import PROV, SH, XSD

import main
import paths
import documents
import data_routes
import data_reading
import points_routes
import value_reading
from rdf_generation import TSS
from test_external_shacl import checks
from test_summaries import UP_TO_DATE, run_example

# The real folder: only read (copied), never written
REAL_FOLDER = paths.METADATA_DIR

TIME_VALUE_ID = "waterinfo_289441042_tss2_points_time_value"
EMBEDDED_ID = "waterinfo_289441042_tss2_points_embedded"   # the 2-field (3 columns) embedded endpoint
CSV_ID = "waterinfo_289423042_tss2"                          # an external CSV source

EX = "https://example.org/ex#"
SOURCE = URIRef(EX + "zevenaarshaven-tv-source")
TIME = EX + "zevenaarshaven-tv-time-field"
VALUE = EX + "zevenaarshaven-tv-value-field"
EMBEDDED_TIME = EX + "zevenaarshaven-time-field"
EMBEDDED_EC20 = EX + "zevenaarshaven-ec20-value-field"
EMBEDDED_COLOR = EX + "zevenaarshaven-quality-color-value-field"

# The time/value endpoint with its 5 points (see the docstring at the top)
TIME_VALUE = '''@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix tss: <https://w3id.org/tss#> .
@prefix ex: <https://example.org/ex#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix quantitykind: <http://qudt.org/vocab/quantitykind/> .
@prefix unit: <http://qudt.org/vocab/unit/> .

# A comment: the file is written again by rdflib, so comments are lost.

ex:zevenaarshaven-tv-source
    a tss:Source ;
    tss:modifiedAt "2026-10-26T09:03:01Z"^^xsd:dateTime ;
    rdfs:label "Waterinfo EC20, Kanaal Gent-Terneuzen Zevenaarshaven (NL), time and value points"@en ;
    tss:sourceDescription [
        dcterms:spatial [
            a dcterms:Location ;
            rdfs:label "Kanaal Gent-Terneuzen Zevenaarshaven (NL)"@en
        ]
    ] ;
    tss:mutable true ;
    tss:producedBy ex:289441042 ;
    tss:embedded true ;
    tss:timeField ex:zevenaarshaven-tv-time-field ;
    tss:key ex:zevenaarshaven-tv-time-field ;
    tss:from "2021-03-03T23:15:00Z"^^xsd:dateTime ;
    tss:until "2026-01-01T00:00:00Z"^^xsd:dateTime ;
    tss:committedUntil "2026-10-26T09:03:01Z"^^xsd:dateTime ;
    tss:hasField ex:zevenaarshaven-tv-time-field,
        ex:zevenaarshaven-tv-value-field ;
    tss:points """[
        { "time": "2021-03-03T23:15:00Z", "value": "4797.72"},
        { "time": "2021-03-03T23:30:00Z", "value": "4717.8"},
        { "time": "2021-03-03T23:45:00Z", "value": "4745.36"},
        { "time": "2021-03-04T00:30:00Z", "value": "4716.1"},
        { "time": "2021-03-04T01:00:00Z", "value": "4775.96"}
      ]"""^^rdf:JSON .

ex:zevenaarshaven-tv-time-field
    a tss:Field ;
    tss:columnName "time" ;
    rdfs:label "Observation time"@en ;
    tss:datatype xsd:dateTime ;
    tss:fieldDescription [
        dcterms:description "Observation time"@en
    ] ;
    tss:summary [
        tss:from "2021-03-03T23:15:00+00:00"^^xsd:dateTime ;
        tss:until "2026-01-01T00:00:00+00:00"^^xsd:dateTime ;
        tss:missingValuePolicy tss:ExcludeEmptyValues ;
        tss:count "5"^^xsd:integer ;
        tss:missing "0"^^xsd:integer ;
        tss:firstTime "2021-03-03T23:15:00+00:00"^^xsd:dateTime ;
        tss:lastTime "2021-03-04T01:00:00+00:00"^^xsd:dateTime ;
        prov:generatedAtTime "2026-10-26T09:03:01+00:00"^^xsd:dateTime
    ] .

ex:zevenaarshaven-tv-value-field
    a tss:Field ;
    tss:columnName "value" ;
    rdfs:label "Electrical conductivity at 20 °C, Kanaal Gent-Terneuzen Zevenaarshaven (NL)"@en ;
    tss:datatype xsd:double ;
    tss:minimum "0"^^xsd:double ;
    tss:maximum "10000"^^xsd:double ;
    tss:observedProperty quantitykind:ElectricConductivity ;
    tss:unit unit:MicroS-PER-CentiM ;
    tss:sensor ex:sensor-zevenaarshaven-ec20 ;
    tss:featureOfInterest ex:water-zevenaarshaven ;
    tss:fieldDescription [
        dcterms:description "Electrical conductivity at 20 °C, Kanaal Gent-Terneuzen Zevenaarshaven (NL)"@en ;
        dcterms:subject quantitykind:ElectricConductivity ;
        dcterms:relation ex:sensor-zevenaarshaven-ec20, ex:water-zevenaarshaven
    ] ;
    tss:summary [
        tss:from "2021-03-03T23:15:00+00:00"^^xsd:dateTime ;
        tss:until "2026-01-01T00:00:00+00:00"^^xsd:dateTime ;
        tss:missingValuePolicy tss:ExcludeEmptyValues ;
        tss:count "5"^^xsd:integer ;
        tss:missing "0"^^xsd:integer ;
        tss:firstTime "2021-03-03T23:15:00+00:00"^^xsd:dateTime ;
        tss:lastTime "2021-03-04T01:00:00+00:00"^^xsd:dateTime ;
        tss:observedMinimum "4716.1"^^xsd:double ;
        tss:observedMaximum "4797.72"^^xsd:double ;
        tss:mean "4750.588"^^xsd:double ;
        tss:median "4745.36"^^xsd:double ;
        prov:generatedAtTime "2026-10-26T09:03:01+00:00"^^xsd:dateTime
    ] .
'''


def real_files():
    """The bytes of every file in the real "metadata endpoint/" folder, by name"""
    found = {}
    for path in sorted(REAL_FOLDER.iterdir()):
        if path.is_file():
            found[path.name] = path.read_bytes()
    return found


def setUpModule():
    global REAL_BEFORE
    REAL_BEFORE = real_files()


def tearDownModule():
    # No test may have changed (or added, or removed) a file of the real folder
    assert real_files() == REAL_BEFORE, "a test changed the real 'metadata endpoint/' folder"


class AddPoint(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "metadata endpoint"
        self.folder.mkdir()
        patcher = patch.object(paths, "METADATA_DIR", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.write(TIME_VALUE_ID, TIME_VALUE)
        self.client = TestClient(main.app)

    # --- helpers --------------------------------------------------------------

    def write(self, endpoint_id, text):
        (self.folder / f"{endpoint_id}.ttl").write_bytes(text.encode("utf-8"))

    def copy(self, endpoint_id, mutable=None):
        """Copy a bundled endpoint into this test's folder; mutable True/False: say so in the copy"""
        text = (REAL_FOLDER / f"{endpoint_id}.ttl").read_bytes().decode("utf-8")
        if mutable is not None:
            self.assertEqual(text.count("tss:mutable "), 1)
            text = re.sub(r"tss:mutable (true|false)", "tss:mutable " + ("true" if mutable else "false"), text)
        self.write(endpoint_id, text)

    def file_bytes(self, endpoint_id=TIME_VALUE_ID):
        return (self.folder / f"{endpoint_id}.ttl").read_bytes()

    def post(self, values, endpoint_id=TIME_VALUE_ID):
        return self.client.post(f"/api/endpoints/{endpoint_id}/points", json={"values": values})

    def exact_graph(self, endpoint_id=TIME_VALUE_ID):
        """The file as rdflib reads it with every literal's text as written (as the route does)"""
        path = self.folder / f"{endpoint_id}.ttl"
        return points_routes.read_exactly(path.read_bytes().decode("utf-8"), path.as_uri(), path.name)

    def point_texts(self, endpoint_id=TIME_VALUE_ID):
        """The points of the file, read as RDF like every tab reads them, in time order:
        [{field IRI: the text of its value}]"""
        graph, source = documents.load_source(endpoint_id)
        found = []
        for row in data_reading.point_rows(graph, source):
            texts = {}
            for field, value in row.items():
                texts[str(field)] = str(value)
            found.append(texts)
        return found

    def summary(self, graph, field):
        node = graph.value(URIRef(field), TSS.summary)
        self.assertIsNotNone(node)
        return node

    def refused(self, response, status, words, endpoint_id=TIME_VALUE_ID, before=None):
        """The answer is an error with this status whose message has these words, and the file is unchanged"""
        self.assertEqual(response.status_code, status, response.text)
        self.assertIn(words, response.json()["detail"])
        if before is not None:
            self.assertEqual(self.file_bytes(endpoint_id), before)

    # --- a point that fits --------------------------------------------------------

    def test_a_valid_append(self):
        before = self.exact_graph()
        old_modified = value_reading.literal_time(before.value(SOURCE, TSS.modifiedAt))
        response = self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "4760.0"})
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()
        self.assertTrue(answer["conforms"])
        self.assertEqual(answer["points"], 6)
        # The window does not change: the point is inside it
        self.assertEqual((answer["from"], answer["until"]), ("2021-03-03T23:15:00Z", "2026-01-01T00:00:00Z"))

        # The file: 6 points in time order, the new one last, every old value's text as it was
        points = self.point_texts()
        self.assertEqual([point[TIME] for point in points], [
            "2021-03-03T23:15:00Z", "2021-03-03T23:30:00Z", "2021-03-03T23:45:00Z",
            "2021-03-04T00:30:00Z", "2021-03-04T01:00:00Z", "2021-03-04T01:15:00Z"])
        self.assertEqual([point[VALUE] for point in points], ["4797.72", "4717.8", "4745.36", "4716.1", "4775.96", "4760.0"])
        text = self.file_bytes().decode("utf-8")
        self.assertIn('        { "time": "2021-03-04T01:15:00Z", "value": "4760.0"}\n      ]"""^^rdf:JSON', text)
        self.assertNotIn("# A comment", text)  # comments are lost (known and accepted)

        # The stored summaries, recomputed
        graph = self.exact_graph()
        value = self.summary(graph, VALUE)
        self.assertEqual(graph.value(value, TSS["count"]), Literal("6", datatype=XSD.integer))
        self.assertEqual(graph.value(value, TSS.missing), Literal("0", datatype=XSD.integer))
        self.assertEqual(str(graph.value(value, TSS.mean)), "4752.156666666667")
        self.assertEqual(str(graph.value(value, TSS.median)), "4752.68")
        self.assertEqual(str(graph.value(value, TSS.observedMinimum)), "4716.1")
        self.assertEqual(str(graph.value(value, TSS.observedMaximum)), "4797.72")
        self.assertEqual(str(graph.value(value, TSS.firstTime)), "2021-03-03T23:15:00Z")
        self.assertEqual(str(graph.value(value, TSS.lastTime)), "2021-03-04T01:15:00Z")
        self.assertEqual(graph.value(value, TSS.missingValuePolicy), TSS.ExcludeEmptyValues)
        time = self.summary(graph, TIME)
        self.assertEqual(str(graph.value(time, TSS["count"])), "6")
        self.assertEqual(str(graph.value(time, TSS.lastTime)), "2021-03-04T01:15:00Z")
        self.assertIsNone(graph.value(time, TSS.mean))  # not added: the time field's summary never stated one
        # prov:generatedAtTime of each summary and the source's tss:modifiedAt: now, in UTC with Z
        modified = graph.value(SOURCE, TSS.modifiedAt)
        self.assertEqual(str(modified), answer["modifiedAt"])
        self.assertTrue(str(modified).endswith("Z"))
        self.assertGreater(value_reading.literal_time(modified), old_modified)
        for summary in (value, time):
            self.assertEqual(graph.value(summary, PROV.generatedAtTime), modified)
        # tss:committedUntil: the time of this write too (one value), not the new point's time,
        # so here it lies after tss:until
        self.assertEqual(list(graph.objects(SOURCE, TSS.committedUntil)), [modified])
        self.assertEqual(answer["committedUntil"], answer["modifiedAt"])

        # The SPARQL updates that ran: the points, the source, and one per summary
        updates = [update["update"] for update in answer["sparql"]]
        self.assertEqual(len(updates), 4)
        self.assertIn("DELETE { ?source tss:points ?old }\nINSERT { ?source tss:points ?new }", updates[0])
        self.assertIn("tss:modifiedAt ?modifiedAt", updates[1])
        self.assertIn("tss:committedUntil ?committedUntil", updates[1])
        self.assertIn("tss:mean ?mean", updates[3])
        bindings = {binding["variable"]: binding["value"] for binding in answer["sparql"][0]["bindings"]}
        self.assertEqual(bindings["?source"], "ex:zevenaarshaven-tv-source")
        self.assertTrue(bindings["?new"].endswith('"""^^rdf:JSON'))

        # SHACL: the answer's report and the SHACL tab on the new file both conform
        report = Graph().parse(data=answer["report"], format="turtle")
        self.assertEqual(checks(report), [])
        shacl = self.client.get(f"/api/endpoints/{TIME_VALUE_ID}/shacl")
        self.assertEqual(checks(Graph().parse(data=shacl.text, format="turtle")), [])

        # The Query tab's "Check the stored summary" and the SQL box's check agree with the summaries
        summary = data_routes.query_series(TIME_VALUE_ID, VALUE)["summary"]
        self.assertEqual((summary["count"], summary["empty"], summary["median"]), (6, 0, 4752.68))
        self.assertEqual(summary["mean"], 4752.157)
        columns, rows = run_example(TIME_VALUE_ID, UP_TO_DATE)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row[columns.index("up_to_date")], row)

    def test_a_point_after_until_extends_until(self):
        response = self.post({TIME: "2026-03-01T12:00:00Z", VALUE: "4800"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["until"], "2026-03-01T12:00:01Z")  # the newest point plus one second
        self.assertEqual(response.json()["from"], "2021-03-03T23:15:00Z")
        graph = self.exact_graph()
        self.assertEqual(str(graph.value(SOURCE, TSS.until)), "2026-03-01T12:00:01Z")
        for field in (TIME, VALUE):
            summary = self.summary(graph, field)
            self.assertEqual(str(graph.value(summary, TSS.until)), "2026-03-01T12:00:01Z")
            self.assertEqual(str(graph.value(summary, TSS.lastTime)), "2026-03-01T12:00:00Z")

    def test_a_point_before_from_moves_from(self):
        response = self.post({TIME: "2020-12-31T23:00:00+01:00", VALUE: "4700"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["from"], "2020-12-31T22:00:00Z")  # the earliest point, in UTC
        self.assertEqual(response.json()["until"], "2026-01-01T00:00:00Z")
        graph = self.exact_graph()
        self.assertEqual(str(graph.value(SOURCE, TSS["from"])), "2020-12-31T22:00:00Z")
        summary = self.summary(graph, VALUE)
        self.assertEqual(str(graph.value(summary, TSS["from"])), "2020-12-31T22:00:00Z")
        self.assertEqual(str(graph.value(summary, TSS.firstTime)), "2020-12-31T22:00:00Z")
        self.assertEqual(str(graph.value(summary, TSS.observedMinimum)), "4700.0")
        # The point keeps its time exactly as it was typed, and comes first
        self.assertEqual(self.point_texts()[0][TIME], "2020-12-31T23:00:00+01:00")

    # --- refused, and nothing changes -------------------------------------------------

    def test_a_duplicate_time_is_refused(self):
        before = self.file_bytes()
        self.refused(self.post({TIME: "2021-03-03T23:30:00Z", VALUE: "1"}), 409, "already a point", before=before)
        # The same moment written another way is the same time
        self.refused(self.post({TIME: "2021-03-04T00:30:00+00:00", VALUE: "1"}), 409, "already a point", before=before)

    def test_a_value_that_is_no_number_is_refused(self):
        before = self.file_bytes()
        self.refused(self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "abc"}), 400, "'abc' is not a valid xsd:double", before=before)

    def test_a_time_that_is_no_valid_date_time_or_no_time_is_refused(self):
        before = self.file_bytes()
        self.refused(self.post({TIME: "2021-02-30T01:15:00Z", VALUE: "1"}), 400, "not a valid xsd:dateTime", before=before)
        self.refused(self.post({TIME: "yesterday", VALUE: "1"}), 400, "not a valid xsd:dateTime", before=before)
        self.refused(self.post({VALUE: "1"}), 400, "needs a time", before=before)
        self.refused(self.post({TIME: "", VALUE: "1"}), 400, "needs a time", before=before)

    def test_a_time_without_a_timezone_is_added_as_written(self):
        # No timezone needed: the time is read as UTC (to sort, to compare keys, for the window and
        # the summaries) and written into the points exactly as it was typed; SHACL (P2) agrees
        response = self.post({TIME: "2021-03-04T01:15:00", VALUE: "4760.0"})
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()
        self.assertTrue(answer["conforms"])
        self.assertEqual((answer["from"], answer["until"]), ("2021-03-03T23:15:00Z", "2026-01-01T00:00:00Z"))
        points = self.point_texts()
        self.assertEqual(points[-1][TIME], "2021-03-04T01:15:00")
        self.assertIn('{ "time": "2021-03-04T01:15:00", "value": "4760.0"}', self.file_bytes().decode("utf-8"))
        graph = self.exact_graph()
        self.assertEqual(str(graph.value(self.summary(graph, VALUE), TSS.lastTime)), "2021-03-04T01:15:00Z")
        # The same moment with a timezone is the same time: a duplicate
        before = self.file_bytes()
        self.refused(self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "1"}), 409, "already a point", before=before)

    def test_a_value_above_the_maximum_fails_shacl_and_nothing_is_written(self):
        before = self.file_bytes()
        response = self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "20000"})
        self.assertEqual(response.status_code, 422, response.text)
        answer = response.json()
        self.assertFalse(answer["conforms"])
        self.assertIn((SH.Violation, "P5"), checks(Graph().parse(data=answer["report"], format="turtle")))
        self.assertEqual(len(answer["sparql"]), 4)  # the updates ran, on the graph in memory only
        self.assertEqual(self.file_bytes(), before)

    def test_an_immutable_source_is_refused(self):
        self.write(TIME_VALUE_ID, TIME_VALUE.replace("tss:mutable true", "tss:mutable false"))
        before = self.file_bytes()
        self.refused(self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "1"}), 409, "tss:mutable true", before=before)
        # Not stating tss:mutable at all is not enough either
        self.write(TIME_VALUE_ID, TIME_VALUE.replace("tss:mutable true ;", ""))
        before = self.file_bytes()
        self.refused(self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "1"}), 409, "tss:mutable true", before=before)

    def test_an_external_source_is_refused(self):
        self.copy(CSV_ID, mutable=True)  # even when it says its data may change
        before = self.file_bytes(CSV_ID)
        response = self.post({EX + "grootdok-time-field": "2021-03-04T01:15:00Z"}, endpoint_id=CSV_ID)
        self.refused(response, 400, "tss:embedded true", endpoint_id=CSV_ID, before=before)

    def test_an_unknown_field_is_refused(self):
        before = self.file_bytes()
        response = self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "1", EX + "no-such-field": "2"})
        self.refused(response, 400, "is not a field of this source", before=before)

    def test_an_unknown_endpoint_is_404(self):
        self.refused(self.post({TIME: "2021-03-04T01:15:00Z"}, endpoint_id="no_such_endpoint"), 404, "Unknown metadata endpoint")

    def test_a_document_with_relative_iris_is_refused(self):
        # <../notes.txt> resolves to the folder next to the file: written back, it would become absolute
        self.write(TIME_VALUE_ID, TIME_VALUE + "\nex:zevenaarshaven-tv-source rdfs:seeAlso <../notes.txt> .\n")
        before = self.file_bytes()
        self.refused(self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "1"}), 400, "relative IRIs", before=before)

    def test_points_with_an_undeclared_key_are_refused(self):
        # Writing the points again would lose the "note" key (SHACL only warns about it, P6)
        self.write(TIME_VALUE_ID, TIME_VALUE.replace('"value": "4717.8"}', '"value": "4717.8", "note": "x"}'))
        before = self.file_bytes()
        self.refused(self.post({TIME: "2021-03-04T01:15:00Z", VALUE: "1"}), 400, "no field's tss:columnName", before=before)

    # --- at the same time ------------------------------------------------------------------

    def test_two_appends_at_once_both_end_up_in_the_file(self):
        start = threading.Barrier(2)
        answers = {}

        def append(time, value):
            client = TestClient(main.app)
            start.wait()  # both threads send their request at the same moment
            answers[time] = self.post_with(client, {TIME: time, VALUE: value})

        threads = [threading.Thread(target=append, args=("2021-03-04T01:15:00Z", "4760.0")),
                   threading.Thread(target=append, args=("2021-03-04T01:30:00Z", "4761.0"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for time, response in answers.items():
            self.assertEqual(response.status_code, 200, response.text)
        times = [point[TIME] for point in self.point_texts()]
        self.assertEqual(len(times), 7)
        self.assertIn("2021-03-04T01:15:00Z", times)
        self.assertIn("2021-03-04T01:30:00Z", times)
        graph = self.exact_graph()
        self.assertEqual(str(graph.value(self.summary(graph, VALUE), TSS["count"])), "7")

    def post_with(self, client, values):
        return client.post(f"/api/endpoints/{TIME_VALUE_ID}/points", json={"values": values})

    # --- the other embedded endpoint (three columns) ----------------------------------------

    def test_the_three_column_embedded_endpoint_accepts_a_point(self):
        self.copy(EMBEDDED_ID, mutable=True)
        color = 'rgba(0, 128, 0, 1.0) "quoted" \\ and ° '  # quotes, a backslash and a non-ASCII letter
        response = self.post({EMBEDDED_TIME: "2021-03-04T01:15:00Z", EMBEDDED_EC20: "4760.0", EMBEDDED_COLOR: color},
                             endpoint_id=EMBEDDED_ID)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["points"], 6)
        last = self.point_texts(EMBEDDED_ID)[-1]
        self.assertEqual(last, {EMBEDDED_TIME: "2021-03-04T01:15:00Z", EMBEDDED_EC20: "4760.0", EMBEDDED_COLOR: color})
        graph = self.exact_graph(EMBEDDED_ID)
        color_summary = self.summary(graph, EMBEDDED_COLOR)
        self.assertEqual(str(graph.value(color_summary, TSS["count"])), "6")
        self.assertIsNone(graph.value(color_summary, TSS.mean))  # a text field: no numbers
        self.assertEqual(str(graph.value(self.summary(graph, EMBEDDED_EC20), TSS.mean)), "4752.156666666667")

    def test_a_point_without_a_value_counts_as_missing(self):
        self.copy(EMBEDDED_ID, mutable=True)
        response = self.post({EMBEDDED_TIME: "2021-03-04T01:15:00Z", EMBEDDED_EC20: "4760.0", EMBEDDED_COLOR: ""},
                             endpoint_id=EMBEDDED_ID)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('{ "time": "2021-03-04T01:15:00Z", "ec20": "4760.0"}', self.file_bytes(EMBEDDED_ID).decode("utf-8"))
        graph = self.exact_graph(EMBEDDED_ID)
        color_summary = self.summary(graph, EMBEDDED_COLOR)
        self.assertEqual((str(graph.value(color_summary, TSS["count"])), str(graph.value(color_summary, TSS.missing))), ("5", "1"))
        self.assertEqual(str(graph.value(color_summary, TSS.lastTime)), "2021-03-04T01:00:00Z")

    def test_the_bundled_three_column_endpoint_is_immutable(self):
        self.copy(EMBEDDED_ID)  # as it is: tss:mutable false
        before = self.file_bytes(EMBEDDED_ID)
        response = self.post({EMBEDDED_TIME: "2021-03-04T01:15:00Z"}, endpoint_id=EMBEDDED_ID)
        self.refused(response, 409, "tss:mutable true", endpoint_id=EMBEDDED_ID, before=before)


if __name__ == "__main__":
    unittest.main()
