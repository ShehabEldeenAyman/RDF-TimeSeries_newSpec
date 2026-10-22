"""The RDF generation tab: python -B -m unittest -v test_rdf_generation.

POST /rdf answers with the counts (JSON) and two addresses: the preview (Turtle) and the whole
file (N-Triples). The tests read the RDF back with rdflib and compare RDF terms and values."""
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from rdflib import Graph, Namespace, RDF
from rdflib.compare import isomorphic
from rdflib.namespace import XSD

import main

SOSA = Namespace("http://www.w3.org/ns/sosa/")
EX = "https://example.org/ex#"  # the series are identified by their field's full IRI
EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL_CSV = "waterinfo_289423042_tss2"
EXTERNAL_PARQUET = "waterinfo_289429042_tss2_parquet"


class RdfGeneration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def preview(self, result):
        """GET the preview of a generation (its previewUrl), as an RDF graph"""
        response = self.client.get(result["previewUrl"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/turtle; charset=utf-8")
        return Graph().parse(data=response.text, format="turtle")

    def test_the_answer_holds_counts_and_addresses_only(self):
        response = self.client.post(f"/api/endpoints/{EMBEDDED}/rdf")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sorted(response.json()), ["bytes", "downloadUrl", "filename", "observations", "previewObservations",
                                                   "previewTruncated", "previewUrl", "series", "triples"])

    def test_embedded_points_become_observations(self):
        result = main.generate_rdf(EMBEDDED)
        self.assertEqual(result["observations"], 10)
        self.assertEqual([(s["field"], s["observations"]) for s in result["series"]],
                         [(EX + "zevenaarshaven-ec20-value-field", 5), (EX + "zevenaarshaven-quality-color-value-field", 5)])
        self.assertFalse(result["previewTruncated"])
        graph = self.preview(result)
        self.assertEqual(len(set(graph.subjects(RDF.type, SOSA.Observation))), 10)
        self.assertEqual(len(graph), result["triples"])

        # The whole graph, downloaded as N-Triples: the same triples as the preview
        response = self.client.get(result["downloadUrl"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/n-triples")
        self.assertIn('filename="observations.nt"', response.headers["content-disposition"])
        self.assertEqual(len(response.content), result["bytes"])
        everything = Graph().parse(data=response.text, format="nt")
        self.assertEqual(len(set(everything.subjects(RDF.type, SOSA.Observation))), 10)
        self.assertEqual(len(everything), result["triples"])
        self.assertTrue(isomorphic(everything, graph))

    def test_external_csv_preview_and_full_download(self):
        result = main.generate_rdf(EXTERNAL_CSV)
        self.assertEqual(result["observations"], 2 * 94853)  # Value and Quality Code Color per row
        self.assertTrue(result["previewTruncated"])
        self.assertEqual(result["previewObservations"], 200)
        preview = self.preview(result)
        self.assertEqual(len(set(preview.subjects(RDF.type, SOSA.Observation))), 200)

        # The whole graph is an N-Triples file (rdflib writes it in batches, see tss2rdf.generate)
        token = result["downloadUrl"].rsplit("/", 1)[-1]
        response = main.download_generated_rdf(EXTERNAL_CSV, token)
        self.assertEqual(response.media_type, "application/n-triples")
        self.assertEqual(response.filename, "observations.nt")
        self.assertEqual(response.path.stat().st_size, result["bytes"])
        self.assertEqual(result["filename"], "observations.nt")

    def test_external_parquet(self):
        result = main.generate_rdf(EXTERNAL_PARQUET)
        self.assertEqual([(s["field"], s["observations"]) for s in result["series"]],
                         [(EX + "rodenhuizedok-quality-code-color-field", 158022), (EX + "rodenhuizedok-ec20-field", 158022)])
        # Read the preview as RDF and compare terms and values, not the Turtle text
        preview = self.preview(result)
        results = []
        for value in preview.objects(None, SOSA.hasSimpleResult):
            results.append((value.toPython(), value.datatype))
        self.assertIn((1605.77, XSD.double), results)
        times = []
        for value in preview.objects(None, SOSA.resultTime):
            times.append((value.toPython(), value.datatype))
        self.assertIn((datetime(2021, 3, 3, 23, 15, tzinfo=timezone.utc), XSD.dateTime), times)

    def test_a_download_of_another_endpoint_is_refused(self):
        token = main.generate_rdf(EMBEDDED)["downloadUrl"].rsplit("/", 1)[-1]
        for route in (main.download_generated_rdf, main.preview_generated_rdf):
            with self.subTest(route=route.__name__):
                with self.assertRaises(main.HTTPException) as caught:
                    route(EXTERNAL_CSV, token)
                self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(self.client.get(f"/api/endpoints/{EMBEDDED}/rdf/preview/unknown").status_code, 404)


if __name__ == "__main__":
    unittest.main()
