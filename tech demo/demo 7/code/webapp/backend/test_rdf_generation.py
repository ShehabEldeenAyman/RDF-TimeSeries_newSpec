"""The RDF generation tab: python -B -m unittest -v test_rdf_generation."""
import unittest

from rdflib import Graph, Namespace, RDF

import main

SOSA = Namespace("http://www.w3.org/ns/sosa/")
EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL_CSV = "waterinfo_289423042_tss2"
EXTERNAL_PARQUET = "waterinfo_289429042_tss2_parquet"


class RdfGeneration(unittest.TestCase):
    def test_embedded_points_become_observations(self):
        result = main.generate_rdf(EMBEDDED)
        self.assertEqual(result["observations"], 10)
        self.assertEqual([(s["column"], s["observations"]) for s in result["series"]], [("ec20", 5), ("qualityColor", 5)])
        self.assertFalse(result["previewTruncated"])
        graph = Graph().parse(data=result["turtle"], format="turtle")
        self.assertEqual(len(set(graph.subjects(RDF.type, SOSA.Observation))), 10)
        self.assertEqual(len(graph), result["triples"])

    def test_external_csv_preview_and_full_download(self):
        result = main.generate_rdf(EXTERNAL_CSV)
        self.assertEqual(result["observations"], 2 * 94853)  # Value and Quality Code Color per row
        self.assertTrue(result["previewTruncated"])
        self.assertEqual(result["previewObservations"], 200)
        preview = Graph().parse(data=result["turtle"], format="turtle")
        self.assertEqual(len(set(preview.subjects(RDF.type, SOSA.Observation))), 200)

        token = result["downloadUrl"].rsplit("/", 1)[-1]
        response = main.download_generated_rdf(EXTERNAL_CSV, token)
        self.assertEqual(response.media_type, "text/turtle")
        self.assertEqual(response.filename, "observations.ttl")
        self.assertEqual(response.path.stat().st_size, result["bytes"])

    def test_external_parquet(self):
        result = main.generate_rdf(EXTERNAL_PARQUET)
        self.assertEqual([(s["column"], s["observations"]) for s in result["series"]],
                         [("Quality Code Color", 158022), ("Value", 158022)])
        self.assertIn('sosa:hasSimpleResult "1605.77"^^xsd:double', result["turtle"])
        self.assertIn('sosa:resultTime "2021-03-03T23:15:00Z"^^xsd:dateTime', result["turtle"])

    def test_a_download_of_another_endpoint_is_refused(self):
        token = main.generate_rdf(EMBEDDED)["downloadUrl"].rsplit("/", 1)[-1]
        with self.assertRaises(main.HTTPException) as caught:
            main.download_generated_rdf(EXTERNAL_CSV, token)
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
