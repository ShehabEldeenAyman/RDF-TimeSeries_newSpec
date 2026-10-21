"""The SHACL tab: python -B -m unittest -v test_external_shacl."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from rdflib import Literal, URIRef

import main

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


class ExternalShacl(unittest.TestCase):
    def check(self, endpoint, change):
        """The default check on a changed copy of an endpoint's metadata (the file stays as it is)"""
        graph = main.load_graph(endpoint)
        change(graph)
        with patch.object(main, "load_graph", return_value=graph):
            return main.check_shacl(endpoint)

    def test_every_external_endpoint_conforms(self):
        for endpoint in EXTERNAL:
            with self.subTest(endpoint=endpoint):
                report = main.check_shacl(endpoint)
                self.assertEqual(report["shapes"], "external_shapes.ttl")
                self.assertFalse(report["custom"])
                self.assertIs(report["embedded"], False)
                self.assertTrue(report["conforms"], report["report"])
                self.assertEqual((report["violations"], report["warnings"]), (0, 0))

    def test_a_missing_required_property_is_a_violation(self):
        source = URIRef(EX + "grootdok-source")
        report = self.check(CSV, lambda graph: graph.remove((source, main.TSS["from"], None)))
        self.assertFalse(report["conforms"])
        self.assertEqual(report["violations"], 1)
        self.assertIn("tss:from", report["report"])

    def test_a_csv_source_needs_its_delimiter(self):
        source = URIRef(EX + "grootdok-source")
        report = self.check(CSV, lambda graph: graph.remove((source, main.TSS.delimiter, None)))
        self.assertFalse(report["conforms"])
        self.assertIn("S10 delimiter", report["report"])

    def test_a_field_without_a_column_name_is_a_violation(self):
        field = URIRef(EX + "grootdok-ec20-field")
        report = self.check(CSV, lambda graph: graph.remove((field, main.TSS.columnName, None)))
        self.assertFalse(report["conforms"])
        self.assertIn("tss:columnName", report["report"])

    def test_a_delimiter_on_parquet_is_only_a_warning(self):
        source = URIRef(EX + "rodenhuizedok-source")
        report = self.check(PARQUET, lambda graph: graph.add((source, main.TSS.delimiter, Literal(","))))
        self.assertTrue(report["conforms"], report["report"])
        self.assertEqual((report["violations"], report["warnings"]), (0, 1))

    def test_embedded_sources_have_no_default_shapes_yet(self):
        with self.assertRaises(HTTPException) as caught:
            main.check_shacl(EMBEDDED)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("no default shapes for embedded", caught.exception.detail)

    def test_without_tss_embedded_no_shapes_are_picked(self):
        graph = main.load_graph(CSV)
        graph.remove((None, main.TSS.embedded, None))
        with patch.object(main, "load_graph", return_value=graph):
            with self.assertRaises(HTTPException) as caught:
                main.check_shacl(CSV)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("tss:embedded", caught.exception.detail)

    def test_uploaded_shapes_work_for_both_profiles(self):
        upload = main.CustomShacl(filename="my-shapes.ttl", turtle=CUSTOM_SHAPES)
        embedded = main.check_custom_shacl(EMBEDDED, upload)
        self.assertTrue(embedded["conforms"])
        self.assertTrue(embedded["custom"])
        self.assertIs(embedded["embedded"], True)
        self.assertEqual(embedded["shapes"], "my-shapes.ttl")
        external = main.check_custom_shacl(CSV, upload)
        self.assertFalse(external["conforms"])
        self.assertEqual(external["violations"], 1)
        self.assertIn("HasValueConstraintComponent", external["report"])

    def test_uploaded_shapes_replace_the_defaults_instead_of_adding_to_them(self):
        graph = main.load_graph(CSV)
        graph.remove((None, main.TSS.mutable, None))
        with patch.object(main, "load_graph", return_value=graph):
            self.assertFalse(main.check_shacl(CSV)["conforms"])
            upload = main.CustomShacl(filename="custom.ttl", turtle=CUSTOM_SHAPES.replace("hasValue true", "hasValue false"))
            self.assertTrue(main.check_custom_shacl(CSV, upload)["conforms"])

    def test_the_defaults_stay_after_a_custom_check(self):
        main.check_custom_shacl(CSV, main.CustomShacl(filename="custom.ttl", turtle=CUSTOM_SHAPES))
        result = main.check_shacl(CSV)
        self.assertTrue(result["conforms"])
        self.assertFalse(result["custom"])
        self.assertEqual(result["shapes"], "external_shapes.ttl")

    def test_a_bad_upload_has_a_clear_error(self):
        for turtle, message in (("@prefix broken", "as Turtle"),
                                ("<https://example.org/a> <https://example.org/b> <https://example.org/c> .", "no SHACL shapes"),
                                ("@prefix sh: <http://www.w3.org/ns/shacl#> . <urn:shape> a sh:NodeShape ; sh:targetNode <urn:a> ; sh:property [ sh:path <urn:b> ; sh:minCount \"invalid\" ] .", "Could not validate")):
            with self.subTest(message=message):
                with self.assertRaises(HTTPException) as caught:
                    main.check_custom_shacl(CSV, main.CustomShacl(filename="bad.ttl", turtle=turtle))
                self.assertEqual(caught.exception.status_code, 400)
                self.assertIn(message, caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
