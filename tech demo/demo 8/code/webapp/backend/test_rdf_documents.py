"""The RDF documents the browser reads: the catalogue (only the bundled endpoints), the metadata
files and the SHACL shapes. Also: the routes of the removed online-endpoint feature are gone.
Run from backend/: python -B -m unittest -v test_rdf_documents.

The answers are read back with rdflib, like the browser reads them with rdf-parse:
the tests compare RDF terms, not Turtle text."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from rdflib import Graph, URIRef, RDF, RDFS
from rdflib.namespace import DCAT, DCTERMS

import main
import paths
import documents
from rdf_generation import TSS

BASE = "http://testserver/"  # the address TestClient gives the backend
LOCAL_IDS = [
    "factory_conductivity_tss2",
    "waterinfo_289423042_tss2",
    "waterinfo_289429042_tss2_parquet",
    "waterinfo_289435042_tss2",
    "waterinfo_289441042_tss2_points_embedded",
    "waterinfo_289441042_tss2_points_time_value",
]
CSV = "waterinfo_289423042_tss2"
TURTLE = URIRef("https://www.iana.org/assignments/media-types/text/turtle")


def read_turtle(response, base):
    """An answer of the backend as an RDF graph"""
    return Graph().parse(data=response.text, format="turtle", publicID=base)


def local_address(endpoint_id):
    return URIRef(f"{BASE}files/metadata%20endpoint/{endpoint_id}.ttl")


class RdfDocuments(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def catalog(self):
        response = self.client.get("/api/catalog")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/turtle; charset=utf-8")
        return read_turtle(response, BASE + "api/catalog")

    def datasets(self, catalog):
        """The datasets of a catalogue: endpoint id -> (dataset IRI, download URL, media type)"""
        found = {}
        for dataset in catalog.objects(URIRef(BASE + "api/catalog"), DCAT.dataset):
            self.assertIn((dataset, RDF.type, DCAT.Dataset), catalog)
            distribution = catalog.value(dataset, DCAT.distribution)
            self.assertIn((distribution, RDF.type, DCAT.Distribution), catalog)
            found[str(catalog.value(dataset, DCTERMS.identifier))] = (
                dataset, catalog.value(distribution, DCAT.downloadURL), catalog.value(distribution, DCAT.mediaType))
        return found

    # --- the catalogue ------------------------------------------------------

    def test_the_catalogue_lists_only_the_bundled_endpoints_with_their_labels(self):
        catalog = self.catalog()
        self.assertIn((URIRef(BASE + "api/catalog"), RDF.type, DCAT.Catalog), catalog)
        datasets = self.datasets(catalog)
        # Exactly the .ttl files of the "metadata endpoint" folder: no online endpoints
        bundled = sorted(path.stem for path in paths.METADATA_DIR.glob("*.ttl"))
        self.assertEqual(sorted(datasets), bundled)
        self.assertEqual(sorted(datasets), LOCAL_IDS)
        self.assertEqual(len(list(catalog.subjects(RDF.type, DCAT.Dataset))), len(LOCAL_IDS))
        for endpoint_id in LOCAL_IDS:
            with self.subTest(endpoint=endpoint_id):
                dataset, download_url, media_type = datasets[endpoint_id]
                # A bundled endpoint: its IRI is its file on this backend, and also where to download it
                self.assertEqual(dataset, local_address(endpoint_id))
                self.assertEqual(download_url, dataset)
                self.assertEqual(media_type, TURTLE)
                # The label is the tss:Source's rdfs:label, with its language tag
                metadata = documents.load_graph(endpoint_id)
                source = metadata.value(predicate=RDF.type, object=TSS.Source)
                self.assertEqual(catalog.value(dataset, RDFS.label), metadata.value(source, RDFS.label))
                self.assertEqual(catalog.value(dataset, RDFS.label).language, "en")
                self.assertIsNone(catalog.value(dataset, RDFS.comment))

    def test_an_unreadable_endpoint_gets_a_comment_and_does_not_break_the_catalogue(self):
        # The CSV endpoint's file can't be read (the others can)
        read_document = documents.read_document

        def broken_csv(location):
            if location == f"metadata endpoint/{CSV}.ttl":
                raise HTTPException(status_code=500, detail=f"Could not read {location}: Bad syntax")
            return read_document(location)

        with patch.object(documents, "read_document", side_effect=broken_csv):
            catalog = self.catalog()
        self.assertEqual(sorted(self.datasets(catalog)), LOCAL_IDS)
        dataset = local_address(CSV)
        self.assertIsNone(catalog.value(dataset, RDFS.label))
        self.assertIn("Can't be read", str(catalog.value(dataset, RDFS.comment)))
        self.assertIsNotNone(catalog.value(local_address(LOCAL_IDS[0]), RDFS.label))

    # --- the files ----------------------------------------------------------

    def test_metadata_files_are_served_as_turtle_and_their_relative_iris_resolve_under_files(self):
        address = str(local_address(CSV))
        response = self.client.get(address)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/turtle; charset=utf-8")
        self.assertEqual(response.content, (paths.METADATA_DIR / f"{CSV}.ttl").read_bytes())
        # Parsed with its own address as base IRI, like the browser does
        metadata = read_turtle(response, address)
        source = metadata.value(predicate=RDF.type, object=TSS.Source)
        data_file = metadata.value(source, TSS.dataFile)
        self.assertEqual(data_file, URIRef(BASE + "files/data/waterinfo_289423042.csv"))
        # ... but data/ is not served: data only goes out through the /api data routes
        self.assertEqual(self.client.get(str(data_file)).status_code, 404)

    def test_shacl_shapes_are_served_as_turtle(self):
        for name in ("embedded_shapes.ttl", "external_shapes.ttl"):
            with self.subTest(shapes=name):
                response = self.client.get(f"/files/SHACL/{name}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "text/turtle; charset=utf-8")
                self.assertGreater(len(read_turtle(response, BASE + "files/SHACL/" + name)), 0)

    # --- the removed online-endpoint feature --------------------------------

    def test_the_online_endpoint_routes_are_gone(self):
        # The user's own endpoints live in the browser now: the backend can't add, remove or
        # download them. No route answers these any more: 404, or 405 from the built frontend
        # (frontend/dist, served at "/", which only answers GET)
        for method, address in [("POST", "/api/endpoints/online?url=https://example.org/x.ttl"),
                                ("DELETE", f"/api/endpoints/{CSV}"),
                                ("DELETE", "/api/endpoints/online-1"),
                                ("GET", f"/api/endpoints/{CSV}/document"),
                                ("GET", "/api/endpoints/online-1/document")]:
            with self.subTest(method=method, address=address):
                response = self.client.request(method, address)
                self.assertIn(response.status_code, (404, 405))
        # The bundled endpoint is still there
        self.assertEqual(sorted(self.datasets(self.catalog())), LOCAL_IDS)

    def test_an_id_that_is_no_bundled_file_is_unknown(self):
        for endpoint_id in ("online-1", "nothing-here", f"{CSV}.ttl"):
            for method, route in [("GET", "rows"), ("GET", "data"), ("GET", "shacl"), ("GET", "sql/tables"),
                                  ("POST", "rdf")]:
                with self.subTest(endpoint=endpoint_id, route=route):
                    response = self.client.request(method, f"/api/endpoints/{endpoint_id}/{route}")
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json()["detail"], f"Unknown metadata endpoint: {endpoint_id}")
            with self.subTest(endpoint=endpoint_id, route="sql"):
                response = self.client.post(f"/api/endpoints/{endpoint_id}/sql", json={"sql": "SELECT 1"})
                self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
