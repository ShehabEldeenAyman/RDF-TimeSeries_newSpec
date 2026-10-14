"""CSVW-driven standalone RDF export: mappings, types, dialect and full downloads."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import XSD

import main
import csv_rdf_generation
from tss2 import TSS, SOSA

EX = Namespace("https://example.org/")


class CsvRdfGeneration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / "readings.csv"
        self.csvw = self.root / "metadata.json"
        self.target = self.root / "observations.ttl"
        self.csv.write_text("Timestamp;A;B;Category\n2025-01-01T00:00:00Z;1.5;2;good,review\n2025-01-01T00:15:00Z;;3;bad,review\n2025-01-01T00:30:00Z;4.5;4;good,review\n", encoding="utf-8")
        self.metadata = {
            "@context": "http://www.w3.org/ns/csvw", "url": "wrong-file.csv",
            "dialect": {"delimiter": ";", "header": True},
            "tableSchema": {"columns": [
                {"name": "timestamp", "titles": "Timestamp", "datatype": {"base": "dateTime"}, "propertyUrl": str(SOSA.resultTime)},
                {"name": "a", "titles": "A", "datatype": {"base": "double", "minimum": 0}, "propertyUrl": str(EX.a)},
                {"name": "b", "titles": "B", "datatype": "integer", "propertyUrl": str(EX.b)},
                {"name": "category", "titles": "Category", "datatype": "string", "propertyUrl": str(EX.category)},
                {"name": "row_type", "virtual": True, "propertyUrl": "rdf:type", "valueUrl": str(SOSA.Observation)},
            ]},
        }
        self.graph = Graph()
        self.graph.bind("ex", EX)
        self.graph.bind("sosa", SOSA)
        self.graph.bind("tss", TSS)
        triples = [
            (EX.group, RDF.type, TSS.SnippetGroup), (EX.group, TSS.embedded, Literal(False)),
            (EX.group, TSS.producedBy, EX.logger), (EX.group, TSS.snippetTemplate, EX.template),
            (EX.group, TSS.dataFile, URIRef(self.csv.as_uri())), (EX.group, TSS.describedby, URIRef(self.csvw.as_uri())),
            (EX.template, TSS.timeColumn, Literal("timestamp")), (EX.template, TSS.pointType, SOSA.Observation),
            (EX.template, TSS["from"], Literal("2025-01-01T00:00:00Z", datatype=XSD.dateTime)),
            (EX.template, TSS.until, Literal("2025-01-01T00:30:00Z", datatype=XSD.dateTime)),
        ]
        for snippet, name in ((EX.seriesA, "a"), (EX.seriesB, "b")):
            triples.extend([(EX.group, TSS.hasSnippet, snippet), (snippet, RDF.type, TSS.Snippet),
                            (snippet, TSS.valueColumn, Literal(name)), (snippet, TSS.about, EX[f"about-{name}"]),
                            (EX[f"about-{name}"], SOSA.madeBySensor, EX[f"sensor-{name}"])])
        for triple in triples:
            self.graph.add(triple)

    def generate(self, **options):
        result = csv_rdf_generation.generate(self.graph, lambda group: (self.metadata, self.csv, URIRef(self.csv.as_uri())), self.target, **options)
        return result, Graph().parse(self.target, format="turtle")

    def test_mapping_types_virtual_columns_series_separation_and_window(self):
        result, graph = self.generate()
        self.assertEqual(result["points"], 3)
        self.assertEqual([entry["points"] for entry in result["series"]], [1, 2])
        readings = set(graph.subjects(RDF.type, SOSA.Observation))
        self.assertEqual(len(readings), 3)
        self.assertTrue(all(isinstance(node, BNode) for node in readings))
        self.assertEqual(set(graph.subjects()), readings)
        for node in readings:
            self.assertEqual(graph.value(node, SOSA.resultTime).datatype, XSD.dateTime)
            a, b = graph.value(node, EX.a), graph.value(node, EX.b)
            self.assertNotEqual(a is None, b is None)
            if a is not None:
                self.assertEqual(a.datatype, XSD.double)
                self.assertEqual(graph.value(node, SOSA.madeBySensor), EX["sensor-a"])
            else:
                self.assertEqual(b.datatype, XSD.integer)
                self.assertEqual(graph.value(node, SOSA.madeBySensor), EX["sensor-b"])
            self.assertEqual(graph.value(node, EX.category).datatype, XSD.string)
            self.assertIsNone(graph.value(node, SOSA.hasSimpleResult))
        self.assertEqual(result["triples"], len(graph))
        text = self.target.read_text(encoding="utf-8")
        self.assertNotIn("https://w3id.org/tss", text)
        self.assertNotIn("ObservationCollection", text)
        self.assertNotIn("logger", text)
        self.assertFalse(list(graph.triples((None, SOSA.hasMember, None))))

    def test_bounded_preview_and_complete_file(self):
        result, graph = self.generate(preview_limit=1)
        self.assertTrue(result["previewTruncated"])
        self.assertEqual(result["previewPoints"], 1)
        preview = Graph().parse(data=result["turtle"], format="turtle")
        self.assertEqual(len(set(preview.subjects(RDF.type, SOSA.Observation))), 1)
        self.assertEqual(len(set(graph.subjects(RDF.type, SOSA.Observation))), 3)
        self.assertEqual(result["bytes"], self.target.stat().st_size)

    def test_headerless_csv_and_uri_value_mapping(self):
        self.csv.write_text("2025-01-01T00:00:00Z;1.5;2;good\n", encoding="utf-8")
        self.metadata["dialect"]["header"] = False
        self.metadata["tableSchema"]["columns"][3]["valueUrl"] = "https://example.org/category/{category}"
        result, graph = self.generate()
        self.assertEqual(result["points"], 2)
        self.assertEqual(set(graph.objects(None, EX.category)), {EX["category/good"]})

    def test_missing_mapping_or_delimiter_wrong_width_and_bad_datatype(self):
        original = copy.deepcopy(self.metadata)
        for broken in ("delimiter", "mapping", "wrong_delimiter", "integer", "minimum"):
            with self.subTest(broken=broken):
                self.metadata = copy.deepcopy(original)
                if broken == "delimiter":
                    del self.metadata["dialect"]["delimiter"]
                elif broken == "mapping":
                    del self.metadata["tableSchema"]["columns"][1]["propertyUrl"]
                elif broken == "wrong_delimiter":
                    self.metadata["dialect"]["delimiter"] = ","
                elif broken == "integer":
                    self.metadata["tableSchema"]["columns"][1]["datatype"] = "integer"
                else:
                    self.metadata["tableSchema"]["columns"][1]["datatype"]["minimum"] = 2
                with self.assertRaises(ValueError):
                    self.generate()

    def test_api_follows_tss_file_links_and_serves_the_saved_export(self):
        self.csvw.write_text(json.dumps(self.metadata), encoding="utf-8")
        with patch.object(main, "load_graph", return_value=self.graph), patch.object(main, "DEMO_DIR", self.root):
            result = main.generate_rdf("fixture")
            token = result["downloadUrl"].rsplit("/", 1)[-1]
            response = main.download_generated_rdf("fixture", token)
            graph = Graph().parse(response.path, format="turtle")
            self.assertEqual(len(set(graph.subjects(RDF.type, SOSA.Observation))), 3)
            self.assertIn("observations.ttl", response.headers["content-disposition"])
            self.assertEqual(response.media_type, "text/turtle")
            with self.assertRaises(HTTPException):
                main.download_generated_rdf("other-endpoint", token)


if __name__ == "__main__":
    unittest.main()
