"""Parquet/context RDF semantics, streaming, and full dashboard downloads."""
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb
from fastapi import HTTPException
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import DCTERMS, XSD
from rdflib.compare import isomorphic

import main
import parquet_rdf_generation
from tss2 import TSS, SOSA

EX = Namespace("https://example.org/")


class ParquetRdfGeneration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parquet = self.root / "readings.parquet"
        self.target = self.root / "observations.ttl"
        self.amount = "12345678901234567890.1234567890"
        with duckdb.connect() as connection:
            connection.execute('CREATE TABLE readings ("Timestamp" TIMESTAMPTZ, "A" DOUBLE, "B" BIGINT, "Description" VARCHAR, "Link" VARCHAR, "Amount" DECIMAL(38, 10))')
            connection.executemany("INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?)", [
                ("2025-01-01T00:00:00Z", 1.5, 2, "good", "category/good", self.amount),
                ("2025-01-01T00:15:00Z", None, 3, "review", "category/review", self.amount),
                ("2025-01-01T00:30:00Z", 4.5, 4, "excluded", "category/bad", self.amount),
            ])
            connection.sql("SELECT * FROM readings").write_parquet(str(self.parquet))
        self.context = {
            "@language": "en",
            "Timestamp": {"@id": str(SOSA.resultTime), "@type": str(XSD.dateTime)},
            "A": {"@id": str(EX.a), "@type": str(XSD.double)},
            "B": {"@id": str(EX.b), "@type": str(XSD.integer)},
            "Description": {"@id": str(EX.description), "@language": "en"},
            "Link": {"@id": str(EX.link), "@type": "@id"},
            "Amount": {"@id": str(EX.amount), "@type": str(XSD.decimal)},
        }
        self.graph = Graph()
        self.graph.bind("ex", EX)
        self.graph.bind("sosa", SOSA)
        self.graph.bind("tss", TSS)
        for triple in [
            (EX.group, RDF.type, TSS.SnippetGroup), (EX.group, TSS.embedded, Literal(False)),
            (EX.group, DCTERMS.format, main.PARQUET_FILE_TYPE),
            (EX.group, TSS.producedBy, EX.logger), (EX.group, TSS.snippetTemplate, EX.template),
            (EX.group, TSS.dataFile, URIRef(self.parquet.as_uri())),
            (EX.template, TSS.timeColumn, Literal("Timestamp")), (EX.template, TSS.pointType, SOSA.Observation),
            (EX.template, TSS["from"], Literal("2025-01-01T00:00:00Z", datatype=XSD.dateTime)),
            (EX.template, TSS.until, Literal("2025-01-01T00:30:00Z", datatype=XSD.dateTime)),
            (EX.template, TSS.about, EX.defaultAbout),
            (EX.defaultAbout, SOSA.madeBySensor, EX.sensorA),
            (EX.sensorA, RDFS.label, Literal("Sensor A")),
            (EX.sensorB, RDFS.label, Literal("Sensor B")),
        ]:
            self.graph.add(triple)
        self.set_context()
        for snippet, column in ((EX.seriesA, "a"), (EX.seriesB, "B")):
            self.graph.add((EX.group, TSS.hasSnippet, snippet))
            self.graph.add((snippet, RDF.type, TSS.Snippet))
            self.graph.add((snippet, TSS.valueColumn, Literal(column)))
        self.graph.add((EX.seriesB, TSS.about, EX.aboutB))
        self.graph.add((EX.aboutB, SOSA.madeBySensor, EX.sensorB))

    def set_context(self):
        self.graph.set((EX.template, TSS.context, Literal(json.dumps({"@context": self.context}), datatype=RDF.JSON)))

    def generate(self, **options):
        result = parquet_rdf_generation.generate(
            self.graph, lambda group: (self.parquet, URIRef("https://example.org/readings.parquet")), self.target, **options)
        return result, Graph().parse(self.target, format="turtle")

    def test_typed_readings_series_separation_inheritance_and_time_window(self):
        result, graph = self.generate()
        self.assertEqual(result["points"], 3)
        self.assertEqual([entry["points"] for entry in result["series"]], [1, 2])
        readings = set(graph.subjects(RDF.type, SOSA.Observation))
        self.assertEqual(len(readings), 3)
        self.assertTrue(all(isinstance(node, BNode) for node in readings))
        for node in readings:
            self.assertEqual(graph.value(node, SOSA.resultTime).datatype, XSD.dateTime)
            a, b = graph.value(node, EX.a), graph.value(node, EX.b)
            self.assertNotEqual(a is None, b is None)
            if a is not None:
                self.assertEqual(a.datatype, XSD.double)
                self.assertEqual(graph.value(node, SOSA.madeBySensor), EX.sensorA)
            else:
                self.assertEqual(b.datatype, XSD.integer)
                self.assertEqual(graph.value(node, SOSA.madeBySensor), EX.sensorB)
            self.assertEqual(graph.value(node, EX.description).language, "en")
            self.assertIn(graph.value(node, EX.link), {EX["category/good"], EX["category/review"]})
            self.assertEqual(graph.value(node, EX.amount).toPython(), Decimal(self.amount))
        self.assertEqual(graph.value(EX.sensorA, RDFS.label), Literal("Sensor A"))
        self.assertEqual(result["triples"], len(graph))
        text = self.target.read_text(encoding="utf-8")
        for forbidden in (str(TSS), "ObservationCollection", "logger", "urn:rdftss-generation:"):
            self.assertNotIn(forbidden, text)
        self.assertFalse(list(graph.triples((None, SOSA.hasMember, None))))

    def test_preview_is_bounded_and_download_is_complete(self):
        result, graph = self.generate(preview_limit=1)
        preview = Graph().parse(data=result["turtle"], format="turtle")
        self.assertEqual(len(set(preview.subjects(RDF.type, SOSA.Observation))), 1)
        self.assertEqual(len(set(graph.subjects(RDF.type, SOSA.Observation))), 3)
        self.assertTrue(result["previewTruncated"])
        self.assertEqual(result["previewPoints"], 1)
        self.assertEqual(result["bytes"], self.target.stat().st_size)

    def test_scalar_export_matches_jsonld_expansion(self):
        result, scalar = self.generate()
        for name in self.context:
            if isinstance(self.context[name], dict):
                self.context[name]["@container"] = "@set"
        self.set_context()
        expanded_result, expanded = self.generate()
        self.assertEqual(result["points"], expanded_result["points"])
        self.assertTrue(isomorphic(scalar, expanded))

    def test_more_than_one_batch_preserves_independent_readings(self):
        self.graph.remove((EX.group, TSS.hasSnippet, EX.seriesB))
        with duckdb.connect() as connection:
            connection.sql("SELECT TIMESTAMPTZ '2025-01-01T00:00:00Z' + i * INTERVAL '1 second' AS Timestamp, i::DOUBLE AS A FROM range(1005) t(i)").write_parquet(str(self.parquet))
        result, graph = self.generate(preview_limit=2)
        self.assertEqual(result["points"], 1005)
        self.assertEqual(len(set(graph.subjects(RDF.type, SOSA.Observation))), 1005)
        self.assertEqual(result["triples"], len(graph))
        self.assertEqual(result["previewPoints"], 2)

    def test_nested_result_nodes_do_not_receive_observation_metadata(self):
        self.graph.remove((EX.group, TSS.hasSnippet, EX.seriesB))
        self.graph.set((EX.seriesA, TSS.valueColumn, Literal("Result")))
        self.context["Result"] = str(SOSA.hasResult)
        self.context["reading"] = {"@id": str(SOSA.hasSimpleResult), "@type": str(XSD.double)}
        self.set_context()
        with duckdb.connect() as connection:
            connection.sql("SELECT TIMESTAMPTZ '2025-01-01T00:00:00Z' AS Timestamp, struct_pack(reading := 1.5::DOUBLE) AS Result").write_parquet(str(self.parquet))
        result, graph = self.generate()
        self.assertEqual(result["points"], 1)
        reading = next(graph.subjects(RDF.type, SOSA.Observation))
        nested = graph.value(reading, SOSA.hasResult)
        self.assertIsInstance(nested, BNode)
        self.assertEqual(graph.value(nested, SOSA.hasSimpleResult).toPython(), 1.5)
        self.assertFalse(list(graph.objects(nested, SOSA.madeBySensor)))
        self.assertFalse(list(graph.objects(nested, RDF.type)))

    def test_missing_context_mapping_or_invalid_datatype_stops_generation(self):
        for broken in ("missing", "unmapped", "tss", "datatype"):
            with self.subTest(broken=broken):
                original = dict(self.context["A"])
                if broken == "missing":
                    self.graph.remove((EX.template, TSS.context, None))
                elif broken == "unmapped":
                    del self.context["A"]
                    self.set_context()
                else:
                    self.context["A"] = {"@id": str(TSS.value) if broken == "tss" else str(EX.a),
                                         "@type": str(XSD.integer) if broken == "datatype" else str(XSD.double)}
                    self.set_context()
                with self.assertRaises(ValueError):
                    self.generate()
                self.context["A"] = original
                self.set_context()

    def test_missing_timestamp_is_rejected(self):
        with duckdb.connect() as connection:
            connection.sql("SELECT NULL::TIMESTAMPTZ AS Timestamp, 1.0 AS A, 2 AS B").write_parquet(str(self.parquet))
        with self.assertRaisesRegex(ValueError, "missing timestamp"):
            self.generate()

    def test_api_needs_no_csvw_and_serves_the_full_export(self):
        with patch.object(main, "load_graph", return_value=self.graph), \
                patch.object(main, "DEMO_DIR", self.root), patch.object(main, "load_csvw_json") as csvw:
            result = main.generate_rdf("fixture")
            csvw.assert_not_called()
        token = result["downloadUrl"].rsplit("/", 1)[-1]
        response = main.download_generated_rdf("fixture", token)
        graph = Graph().parse(response.path, format="turtle")
        self.assertEqual(len(set(graph.subjects(RDF.type, SOSA.Observation))), 3)
        self.assertEqual(response.media_type, "text/turtle")
        self.assertEqual(response.filename, "observations.ttl")

    def test_failed_api_generation_removes_partial_export(self):
        self.graph.remove((EX.template, TSS.context, None))
        before = set(Path(main.RDF_EXPORT_DIRECTORY.name).iterdir())
        with patch.object(main, "load_graph", return_value=self.graph), patch.object(main, "DEMO_DIR", self.root):
            with self.assertRaises(HTTPException) as caught:
                main.generate_rdf("fixture")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("tss:context", caught.exception.detail)
        self.assertEqual(set(Path(main.RDF_EXPORT_DIRECTORY.name).iterdir()), before)


if __name__ == "__main__":
    unittest.main()
