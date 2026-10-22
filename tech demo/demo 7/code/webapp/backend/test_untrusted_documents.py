"""Online documents and uploaded shapes come from other people, so the backend must not let them
read files on this computer. Run from backend/: python -B -m unittest -v test_untrusted_documents.

- tss:dataFile: only files in demo 7's data folder are read, after the path is resolved (so
  data/../../x can't get out), and an online endpoint can't name a local file at all.
- JSON-LD: its "@context" can name a web address or a local file, which rdflib's JSON-LD parser
  would download or read, so online documents and uploaded shapes may not be JSON-LD."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urljoin

import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient
from rdflib import RDF, URIRef

import main
from test_external_shacl import CUSTOM_SHAPES
from test_rdf_documents import FakeResponse

CSV = "waterinfo_289423042_tss2"
LOCAL_DATA = urljoin(main.DEFAULT_BASE, "files/data/")  # http://localhost:5174/files/data/
DEMO_IRI = main.DEMO_DIR.as_uri()  # file:///C:/Code%20exercises/.../demo%207
# A file inside demo 7, but not in its data folder: the backend's own code
NOT_DATA = main.DEMO_DIR / "code" / "webapp" / "backend" / "main.py"


class DataFileLocations(unittest.TestCase):
    """iri_to_location: where a tss:dataFile is, or a 400 when the backend may not read it"""

    def refused(self, iri, data_address):
        with self.assertRaises(HTTPException) as caught:
            main.iri_to_location(URIRef(iri), data_address)
        self.assertEqual(caught.exception.status_code, 400)
        return caught.exception.detail

    def test_a_local_endpoint_reads_files_in_the_data_folder(self):
        for iri, location in [(LOCAL_DATA + "waterinfo_289423042.csv", "data/waterinfo_289423042.csv"),
                              (LOCAL_DATA + "two%20words.csv", "data/two words.csv"),
                              (DEMO_IRI + "/data/waterinfo_289423042.csv", "data/waterinfo_289423042.csv"),
                              ("https://example.org/data/x.csv", "https://example.org/data/x.csv")]:
            with self.subTest(iri=iri):
                self.assertEqual(main.iri_to_location(URIRef(iri), LOCAL_DATA), location)

    def test_a_local_endpoint_can_not_leave_the_data_folder(self):
        for iri in [DEMO_IRI + "/../../../../Windows/win.ini",            # the review's example
                    DEMO_IRI + "/data/../code/webapp/backend/main.py",   # ".." after data/
                    NOT_DATA.as_uri(),                                   # in demo 7, not in data/
                    LOCAL_DATA + "%2e%2e/code/webapp/backend/main.py",  # ".." written as %2e%2e
                    LOCAL_DATA + "..%5c..%5cWindows%5cwin.ini",          # "\" written as %5c
                    LOCAL_DATA,                                          # the folder itself
                    "urn:example:data"]:                                 # no file and no web address
            with self.subTest(iri=iri):
                self.assertIn("data folder", self.refused(iri, LOCAL_DATA))

    def test_an_online_endpoint_can_not_name_a_local_file(self):
        for iri in [DEMO_IRI + "/data/factory_conductivity.csv", DEMO_IRI + "/../../../../Windows/win.ini"]:
            with self.subTest(iri=iri):
                self.assertIn("online endpoint", self.refused(iri, None))
        # The address of the (not served) data folder stays a web address: it is downloaded,
        # never read from the disk
        self.assertEqual(main.iri_to_location(URIRef(LOCAL_DATA + "x.csv"), None), LOCAL_DATA + "x.csv")


class OnlineDocuments(unittest.TestCase):
    """Online endpoints, through the app, with a fake web"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        patcher = patch.object(main, "ONLINE_ENDPOINTS_FILE", self.folder / "online_endpoints.ttl")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.web = {}  # address -> (Content-Type, text)
        patcher = patch.object(main.requests, "get", side_effect=self.fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def fake_get(self, url, **options):
        if url not in self.web:
            raise requests.ConnectionError(f"No route to {url}")
        content_type, text = self.web[url]
        return FakeResponse(url, 200, content_type, text)

    def metadata_with_data_file(self, iri):
        """The grootdok endpoint's metadata (Turtle), with another tss:dataFile"""
        graph = main.load_graph(CSV)
        source = graph.value(predicate=RDF.type, object=main.TSS.Source)
        graph.set((source, main.TSS.dataFile, URIRef(iri)))
        return graph.serialize(format="turtle")

    def test_an_online_data_file_can_not_be_a_local_file(self):
        # The review's example: a file: IRI inside demo 7, followed by "..": C:\Windows\win.ini.
        # And a file that is in demo 7's data folder: an online endpoint may not read it either.
        for iri in [DEMO_IRI + "/../../../../Windows/win.ini", (main.DEMO_DIR / "data" / "waterinfo_289423042.csv").as_uri()]:
            with self.subTest(iri=iri):
                self.web["https://example.org/evil.ttl"] = ("text/turtle", self.metadata_with_data_file(iri))
                response = self.client.post("/api/endpoints/online", params={"url": "https://example.org/evil.ttl"})
                self.assertEqual(response.status_code, 200, response.text)
                for route in ["data", "data/download", "rows", "sql/tables"]:
                    response = self.client.get(f"/api/endpoints/online-1/{route}")
                    self.assertEqual(response.status_code, 400, route)
                    self.assertIn("an online endpoint can only name web addresses", response.json()["detail"])
                response = self.client.post("/api/endpoints/online-1/sql", json={"sql": "SELECT 1"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.client.delete("/api/endpoints/online-1").status_code, 200)

    def test_an_online_json_ld_document_is_refused(self):
        # A JSON-LD document whose only @context is a file on this computer: it holds a tss:Source
        # only when the parser reads that file (as the review's probe showed it would)
        context = self.folder / "context.jsonld"
        context.write_text(json.dumps({"@context": {"Source": "https://w3id.org/tss#Source"}}), encoding="utf-8")
        document = json.dumps({"@context": context.as_uri(), "@id": "https://example.org/s", "@type": "Source"})
        for content_type in ["application/ld+json", "application/ld+json; charset=utf-8"]:
            with self.subTest(content_type=content_type):
                self.web["https://example.org/x.jsonld"] = (content_type, document)
                with patch.object(main.Graph, "parse") as parse:
                    response = self.client.post("/api/endpoints/online", params={"url": "https://example.org/x.jsonld"})
                self.assertEqual(response.status_code, 400)
                self.assertIn("JSON-LD is not read here", response.json()["detail"])
                parse.assert_not_called()  # refused before any parser saw it
                self.assertEqual(main.load_online_endpoints(), {})
        # Sent without a type, it is read as Turtle, which it is not
        self.web["https://example.org/x.jsonld"] = ("text/plain", document)
        response = self.client.post("/api/endpoints/online", params={"url": "https://example.org/x.jsonld"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Could not read", response.json()["detail"])

    def test_a_registered_endpoint_that_turns_into_json_ld_is_refused(self):
        # Registered while it was Turtle (here: by hand); now its server sends JSON-LD
        registry = main.read_registry()
        main.register_online_endpoint(registry, "online-1", "https://example.org/x", [], "text/turtle")
        main.save_registry(registry)
        self.web["https://example.org/x"] = ("application/ld+json", "{}")
        for route in ["document", "rows", "shacl"]:
            response = self.client.get(f"/api/endpoints/online-1/{route}")
            self.assertEqual(response.status_code, 502, route)
            self.assertIn("JSON-LD is not read here", response.json()["detail"])


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
