"""The backend reads every metadata document with the same base IRI as the browser, so relative
IRIs in it, like <#value> or <../data/probe.csv>, are the same IRIs on both sides.
Run from backend/: python -B -m unittest -v test_base_iri.

The browser (frontend/src/App.jsx) parses a bundled document with the IRI the catalogue gives it:
its address on this backend, {base}files/metadata%20endpoint/<id>.ttl.
These tests read the probe document below the way the browser does (rdflib, with the
catalogue's IRI as base IRI), and check that the field IRIs in the answers of /query,
/filter-columns, /rows, /sql/tables, POST /rdf and the SHACL report are those IRIs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import DCTERMS, SH

import main
import paths
import documents
from rdf_generation import TSS
from test_rdf_generation import use_a_test_export_folder

SOSA_OBSERVED_PROPERTY = URIRef("http://www.w3.org/ns/sosa/observedProperty")
BASE = "http://testserver/"  # the address TestClient gives the backend

# A metadata document whose source and fields have relative IRIs. Its data file is
# <../data/probe.csv>: next to the "metadata endpoint" folder.
PROBE = """
@prefix tss: <https://w3id.org/tss#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<#source> a tss:Source ;
    rdfs:label "Probe with relative IRIs"@en ;
    tss:embedded false ;
    tss:mutable false ;
    tss:modifiedAt "2026-10-22T14:13:56Z"^^xsd:dateTime ;
    tss:format tss:CSV ;
    tss:dataFile <../data/probe.csv> ;
    tss:timeField <#time> ;
    tss:hasField <#time>, <#value>, <#code> .

<#time> a tss:Field ; tss:columnName "time" ; tss:datatype xsd:dateTime .
<#value> a tss:Field ; tss:columnName "value" ; tss:datatype xsd:double ; tss:observedProperty <#temperature> .
<#code> a tss:Field ; tss:columnName "code" ; tss:datatype xsd:integer ; tss:describesField <#value> .
"""
PROBE_CSV = "time,value,code\n2021-03-03T23:15:00Z,1.5,1\n2021-03-03T23:30:00Z,2.5,2\n2021-03-03T23:45:00Z,3.5,1\n"


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
        for name, value in [("DEMO_DIR", root), ("METADATA_DIR", root / "metadata endpoint")]:
            patcher = patch.object(paths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        use_a_test_export_folder(self)  # POST /rdf writes its files in a folder of this test
        self.client = TestClient(main.app)

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

    def test_a_field_iri_read_against_another_address_is_unknown(self):
        # The same <#value>, read against another base IRI, is another IRI: no field of this source
        response = self.client.get("/api/endpoints/probe/query", params={"field": "https://example.org/probe.ttl#value"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("Unknown field", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
