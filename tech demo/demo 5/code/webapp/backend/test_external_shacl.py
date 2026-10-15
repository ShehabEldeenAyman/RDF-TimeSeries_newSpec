"""External SHACL profiles and the renamed Parquet endpoint."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import DCTERMS

import main

PARQUET = "waterinfo_289429042_tss2_parquet"
CSV = "waterinfo_289423042_tss2"


class ExternalShacl(unittest.TestCase):
    def setUp(self):
        self.graph = main.load_graph(PARQUET)
        self.group = next(self.graph.subjects(RDF.type, main.TSS.SnippetGroup))
        self.snippet = self.graph.value(self.group, main.TSS.hasSnippet)
        self.template = self.graph.value(self.group, main.TSS.snippetTemplate)

    def check(self):
        with patch.object(main, "load_graph", return_value=self.graph):
            return main.check_shacl(PARQUET)

    def test_renamed_endpoint_is_discovered_and_uses_non_csv_shapes(self):
        self.assertIn(PARQUET, main.local_endpoint_ids())
        info = main.get_endpoint(PARQUET)
        self.assertEqual(info["file"], f"metadata endpoint/{PARQUET}.ttl")
        self.assertEqual(info["csvUrl"], "data/waterinfo_289429042.parquet")
        report = self.check()
        self.assertEqual(report["shapes"], "external_non_csv_shapes.ttl")
        self.assertTrue(report["conforms"], report["report"])
        self.assertEqual(report["warnings"], 0)

    def test_non_csv_requires_context_even_if_csvw_is_provided(self):
        self.graph.remove((None, main.TSS.context, None))
        self.graph.add((self.group, main.TSS.describedby, URIRef("https://example.org/schema.json")))
        report = self.check()
        self.assertFalse(report["conforms"])
        self.assertIn("no tss:context", report["report"])

    def test_non_csv_accepts_context_inherited_from_template(self):
        context = self.graph.value(self.snippet, main.TSS.context)
        self.graph.remove((self.snippet, main.TSS.context, None))
        self.graph.add((self.template, main.TSS.context, context))
        report = self.check()
        self.assertTrue(report["conforms"], report["report"])
        self.assertEqual(report["warnings"], 0)

    def test_snippet_context_can_override_template_context(self):
        self.graph.add((self.template, main.TSS.context, Literal('{"@context": {}}', datatype=RDF.JSON)))
        report = self.check()
        self.assertTrue(report["conforms"], report["report"])

    def test_non_csv_context_must_be_an_rdf_json_object(self):
        original = self.graph.value(self.snippet, main.TSS.context)
        for context in (Literal(str(original)), Literal("[]", datatype=RDF.JSON)):
            with self.subTest(context=context):
                self.graph.set((self.snippet, main.TSS.context, context))
                report = self.check()
                self.assertFalse(report["conforms"])
                self.assertIn("tss:context", report["report"])

    def test_non_csv_allows_field_names_with_spaces(self):
        self.graph.set((self.template, main.TSS.timeColumn, Literal("Observation Time")))
        self.graph.set((self.snippet, main.TSS.valueColumn, Literal("Measured Value")))
        report = self.check()
        self.assertTrue(report["conforms"], report["report"])

    def test_non_csv_profile_accepts_other_file_types(self):
        self.graph.set((self.group, DCTERMS.format, URIRef("http://publications.europa.eu/resource/authority/file-type/JSON")))
        report = self.check()
        self.assertEqual(report["shapes"], "external_non_csv_shapes.ttl")
        self.assertTrue(report["conforms"], report["report"])

    def test_missing_format_is_reported_as_nonconformance(self):
        self.graph.remove((self.group, DCTERMS.format, None))
        report = self.check()
        self.assertFalse(report["conforms"])
        self.assertIn("dcterms:format", report["report"])

    def test_csv_requires_csvw_and_does_not_require_context(self):
        graph = main.load_graph(CSV)
        self.assertFalse(list(graph.triples((None, main.TSS.context, None))))
        self.assertTrue(main.check_shacl(CSV)["conforms"])
        graph.remove((None, main.TSS.describedby, None))
        with patch.object(main, "load_graph", return_value=graph):
            report = main.check_shacl(CSV)
        self.assertFalse(report["conforms"])
        self.assertIn("CSVW metadata file", report["report"])

    def test_each_external_profile_rejects_the_other_format(self):
        for endpoint, shapes_file in ((PARQUET, "external_csv_shapes.ttl"), (CSV, "external_non_csv_shapes.ttl")):
            with self.subTest(endpoint=endpoint, shapes=shapes_file):
                shapes = Graph().parse(main.DEMO_DIR / "SHACL" / shapes_file, format="turtle")
                report = main.shacl_report(main.load_graph(endpoint), shapes, shapes_file, False)
                self.assertFalse(report["conforms"])
                self.assertIn("filetype:CSV", report["report"])

    def test_mixed_external_profiles_need_custom_shapes(self):
        other = URIRef("https://example.org/csv-group")
        self.graph.add((other, RDF.type, main.TSS.SnippetGroup))
        self.graph.add((other, main.TSS.embedded, Literal(False)))
        self.graph.add((other, DCTERMS.format, main.CSV_FILE_TYPE))
        with self.assertRaises(HTTPException) as caught:
            self.check()
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("mixed profiles", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
