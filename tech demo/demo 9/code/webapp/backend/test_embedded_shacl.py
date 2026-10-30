"""The SHACL check of embedded points (SHACL/embedded_shapes.ttl): python -B -m unittest -v test_embedded_shacl.

The embedded points are RDF in the metadata itself: nodes linked to the source with tss:pointOf,
each value a triple with the field's IRI as predicate. The backend validates the metadata (read
with every literal's text kept, documents.parse_document), so the shapes check every value
against its field: its datatype, its range, the time window, the key. Each test takes the
embedded endpoint's file up to its points, appends points of its own (as a new reading would be
appended) and looks at the results of the report: their severity, check id (P1-P7, F9, S6,
S12), focus node and value. The file itself stays as it is.

point() and embedded_graph() are used by the other tests of embedded points too."""
import logging
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, RDF, SH, XSD

import main
import paths
import documents
import shacl_routes
import rdf_generation
from rdf_generation import TSS
# The SHACL command line (code/shacl_report/shacl_report.py); importing main made code/ importable
from shacl_report import shacl_report
from test_external_shacl import BASE, CUSTOM_SHAPES, checks, conforms, read_report, result_of

EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EX = "https://example.org/ex#"
SOURCE = URIRef(EX + "zevenaarshaven-source")
TIME = URIRef(EX + "zevenaarshaven-time-field")
VALUE = URIRef(EX + "zevenaarshaven-ec20-value-field")
COLOR = URIRef(EX + "zevenaarshaven-quality-color-value-field")


class Turtle(str):
    """A value of a point written as Turtle, as it is: a node, or a literal with another datatype"""


# The datatype each value of point() gets, in the file's prefixes (the fields' tss:datatype)
DATATYPES = {"time": "xsd:dateTime", "ec20": "xsd:double", "color": "xsd:string"}
FIELDS = {"time": "ex:zevenaarshaven-time-field", "ec20": "ex:zevenaarshaven-ec20-value-field",
          "color": "ex:zevenaarshaven-quality-color-value-field"}


def point(time="2021-03-03T23:15:00Z", ec20="4797.72", color="green", types=None, more="", of="ex:zevenaarshaven-source"):
    """One point of the embedded endpoint as a Turtle statement, the way a new reading is appended:
        [] tss:pointOf ex:zevenaarshaven-source ;
            ex:zevenaarshaven-time-field "2021-03-03T23:15:00Z"^^xsd:dateTime ; ...
    time, ec20, color: the texts of its values, typed with their field's datatype (types changes
    one, e.g. {"color": "xsd:date"}); None leaves the value out; Turtle(...) is written as it is.
    more: more of the point, in Turtle (e.g. 'ex:extra "y"'); of: its tss:pointOf (None: no link)"""
    kinds = dict(DATATYPES)
    kinds.update(types or {})
    parts = []
    if of is not None:
        parts.append(f"tss:pointOf {of}")
    for name, text in (("time", time), ("ec20", ec20), ("color", color)):
        if text is None:
            continue
        if isinstance(text, Turtle):
            parts.append(f"{FIELDS[name]} {text}")
        else:
            quoted = text.replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'{FIELDS[name]} "{quoted}"^^{kinds[name]}')
    if more:
        parts.append(more)
    return "[] " + " ;\n    ".join(parts) + " .\n"


# The same point a quarter of an hour later
LATER = {"time": "2021-03-03T23:30:00Z", "ec20": "4717.8"}


def embedded_graph(points, keep=False):
    """The embedded endpoint's metadata as the backend reads it (documents.parse_document: every
    literal's text kept), with these points (Turtle statements) appended instead of its own,
    or after its own (keep True)"""
    text, media_type = documents.read_document(documents.endpoint_file(EMBEDDED))
    if not keep:
        text = text[:text.index("# Points:")]  # the file up to its points
    address = documents.local_document_address(EMBEDDED, paths.DEFAULT_BASE)
    return documents.parse_document(text + "\n" + points, media_type, address, EMBEDDED)


class EmbeddedShacl(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        # rdflib logs a long warning for every literal that does not fit its datatype ("4,5" as xsd:double)
        patcher = patch.object(logging.getLogger("rdflib.term"), "disabled", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def validate(self, points=None, change=None, keep=False):
        """The SHACL report (a graph) of the embedded endpoint with other points (Turtle
        statements, see point; with keep, after its own), and/or another change to its metadata"""
        graph = documents.load_graph(EMBEDDED) if points is None else embedded_graph(points, keep)
        if change is not None:
            change(graph)
        with patch.object(documents, "load_graph", return_value=graph):
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

    def test_an_appended_point_conforms(self):
        # The file's own points plus one more statement at the end, as a new reading is added
        appended = point(time="2021-03-04T01:15:00Z")
        self.assertEqual(len(set(embedded_graph(appended, keep=True).subjects(TSS.pointOf, SOURCE))), 6)
        self.assertEqual(checks(self.validate(appended, keep=True)), [])

    def test_a_source_without_points_conforms(self):
        # A new source may have no readings yet; then no field gets the F9 warning either
        self.assertEqual(checks(self.validate("")), [])

    def test_a_double_in_turtles_short_form_conforms_but_a_decimal_does_not(self):
        # 4.79772E3 is Turtle's short form of an xsd:double; 4797.72 alone would be an xsd:decimal
        self.assertEqual(checks(self.validate(point(ec20=Turtle("4.79772E3")))), [])
        report = self.validate(point(ec20=Turtle("4797.72")))
        self.assertEqual(checks(report), [(SH.Violation, "P4")])
        self.assertEqual(report.value(result_of(report, "P4"), SH.value).datatype, XSD.decimal)

    # --- the points and their source: S6, S12 ------------------------------------

    def test_a_node_with_values_but_no_point_of_is_an_s6_violation(self):
        # It is no point of the source, so nothing would read its values
        report = self.validate(point() + point(of=None, **LATER))
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "S6")])
        result = result_of(report, "S6")
        self.assertEqual(report.value(result, SH.focusNode), SOURCE)
        self.assertEqual(report.value(result, SH.sourceConstraintComponent), SH.SPARQLConstraintComponent)

    def test_a_point_of_another_node_is_an_s6_violation(self):
        # A typo in the source's IRI: the point links to no tss:Source, and its values belong to none
        report = self.validate(point() + point(of="ex:zevenaarshaven-sourc", **LATER))
        self.assertEqual(checks(report), [(SH.Violation, "S6"), (SH.Violation, "S6")])
        components = set()
        for result in report.objects(None, SH.result):
            components.add(report.value(result, SH.sourceConstraintComponent))
        self.assertEqual(components, {SH.ClassConstraintComponent, SH.SPARQLConstraintComponent})

    def test_a_point_of_two_sources_is_an_s6_violation(self):
        report = self.validate(point(more="tss:pointOf ex:other-source"))
        self.assertIn((SH.Violation, "S6"), checks(report))
        components = set()
        for result in report.objects(None, SH.result):
            components.add(report.value(result, SH.sourceConstraintComponent))
        self.assertIn(SH.MaxCountConstraintComponent, components)

    def test_the_old_points_literal_is_not_allowed(self):
        # The points are RDF nodes now: a JSON literal (tss:points) can't be appended to
        def old_points(graph):
            graph.add((SOURCE, TSS.points, Literal("[]", datatype=RDF.JSON)))
        report = self.validate(change=old_points)
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "S12")])
        self.assertEqual(report.value(result_of(report, "S12"), SH.resultPath), TSS.points)

    def test_an_embedded_source_cannot_have_a_data_file(self):
        report = self.validate(change=lambda graph: graph.add((SOURCE, TSS.dataFile, URIRef("https://example.org/data.csv"))))
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "S12")])
        self.assertEqual(report.value(result_of(report, "S12"), SH.resultPath), TSS.dataFile)

    def test_a_source_without_a_key_is_a_violation(self):
        # tss:key is required for embedded sources too (S9), as for external ones
        report = self.validate(change=lambda graph: graph.remove((SOURCE, TSS.key, None)))
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "S9")])
        self.assertEqual(report.value(result_of(report, "S9"), SH.sourceConstraintComponent), SH.MinCountConstraintComponent)

    # --- the points: P1-P7, F9 ----------------------------------------------------

    def test_a_point_without_a_time_is_a_p1_violation(self):
        report = self.validate(point(time=None))
        # ... and no point has a time, so the time field also has no values (F9)
        self.assertEqual(checks(report), [(SH.Violation, "P1"), (SH.Warning, "F9")])
        self.assertEqual(report.value(result_of(report, "P1"), SH.focusNode), SOURCE)

    def test_a_point_with_only_its_link_is_a_p1_violation(self):
        report = self.validate(point() + "[] tss:pointOf ex:zevenaarshaven-source .\n")
        self.assertEqual(checks(report), [(SH.Violation, "P1")])

    def test_a_time_outside_the_window_is_a_p2_violation(self):
        # tss:from is 2021-03-03T23:15:00Z (included), tss:until 2026-01-01T00:00:00Z (not included)
        report = self.validate(point(time="1999-01-01T00:00:00Z") + point(time="2026-01-01T00:00:00Z") + point())
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "P2"), (SH.Violation, "P2")])
        times = set()
        for result in report.objects(None, SH.result):
            times.add(report.value(result, SH.value).toPython().year)
        self.assertEqual(times, {1999, 2026})

    def test_a_duplicate_time_is_a_p3_violation(self):
        # The time field is the source's tss:key
        report = self.validate(point() + point(ec20="1"))
        self.assertEqual(checks(report), [(SH.Violation, "P3")])
        result = result_of(report, "P3")
        self.assertEqual(report.value(result, SH.focusNode), SOURCE)
        self.assertEqual(report.value(result, SH.value).toPython().hour, 23)

    def test_a_key_of_two_fields_needs_both_values_to_repeat(self):
        def two_keys(graph):
            graph.add((SOURCE, TSS.key, COLOR))
        # The same time, another colour: a different key
        report = self.validate(point() + point(color="red"), two_keys)
        self.assertEqual(checks(report), [])
        # The same time and colour: the same key (both key values are reported)
        report = self.validate(point() + point(ec20="1"), two_keys)
        self.assertEqual(checks(report), [(SH.Violation, "P3"), (SH.Violation, "P3")])

    def test_two_values_for_one_field_are_a_p7_violation(self):
        report = self.validate(point(ec20=Turtle('"1"^^xsd:double, "2"^^xsd:double')))
        self.assertEqual(checks(report), [(SH.Violation, "P7")])
        result = result_of(report, "P7")
        self.assertEqual(report.value(result, SH.focusNode), SOURCE)
        self.assertIn(str(VALUE), str(report.value(result, SH.resultMessage)))
        # Two times in one point: also P7 (P3 compares points, and this is only one)
        report = self.validate(point(time=Turtle('"2021-03-03T23:15:00Z"^^xsd:dateTime, "2021-03-03T23:30:00Z"^^xsd:dateTime')))
        self.assertEqual(checks(report), [(SH.Violation, "P7")])

    def test_not_a_date_as_a_time_is_a_p4_violation(self):
        report = self.validate(point(time="not a date"))
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "P4")])
        result = result_of(report, "P4")
        self.assertEqual(report.value(result, SH.focusNode), TIME)
        self.assertEqual(str(report.value(result, SH.value)), "not a date")

    def test_a_day_that_does_not_exist_is_a_p4_violation(self):
        report = self.validate(point(time="2021-02-30T23:15:00Z"))
        self.assertEqual(checks(report), [(SH.Violation, "P4")])

    def test_a_date_with_a_timezone_is_a_valid_xsd_date(self):
        # rdflib's xsd:dateTime("2021-03-04Z") gives no value, but "2021-03-04Z" is a valid xsd:date
        def date_color(graph):
            graph.set((COLOR, TSS.datatype, XSD.date))
        date = {"color": "xsd:date"}
        report = self.validate(point(color="2021-03-04Z", types=date)
                               + point(time="2021-03-03T23:30:00Z", color="2021-03-04+02:00", types=date)
                               + point(time="2021-03-03T23:45:00Z", color="2021-03-04", types=date), date_color)
        self.assertEqual(checks(report), [])
        # A day that does not exist, with or without a timezone, and a date-time, are no xsd:date
        for text in ["2021-02-30Z", "2021-02-30", "2021-03-04T00:00:00Z"]:
            with self.subTest(text=text):
                report = self.validate(point(color=text, types=date), date_color)
                self.assertEqual(checks(report), [(SH.Violation, "P4")])
                self.assertIn(f"P4 datatype: {text} (the point", str(report.value(result_of(report, "P4"), SH.resultMessage)))

    def test_a_time_typed_xsd_date_time_stamp_needs_a_timezone(self):
        # xsd:dateTimeStamp is an xsd:dateTime with a timezone that can't be left out
        def stamp_time(graph):
            graph.set((TIME, TSS.datatype, XSD.dateTimeStamp))
        stamp = {"time": "xsd:dateTimeStamp"}
        report = self.validate(point(types=stamp) + point(time="2021-03-04T00:30:00+01:00", types=stamp), stamp_time)
        self.assertEqual(checks(report), [])
        # Without a timezone it is no xsd:dateTimeStamp: P4 (and only P4: P2 reads the time as UTC,
        # so it lies in the window)
        report = self.validate(point(types=stamp) + point(time="2021-03-03T23:30:00", types=stamp), stamp_time)
        self.assertEqual(checks(report), [(SH.Violation, "P4")])
        self.assertIn("P4 datatype: 2021-03-03T23:30:00 (the point", str(report.value(result_of(report, "P4"), SH.resultMessage)))

    def test_p2_reads_a_time_without_a_timezone_as_utc(self):
        # The window is [2021-03-03T23:15:00Z, 2026-01-01T00:00:00Z). A time without a timezone is
        # read as UTC, as every reader of the points does: inside the window it is fine ...
        self.assertEqual(checks(self.validate(point() + point(time="2021-03-03T23:30:00"))), [])
        # ... before tss:from, or at tss:until, it is outside it
        self.assertEqual(checks(self.validate(point() + point(time="2021-03-03T23:00:00"))), [(SH.Violation, "P2")])
        self.assertEqual(checks(self.validate(point() + point(time="2026-01-01T00:00:00"))), [(SH.Violation, "P2")])

    def test_a_wrong_lexical_form_for_xsd_double_is_a_p4_violation(self):
        report = self.validate(point(ec20="4,5") + point(**LATER))
        self.assertEqual(checks(report), [(SH.Violation, "P4")])
        result = result_of(report, "P4")
        self.assertEqual(report.value(result, SH.focusNode), VALUE)
        value = report.value(result, SH.value)
        self.assertEqual((str(value), value.datatype), ("4,5", XSD.double))

    def test_texts_rdflib_would_rewrite_are_p4_violations(self):
        # The metadata keeps every literal's text (documents.parse_document turns rdflib's normalising
        # off), so SHACL sees " 12 " and "1_000", which rdflib would otherwise make "12.0" and "1000.0"
        for text in [" 12 ", "1_000"]:
            with self.subTest(text=text):
                report = self.validate(point(ec20=text) + point(**LATER))
                self.assertEqual(checks(report), [(SH.Violation, "P4")])
                result = result_of(report, "P4")
                self.assertEqual(report.value(result, SH.focusNode), VALUE)
                self.assertIn(f"P4 datatype: {text} (the point at 2021-03-03T23:15:00Z)", str(report.value(result, SH.resultMessage)))
                # ... and the report's Turtle writes the value with that text (rdflib reads it back as a number)
                self.assertIn(f'sh:value "{text}"^^xsd:double', self.report_text)

    def test_yes_as_an_xsd_boolean_is_a_p4_violation(self):
        # rdflib would make "yes"^^xsd:boolean "false" (a valid boolean)
        def boolean_color(graph):
            graph.set((COLOR, TSS.datatype, XSD.boolean))
        boolean = {"color": "xsd:boolean"}
        report = self.validate(point(color="yes", types=boolean) + point(color="true", types=boolean, **LATER), boolean_color)
        self.assertEqual(checks(report), [(SH.Violation, "P4")])
        result = result_of(report, "P4")
        self.assertEqual(report.value(result, SH.focusNode), COLOR)
        self.assertIn("P4 datatype: yes (the point", str(report.value(result, SH.resultMessage)))
        # Written in full: rdflib's short form (a bare yes) would be broken Turtle
        self.assertIn('sh:value "yes"^^xsd:boolean', self.report_text)

    def test_another_datatype_or_a_language_tag_is_a_p4_violation(self):
        for changed in ({"ec20": Turtle('"4797.72"^^xsd:string')}, {"color": Turtle('"green"@en')}):
            with self.subTest(changed=changed):
                report = self.validate(point(**changed))
                self.assertEqual(checks(report), [(SH.Violation, "P4")])

    def test_a_node_as_a_value_is_a_p4_violation(self):
        # An IRI, or a blank node with values of its own, is no literal
        for value in (Turtle("ex:something"), Turtle('[ ex:a "1" ]')):
            with self.subTest(value=value):
                report = self.validate(point(ec20=value))
                self.assertEqual(checks(report), [(SH.Violation, "P4")])
                self.assertEqual(report.value(result_of(report, "P4"), SH.focusNode), VALUE)

    def test_a_value_above_the_maximum_is_a_p5_violation(self):
        # The ec20 field has tss:minimum 0 and tss:maximum 10000
        report = self.validate(point(ec20="99999") + point(time="2021-03-03T23:30:00Z", ec20="-1"))
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "P5"), (SH.Violation, "P5")])
        values = set()
        messages = set()
        for result in report.objects(None, SH.result):
            self.assertEqual(report.value(result, SH.focusNode), VALUE)
            values.add(report.value(result, SH.value).toPython())
            messages.add(str(report.value(result, SH.resultMessage)))
        self.assertEqual(values, {99999.0, -1.0})
        # The times keep their text (...Z), as they are written in the points, and so does the
        # field's tss:maximum ("10000"^^xsd:double: rdflib's normalising would make it 10000.0)
        self.assertIn("P5 range: 99999 (the point at 2021-03-03T23:15:00Z) is above the field's tss:maximum 10000.", messages)

    def test_a_property_that_is_no_field_is_only_a_p6_warning(self):
        report = self.validate(point(more='ex:extra "y"'))
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [(SH.Warning, "P6")])
        self.assertEqual(report.value(result_of(report, "P6"), SH.value), URIRef(EX + "extra"))

    def test_a_point_with_only_other_properties_is_a_p1_violation_and_a_p6_warning(self):
        # The data routes and the converter skip such a point, like the browser does
        # (test_storage_profiles, test_tss2rdf); SHACL says what is wrong with it
        report = self.validate(point() + '[] tss:pointOf ex:zevenaarshaven-source ; ex:extra "x" .\n')
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "P1"), (SH.Warning, "P6")])

    def test_a_field_missing_from_the_points_is_only_a_warning(self):
        report = self.validate(point(ec20=None) + point(time="2021-03-03T23:30:00Z", ec20=None))
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [(SH.Warning, "F9")])
        self.assertEqual(report.value(result_of(report, "F9"), SH.focusNode), VALUE)


class ShaclAndParsesTakeTurns(unittest.TestCase):
    """rdflib.NORMALIZE_LITERALS is one switch for the whole program. Reading the metadata turns
    it off for a moment (parse_exact, to keep the exact texts of the points), and so does
    pyshacl's validate() while it reads the shapes. Both hold tss2rdf.NORMALIZE_LOCK, so they
    take turns."""

    def looking_validate(self, validate, held):
        """A validate() that notes whether the lock is held when it is called, then validates"""
        def validate_and_look(*arguments, **options):
            held.append(rdf_generation.NORMALIZE_LOCK.locked())
            return validate(*arguments, **options)
        return validate_and_look

    def test_the_backend_validates_while_holding_the_lock(self):
        held = []
        with patch.object(shacl_report, "validate", side_effect=self.looking_validate(shacl_report.validate, held)):
            response = TestClient(main.app).get(f"/api/endpoints/{EMBEDDED}/shacl")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(held, [True])
        self.assertFalse(rdf_generation.NORMALIZE_LOCK.locked())  # and lets it go afterwards

    def test_the_command_line_validates_while_holding_the_lock(self):
        held = []
        shapes_path = paths.SHACL_DIR / "embedded_shapes.ttl"
        with patch.object(shacl_report, "validate", side_effect=self.looking_validate(shacl_report.validate, held)):
            report, node, point_triples = shacl_report.validation_report(
                documents.load_graph(EMBEDDED), shacl_report.read_rdf(shapes_path))
        self.assertEqual(held, [True])
        self.assertEqual(point_triples, 15)
        self.assertTrue(report.value(node, SH.conforms).toPython())

    def test_parses_keep_their_texts_while_shacl_runs_in_other_threads(self):
        # 400 points with times in "...Z" and the value " 12 ": rdflib's normalising would make
        # them "...+00:00" and "12.0". Without the lock around pyshacl, a parse could lose some
        # of these texts while other threads validate.
        points = []
        for number in range(400):
            points.append(point(time=f"2021-03-04T{number // 60:02d}:{number % 60:02d}:00Z", ec20=" 12 ", color=None))
        text = "".join(points)
        shapes = Graph().parse(data=CUSTOM_SHAPES, format="turtle")

        stop = threading.Event()

        def validate_again_and_again():
            while not stop.is_set():
                shacl_routes.run_validation(Graph(), shapes, "the test shapes")

        threads = [threading.Thread(target=validate_again_and_again) for number in range(2)]
        for thread in threads:
            thread.start()
        changed = 0  # parses that lost some text
        try:
            for number in range(30):
                texts = set()
                for value in rdf_generation.point_graph(embedded_graph(text), SOURCE).objects():
                    texts.add(str(value))
                if " 12 " not in texts or any(value.endswith("+00:00") for value in texts):
                    changed += 1
        finally:
            stop.set()
            for thread in threads:
                thread.join()
        self.assertEqual(changed, 0)


if __name__ == "__main__":
    unittest.main()
