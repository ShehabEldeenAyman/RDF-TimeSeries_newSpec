"""What the backend reads must stay inside demo 7, and it never downloads anything.
Run from backend/: python -B -m unittest -v test_untrusted_documents.

- tss:dataFile: only files in demo 7's data folder are read, after the path is resolved (so
  data/../../x can't get out). A web address is refused with a clear message: this backend reads
  only the demo's own data files; the user's own endpoints are read by the browser.
- JSON-LD: its "@context" can name a web address or a local file, which rdflib's JSON-LD parser
  would download or read, so uploaded shapes may not be JSON-LD."""
import json
import socket
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

from fastapi import HTTPException
from fastapi.testclient import TestClient
from rdflib import RDF, URIRef

import main
import paths
import documents
import source_reading
from rdf_generation import TSS
from test_external_shacl import CUSTOM_SHAPES

CSV = "waterinfo_289423042_tss2"
PARQUET = "waterinfo_289429042_tss2_parquet"
CSV_VALUE = "https://example.org/ex#grootdok-ec20-field"
LOCAL_DATA = urljoin(paths.DEFAULT_BASE, "files/data/")  # http://localhost:5174/files/data/
DEMO_IRI = paths.DEMO_DIR.as_uri()  # file:///C:/Code%20exercises/.../demo%207
# A file inside demo 7, but not in its data folder: the backend's own code
NOT_DATA = paths.DEMO_DIR / "code" / "webapp" / "backend" / "main.py"
# What the 400 for a web address says
WEB_ADDRESS_REFUSED = "this backend reads only the demo's own data files; user endpoints are read by the browser"


class DataFileLocations(unittest.TestCase):
    """iri_to_location: where a tss:dataFile is, or a 400 when the backend may not read it"""

    def refused(self, iri):
        with self.assertRaises(HTTPException) as caught:
            source_reading.iri_to_location(URIRef(iri), LOCAL_DATA)
        self.assertEqual(caught.exception.status_code, 400)
        return caught.exception.detail

    def test_files_in_the_data_folder_are_read(self):
        for iri, location in [(LOCAL_DATA + "waterinfo_289423042.csv", "data/waterinfo_289423042.csv"),
                              (LOCAL_DATA + "two%20words.csv", "data/two words.csv"),
                              (DEMO_IRI + "/data/waterinfo_289423042.csv", "data/waterinfo_289423042.csv")]:
            with self.subTest(iri=iri):
                self.assertEqual(source_reading.iri_to_location(URIRef(iri), LOCAL_DATA), location)

    def test_a_web_address_is_refused_with_a_clear_message(self):
        for iri in ["https://example.org/data/x.csv",
                    "http://example.org/data/x.parquet",
                    "HTTPS://EXAMPLE.ORG/data/x.csv",                                    # the scheme in capitals
                    "http://127.0.0.1:5174/files/data/x.csv",                            # another host name
                    urljoin(paths.DEFAULT_BASE, "files/metadata%20endpoint/x.csv")]:     # this server, but not files/data/
            with self.subTest(iri=iri):
                detail = self.refused(iri)
                self.assertIn("is a web address", detail)
                self.assertIn(WEB_ADDRESS_REFUSED, detail)

    def test_the_data_folder_can_not_be_left(self):
        for iri in [DEMO_IRI + "/../../../../Windows/win.ini",            # the review's example
                    DEMO_IRI + "/data/../code/webapp/backend/main.py",   # ".." after data/
                    NOT_DATA.as_uri(),                                   # in demo 7, not in data/
                    LOCAL_DATA + "%2e%2e/code/webapp/backend/main.py",  # ".." written as %2e%2e
                    LOCAL_DATA + "..%5c..%5cWindows%5cwin.ini",          # "\" written as %5c
                    LOCAL_DATA,                                          # the folder itself
                    "urn:example:data"]:                                 # no file and no web address
            with self.subTest(iri=iri):
                self.assertIn("data folder", self.refused(iri))


class WebDataFileThroughTheRoutes(unittest.TestCase):
    """A bundled document whose tss:dataFile is a web address (patched in): every data route
    answers the clear 400, and the backend never looks the address up"""

    def setUp(self):
        self.client = TestClient(main.app)
        # Nothing may go out to the network: looking up a host name is the first step of every
        # download, so it fails the test. (TestClient itself talks to the app without a network.)
        patcher = patch.object(socket, "getaddrinfo", side_effect=AssertionError("the backend looked up a web address"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def with_data_file(self, endpoint_id, iri):
        """The endpoint's metadata, with this tss:dataFile instead of its own"""
        graph = documents.load_graph(endpoint_id, "http://testserver/")
        source = graph.value(predicate=RDF.type, object=TSS.Source)
        graph.set((source, TSS.dataFile, URIRef(iri)))
        return patch.object(documents, "load_graph", return_value=graph)

    def test_every_data_route_refuses_a_web_address(self):
        for endpoint_id, iri in [(CSV, "https://example.org/data/waterinfo_289423042.csv"),
                                 (PARQUET, "http://example.org/data/waterinfo_289429042.parquet")]:
            address = f"/api/endpoints/{endpoint_id}"
            with self.with_data_file(endpoint_id, iri):
                for method, route, options in [("GET", "/data", {}),
                                               ("GET", "/data/download", {}),
                                               ("GET", "/rows", {}),
                                               ("GET", "/filter-columns", {"params": {"field": CSV_VALUE}}),
                                               ("GET", "/query", {"params": {"field": CSV_VALUE}}),
                                               ("GET", "/sql/tables", {}),
                                               ("POST", "/sql", {"json": {"sql": "SELECT 1"}}),
                                               ("POST", "/rdf", {})]:
                    with self.subTest(endpoint=endpoint_id, route=route):
                        response = self.client.request(method, address + route, **options)
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertEqual(response.json()["detail"], f"tss:dataFile <{iri}> is a web address: {WEB_ADDRESS_REFUSED}.")


class UploadedShapes(unittest.TestCase):
    def test_json_ld_shapes_are_refused(self):
        client = TestClient(main.app)
        shapes = json.dumps({"@context": "file:///C:/Windows/win.ini", "@id": "urn:shape"})
        for content_type in ["application/ld+json", "application/ld+json; charset=utf-8"]:
            with self.subTest(content_type=content_type):
                response = client.post(f"/api/endpoints/{CSV}/shacl", content=shapes, headers={"Content-Type": content_type})
                self.assertEqual(response.status_code, 400)
                self.assertIn("JSON-LD is not read here", response.json()["detail"])
        # The same shapes as Turtle are fine
        response = client.post(f"/api/endpoints/{CSV}/shacl", content=CUSTOM_SHAPES, headers={"Content-Type": "text/turtle"})
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
