"""Exercise the portable CLI, its RDF semantics, and failed-conversion handling."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from rdflib import BNode, Graph, Namespace, RDF, URIRef
from rdflib.namespace import XSD

DIRECTORY = Path(__file__).resolve().parent
DEMO = DIRECTORY.parents[1]
SOSA = Namespace("http://www.w3.org/ns/sosa/")
EX = Namespace("https://example.org/")


class StandaloneCli(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        portable = self.root / "portable"
        portable.mkdir()
        self.script = portable / "tss2rdf.py"
        shutil.copy2(DIRECTORY / "tss2rdf.py", self.script)
        self.working = self.root / "working directory"
        self.working.mkdir()
        self.output = self.working / "output" / "observations.ttl"
        self.tss = self.working / "external.ttl"
        self.csv = self.working / "readings.csv"
        self.csvw = self.working / "readings.csv-metadata.json"
        self.embedded = DEMO / "metadata endpoint" / "waterinfo_289441042_tss2_points_embedded.ttl"
        self.tss.write_text('''
@prefix tss: <https://w3id.org/tss#> .
@prefix ex: <https://example.org/> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:group a tss:SnippetGroup ; tss:embedded false ; tss:producedBy ex:logger ;
    tss:dataFile <https://original.example/readings.csv> ;
    tss:describedby <https://original.example/metadata.json> ;
    tss:snippetTemplate [
        tss:pointType sosa:Observation ; tss:timeColumn "time" ;
        tss:from "2025-01-01T00:00:00Z"^^xsd:dateTime ;
        tss:until "2025-01-01T00:30:00Z"^^xsd:dateTime ;
        tss:about [sosa:madeBySensor ex:sensor-b] ] ;
    tss:hasSnippet ex:series-a, ex:series-b .
ex:series-a a tss:Snippet ; tss:valueColumn "a" ;
    tss:about [sosa:madeBySensor ex:sensor-a] .
ex:series-b a tss:Snippet ; tss:valueColumn "b" .
''', encoding="utf-8")
        self.csv.write_text('Timestamp;A;B;Quality\n2025-01-01T00:00:00Z;1.5;2;"good;review"\n2025-01-01T00:15:00Z;;3;good\n2025-01-01T00:30:00Z;4.5;4;good\n', encoding="utf-8")
        self.metadata = {
            "@context": "http://www.w3.org/ns/csvw", "url": "not-the-input.csv",
            "dialect": {"delimiter": ";", "header": True},
            "tableSchema": {"columns": [
                {"name": "time", "titles": "Timestamp", "datatype": "dateTime", "propertyUrl": str(SOSA.resultTime)},
                {"name": "a", "titles": "A", "datatype": {"base": "double", "minimum": 0}, "propertyUrl": str(EX.a)},
                {"name": "b", "titles": "B", "datatype": "integer", "propertyUrl": str(EX.b)},
                {"name": "quality", "titles": "Quality", "datatype": "string", "propertyUrl": str(EX.quality)},
                {"name": "row_type", "virtual": True, "propertyUrl": "rdf:type", "valueUrl": str(SOSA.Observation)},
                {"name": "source_row", "virtual": True, "propertyUrl": str(EX.row), "valueUrl": "rows/{_row}/{_sourceRow}"},
            ]},
        }
        self.csvw.write_text(json.dumps(self.metadata), encoding="utf-8")

    def invoke(self, *args):
        return subprocess.run([sys.executable, "-B", str(self.script), *map(str, args)],
                              cwd=self.working, capture_output=True, text=True,
                              encoding="utf-8", timeout=30)

    def external_args(self, output=None):
        return ["--mode", "external", "--tss", self.tss, "--csv", self.csv,
                "--csvw", self.csvw, "--output", output or self.output]

    def assert_success(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stderr)
        stats = json.loads(completed.stdout)
        graph = Graph().parse(stats["output"], format="turtle")
        self.assertEqual(stats["triples"], len(graph))
        self.assertEqual(stats["bytes"], Path(stats["output"]).stat().st_size)
        text = Path(stats["output"]).read_text(encoding="utf-8")
        self.assertNotIn("https://w3id.org/tss", text)
        self.assertNotIn("@prefix tss:", text)
        self.assertNotIn("logger", text)
        self.assertFalse(list(graph.subjects(RDF.type, SOSA.ObservationCollection)))
        self.assertFalse(list(graph.triples((None, SOSA.hasMember, None))))
        readings = set(graph.subjects(RDF.type, SOSA.Observation))
        self.assertEqual(len(readings), stats["points"])
        self.assertTrue(all(isinstance(node, BNode) for node in readings))
        return stats, graph

    def test_real_embedded_dataset_from_a_standalone_copy(self):
        stats, graph = self.assert_success(self.invoke("--mode", "embedded", "--tss", self.embedded,
                                                      "--output", self.output))
        self.assertEqual(stats["points"], 10)
        self.assertEqual(stats["triples"], 55)
        self.assertEqual(len(stats["series"]), 2)
        values = list(graph.objects(None, SOSA.hasSimpleResult))
        self.assertEqual(sum(value.datatype == XSD.double for value in values), 5)
        self.assertEqual(sum(value.datatype == XSD.string for value in values), 5)

    def test_external_mappings_separate_series_inheritance_and_time_windows(self):
        stats, graph = self.assert_success(self.invoke(*self.external_args()))
        self.assertEqual(stats["points"], 3)
        self.assertEqual(stats["triples"], 18)
        self.assertEqual([series["points"] for series in stats["series"]], [1, 2])
        for node in graph.subjects(RDF.type, SOSA.Observation):
            a, b = graph.value(node, EX.a), graph.value(node, EX.b)
            self.assertNotEqual(a is None, b is None)
            self.assertEqual(graph.value(node, SOSA.resultTime).datatype, XSD.dateTime)
            if a is not None:
                self.assertEqual(a.datatype, XSD.double)
                self.assertEqual(graph.value(node, SOSA.madeBySensor), EX["sensor-a"])
            else:
                self.assertEqual(b.datatype, XSD.integer)
                self.assertEqual(graph.value(node, SOSA.madeBySensor), EX["sensor-b"])
        self.assertIn("good;review", {str(value) for value in graph.objects(None, EX.quality)})
        self.assertEqual(set(graph.objects(None, EX.row)), {
            URIRef("https://original.example/rows/1/2"), URIRef("https://original.example/rows/2/3"),
        })

    def test_missing_external_paths_and_irrelevant_embedded_paths_are_usage_errors(self):
        for args in (["--mode", "external", "--tss", self.tss],
                     ["--mode", "embedded", "--tss", self.embedded, "--csv", self.csv]):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)
        self.assertFalse(self.output.exists())

    def test_mode_must_match_the_tss_storage_flag(self):
        for args in (["--mode", "embedded", "--tss", self.tss, "--output", self.output],
                     ["--mode", "external", "--tss", self.embedded, "--csv", self.csv,
                      "--csvw", self.csvw, "--output", self.output]):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("conflicts with tss:embedded", result.stderr)
                self.assertFalse(self.output.exists())

    def test_bad_delimiter_or_values_preserve_existing_output(self):
        self.output.parent.mkdir()
        self.output.write_text("previous graph", encoding="utf-8")
        for broken in ("missing delimiter", "wrong delimiter", "bad value"):
            with self.subTest(broken=broken):
                metadata = json.loads(json.dumps(self.metadata))
                if broken == "missing delimiter":
                    del metadata["dialect"]["delimiter"]
                elif broken == "wrong delimiter":
                    metadata["dialect"]["delimiter"] = ","
                else:
                    metadata["tableSchema"]["columns"][1]["datatype"]["minimum"] = 2
                self.csvw.write_text(json.dumps(metadata), encoding="utf-8")
                result = self.invoke(*self.external_args())
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.output.read_text(encoding="utf-8"), "previous graph")
                self.assertFalse(list(self.output.parent.glob(".tss2rdf-*")))

    def test_output_cannot_overwrite_an_input(self):
        for input_path in (self.tss, self.csv, self.csvw):
            with self.subTest(input=input_path):
                before = input_path.read_bytes()
                result = self.invoke(*self.external_args(output=input_path))
                self.assertEqual(result.returncode, 1)
                self.assertIn("different from every input", result.stderr)
                self.assertEqual(input_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
