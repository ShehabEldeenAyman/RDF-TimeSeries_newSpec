"""Semantic checks for embedded RDF generation, including nested point results."""
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from rdflib import BNode, Graph, Literal, RDF, URIRef
from rdflib.namespace import DCTERMS, XSD

import main
import rdf_generation
from tss2 import TSS, SOSA, QUDT

EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
VALUE = URIRef("https://example.org/ex#snippet-zevenaarshaven-ec20")
TEXT = URIRef("https://example.org/ex#snippet-zevenaarshaven-quality-code-color")


class RdfGeneration(unittest.TestCase):
    def setUp(self):
        self.source = main.load_graph(EMBEDDED)
        self.base = main.endpoint_base(EMBEDDED)

    def generate(self):
        result = rdf_generation.generate(self.source, self.base)
        return result, Graph().parse(data=result["turtle"], format="turtle")

    def set_json(self, node, predicate, value):
        self.source.set((node, predicate, Literal(json.dumps(value), datatype=RDF.JSON)))

    def test_real_file_produces_domain_observations_without_tss(self):
        result, graph = self.generate()
        self.assertEqual(result["points"], 10)
        self.assertEqual(len(result["series"]), 2)
        self.assertEqual(result["triples"], len(graph))
        conductivity = list(graph.subjects(SOSA.observedProperty, URIRef("http://qudt.org/vocab/quantitykind/ElectricConductivity")))
        quality = list(graph.subjects(SOSA.observedProperty, URIRef("https://example.org/ex#qualityCodeColor")))
        self.assertEqual(len(conductivity), 5)
        self.assertEqual(len(quality), 5)
        for node in conductivity:
            self.assertIn((node, RDF.type, SOSA.Observation), graph)
            self.assertEqual(graph.value(node, SOSA.resultTime).datatype, XSD.dateTime)
            self.assertEqual(graph.value(node, SOSA.hasSimpleResult).datatype, XSD.double)
            self.assertIsNotNone(graph.value(node, SOSA.madeBySensor))
            self.assertIsNotNone(graph.value(node, QUDT.hasUnit))
        for node in quality:
            self.assertEqual(graph.value(node, SOSA.hasSimpleResult).datatype, XSD.string)
            self.assertIsNone(graph.value(node, SOSA.hasFeatureOfInterest))
        self.assertEqual(len(graph), 55)
        self.assertTrue(all(isinstance(node, BNode) for node in conductivity + quality))
        self.assertEqual(set(graph.subjects()), set(conductivity + quality))
        self.assertFalse(list(graph.subjects(RDF.type, SOSA.ObservationCollection)))
        self.assertFalse(list(graph.triples((None, SOSA.hasMember, None))))
        self.assertFalse(list(graph.subjects(RDF.type, RDF.Bag)))
        self.assertFalse(list(graph.triples((None, main.RDFS.member, None))))
        self.assertNotIn("https://w3id.org/tss", result["turtle"])
        self.assertNotIn("@prefix tss:", result["turtle"])
        self.assertNotIn("snippet-", result["turtle"])
        for control in (VALUE, TEXT, next(self.source.subjects(RDF.type, TSS.SnippetGroup))):
            self.assertFalse(any(control in triple for triple in graph))
        self.assertFalse(any(str(term).startswith(str(TSS)) for triple in graph for term in triple))
        self.assertFalse(list(graph.triples((None, DCTERMS.isPartOf, None))))
        self.assertFalse(any(str(node).startswith("urn:rdftss-generation:") for triple in graph for node in triple))

    def test_key_order_does_not_change_mapping_and_explicit_ids_are_preserved(self):
        self.set_json(VALUE, TSS.points, [{"value": "3.5", "@id": "https://example.org/reading/1", "time": "2025-01-01T00:00:00Z"}])
        _, graph = self.generate()
        node = URIRef("https://example.org/reading/1")
        self.assertEqual(float(graph.value(node, SOSA.hasSimpleResult)), 3.5)
        self.assertIn((node, RDF.type, SOSA.Observation), graph)

    def test_nested_results_do_not_receive_point_metadata(self):
        context = {"time": {"@id": str(SOSA.resultTime), "@type": str(XSD.dateTime)},
                   "value": str(SOSA.hasResult), "amount": {"@id": "https://example.org/amount", "@type": str(XSD.double)}}
        self.set_json(VALUE, TSS.context, context)
        self.set_json(VALUE, TSS.points, [{"time": "2025-01-01T00:00:00Z", "value": {"amount": "3.5"}}])
        _, graph = self.generate()
        root = next(graph.subjects(SOSA.hasResult, None))
        child = graph.value(root, SOSA.hasResult)
        self.assertEqual(float(graph.value(child, URIRef("https://example.org/amount"))), 3.5)
        self.assertNotIn((child, RDF.type, SOSA.Observation), graph)
        self.assertIsNone(graph.value(child, SOSA.madeBySensor))

    def test_inheritance_and_whole_about_override(self):
        group = next(self.source.subjects(RDF.type, TSS.SnippetGroup))
        template = self.source.value(group, TSS.snippetTemplate)
        context = self.source.value(VALUE, TSS.context)
        self.source.remove((VALUE, TSS.context, None))
        self.source.set((template, TSS.context, context))
        template_about = URIRef("https://example.org/shared")
        self.source.set((template, TSS.about, template_about))
        self.source.add((template_about, SOSA.madeBySensor, URIRef("https://example.org/default-sensor")))
        self.source.remove((VALUE, TSS.about, None))
        self.source.set((TEXT, TSS.pointType, URIRef("https://example.org/QualityObservation")))
        _, graph = self.generate()
        for root in graph.subjects(SOSA.madeBySensor, URIRef("https://example.org/default-sensor")):
            self.assertEqual(graph.value(root, SOSA.madeBySensor), URIRef("https://example.org/default-sensor"))
        for root in graph.subjects(RDF.type, URIRef("https://example.org/QualityObservation")):
            self.assertIsNone(graph.value(root, SOSA.madeBySensor))
            self.assertIn((root, RDF.type, URIRef("https://example.org/QualityObservation")), graph)

    def test_invalid_json_unmapped_keys_and_bad_datatypes_are_rejected(self):
        with self.subTest("malformed JSON"):
            self.source.set((VALUE, TSS.points, Literal("[broken", datatype=RDF.JSON)))
            with self.assertRaisesRegex(ValueError, "Invalid JSON"):
                self.generate()
        self.source = main.load_graph(EMBEDDED)
        with self.subTest("missing mapping"):
            self.set_json(VALUE, TSS.context, {"time": str(SOSA.resultTime)})
            with self.assertRaisesRegex(ValueError, "does not map"):
                self.generate()
        self.source = main.load_graph(EMBEDDED)
        with self.subTest("wrong datatype"):
            self.set_json(VALUE, TSS.points, [{"time": "2025-01-01T00:00:00Z", "value": "not-a-number"}])
            with self.assertRaisesRegex(ValueError, "invalid .*double"):
                self.generate()

    def test_embedded_api_returns_download_filename(self):
        result = main.generate_rdf(EMBEDDED)
        self.assertEqual(result["filename"], "observations.ttl")
        self.assertEqual(len(Graph().parse(data=result["turtle"], format="turtle")), result["triples"])
        with patch.object(main, "load_graph", return_value=self.source):
            self.source.remove((VALUE, TSS.context, None))
            with self.assertRaises(HTTPException) as caught:
                main.generate_rdf(EMBEDDED)
            self.assertEqual(caught.exception.status_code, 400)

    def test_nested_about_metadata_and_sensor_descriptions_survive(self):
        from rdflib import BNode
        about = self.source.value(VALUE, TSS.about)
        details = BNode()
        self.source.add((about, URIRef("https://example.org/calibration"), details))
        self.source.add((details, URIRef("https://example.org/offset"), Literal(0.5)))
        self.source.add((details, TSS.mutable, Literal(False)))
        sensor = self.source.value(about, SOSA.madeBySensor)
        self.source.add((sensor, RDF.type, SOSA.Sensor))
        self.source.add((sensor, main.RDFS.label, Literal("Conductivity sensor")))
        _, graph = self.generate()
        root = next(graph.subjects(SOSA.madeBySensor, sensor))
        nested = graph.value(root, URIRef("https://example.org/calibration"))
        self.assertEqual(float(graph.value(nested, URIRef("https://example.org/offset"))), 0.5)
        self.assertIn((sensor, RDF.type, SOSA.Sensor), graph)
        self.assertEqual(str(graph.value(sensor, main.RDFS.label)), "Conductivity sensor")
        self.assertFalse(list(graph.triples((None, TSS.mutable, None))))

    def test_tss_context_or_point_type_is_rejected_instead_of_losing_readings(self):
        self.set_json(VALUE, TSS.context, {"time": str(TSS.time), "value": str(TSS.value)})
        with self.assertRaisesRegex(ValueError, "uses the TSS vocabulary"):
            self.generate()
        self.source = main.load_graph(EMBEDDED)
        self.source.set((VALUE, TSS.pointType, TSS.Snippet))
        with self.assertRaisesRegex(ValueError, "not a TSS class"):
            self.generate()


if __name__ == "__main__":
    unittest.main()
