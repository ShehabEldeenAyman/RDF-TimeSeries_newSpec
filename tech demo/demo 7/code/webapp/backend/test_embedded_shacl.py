"""The SHACL check of embedded points (SHACL/embedded_shapes.ttl): python -B -m unittest -v test_embedded_shacl.

The backend validates the metadata plus the points read as RDF (lift_points: the tss:points
JSON read as JSON-LD, with a context made from the fields), so the shapes check every value
against its field: its datatype, its range, the time window, the key. Each test changes the
tss:points of the embedded endpoint in memory (the file stays as it is) and looks at the
results of the report: their severity, check id (P1-P6, F9, S6), focus node and value."""
import logging
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from rdflib import Graph, Literal, URIRef, RDF
from rdflib.namespace import DCTERMS, SH, XSD

import main
# The SHACL command line (code/shacl_report/shacl_report.py); importing main made code/ importable
from shacl_report import shacl_report
from test_external_shacl import BASE, CUSTOM_SHAPES, checks, conforms, read_report, result_of

EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EX = "https://example.org/ex#"
SOURCE = URIRef(EX + "zevenaarshaven-source")
TIME = URIRef(EX + "zevenaarshaven-time-field")
VALUE = URIRef(EX + "zevenaarshaven-ec20-value-field")
COLOR = URIRef(EX + "zevenaarshaven-quality-color-value-field")

# One good point, and the same point at another time
POINT = '{"time": "2021-03-03T23:15:00Z", "ec20": "4797.72", "qualityColor": "green"}'
LATER = '{"time": "2021-03-03T23:30:00Z", "ec20": "4717.8", "qualityColor": "green"}'


class EmbeddedShacl(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        # rdflib logs a long warning for every literal that does not fit its datatype ("4,5" as xsd:double)
        patcher = patch.object(logging.getLogger("rdflib.term"), "disabled", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def validate(self, points=None, change=None):
        """The SHACL report (a graph) of the embedded endpoint with other tss:points
        (the text of an rdf:JSON literal), and/or another change to its metadata"""
        graph = main.load_graph(EMBEDDED)
        if points is not None:
            graph.set((SOURCE, main.TSS.points, Literal(points, datatype=RDF.JSON)))
        if change is not None:
            change(graph)
        with patch.object(main, "load_graph", return_value=graph):
            response = self.client.get(f"/api/endpoints/{EMBEDDED}/shacl")
        self.assertEqual(response.status_code, 200, response.text)
        self.report_text = response.text  # the Turtle itself, for tests that look at how a value is written
        report, node = read_report(response)
        return report

    def test_the_embedded_endpoint_conforms(self):
        report = self.validate()
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [])
        node = report.value(predicate=RDF.type, object=SH.ValidationReport)
        self.assertEqual(report.value(node, DCTERMS.source), URIRef(BASE + "files/SHACL/embedded_shapes.ttl"))

    def test_points_as_json_numbers_conform_too(self):
        report = self.validate('[{"time": "2021-03-03T23:15:00Z", "ec20": 4797.72, "qualityColor": "green"}]')
        self.assertEqual(checks(report), [])

    # --- tss:points must be readable (S6) -------------------------------------

    def test_invalid_json_is_an_s6_violation(self):
        report = self.validate('[{"time": , "ec20": "1"}]')
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "S6")])
        # The backend adds this result itself: SHACL can't look inside the rdf:JSON literal
        result = result_of(report, "S6")
        self.assertEqual(report.value(result, SH.focusNode), SOURCE)
        self.assertEqual(report.value(result, SH.resultPath), main.TSS.points)
        self.assertEqual(report.value(result, SH.sourceConstraintComponent), SH.SPARQLConstraintComponent)

    def test_points_typed_xsd_string_are_an_s6_violation(self):
        text = str(main.load_graph(EMBEDDED).value(SOURCE, main.TSS.points))
        report = self.validate(change=lambda graph: graph.set((SOURCE, main.TSS.points, Literal(text, datatype=XSD.string))))
        self.assertFalse(conforms(report))
        # Twice S6: the shapes (sh:datatype rdf:JSON), and the backend, which can't read them as JSON-LD
        self.assertEqual(checks(report), [(SH.Violation, "S6"), (SH.Violation, "S6")])
        components = set()
        for result in report.objects(None, SH.result):
            components.add(report.value(result, SH.sourceConstraintComponent))
        self.assertEqual(components, {SH.DatatypeConstraintComponent, SH.SPARQLConstraintComponent})

    def test_a_value_that_is_no_object_is_skipped_by_json_ld(self):
        # JSON-LD drops a value that is not an object (here 42 and "ec20") without a word, in
        # rdflib and in the browser's parser alike, so the shapes never see it: a known limit
        report = self.validate(f'[{POINT}, 42, "ec20"]')
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [])
        graph = main.load_graph(EMBEDDED)
        graph.set((SOURCE, main.TSS.points, Literal(f'[{POINT}, 42, "ec20"]', datatype=RDF.JSON)))
        self.assertEqual(len(set(main.rdf_generation.lift_points(graph, SOURCE).subjects())), 1)

    # --- the points: P1-P6 ---------------------------------------------------

    def test_a_point_without_a_time_is_a_p1_violation(self):
        report = self.validate('[{"ec20": "1", "qualityColor": "green"}]')
        # ... and no point has a time, so the time field also has no values (F9)
        self.assertEqual(checks(report), [(SH.Violation, "P1"), (SH.Warning, "F9")])
        self.assertEqual(report.value(result_of(report, "P1"), SH.focusNode), SOURCE)

    def test_a_time_outside_the_window_is_a_p2_violation(self):
        # tss:from is 2021-03-03T23:15:00Z (included), tss:until 2026-01-01T00:00:00Z (not included)
        report = self.validate('[{"time": "1999-01-01T00:00:00Z", "ec20": "1", "qualityColor": "green"},'
                               ' {"time": "2026-01-01T00:00:00Z", "ec20": "1", "qualityColor": "green"},'
                               ' {"time": "2021-03-03T23:15:00Z", "ec20": "1", "qualityColor": "green"}]')
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "P2"), (SH.Violation, "P2")])
        times = set()
        for result in report.objects(None, SH.result):
            times.add(report.value(result, SH.value).toPython().year)
        self.assertEqual(times, {1999, 2026})

    def test_a_duplicate_time_is_a_p3_violation(self):
        # The time field is the source's tss:key
        report = self.validate(f'[{POINT}, {POINT.replace("4797.72", "1")}]')
        self.assertEqual(checks(report), [(SH.Violation, "P3")])
        result = result_of(report, "P3")
        self.assertEqual(report.value(result, SH.focusNode), SOURCE)
        self.assertEqual(report.value(result, SH.value).toPython().hour, 23)

    def test_a_key_of_two_fields_needs_both_values_to_repeat(self):
        def two_keys(graph):
            graph.add((SOURCE, main.TSS.key, COLOR))
        # The same time, another colour: a different key
        report = self.validate(f'[{POINT}, {POINT.replace("green", "red")}]', two_keys)
        self.assertEqual(checks(report), [])
        # The same time and colour: the same key (both key values are reported)
        report = self.validate(f'[{POINT}, {POINT.replace("4797.72", "1")}]', two_keys)
        self.assertEqual(checks(report), [(SH.Violation, "P3"), (SH.Violation, "P3")])

    def test_not_a_date_as_a_time_is_a_p4_violation(self):
        report = self.validate('[{"time": "not a date", "ec20": "1", "qualityColor": "green"}]')
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "P4")])
        result = result_of(report, "P4")
        self.assertEqual(report.value(result, SH.focusNode), TIME)
        self.assertEqual(str(report.value(result, SH.value)), "not a date")

    def test_a_day_that_does_not_exist_is_a_p4_violation(self):
        report = self.validate('[{"time": "2021-02-30T23:15:00Z", "ec20": "1", "qualityColor": "green"}]')
        self.assertEqual(checks(report), [(SH.Violation, "P4")])

    def test_a_date_with_a_timezone_is_a_valid_xsd_date(self):
        # rdflib's xsd:dateTime("2021-03-04Z") gives no value, but "2021-03-04Z" is a valid xsd:date
        def date_color(graph):
            graph.set((COLOR, main.TSS.datatype, XSD.date))
        report = self.validate('[{"time": "2021-03-03T23:15:00Z", "ec20": "1", "qualityColor": "2021-03-04Z"},'
                               ' {"time": "2021-03-03T23:30:00Z", "ec20": "1", "qualityColor": "2021-03-04+02:00"},'
                               ' {"time": "2021-03-03T23:45:00Z", "ec20": "1", "qualityColor": "2021-03-04"}]', date_color)
        self.assertEqual(checks(report), [])
        # A day that does not exist, with or without a timezone, and a date-time, are no xsd:date
        for text in ["2021-02-30Z", "2021-02-30", "2021-03-04T00:00:00Z"]:
            with self.subTest(text=text):
                report = self.validate(f'[{POINT.replace("green", text)}]', date_color)
                self.assertEqual(checks(report), [(SH.Violation, "P4")])
                self.assertIn(f"P4 datatype: {text} (the point", str(report.value(result_of(report, "P4"), SH.resultMessage)))

    def test_a_time_typed_xsd_date_time_stamp_needs_a_timezone(self):
        # xsd:dateTimeStamp is an xsd:dateTime with a timezone that can't be left out
        def stamp_time(graph):
            graph.set((TIME, main.TSS.datatype, XSD.dateTimeStamp))
        report = self.validate(f'[{POINT}, {LATER.replace("2021-03-03T23:30:00Z", "2021-03-04T00:30:00+01:00")}]', stamp_time)
        self.assertEqual(checks(report), [])
        # Without a timezone it is no xsd:dateTimeStamp: P4. (P2 reports such a time too, for any
        # datatype: rdflib's SPARQL takes a time without a timezone as before every tss:from.)
        report = self.validate(f'[{POINT}, {LATER.replace("23:30:00Z", "23:30:00")}]', stamp_time)
        self.assertIn((SH.Violation, "P4"), checks(report))
        self.assertIn("P4 datatype: 2021-03-03T23:30:00 (the point", str(report.value(result_of(report, "P4"), SH.resultMessage)))

    def test_a_wrong_lexical_form_for_xsd_double_is_a_p4_violation(self):
        report = self.validate(f'[{POINT.replace("4797.72", "4,5")}, {LATER}]')
        self.assertEqual(checks(report), [(SH.Violation, "P4")])
        result = result_of(report, "P4")
        self.assertEqual(report.value(result, SH.focusNode), VALUE)
        value = report.value(result, SH.value)
        self.assertEqual((str(value), value.datatype), ("4,5", XSD.double))

    def test_texts_rdflib_would_rewrite_are_p4_violations(self):
        # The points keep their text (lift_points turns rdflib's normalising off), so SHACL
        # sees " 12 " and "1_000", which rdflib would otherwise make "12.0" and "1000.0"
        for text in [" 12 ", "1_000"]:
            with self.subTest(text=text):
                report = self.validate(f'[{POINT.replace("4797.72", text)}, {LATER}]')
                self.assertEqual(checks(report), [(SH.Violation, "P4")])
                result = result_of(report, "P4")
                self.assertEqual(report.value(result, SH.focusNode), VALUE)
                self.assertIn(f"P4 datatype: {text} (the point at 2021-03-03T23:15:00Z)", str(report.value(result, SH.resultMessage)))
                # ... and the report's Turtle writes the value with that text (rdflib reads it back as a number)
                self.assertIn(f'sh:value "{text}"^^xsd:double', self.report_text)

    def test_yes_as_an_xsd_boolean_is_a_p4_violation(self):
        # rdflib would make "yes"^^xsd:boolean "false" (a valid boolean)
        def boolean_color(graph):
            graph.set((COLOR, main.TSS.datatype, XSD.boolean))
        report = self.validate(f'[{POINT.replace("green", "yes")}, {LATER.replace("green", "true")}]', boolean_color)
        self.assertEqual(checks(report), [(SH.Violation, "P4")])
        result = result_of(report, "P4")
        self.assertEqual(report.value(result, SH.focusNode), COLOR)
        self.assertIn("P4 datatype: yes (the point", str(report.value(result, SH.resultMessage)))
        # Written in full: rdflib's short form (a bare yes) would be broken Turtle
        self.assertIn('sh:value "yes"^^xsd:boolean', self.report_text)

    def test_a_json_object_as_a_value_is_a_p4_violation(self):
        # The object becomes a node inside the point, not a literal; its key "a" is no field's
        report = self.validate("[" + POINT.replace('"4797.72"', '{"a": "1"}') + "]")
        self.assertEqual(checks(report), [(SH.Violation, "P4"), (SH.Warning, "P6")])
        self.assertEqual(report.value(result_of(report, "P4"), SH.focusNode), VALUE)

    def test_json_ld_keywords_in_the_points_are_an_s6_violation(self):
        # A key that starts with @ is refused before the points are read (no context is fetched)
        for points in ['[{"@context": "http://127.0.0.1:9/ctx.jsonld", "time": "2021-03-03T23:15:00Z"}]',
                       "[" + POINT.replace('"4797.72"', '{"@value": "1", "@type": "http://www.w3.org/2001/XMLSchema#integer"}') + "]",
                       '[{"\\u0040id": "urn:x:p", "time": "2021-03-03T23:15:00Z"}]']:
            with self.subTest(points=points):
                report = self.validate(points)
                self.assertFalse(conforms(report))
                self.assertEqual(checks(report), [(SH.Violation, "S6")])
                self.assertIn("starts with @", str(report.value(result_of(report, "S6"), SH.resultMessage)))

    def test_a_value_above_the_maximum_is_a_p5_violation(self):
        # The ec20 field has tss:minimum 0 and tss:maximum 10000
        report = self.validate(f'[{POINT.replace("4797.72", "99999")}, {LATER.replace("4717.8", "-1")}]')
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "P5"), (SH.Violation, "P5")])
        values = set()
        messages = set()
        for result in report.objects(None, SH.result):
            self.assertEqual(report.value(result, SH.focusNode), VALUE)
            values.add(report.value(result, SH.value).toPython())
            messages.add(str(report.value(result, SH.resultMessage)))
        self.assertEqual(values, {99999.0, -1.0})
        # The times keep their text (...Z), as they are written in the points
        self.assertIn("P5 range: 99999 (the point at 2021-03-03T23:15:00Z) is above the field's tss:maximum 10000.0.", messages)

    def test_an_undeclared_key_is_only_a_p6_warning(self):
        report = self.validate("[" + POINT.replace('"qualityColor"', '"extra": "y", "qualityColor"') + "]")
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [(SH.Warning, "P6")])
        self.assertEqual(report.value(result_of(report, "P6"), SH.value), Literal("extra"))

    def test_a_point_without_any_field_value_is_only_a_p6_warning(self):
        # A point with only an undeclared key, or with only empty values and an undeclared key,
        # has nothing to check but its keys. The data routes and the converter skip it, like
        # the browser does (test_storage_profiles, test_tss2rdf).
        report = self.validate(f'[{POINT}, {{"extra": "x"}}, {{"time": "", "ec20": "", "note": "y"}}]')
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [(SH.Warning, "P6"), (SH.Warning, "P6")])
        keys = set()
        for result in report.objects(None, SH.result):
            keys.add(str(report.value(result, SH.value)))
        self.assertEqual(keys, {"extra", "note"})

    def test_an_undeclared_key_with_a_space_still_gives_a_report(self):
        # "Some Key" gets the predicate urn:tss:unmapped:Some%20Key (a space can't be in an IRI).
        # The point has no time, so P1 copies the point with its values into the report:
        # the report must still be written as Turtle
        report = self.validate('[{"ec20": "1", "qualityColor": "green", "Some Key": "x"}]')
        self.assertEqual(checks(report), [(SH.Violation, "P1"), (SH.Warning, "F9"), (SH.Warning, "P6")])
        self.assertEqual(report.value(result_of(report, "P6"), SH.value), Literal("Some%20Key"))
        self.assertIn(URIRef("urn:tss:unmapped:Some%20Key"), set(report.predicates()))
        self.assertGreater(len(report.serialize(format="turtle")), 0)

    def test_an_undeclared_key_with_a_colon_is_a_p6_warning_too(self):
        # JSON-LD reads "a:b" as an IRI of its own, not under urn:tss:unmapped:
        report = self.validate("[" + POINT.replace('"qualityColor"', '"a:b": "y", "qualityColor"') + "]")
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [(SH.Warning, "P6")])
        self.assertEqual(report.value(result_of(report, "P6"), SH.value), Literal("a:b"))

    def test_a_field_missing_from_the_points_is_only_a_warning(self):
        # The ec20 field now reads the key "ec25", which no point has; their "ec20" is unknown now
        report = self.validate(change=lambda graph: graph.set((VALUE, main.TSS.columnName, Literal("ec25"))))
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [(SH.Warning, "F9"), (SH.Warning, "P6")])
        self.assertEqual(report.value(result_of(report, "F9"), SH.focusNode), VALUE)

    def test_an_embedded_source_cannot_have_a_data_file(self):
        report = self.validate(change=lambda graph: graph.add((SOURCE, main.TSS.dataFile, URIRef("https://example.org/data.csv"))))
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "S12")])
        self.assertEqual(report.value(result_of(report, "S12"), SH.resultPath), main.TSS.dataFile)


class ShaclAndLiftsTakeTurns(unittest.TestCase):
    """rdflib.NORMALIZE_LITERALS is one switch for the whole program. The lift of the points
    turns it off for a moment (to keep the exact texts), and so does pyshacl's validate() while
    it reads the shapes. Both hold tss2rdf.NORMALIZE_LOCK, so they take turns."""

    def looking_validate(self, validate, held):
        """A validate() that notes whether the lock is held when it is called, then validates"""
        def validate_and_look(*arguments, **options):
            held.append(main.NORMALIZE_LOCK.locked())
            return validate(*arguments, **options)
        return validate_and_look

    def test_the_backend_validates_while_holding_the_lock(self):
        held = []
        with patch.object(main, "shacl_validate", side_effect=self.looking_validate(main.shacl_validate, held)):
            response = TestClient(main.app).get(f"/api/endpoints/{EMBEDDED}/shacl")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(held, [True])
        self.assertFalse(main.NORMALIZE_LOCK.locked())  # and lets it go afterwards

    def test_the_command_line_validates_while_holding_the_lock(self):
        held = []
        shapes_path = main.SHACL_DIR / "embedded_shapes.ttl"
        with patch.object(shacl_report, "validate", side_effect=self.looking_validate(shacl_report.validate, held)):
            report, node, point_triples = shacl_report.validation_report(
                main.load_graph(EMBEDDED), shacl_report.read_rdf(shapes_path), shapes_path)
        self.assertEqual(held, [True])
        self.assertTrue(report.value(node, SH.conforms).toPython())

    def test_lifts_keep_their_texts_while_shacl_runs_in_other_threads(self):
        # 400 points with times in "...Z" and the value " 12 ": rdflib's normalising would make
        # them "...+00:00" and "12.0". Without the lock around pyshacl, about 4 in 10 lifts lost
        # some of these texts while two other threads validated.
        points = []
        for number in range(400):
            points.append(f'{{"time": "2021-03-04T{number // 60:02d}:{number % 60:02d}:00Z", "ec20": " 12 "}}')
        graph = main.load_graph(EMBEDDED)
        graph.set((SOURCE, main.TSS.points, Literal("[" + ", ".join(points) + "]", datatype=RDF.JSON)))
        shapes = Graph().parse(data=CUSTOM_SHAPES, format="turtle")

        stop = threading.Event()

        def validate_again_and_again():
            while not stop.is_set():
                main.validation_report(Graph(), shapes, "the test shapes")

        threads = [threading.Thread(target=validate_again_and_again) for number in range(2)]
        for thread in threads:
            thread.start()
        changed = 0  # lifts that lost some text
        try:
            for number in range(60):
                texts = set()
                for value in main.rdf_generation.lift_points(graph, SOURCE).objects():
                    texts.add(str(value))
                if " 12 " not in texts or any(text.endswith("+00:00") for text in texts):
                    changed += 1
        finally:
            stop.set()
            for thread in threads:
                thread.join()
        self.assertEqual(changed, 0)


if __name__ == "__main__":
    unittest.main()
