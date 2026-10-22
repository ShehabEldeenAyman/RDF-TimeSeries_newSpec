"""The data routes read the metadata as RDF terms, so the prefixes a metadata file uses
must not change any answer. Run from backend/: python -B -m unittest -v test_prefix_independence.

Two endpoints (the grootdok CSV and the Parquet one) are written again with rdflib, into a
temporary folder, in two other ways:
  - "other prefixes": tss: is t:, xsd: is x:, ex: is e:, rdfs: is r:
  - "no prefixes": every IRI in full (N-Triples, which is also valid Turtle)
Their tss:dataFile is the absolute file:/// IRI of the real data file. The endpoint lookup
(load_graph) is patched to read these copies, and the answers of /data, /rows, /query,
/filter-columns and /sql (including the DuckDB column types and the metadata tables)
must be the same as for the original files."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from rdflib import Graph, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

import main

CSV = "waterinfo_289423042_tss2"
PARQUET = "waterinfo_289429042_tss2_parquet"
EX = Namespace("https://example.org/ex#")
VALUE_FIELDS = {CSV: EX["grootdok-ec20-field"], PARQUET: EX["rodenhuizedok-ec20-field"]}
TABLES = {CSV: "waterinfo_289423042", PARQUET: "waterinfo_289429042"}
# The "Quality Code" field of each source, and a code that occurs in its data file, to filter on
QUALITY_FIELDS = {CSV: EX["grootdok-quality-code-field"], PARQUET: EX["rodenhuizedok-quality-code-field"]}
QUALITY_CODES = {CSV: "180", PARQUET: "100"}


def other_prefixes(graph):
    """A copy of a graph with other prefixes: t: for tss:, x: for xsd:, ..."""
    copy = Graph(bind_namespaces="none")
    copy.bind("t", main.TSS)
    copy.bind("x", XSD)
    copy.bind("e", EX)
    copy.bind("r", RDFS)
    for triple in graph:
        copy.add(triple)
    return copy.serialize(format="turtle")


def no_prefixes(graph):
    """A graph with every IRI written in full: N-Triples, which is also valid Turtle"""
    return graph.serialize(format="nt")


class PrefixIndependence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        cls.copies = {}  # (endpoint, way) -> the Turtle file
        for endpoint_id in (CSV, PARQUET):
            # The original is parsed with its address on this backend as base IRI (like the
            # browser does), so its tss:dataFile <../data/x.csv> is .../files/data/x.csv. The copies
            # name the real data file by its absolute file:/// IRI instead (a local endpoint may).
            original = main.load_graph(endpoint_id)
            source = original.value(predicate=RDF.type, object=main.TSS.Source)
            location = main.read_source(original, main.local_data_address(endpoint_id, main.DEFAULT_BASE))["dataFile"]
            original.set((source, main.TSS.dataFile, URIRef((main.DEMO_DIR / location).as_uri())))
            for way, write in [("other prefixes", other_prefixes), ("no prefixes", no_prefixes)]:
                path = folder / f"{endpoint_id} {way}.ttl"
                path.write_text(write(original), encoding="utf-8")
                cls.copies[(endpoint_id, way)] = path

    def setUp(self):
        self.client = TestClient(main.app)

    def answers(self, endpoint_id, path=None):
        """The answers of the data routes for an endpoint; with path: read from that file instead"""
        if path is None:
            return self.ask(endpoint_id)
        text = path.read_text(encoding="utf-8")

        def load_copy(asked_id, base=None):
            return main.parse_document(text, "text/turtle", path.as_uri(), path.name)

        with patch.object(main, "load_graph", side_effect=load_copy):
            return self.ask(endpoint_id)

    def get(self, address, **params):
        response = self.client.get(address, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def post_sql(self, endpoint_id, sql):
        response = self.client.post(f"/api/endpoints/{endpoint_id}/sql", json={"sql": sql})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ask(self, endpoint_id):
        base = f"/api/endpoints/{endpoint_id}"
        field = str(VALUE_FIELDS[endpoint_id])
        table = TABLES[endpoint_id]
        answers = {"data": self.get(base + "/data", lines=5)}
        answers["rows"] = self.get(base + "/rows", limit=5)

        answers["query"] = self.get(base + "/query", field=field, end="2021-03-10T00:00:00Z", limit=20)
        answers["hourly"] = self.get(base + "/query", field=field, end="2021-03-10T00:00:00Z", resample="hour", aggregate="max")
        answers["filter"] = self.get(base + "/query", field=field, filterField=str(QUALITY_FIELDS[endpoint_id]),
                                     filter_value=QUALITY_CODES[endpoint_id], limit=5)
        answers["filter columns"] = self.get(base + "/filter-columns", field=field)

        schema = self.get(base + "/sql/tables")
        answers["sql tables"] = schema["tables"]
        answers["sql examples"] = schema["examples"]
        answers["sql types"] = self.post_sql(endpoint_id, f'SELECT typeof("Timestamp"), typeof("Value"), typeof(ts_id), '
                                                          f'typeof("Quality Code") FROM {table} LIMIT 1')["rows"]
        answers["sql values"] = self.post_sql(endpoint_id, f'SELECT count(*), min("Value"), max("Value"), min("Timestamp"), '
                                                           f'sum(ts_id) FROM {table}')["rows"]
        answers["sql window"] = self.post_sql(endpoint_id, f"SELECT count(*), min(s.from_time), min(s.until_time), bool_and(s.mutable) "
                                                           f'FROM {table} AS d, source AS s '
                                                           f'WHERE d."Timestamp" >= s.from_time AND d."Timestamp" < s.until_time')["rows"]
        answers["sql roles"] = self.post_sql(endpoint_id, "SELECT column_name, role, describes FROM fields ORDER BY column_name")["rows"]
        # The metadata tables write IRIs in full, so they are the same whatever the prefixes
        answers["sql source"] = self.post_sql(endpoint_id, "SELECT * FROM source")["rows"]
        answers["sql fields"] = self.post_sql(endpoint_id, "SELECT * FROM fields ORDER BY field")["rows"]
        return answers

    def test_the_copies_really_use_other_prefixes(self):
        for endpoint_id in (CSV, PARQUET):
            other = self.copies[(endpoint_id, "other prefixes")].read_text(encoding="utf-8")
            self.assertIn("@prefix t: <https://w3id.org/tss#>", other)
            self.assertNotIn("tss:", other)
            self.assertNotIn("xsd:", other)
            plain = self.copies[(endpoint_id, "no prefixes")].read_text(encoding="utf-8")
            self.assertNotIn("@prefix", plain)
            # The data file is the real one, as an absolute file:/// IRI
            copy = Graph().parse(self.copies[(endpoint_id, "no prefixes")], format="turtle")
            source = copy.value(predicate=RDF.type, object=main.TSS.Source)
            data_file = str(copy.value(source, main.TSS.dataFile))
            self.assertTrue(data_file.startswith("file:///"), data_file)
            data_address = main.local_data_address(endpoint_id, main.DEFAULT_BASE)
            graph, original = main.load_source(endpoint_id)
            self.assertEqual(main.iri_to_location(data_file, data_address), original["dataFile"])

    def test_csv_answers_do_not_depend_on_prefixes(self):
        self.compare(CSV)

    def test_parquet_answers_do_not_depend_on_prefixes(self):
        self.compare(PARQUET)

    def compare(self, endpoint_id):
        original = self.answers(endpoint_id)
        # A few checks that the original answers hold real data (so equal is not "both empty")
        self.assertGreater(original["query"]["summary"]["count"], 0)
        self.assertGreater(original["filter"]["summary"]["count"], 0)
        self.assertEqual(len(original["rows"]["rows"]), 5)
        self.assertEqual(original["sql source"][0][3], "https://w3id.org/tss#" + ("CSV" if endpoint_id == CSV else "Parquet"))
        self.assertEqual(original["sql types"][0][3], "BIGINT")
        self.assertEqual(original["sql types"][0][:2], ["TIMESTAMP WITH TIME ZONE", "DOUBLE"])
        for way in ("other prefixes", "no prefixes"):
            with self.subTest(way=way):
                copy = self.answers(endpoint_id, self.copies[(endpoint_id, way)])
                for name in original:
                    self.assertEqual(copy[name], original[name], name)


if __name__ == "__main__":
    unittest.main()
