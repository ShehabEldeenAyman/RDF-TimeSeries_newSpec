"""The backend reads every metadata document with the same base IRI as the browser, so relative
IRIs in it, like <#value> or <../data/probe.csv>, are the same IRIs on both sides.
Run from backend/: python -B -m unittest -v test_base_iri.

The browser (frontend/src/App.jsx) parses a document with the IRI the catalogue gives it:
- a local endpoint: its address on this backend, {base}files/metadata%20endpoint/<id>.ttl;
- an online endpoint: the address it was registered with, also when its server redirects.
These tests read the probe document below the way the browser does (rdflib, with the
catalogue's IRI as base IRI), and check that the field IRIs in the answers of /query,
/filter-columns, /rows, /sql/tables, POST /rdf and the SHACL report are those IRIs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient
from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import DCAT, DCTERMS, SH

import main
import paths
import files
import documents
from rdf_generation import TSS
from test_rdf_documents import FakeResponse

SOSA_OBSERVED_PROPERTY = URIRef("http://www.w3.org/ns/sosa/observedProperty")
BASE = "http://testserver/"  # the address TestClient gives the backend

# A metadata document whose source and fields have relative IRIs. Its data file is
# <../data/probe.csv>: next to the "metadata endpoint" folder for a local endpoint.
PROBE = """
@prefix tss: <https://w3id.org/tss#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# The document's own last edit, as in every metadata endpoint (SHACL D1 wants exactly one).
# <> is the document itself, so it is relative too.
<> tss:modifiedAt "2026-10-22T14:13:56Z"^^xsd:dateTime .

<#source> a tss:Source ;
    rdfs:label "Probe with relative IRIs"@en ;
    tss:embedded false ;
    tss:mutable false ;
    tss:format tss:CSV ;
    tss:dataFile <../data/probe.csv> ;
    tss:timeField <#time> ;
    tss:hasField <#time>, <#value>, <#code> .

<#time> a tss:Field ; tss:columnName "time" ; tss:datatype xsd:dateTime .
<#value> a tss:Field ; tss:columnName "value" ; tss:datatype xsd:double ; tss:observedProperty <#temperature> .
<#code> a tss:Field ; tss:columnName "code" ; tss:datatype xsd:integer ; tss:describesField <#value> .
"""
PROBE_CSV = "time,value,code\n2021-03-03T23:15:00Z,1.5,1\n2021-03-03T23:30:00Z,2.5,2\n2021-03-03T23:45:00Z,3.5,1\n"

# The online copy: registered at a w3id-like address that redirects to a mirror
REGISTERED = "https://w3id.example.org/tss/probe"
MIRROR = "https://mirror.example.org/x/y/probe.ttl"
ONLINE_DATA = "https://w3id.example.org/data/probe.csv"  # <../data/probe.csv> read against REGISTERED


class SameIrisAsTheBrowser(unittest.TestCase):
    def setUp(self):
        # A demo 7 folder of its own, with the probe as its only local endpoint
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "metadata endpoint").mkdir()
        (root / "data").mkdir()
        (root / "metadata endpoint" / "probe.ttl").write_text(PROBE, encoding="utf-8")
        (root / "data" / "probe.csv").write_text(PROBE_CSV, encoding="utf-8")
        for name, value in [("DEMO_DIR", root), ("METADATA_DIR", root / "metadata endpoint"),
                            ("ONLINE_ENDPOINTS_FILE", root / "online_endpoints.ttl")]:
            patcher = patch.object(paths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        # A fake web: address -> (final address after redirects, Content-Type, text)
        self.web = {REGISTERED: (MIRROR, "text/turtle", PROBE), ONLINE_DATA: (ONLINE_DATA, "text/csv", PROBE_CSV)}
        patcher = patch.object(files.requests, "get", side_effect=self.fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def fake_get(self, url, **options):
        if url not in self.web:
            raise requests.ConnectionError(f"No route to {url}")
        final_url, content_type, text = self.web[url]
        return FakeResponse(final_url, 200, content_type, text)

    def get(self, address, **params):
        response = self.client.get(address, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def browser_reads(self, endpoint_id):
        """The probe as the browser reads it: parsed with the IRI the catalogue gives the endpoint.
        Gives its fields by column name: {"time": <IRI>, "value": <IRI>, "code": <IRI>}, and the graph."""
        catalog = Graph().parse(data=self.client.get("/api/catalog").text, format="turtle")
        dataset = catalog.value(predicate=DCTERMS.identifier, object=Literal(endpoint_id))
        self.assertIsInstance(dataset, URIRef)
        graph = Graph().parse(data=PROBE, format="turtle", publicID=str(dataset))
        source = graph.value(predicate=RDF.type, object=TSS.Source)
        fields = {}
        for field in graph.objects(source, TSS.hasField):
            fields[str(graph.value(field, TSS.columnName))] = str(field)
        return fields, graph

    def check_answers(self, endpoint_id):
        """Every answer names the fields by the IRIs the browser reads"""
        fields, browser = self.browser_reads(endpoint_id)
        address = f"/api/endpoints/{endpoint_id}"

        # /query and /filter-columns take the browser's field IRI ...
        query = self.get(address + "/query", field=fields["value"])
        self.assertEqual([reading["value"] for reading in query["readings"]], [1.5, 2.5, 3.5])
        # ... and answer with it
        self.assertEqual(self.get(address + "/filter-columns", field=fields["value"]),
                         [{"field": fields["code"], "values": ["1", "2"]}])
        self.assertEqual(self.get(address + "/rows")["columns"], [fields["time"], fields["value"], fields["code"]])

        tables = self.get(address + "/sql/tables")
        columns = {}
        for column in tables["tables"][0]["columns"]:
            columns[column["name"]] = column["field"]
        self.assertEqual(columns, fields)
        for example in tables["examples"]:
            if example["field"] is not None:
                self.assertIn(example["field"], fields.values())

        response = self.client.post(address + "/rdf")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["series"], [{"field": fields["value"], "observations": 3}])
        # The observations name the field's observed property <#temperature> as the browser reads it
        preview = Graph().parse(data=self.client.get(response.json()["previewUrl"]).text, format="turtle")
        temperature = browser.value(URIRef(fields["value"]), TSS.observedProperty)
        self.assertEqual(set(preview.objects(None, SOSA_OBSERVED_PROPERTY)), {temperature})

        # The SHACL report: the probe misses tss:from and more, so it has results; their focus
        # nodes are the source or fields the browser knows
        report = Graph().parse(data=self.client.get(address + "/shacl").text, format="turtle")
        focus_nodes = set(report.objects(None, SH.focusNode))
        self.assertGreater(len(focus_nodes), 0)
        for node in focus_nodes:
            self.assertIn((node, None, None), browser)

    def test_a_local_endpoint_is_read_with_its_address_on_this_backend(self):
        fields, browser = self.browser_reads("probe")
        self.assertEqual(fields["value"], BASE + "files/metadata%20endpoint/probe.ttl#value")
        self.check_answers("probe")
        # Its <../data/probe.csv> is {base}files/data/probe.csv, which is the file data/probe.csv
        # (also without a request: then the base is DEFAULT_BASE, http://localhost:5174/)
        graph, source = documents.load_source("probe")
        self.assertIn(URIRef("http://localhost:5174/files/data/probe.csv"), set(graph.objects(None, TSS.dataFile)))
        self.assertEqual(source["dataFile"], "data/probe.csv")

    def test_an_online_endpoint_is_read_with_its_registered_address(self):
        response = self.client.post("/api/endpoints/online", params={"url": REGISTERED})
        self.assertEqual(response.status_code, 200, response.text)
        fields, browser = self.browser_reads("online-1")
        self.assertEqual(fields["value"], REGISTERED + "#value")
        self.check_answers("online-1")
        # Its data file is read against the registered address too, and stays a web address
        graph, source = documents.load_source("online-1")
        self.assertEqual(source["dataFile"], ONLINE_DATA)
        # The IRI against the mirror (where the redirect ends) is no field of this source
        response = self.client.get("/api/endpoints/online-1/query", params={"field": MIRROR + "#value"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("Unknown field", response.json()["detail"])
        # The document route names its IRI in Content-Location
        document = self.client.get("/api/endpoints/online-1/document")
        self.assertEqual(document.headers["content-location"], REGISTERED)
        # The catalogue's dataset IRI is the registered address (the browser's base IRI)
        catalog = Graph().parse(data=self.client.get("/api/catalog").text, format="turtle")
        self.assertIn((URIRef(REGISTERED), RDF.type, DCAT.Dataset), catalog)


if __name__ == "__main__":
    unittest.main()
