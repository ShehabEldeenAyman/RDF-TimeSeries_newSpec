"""The SHACL tab (the default shapes, and uploaded shapes): python -B -m unittest -v test_external_shacl.

The answer of the SHACL routes is the validation report as RDF (an sh:ValidationReport, in Turtle).
The tests read it back with rdflib and look at its results with the SHACL terms
(rdflib.namespace.SH): the severity, the focus node, the path, the constraint component,
and the id of the check (S2, F2, ...). The embedded points have tests of their own
(test_embedded_shacl.py), which use the helper functions below."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from rdflib import Graph, Literal, URIRef, RDF
from rdflib.namespace import DCTERMS, SH

import main
import documents
import shacl_routes
from rdf_generation import TSS

BASE = "http://testserver/"  # the address TestClient gives the backend
EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL = ["waterinfo_289423042_tss2", "waterinfo_289435042_tss2", "waterinfo_289429042_tss2_parquet", "factory_conductivity_tss2"]
CSV = "waterinfo_289423042_tss2"
PARQUET = "waterinfo_289429042_tss2_parquet"
EX = "https://example.org/ex#"

# Custom shapes: every source must be embedded
CUSTOM_SHAPES = '''
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix tss: <https://w3id.org/tss#> .
@prefix ex: <https://example.org/> .
ex:OnlyEmbedded a sh:NodeShape ;
    sh:targetClass tss:Source ;
    sh:property [ sh:path tss:embedded ; sh:hasValue true ] .
'''


# --- Reading a report (also used by test_embedded_shacl.py) ------------------

def read_report(response):
    """The answer of a SHACL route as an RDF graph, and its sh:ValidationReport node"""
    report = Graph().parse(data=response.text, format="turtle")
    return report, report.value(predicate=RDF.type, object=SH.ValidationReport)


def conforms(report):
    """The report's sh:conforms, as True or False"""
    node = report.value(predicate=RDF.type, object=SH.ValidationReport)
    return report.value(node, SH.conforms).toPython()


def results(report):
    """The results (sh:ValidationResult nodes) of a report"""
    node = report.value(predicate=RDF.type, object=SH.ValidationReport)
    return list(report.objects(node, SH.result))


def count(report, severity):
    """How many results of one severity (SH.Violation, SH.Warning or SH.Info) the report holds"""
    number = 0
    for result in results(report):
        if report.value(result, SH.resultSeverity) == severity:
            number += 1
    return number


def check_id(report, result):
    """The id of the check that gave a result (S2, F2, P5, ...): the first word of the sh:name
    of its shape, or, for a SPARQL check (its shape has no name), of its message"""
    shape = report.value(result, SH.sourceShape)
    name = None
    if shape is not None:
        name = report.value(shape, SH.name)
    if name is None:
        name = report.value(result, SH.resultMessage)
    return str(name).split()[0].rstrip(":")


def checks(report):
    """Every result of a report as (severity, check id), sorted, e.g. [(SH.Violation, "P5")]"""
    found = []
    for result in results(report):
        found.append((report.value(result, SH.resultSeverity), check_id(report, result)))
    found.sort()
    return found


def result_of(report, check):
    """The one result of a check (by its id)"""
    found = []
    for result in results(report):
        if check_id(report, result) == check:
            found.append(result)
    assert len(found) == 1, f"{len(found)} results of {check}"
    return found[0]


class ExternalShacl(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def validate(self, endpoint, change=None):
        """GET the SHACL report of an endpoint, as a graph. change: a function that changes a copy
        of the endpoint's metadata first (the file stays as it is)"""
        graph = documents.load_graph(endpoint)
        if change is not None:
            change(graph)
        with patch.object(documents, "load_graph", return_value=graph):
            response = self.client.get(f"/api/endpoints/{endpoint}/shacl")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/turtle; charset=utf-8")
        report, node = read_report(response)
        return report

    def upload(self, endpoint, shapes, content_type="text/turtle"):
        """POST shapes as the request body"""
        return self.client.post(f"/api/endpoints/{endpoint}/shacl", content=shapes,
                                headers={"Content-Type": content_type})

    def test_every_external_endpoint_conforms(self):
        for endpoint in EXTERNAL:
            with self.subTest(endpoint=endpoint):
                report = self.validate(endpoint)
                self.assertTrue(conforms(report))
                self.assertEqual(checks(report), [])
                # The report says which shapes were used: their address on this backend
                node = report.value(predicate=RDF.type, object=SH.ValidationReport)
                self.assertEqual(report.value(node, DCTERMS.source), URIRef(BASE + "files/SHACL/external_shapes.ttl"))

    def test_a_missing_required_property_is_a_violation(self):
        # tss:key is required (at least one): the field(s) whose values identify a row
        source = URIRef(EX + "grootdok-source")
        report = self.validate(CSV, lambda graph: graph.remove((source, TSS.key, None)))
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "S5")])
        result = result_of(report, "S5")
        self.assertEqual(report.value(result, SH.focusNode), source)
        self.assertEqual(report.value(result, SH.resultPath), TSS.key)
        self.assertEqual(report.value(result, SH.sourceConstraintComponent), SH.MinCountConstraintComponent)

    def test_tss_from_is_optional(self):
        # Without tss:from the window is open at its start
        source = URIRef(EX + "grootdok-source")
        report = self.validate(CSV, lambda graph: graph.remove((source, TSS["from"], None)))
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [])

    def test_a_csv_source_may_leave_out_its_delimiter_and_encoding(self):
        # They default to "," and "utf-8"
        source = URIRef(EX + "grootdok-source")

        def without_both(graph):
            graph.remove((source, TSS.delimiter, None))
            graph.remove((source, TSS.encoding, None))
        report = self.validate(CSV, without_both)
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [])
        # When stated, the delimiter must still be one character
        report = self.validate(CSV, lambda graph: graph.set((source, TSS.delimiter, Literal(";;"))))
        self.assertEqual(checks(report), [(SH.Violation, "S10")])

    def test_a_field_without_a_column_name_is_a_violation(self):
        field = URIRef(EX + "grootdok-ec20-field")
        report = self.validate(CSV, lambda graph: graph.remove((field, TSS.columnName, None)))
        self.assertFalse(conforms(report))
        self.assertEqual(checks(report), [(SH.Violation, "F2")])
        result = result_of(report, "F2")
        self.assertEqual(report.value(result, SH.focusNode), field)
        self.assertEqual(report.value(result, SH.resultPath), TSS.columnName)

    def test_a_delimiter_on_parquet_is_only_a_warning(self):
        source = URIRef(EX + "rodenhuizedok-source")
        report = self.validate(PARQUET, lambda graph: graph.add((source, TSS.delimiter, Literal(","))))
        self.assertTrue(conforms(report))
        self.assertEqual(checks(report), [(SH.Warning, "S15")])
        self.assertEqual((count(report, SH.Violation), count(report, SH.Warning)), (0, 1))

    def test_without_tss_embedded_no_shapes_are_picked(self):
        graph = documents.load_graph(CSV)
        graph.remove((None, TSS.embedded, None))
        with patch.object(documents, "load_graph", return_value=graph):
            response = self.client.get(f"/api/endpoints/{CSV}/shacl")
        self.assertEqual(response.status_code, 400)
        self.assertIn("tss:embedded", response.json()["detail"])

    def test_uploaded_shapes_work_for_both_profiles(self):
        response = self.upload(EMBEDDED, CUSTOM_SHAPES)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/turtle; charset=utf-8")
        embedded, node = read_report(response)
        self.assertTrue(conforms(embedded))
        # Uploaded shapes have no address, so the report names no shapes file
        self.assertIsNone(embedded.value(node, DCTERMS.source))

        external, node = read_report(self.upload(CSV, CUSTOM_SHAPES))
        self.assertFalse(conforms(external))
        self.assertEqual(count(external, SH.Violation), 1)
        self.assertEqual(external.value(results(external)[0], SH.sourceConstraintComponent), SH.HasValueConstraintComponent)

    def test_uploaded_shapes_can_be_rdf_xml(self):
        # The same shapes, written as RDF/XML by rdflib and sent as application/rdf+xml.
        # (JSON-LD shapes are refused: see test_untrusted_documents.)
        shapes = Graph().parse(data=CUSTOM_SHAPES, format="turtle").serialize(format="xml")
        response = self.upload(CSV, shapes, content_type="application/rdf+xml")
        self.assertEqual(response.status_code, 200, response.text)
        report, node = read_report(response)
        self.assertEqual(count(report, SH.Violation), 1)

    def test_uploaded_shapes_replace_the_defaults_instead_of_adding_to_them(self):
        graph = documents.load_graph(CSV)
        graph.remove((None, TSS.mutable, None))
        with patch.object(documents, "load_graph", return_value=graph):
            default, node = read_report(self.client.get(f"/api/endpoints/{CSV}/shacl"))
            self.assertFalse(conforms(default))
            custom, node = read_report(self.upload(CSV, CUSTOM_SHAPES.replace("hasValue true", "hasValue false")))
            self.assertTrue(conforms(custom))

    def test_the_defaults_stay_after_a_custom_check(self):
        self.assertEqual(self.upload(CSV, CUSTOM_SHAPES).status_code, 200)
        report = self.validate(CSV)
        self.assertTrue(conforms(report))
        node = report.value(predicate=RDF.type, object=SH.ValidationReport)
        self.assertEqual(report.value(node, DCTERMS.source), URIRef(BASE + "files/SHACL/external_shapes.ttl"))

    def test_a_bad_upload_has_a_clear_error(self):
        too_big = "# " + "x" * shacl_routes.MAX_SHAPES_BYTES
        invalid = ("@prefix sh: <http://www.w3.org/ns/shacl#> . <urn:shape> a sh:NodeShape ; sh:targetNode <urn:a> ; "
                   "sh:property [ sh:path <urn:b> ; sh:minCount \"invalid\" ] .")
        for shapes, content_type, status, message in (
                ("@prefix broken", "text/turtle", 400, "Could not read the shapes"),
                ("<https://example.org/a> <https://example.org/b> <https://example.org/c> .", "text/turtle", 400, "no SHACL shapes"),
                ("", "text/turtle", 400, "no SHACL shapes"),
                (invalid, "text/turtle", 400, "Could not validate"),
                ("<html></html>", "text/html", 415, "not an RDF syntax"),
                (too_big, "text/turtle", 413, "at most 5 MB")):
            with self.subTest(message=message, content_type=content_type):
                response = self.upload(CSV, shapes, content_type)
                self.assertEqual(response.status_code, status)
                self.assertIn(message, response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
