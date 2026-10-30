"""The RDF generation tab: python -B -m unittest -v test_rdf_generation.

POST /rdf answers with the counts (JSON) and two addresses: the preview (Turtle) and the whole
file (N-Triples). The tests read the RDF back with rdflib and compare RDF terms and values.
They also check that no generated data is left behind: each endpoint keeps only its latest
export, a token only works for its own endpoint, and the export folder is emptied when the
backend starts and stops."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from rdflib import Graph, Namespace, RDF
from rdflib.compare import isomorphic
from rdflib.namespace import XSD

import main
import paths
import documents
import rdf_routes
from test_embedded_shacl import embedded_graph, point

SOSA = Namespace("http://www.w3.org/ns/sosa/")
EX = "https://example.org/ex#"  # the series are identified by their field's full IRI
EMBEDDED = "waterinfo_289441042_tss2_points_embedded"
EXTERNAL_CSV = "waterinfo_289423042_tss2"
EXTERNAL_PARQUET = "waterinfo_289429042_tss2_parquet"
# A file left behind in the export folder (by a backend that was killed before it could clean up)
LEFT_BEHIND = '_:o1 <urn:example:p> "old" .\n'


def use_a_test_export_folder(test):
    """Let the RDF generation write into a folder of this test (it does not exist yet: POST /rdf
    makes it), not into the backend's own export folder (paths.RDF_EXPORT_DIR), and start
    without any export. Gives that folder. (Also used by test_base_iri and test_storage_profiles.)"""
    temporary = tempfile.TemporaryDirectory()
    test.addCleanup(temporary.cleanup)
    folder = Path(temporary.name) / "exports"
    for patcher in (patch.object(paths, "RDF_EXPORT_DIR", folder), patch.dict(rdf_routes.RDF_EXPORTS, clear=True)):
        patcher.start()
        test.addCleanup(patcher.stop)
    return folder


def token_of(result):
    """The token in the addresses of a generation's answer"""
    return result["downloadUrl"].rsplit("/", 1)[-1]


class RdfGeneration(unittest.TestCase):
    def setUp(self):
        self.folder = use_a_test_export_folder(self)
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
        result = rdf_routes.generate_rdf(EMBEDDED)
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
        result = rdf_routes.generate_rdf(EXTERNAL_CSV)
        self.assertEqual(result["observations"], 2 * 94853)  # Value and Quality Code Color per row
        self.assertTrue(result["previewTruncated"])
        self.assertEqual(result["previewObservations"], 200)
        preview = self.preview(result)
        self.assertEqual(len(set(preview.subjects(RDF.type, SOSA.Observation))), 200)

        # The whole graph is an N-Triples file (rdflib writes it in batches, see tss2rdf.generate)
        token = result["downloadUrl"].rsplit("/", 1)[-1]
        response = rdf_routes.download_generated_rdf(EXTERNAL_CSV, token)
        self.assertEqual(response.media_type, "application/n-triples")
        self.assertEqual(response.filename, "observations.nt")
        self.assertEqual(response.path.stat().st_size, result["bytes"])
        self.assertEqual(result["filename"], "observations.nt")

    def test_external_parquet(self):
        result = rdf_routes.generate_rdf(EXTERNAL_PARQUET)
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
        token = token_of(rdf_routes.generate_rdf(EMBEDDED))
        for route in (rdf_routes.download_generated_rdf, rdf_routes.preview_generated_rdf):
            with self.subTest(route=route.__name__):
                with self.assertRaises(HTTPException) as caught:
                    route(EXTERNAL_CSV, token)
                self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(self.client.get(f"/api/endpoints/{EMBEDDED}/rdf/preview/unknown").status_code, 404)

    # --- no generated data is left behind --------------------------------------

    def files_in_the_folder(self):
        return sorted(path.name for path in self.folder.iterdir())

    def assert_gone(self, endpoint_id, token):
        """The addresses of this generation answer 404"""
        for kind in ("preview", "download"):
            response = self.client.get(f"/api/endpoints/{endpoint_id}/rdf/{kind}/{token}")
            self.assertEqual(response.status_code, 404, kind)
            self.assertIn("no longer available", response.json()["detail"])

    def assert_served(self, endpoint_id, token):
        """The addresses of this generation work"""
        for kind in ("preview", "download"):
            response = self.client.get(f"/api/endpoints/{endpoint_id}/rdf/{kind}/{token}")
            self.assertEqual(response.status_code, 200, kind)

    def test_the_export_files_are_in_the_export_folder(self):
        token = token_of(rdf_routes.generate_rdf(EMBEDDED))
        self.assertEqual(self.files_in_the_folder(), [f"{token}-preview.ttl", f"{token}.nt"])

    def test_a_second_generation_deletes_the_files_of_the_first(self):
        first = token_of(rdf_routes.generate_rdf(EMBEDDED))
        second = token_of(rdf_routes.generate_rdf(EMBEDDED))
        self.assertNotEqual(first, second)
        # Only the second one's files are left, and only its addresses work
        self.assertEqual(self.files_in_the_folder(), [f"{second}-preview.ttl", f"{second}.nt"])
        self.assert_gone(EMBEDDED, first)
        self.assert_served(EMBEDDED, second)

    def test_a_token_only_works_for_its_own_endpoint(self):
        # Endpoint B (the CSV endpoint's id) gets an export of its own. To keep the test quick,
        # its metadata is the embedded endpoint's (a few points instead of 94853 rows).
        load_graph = documents.load_graph

        def embedded_metadata(endpoint_id, base=paths.DEFAULT_BASE):
            return load_graph(EMBEDDED, base)

        token_a = token_of(rdf_routes.generate_rdf(EMBEDDED))
        with patch.object(documents, "load_graph", side_effect=embedded_metadata):
            token_b = token_of(rdf_routes.generate_rdf(EXTERNAL_CSV))
        # Both exports are kept: the latest one of each endpoint
        self.assertEqual(len(self.files_in_the_folder()), 4)
        # A's token does not serve for B (which has an export of its own), nor B's for A ...
        self.assert_gone(EXTERNAL_CSV, token_a)
        self.assert_gone(EMBEDDED, token_b)
        # ... but each token serves for its own endpoint
        self.assert_served(EMBEDDED, token_a)
        self.assert_served(EXTERNAL_CSV, token_b)

    def test_a_failed_generation_leaves_no_files(self):
        # A point with a value that is no xsd:double: the converter stops
        graph = embedded_graph(point(ec20="abc", color="red"))
        with patch.object(documents, "load_graph", return_value=graph):
            response = self.client.post(f"/api/endpoints/{EMBEDDED}/rdf")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.files_in_the_folder(), [])
        self.assertEqual(rdf_routes.RDF_EXPORTS, {})

    def test_the_folder_is_emptied_at_startup(self):
        token = token_of(rdf_routes.generate_rdf(EMBEDDED))
        (self.folder / "0123456789abcdef.nt").write_text(LEFT_BEHIND, encoding="utf-8")
        rdf_routes.empty_export_folder()  # what main.py runs when the backend starts
        self.assertEqual(self.files_in_the_folder(), [])
        self.assertEqual(rdf_routes.RDF_EXPORTS, {})
        self.assert_gone(EMBEDDED, token)

    def test_the_backend_empties_the_folder_when_it_starts_and_stops(self):
        self.folder.mkdir()
        (self.folder / "0123456789abcdef.nt").write_text(LEFT_BEHIND, encoding="utf-8")
        # "with TestClient(...)" starts and stops the app, like uvicorn does (see main.lifespan)
        with TestClient(main.app) as client:
            self.assertEqual(self.files_in_the_folder(), [])
            response = client.post(f"/api/endpoints/{EMBEDDED}/rdf")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(self.files_in_the_folder()), 2)
        self.assertEqual(self.files_in_the_folder(), [])


if __name__ == "__main__":
    unittest.main()
