"""The RDF documents the browser reads: the catalogue, the metadata files, the SHACL shapes,
and the online endpoints. Run from backend/: python -B -m unittest -v test_rdf_documents.

The answers are read back with rdflib, like the browser reads them with rdf-parse:
the tests compare RDF terms, not Turtle text."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from requests.structures import CaseInsensitiveDict
from fastapi import HTTPException
from fastapi.testclient import TestClient
from rdflib import Graph, Literal, URIRef, RDF, RDFS
from rdflib.namespace import DCAT, DCTERMS

import main
import paths
import files
import online_registry
import documents
from rdf_generation import TSS

BASE = "http://testserver/"  # the address TestClient gives the backend
LOCAL_IDS = [
    "factory_conductivity_tss2",
    "waterinfo_289423042_tss2",
    "waterinfo_289429042_tss2_parquet",
    "waterinfo_289435042_tss2",
    "waterinfo_289441042_tss2_points_embedded",
]
CSV = "waterinfo_289423042_tss2"
TURTLE = URIRef("https://www.iana.org/assignments/media-types/text/turtle")


def read_turtle(response, base):
    """An answer of the backend as an RDF graph"""
    return Graph().parse(data=response.text, format="turtle", publicID=base)


def local_address(endpoint_id):
    return URIRef(f"{BASE}files/metadata%20endpoint/{endpoint_id}.ttl")


class FakeResponse:
    """What requests.get gives back, for a fake online server (also used by test_base_iri.py
    and test_untrusted_documents.py)"""

    def __init__(self, url, status_code, content_type, text):
        self.url = url  # the address after redirects
        self.status_code = status_code
        self.headers = CaseInsensitiveDict()
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        self.text = text
        self.encoding = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error for url: {self.url}")

    def close(self):
        pass

    # A streamed download (an online data file): its lines, or its bytes in pieces
    def iter_lines(self, decode_unicode=False):
        for line in self.text.splitlines():
            yield line

    def iter_content(self, chunk_size=1):
        yield self.text.encode("utf-8")

    # "with download(...) as response:"
    def __enter__(self):
        return self

    def __exit__(self, *error):
        return False


class RdfDocuments(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        # The online endpoints go in a registry file of their own, not the real one
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.registry = Path(temporary.name) / "online_endpoints.ttl"
        patcher = patch.object(paths, "ONLINE_ENDPOINTS_FILE", self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)

        # A fake web: address -> (final address after redirects, status, Content-Type, text)
        self.web = {}
        self.requested = []  # every address the backend asked the fake web for
        patcher = patch.object(files.requests, "get", side_effect=self.fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_get(self, url, **options):
        self.requested.append(url)
        if url not in self.web:
            raise requests.ConnectionError(f"No route to {url}")
        final_url, status_code, content_type, text = self.web[url]
        return FakeResponse(final_url, status_code, content_type, text)

    def publish(self, url, text, content_type="text/turtle; charset=utf-8", final_url=None):
        """Put a document on the fake web"""
        self.web[url] = (final_url or url, 200, content_type, text)

    def endpoint_text(self, endpoint_id):
        return (paths.METADATA_DIR / f"{endpoint_id}.ttl").read_text(encoding="utf-8")

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

    def test_the_catalogue_lists_every_local_endpoint_with_its_label(self):
        catalog = self.catalog()
        self.assertIn((URIRef(BASE + "api/catalog"), RDF.type, DCAT.Catalog), catalog)
        datasets = self.datasets(catalog)
        self.assertEqual(sorted(datasets), LOCAL_IDS)
        for endpoint_id in LOCAL_IDS:
            with self.subTest(endpoint=endpoint_id):
                dataset, download_url, media_type = datasets[endpoint_id]
                # A local endpoint: its IRI is also where to download it
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

    def test_the_catalogue_takes_online_labels_from_the_registry_without_downloading(self):
        url = "https://example.org/grootdok.ttl"
        self.publish(url, self.endpoint_text(CSV))
        self.assertEqual(self.client.post("/api/endpoints/online", params={"url": url}).status_code, 200)
        # One registered by hand, without a label (e.g. by an older backend)
        registry = online_registry.read_registry()
        online_registry.register_online_endpoint(registry, "online-2", "https://example.org/unnamed.ttl", [], "text/turtle")
        online_registry.save_registry(registry)

        # The other servers are gone now: the catalogue does not need them
        self.web.clear()
        self.requested.clear()
        catalog = self.catalog()
        self.assertEqual(self.requested, [])
        self.assertEqual(sorted(self.datasets(catalog)), sorted(LOCAL_IDS + ["online-1", "online-2"]))
        # The source's label, with its language tag, as it was when the endpoint was added
        metadata = documents.load_graph(CSV)
        source = metadata.value(predicate=RDF.type, object=TSS.Source)
        label = catalog.value(URIRef(url), RDFS.label)
        self.assertEqual(label, metadata.value(source, RDFS.label))
        self.assertEqual(label.language, "en")
        self.assertIsNone(catalog.value(URIRef(url), RDFS.comment))
        # Without a noted label, the label is the address
        self.assertEqual(catalog.value(URIRef("https://example.org/unnamed.ttl"), RDFS.label),
                         Literal("https://example.org/unnamed.ttl"))

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

    def test_the_document_route_gives_the_local_file_as_it_is(self):
        response = self.client.get(f"/api/endpoints/{CSV}/document")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/turtle; charset=utf-8")
        self.assertEqual(response.content, (paths.METADATA_DIR / f"{CSV}.ttl").read_bytes())
        self.assertEqual(self.client.get("/api/endpoints/nothing-here/document").status_code, 404)

    # --- online endpoints ---------------------------------------------------

    def test_add_and_remove_an_online_endpoint(self):
        url = "https://w3id.example.org/tss/grootdok"
        final_url = "https://mirror.example.org/tss/metadata/grootdok.ttl"  # where the redirect ends
        self.publish(url, self.endpoint_text(CSV), final_url=final_url)

        response = self.client.post("/api/endpoints/online", params={"url": url})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/turtle; charset=utf-8")
        datasets = self.datasets(read_turtle(response, BASE + "api/catalog"))
        dataset, download_url, media_type = datasets["online-1"]
        # An online endpoint: its own IRI, downloaded through this backend
        self.assertEqual(dataset, URIRef(url))
        self.assertEqual(download_url, URIRef(BASE + "api/endpoints/online-1/document"))
        self.assertEqual(media_type, TURTLE)
        self.assertEqual(read_turtle(response, BASE).value(dataset, RDFS.label).language, "en")

        # The registry is RDF too, with the source's label and the document's media type
        registry = Graph().parse(self.registry, format="turtle")
        self.assertIn((URIRef(url), RDF.type, DCAT.Dataset), registry)
        self.assertEqual(registry.value(URIRef(url), DCTERMS.identifier), Literal("online-1"))
        self.assertEqual(registry.value(URIRef(url), RDFS.label).language, "en")
        self.assertEqual(registry.value(registry.value(URIRef(url), DCAT.distribution), DCAT.mediaType), TURTLE)

        # The document, through the backend, with its own IRI: the registered address, not
        # where the redirect ended. The browser reads relative IRIs against that IRI (the
        # catalogue's dataset IRI), and so does the backend.
        document = self.client.get("/api/endpoints/online-1/document")
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document.headers["content-type"], "text/turtle; charset=utf-8")
        self.assertEqual(document.text, self.endpoint_text(CSV))
        self.assertEqual(document.headers["content-location"], url)
        graph, source = documents.load_source("online-1")
        self.assertEqual(source["dataFile"], "https://w3id.example.org/data/waterinfo_289423042.csv")

        # Removing it gives the catalogue without it
        response = self.client.delete("/api/endpoints/online-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(self.datasets(read_turtle(response, BASE + "api/catalog"))), LOCAL_IDS)
        self.assertEqual(online_registry.load_online_endpoints(), {})
        self.assertEqual(len(Graph().parse(self.registry, format="turtle")), 0)  # its distribution went too

    def test_online_ids_are_the_first_free_number(self):
        for name in ("a", "b", "c"):
            self.publish(f"https://example.org/{name}.ttl", self.endpoint_text(CSV))
        for name in ("a", "b"):
            self.assertEqual(self.client.post("/api/endpoints/online", params={"url": f"https://example.org/{name}.ttl"}).status_code, 200)
        self.assertEqual(self.client.delete("/api/endpoints/online-1").status_code, 200)
        self.assertEqual(self.client.post("/api/endpoints/online", params={"url": "https://example.org/c.ttl"}).status_code, 200)
        self.assertEqual(online_registry.load_online_endpoints(), {"online-1": "https://example.org/c.ttl",
                                                                   "online-2": "https://example.org/b.ttl"})

    def test_an_online_document_is_parsed_by_its_content_type(self):
        # The same metadata as RDF/XML (written by rdflib), sent as application/rdf+xml.
        # (JSON-LD is refused: see test_untrusted_documents.)
        url = "https://example.org/grootdok.rdf"
        self.publish(url, documents.load_graph(CSV).serialize(format="xml"), content_type="application/rdf+xml")
        response = self.client.post("/api/endpoints/online", params={"url": url})
        self.assertEqual(response.status_code, 200, response.text)
        dataset, download_url, media_type = self.datasets(read_turtle(response, BASE))["online-1"]
        self.assertEqual(media_type, URIRef("https://www.iana.org/assignments/media-types/application/rdf+xml"))
        document = self.client.get("/api/endpoints/online-1/document")
        self.assertEqual(document.headers["content-type"], "application/rdf+xml")
        graph, source = documents.load_source("online-1")
        self.assertEqual(source["format"], TSS.CSV)

    def test_bad_online_endpoints_are_refused(self):
        self.publish("https://example.org/page.html", "<html></html>", content_type="text/html")
        self.publish("https://example.org/broken.ttl", "@prefix broken")
        self.publish("https://example.org/no-source.ttl", "<https://example.org/a> <https://example.org/b> <https://example.org/c> .")
        self.web["https://example.org/gone.ttl"] = ("https://example.org/gone.ttl", 404, "text/plain", "Not Found")
        self.publish("https://example.org/fine.ttl", self.endpoint_text(CSV))
        self.assertEqual(self.client.post("/api/endpoints/online", params={"url": "https://example.org/fine.ttl"}).status_code, 200)

        for url, message in [("ftp://example.org/x.ttl", "http:// or https://"),
                             ("https://example.org/with space.ttl", "spaces"),
                             ("https://example.org/fine.ttl", "already in the list"),
                             ("https://example.org/page.html", "not an RDF syntax"),
                             ("https://example.org/broken.ttl", "Could not read"),
                             ("https://example.org/no-source.ttl", "no tss:Source"),
                             ("https://example.org/gone.ttl", "404"),
                             ("https://unknown.example.org/x.ttl", "Could not download")]:
            with self.subTest(url=url):
                response = self.client.post("/api/endpoints/online", params={"url": url})
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.json()["detail"])
        # Nothing was added for them
        self.assertEqual(list(online_registry.load_online_endpoints()), ["online-1"])

    def test_a_local_endpoints_own_address_is_no_online_endpoint(self):
        # The address the browser reads a local endpoint from is that endpoint's IRI in the
        # catalogue already: registering it again would give one dataset two ids
        address = str(local_address(CSV))
        self.publish(address, self.endpoint_text(CSV))
        response = self.client.post("/api/endpoints/online", params={"url": address})
        self.assertEqual(response.status_code, 400)
        self.assertIn("This is a local endpoint", response.json()["detail"])
        self.assertIn(CSV, response.json()["detail"])
        self.assertEqual(self.requested, [])  # refused before anything was downloaded
        self.assertEqual(online_registry.load_online_endpoints(), {})
        # Every dataset of the catalogue still has exactly one id and one distribution
        catalog = self.catalog()
        for dataset in catalog.objects(URIRef(BASE + "api/catalog"), DCAT.dataset):
            self.assertEqual(len(list(catalog.objects(dataset, DCTERMS.identifier))), 1)
            self.assertEqual(len(list(catalog.objects(dataset, DCAT.distribution))), 1)

    def test_only_online_endpoints_can_be_removed(self):
        for endpoint_id in (CSV, "online-7"):
            with self.subTest(endpoint=endpoint_id):
                response = self.client.delete(f"/api/endpoints/{endpoint_id}")
                self.assertEqual(response.status_code, 404)
                self.assertIn("not an online endpoint", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
