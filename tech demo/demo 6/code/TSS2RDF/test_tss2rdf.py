"""Tests for the converter: python -B -m unittest -v test_tss2rdf (from code/TSS2RDF/)."""
from pathlib import Path
import tempfile
import unittest

import duckdb
from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import XSD

import tss2rdf

SOSA = Namespace("http://www.w3.org/ns/sosa/")
QUDT = Namespace("http://qudt.org/schema/qudt/")
EX = Namespace("https://example.org/ex#")
DEMO = Path(__file__).resolve().parents[2]

# A small source with: a time field, a measured field, a describing field with its own
# observed property, and a field without one (which must not become observations)
METADATA = """
@prefix tss: <https://w3id.org/tss#> .
@prefix ex: <https://example.org/ex#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:source a tss:Source ;
    rdfs:label "Test source" ;
    tss:embedded false ;
    tss:dataFile <DATA> ;
    tss:format FORMAT ;
    tss:delimiter ";" ;
    tss:header HEADER ;
    tss:from "2025-01-01T00:00:00Z"^^xsd:dateTime ;
    tss:until "2025-01-01T01:00:00Z"^^xsd:dateTime ;
    tss:timeField ex:time ;
    tss:key ex:time ;
    tss:hasField ex:time, ex:value, ex:flag, ex:code .

ex:time a tss:Field ; tss:columnName "time" ; tss:columnIndex 0 ; tss:datatype xsd:dateTime .
ex:value a tss:Field ; tss:columnName "value" ; tss:columnIndex 1 ; tss:datatype xsd:double ;
    tss:observedProperty ex:temperature ; tss:unit ex:degree ; tss:sensor ex:sensor ; tss:featureOfInterest ex:river .
ex:flag a tss:Field ; tss:columnName "flag" ; tss:columnIndex 2 ; tss:datatype xsd:string ;
    tss:observedProperty ex:flagColour ; tss:describesField ex:value .
ex:code a tss:Field ; tss:columnName "code" ; tss:columnIndex 3 ; tss:datatype xsd:integer ;
    tss:describesField ex:value .

ex:sensor rdfs:label "Thermometer" ; tss:embedded false .
"""

ROWS = [
    ["2024-12-31T23:45:00Z", "1.5", "green", "100"],   # before tss:from: left out
    ["2025-01-01T00:00:00Z", "2.25", "green", "100"],  # from is included
    ["2025-01-01T00:15:00Z", "", "red", "200"],        # empty value: no value observation
    ["2025-01-01T00:30:00Z", "3.0E1", "", "100"],      # empty flag: no flag observation
    ["2025-01-01T01:00:00Z", "4", "green", "100"],     # until is not included
]


class Converter(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "observations.ttl"

    def write_csv(self, rows, header=True):
        lines = []
        if header:
            lines.append("time;value;flag;code")
        for row in rows:
            lines.append(";".join(row))
        data = self.root / "data.csv"
        data.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.write_metadata(data, "tss:CSV", header)

    def write_metadata(self, data, file_format, header=True):
        text = METADATA.replace("<DATA>", f"<{data.as_uri()}>").replace("FORMAT", file_format)
        text = text.replace("HEADER", "true" if header else "false")
        path = self.root / "metadata.ttl"
        path.write_text(text, encoding="utf-8")
        return path

    def observations(self):
        graph = Graph().parse(self.output, format="turtle")
        found = []
        for observation in graph.subjects(RDF.type, SOSA.Observation):
            found.append((
                graph.value(observation, SOSA.observedProperty),
                graph.value(observation, SOSA.resultTime),
                graph.value(observation, SOSA.hasSimpleResult),
            ))
        return graph, sorted(found)

    def test_csv_rows_become_observations_inside_the_window(self):
        stats = tss2rdf.convert(self.write_csv(ROWS), self.output)
        graph, found = self.observations()
        self.assertEqual(stats["observations"], 4)
        self.assertEqual([(s["column"], s["observations"]) for s in stats["series"]], [("value", 2), ("flag", 2)])
        self.assertEqual(len(found), 4)
        # the measured field carries its sensor, feature and unit; the flag only its own property
        for observation in graph.subjects(SOSA.observedProperty, EX.temperature):
            self.assertEqual(graph.value(observation, SOSA.madeBySensor), EX.sensor)
            self.assertEqual(graph.value(observation, SOSA.hasFeatureOfInterest), EX.river)
            self.assertEqual(graph.value(observation, QUDT.hasUnit), EX.degree)
        for observation in graph.subjects(SOSA.observedProperty, EX.flagColour):
            self.assertIsNone(graph.value(observation, SOSA.madeBySensor))
        # the value keeps its text from the CSV ("3.0E1", not 30.0)
        self.assertIn('"3.0E1"^^xsd:double', self.output.read_text(encoding="utf-8"))
        # a field without tss:observedProperty (code) is not in the RDF
        self.assertNotIn('"100"', self.output.read_text(encoding="utf-8"))
        self.assertEqual(len(graph), stats["triples"])

    def test_window_and_empty_cells(self):
        tss2rdf.convert(self.write_csv(ROWS), self.output)
        graph, found = self.observations()
        # (rdflib reads "...Z" back as "...+00:00", so compare the moments themselves)
        times = sorted({time.toPython().strftime("%H:%M") for prop, time, value in found})
        self.assertEqual(times, ["00:00", "00:15", "00:30"])
        values = sorted(value.toPython() for prop, time, value in found if prop == EX.temperature)
        self.assertEqual(values, [2.25, 30.0])  # the empty value at 00:15 gave no observation
        flags = sorted(str(value) for prop, time, value in found if prop == EX.flagColour)
        self.assertEqual(flags, ["green", "red"])     # the empty flag at 00:30 neither

    def test_sensor_description_is_copied_without_tss_terms(self):
        tss2rdf.convert(self.write_csv(ROWS), self.output)
        graph, found = self.observations()
        self.assertIn((EX.sensor, URIRef("http://www.w3.org/2000/01/rdf-schema#label"), Literal("Thermometer")), graph)
        for triple in graph:
            for term in triple:
                self.assertFalse(str(term).startswith("https://w3id.org/tss#"), triple)

    def test_headerless_csv_uses_column_indexes(self):
        stats = tss2rdf.convert(self.write_csv(ROWS, header=False), self.output)
        self.assertEqual(stats["observations"], 4)

    def test_parquet_keeps_its_values(self):
        data = self.root / "data.parquet"
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE t (time TIMESTAMPTZ, value DOUBLE, flag VARCHAR, code BIGINT)")
            connection.execute("INSERT INTO t VALUES ('2025-01-01T00:00:00Z', 2.25, 'green', 100), ('2025-01-01T00:15:00Z', NULL, 'red', 200)")
            connection.sql("SELECT * FROM t").write_parquet(str(data))
        stats = tss2rdf.convert(self.write_metadata(data, "tss:Parquet"), self.output)
        graph, found = self.observations()
        self.assertEqual(stats["observations"], 3)  # 2 flags + 1 value (the other value is NULL)
        self.assertIn((EX.temperature, Literal("2025-01-01T00:00:00Z", datatype=XSD.dateTime), Literal("2.25", datatype=XSD.double)), found)

    def test_a_value_that_does_not_fit_stops_and_keeps_the_old_output(self):
        self.output.write_text("earlier result", encoding="utf-8")
        rows = [["2025-01-01T00:00:00Z", "not a number", "green", "100"]]
        with self.assertRaises(ValueError) as caught:
            tss2rdf.convert(self.write_csv(rows), self.output)
        self.assertIn("CSV line 2", str(caught.exception))
        self.assertIn("xsd:double", str(caught.exception))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "earlier result")

    def test_a_bad_time_stops(self):
        rows = [["2025-01-01 00:00", "1", "green", "100"]]
        with self.assertRaises(ValueError) as caught:
            tss2rdf.convert(self.write_csv(rows), self.output)
        self.assertIn("xsd:dateTime", str(caught.exception))

    def test_the_demo_files(self):
        """The demo's embedded file, and the external CSV file (only its row count is checked)"""
        embedded = tss2rdf.convert(DEMO / "metadata endpoint" / "waterinfo_289441042_tss2_points_embedded.ttl", self.output)
        self.assertEqual(embedded["observations"], 10)
        self.assertEqual(embedded["data"], "tss:points")
        external = tss2rdf.convert(DEMO / "metadata endpoint" / "waterinfo_289423042_tss2.ttl", self.output)
        self.assertEqual([(s["column"], s["observations"]) for s in external["series"]],
                         [("Value", 94853), ("Quality Code Color", 94853)])

    def test_command_line(self):
        path = self.write_csv(ROWS)
        self.assertEqual(tss2rdf.main(["--tss", str(path), "--output", str(self.output)]), 0)
        self.assertTrue(self.output.is_file())


if __name__ == "__main__":
    unittest.main()
