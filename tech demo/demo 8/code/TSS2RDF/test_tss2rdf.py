"""Tests for the converter: python -B -m unittest -v test_tss2rdf (from code/TSS2RDF/)."""
import collections
import contextlib
from datetime import datetime, time, timezone
from decimal import Decimal
import io
import logging
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

import duckdb
import rdflib
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import DCTERMS, XSD

import tss2rdf

# rdflib logs a warning, with a traceback, for every literal whose text does not fit its
# datatype (an empty "" double, "not a number", ...). Some tests make such values on purpose.
logging.getLogger("rdflib.term").setLevel(logging.ERROR)

TSS = Namespace("https://w3id.org/tss#")
SOSA = Namespace("http://www.w3.org/ns/sosa/")
QUDT = Namespace("http://qudt.org/schema/qudt/")
EX = Namespace("https://example.org/ex#")
DEMO = Path(__file__).resolve().parents[2]
EMBEDDED_DEMO = DEMO / "metadata endpoint" / "waterinfo_289441042_tss2_points_embedded.ttl"
CSV_DEMO = DEMO / "metadata endpoint" / "waterinfo_289423042_tss2.ttl"

# A small source with: a time field, a measured field, a describing field with its own
# observed property, and a field without one (which must not become observations).
# Where its data is (a file or embedded points) is set by each test, through rdflib.
METADATA = """
@prefix tss: <https://w3id.org/tss#> .
@prefix ex: <https://example.org/ex#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:source a tss:Source ;
    rdfs:label "Test source" ;
    tss:delimiter ";" ;
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

# The sensor's description has nested blank nodes, and TSS terms that must stay behind
ex:sensor rdfs:label "Thermometer" ; tss:embedded false ;
    ex:calibration [ rdfs:label "Calibrated in 2024" ; ex:by [ rdfs:label "Test lab" ] ] ;
    tss:note [ rdfs:label "Internal note" ] .
"""

ROWS = [
    ["2024-12-31T23:45:00Z", "1.5", "green", "100"],   # before tss:from: left out
    ["2025-01-01T00:00:00Z", "2.25", "green", "100"],  # from is included
    ["2025-01-01T00:15:00Z", "", "red", "200"],        # empty value: no value observation
    ["2025-01-01T00:30:00Z", "3.0E1", "", "100"],      # empty flag: no flag observation
    ["2025-01-01T01:00:00Z", "4", "green", "100"],     # until is not included
]

# The same kind of rows as embedded points: RDF nodes linked to the source with tss:pointOf,
# each value a triple with the field's IRI as predicate (on purpose not in time order)
POINTS = """
[] tss:pointOf ex:source ; ex:time "2025-01-01T00:30:00Z"^^xsd:dateTime ; ex:value "3.0E1"^^xsd:double ;
    ex:flag "red" ; ex:code "200"^^xsd:integer .
[] tss:pointOf ex:source ; ex:time "2024-12-31T23:45:00Z"^^xsd:dateTime ; ex:value "1.5"^^xsd:double ;
    ex:flag "green" .
[] tss:pointOf ex:source ; ex:time "2025-01-01T00:00:00Z"^^xsd:dateTime ; ex:value "2.25"^^xsd:double ;
    ex:flag "green" ; ex:code "100"^^xsd:integer .
"""


def metadata_graph(embedded, data=None, file_format=None, header=True):
    """The test metadata as a graph, with where its data is set through rdflib"""
    graph = Graph().parse(data=METADATA, format="turtle")
    graph.set((EX.source, TSS.embedded, Literal(embedded)))
    if data is not None:
        graph.set((EX.source, TSS.dataFile, URIRef(data.as_uri())))
        graph.set((EX.source, TSS["format"], file_format))
        graph.set((EX.source, TSS.header, Literal(header)))
    return graph


def embedded_text(points, more="", metadata=METADATA):
    """The test metadata as the text of a TSS file with embedded points: tss:embedded true, then
    more Turtle (e.g. another tss:datatype), then the points, appended like new readings"""
    return metadata + "\nex:source tss:embedded true .\n" + more + points


def exact_graph(text):
    """A TSS file's text as a graph, every literal's text kept (as the converter reads its file)"""
    return tss2rdf.parse_exact(Graph(), data=text, format="turtle")


def observations_in(path):
    """The output (N-Triples) parsed back with rdflib, and its (property, time, value) triples, sorted"""
    graph = Graph().parse(path, format="nt")
    found = []
    for observation in graph.subjects(RDF.type, SOSA.Observation):
        found.append((
            graph.value(observation, SOSA.observedProperty),
            graph.value(observation, SOSA.resultTime),
            graph.value(observation, SOSA.hasSimpleResult),
        ))
    return graph, sorted(found)


def parse_exactly(source, rdf_format="nt"):
    """The output parsed back with rdflib, with every literal's text kept as it is written
    ("3.0E1", "...Z"); rdflib's parsers would otherwise rewrite it ("30.0", "...+00:00").
    source: a file (N-Triples), or the text itself for rdf_format "turtle" (the preview)"""
    normalize = rdflib.NORMALIZE_LITERALS
    rdflib.NORMALIZE_LITERALS = False
    try:
        if rdf_format == "turtle":
            return Graph().parse(data=source, format="turtle")
        return Graph().parse(source, format=rdf_format)
    finally:
        rdflib.NORMALIZE_LITERALS = normalize


def observation_shapes(graph):
    """Each observation as the set of its (predicate, object) pairs, counted. An observation is
    a blank node, which has no name to compare, so observations are compared by what they say."""
    shapes = collections.Counter()
    for observation in graph.subjects(RDF.type, SOSA.Observation):
        shapes[frozenset(graph.predicate_objects(observation))] += 1
    return shapes


def per_field(stats):
    """The observations per field, as (field IRI, count)"""
    counts = []
    for series in stats["series"]:
        counts.append((series["field"], series["observations"]))
    return counts


class TempFolder(unittest.TestCase):
    """Every test writes its files in a temporary folder, never in the repository"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "observations.nt"

    def save(self, graph):
        path = self.root / "metadata.ttl"
        graph.serialize(destination=str(path), format="turtle")
        return path


class CsvAndParquet(TempFolder):
    def write_csv(self, rows, header=True):
        lines = []
        if header:
            lines.append("time;value;flag;code")
        for row in rows:
            lines.append(";".join(row))
        data = self.root / "data.csv"
        data.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.save(metadata_graph(False, data, TSS.CSV, header))

    def test_csv_rows_become_observations_inside_the_window(self):
        stats = tss2rdf.convert(self.write_csv(ROWS), self.output)
        graph, found = observations_in(self.output)
        self.assertEqual(stats["observations"], 4)
        self.assertEqual(per_field(stats), [(str(EX.value), 2), (str(EX.flag), 2)])
        self.assertEqual(len(found), 4)
        # the measured field carries its sensor, feature and unit; the flag only its own property
        for observation in graph.subjects(SOSA.observedProperty, EX.temperature):
            self.assertEqual(graph.value(observation, SOSA.madeBySensor), EX.sensor)
            self.assertEqual(graph.value(observation, SOSA.hasFeatureOfInterest), EX.river)
            self.assertEqual(graph.value(observation, QUDT.hasUnit), EX.degree)
        for observation in graph.subjects(SOSA.observedProperty, EX.flagColour):
            self.assertIsNone(graph.value(observation, SOSA.madeBySensor))
        # every observation links back to its source (the tss:Source's IRI)
        for observation in graph.subjects(RDF.type, SOSA.Observation):
            self.assertEqual(list(graph.objects(observation, DCTERMS.source)), [EX.source])
        # a field without tss:observedProperty (code) is not in the RDF
        self.assertEqual(set(graph.objects(None, SOSA.observedProperty)), {EX.temperature, EX.flagColour})
        for value in graph.objects(None, SOSA.hasSimpleResult):
            self.assertNotIn(str(value), ["100", "200"])
        self.assertEqual(len(graph), stats["triples"])

    def test_a_csv_without_delimiter_and_encoding_is_read_with_comma_and_utf8(self):
        # tss:delimiter and tss:encoding are optional: "," and "utf-8" when not stated
        lines = ["time,value,flag,code"]
        for row in ROWS:
            lines.append(",".join(row))
        data = self.root / "data.csv"
        data.write_text("\n".join(lines) + "\n", encoding="utf-8")
        graph = metadata_graph(False, data, TSS.CSV)
        graph.remove((EX.source, TSS.delimiter, None))
        self.assertIsNone(graph.value(EX.source, TSS.encoding))
        stats = tss2rdf.convert(self.save(graph), self.output)
        self.assertEqual(stats["observations"], 4)
        self.assertEqual(per_field(stats), [(str(EX.value), 2), (str(EX.flag), 2)])

    def test_values_keep_their_text(self):
        # (rdflib's Turtle parser rewrites "3.0E1" as "30.0", so look at the literals the converter makes)
        field = {"column": "value", "datatype": XSD.double, "language": None}
        value = tss2rdf.value_literal("3.0E1", field, "CSV line 5", Graph().namespace_manager)
        self.assertEqual((str(value), value.datatype), ("3.0E1", XSD.double))
        time_literal, when = tss2rdf.read_time("2025-01-01T00:30:00Z", "CSV line 5")
        self.assertEqual(str(time_literal), "2025-01-01T00:30:00Z")
        self.assertEqual(when, datetime(2025, 1, 1, 0, 30, tzinfo=timezone.utc))
        # and the output holds the same value
        tss2rdf.convert(self.write_csv(ROWS), self.output)
        graph, found = observations_in(self.output)
        values = sorted(value.toPython() for prop, when, value in found if prop == EX.temperature)
        self.assertEqual(values, [2.25, 30.0])
        self.assertIn('"3.0E1"^^<http://www.w3.org/2001/XMLSchema#double>', self.output.read_text(encoding="utf-8"))

    def test_the_output_holds_exactly_the_expected_triples(self):
        """Every triple of the output, compared with the triples the rows must give, with the
        literals' texts exactly as they are in the CSV file"""
        stats = tss2rdf.convert(self.write_csv(ROWS), self.output)
        graph = parse_exactly(self.output)

        def value_observation(when, value):
            """The pairs of an observation of the value field (its source, sensor, feature and unit)"""
            return frozenset([(RDF.type, SOSA.Observation), (DCTERMS.source, EX.source),
                              (SOSA.observedProperty, EX.temperature),
                              (SOSA.madeBySensor, EX.sensor), (SOSA.hasFeatureOfInterest, EX.river),
                              (QUDT.hasUnit, EX.degree),
                              (SOSA.resultTime, Literal(when, datatype=XSD.dateTime, normalize=False)),
                              (SOSA.hasSimpleResult, Literal(value, datatype=XSD.double, normalize=False))])

        def flag_observation(when, colour):
            """The pairs of an observation of the flag field (its source and its own observed property)"""
            return frozenset([(RDF.type, SOSA.Observation), (DCTERMS.source, EX.source),
                              (SOSA.observedProperty, EX.flagColour),
                              (SOSA.resultTime, Literal(when, datatype=XSD.dateTime, normalize=False)),
                              (SOSA.hasSimpleResult, Literal(colour, datatype=XSD.string, normalize=False))])

        expected = collections.Counter([
            value_observation("2025-01-01T00:00:00Z", "2.25"), flag_observation("2025-01-01T00:00:00Z", "green"),
            flag_observation("2025-01-01T00:15:00Z", "red"), value_observation("2025-01-01T00:30:00Z", "3.0E1"),
        ])
        self.assertEqual(observation_shapes(graph), expected)
        # The rest is the sensor's description: its label, and two nested blank nodes with a label each
        observations = set(graph.subjects(RDF.type, SOSA.Observation))
        rest = []
        for triple in graph:
            if triple[0] not in observations:
                rest.append(triple)
        self.assertEqual(len(rest), 5)
        self.assertEqual(len(graph), 2 * 8 + 2 * 5 + 5)  # 2 value observations (8 triples), 2 flags (5), the description
        self.assertEqual(len(graph), stats["triples"])

    def test_the_output_is_n_triples(self):
        tss2rdf.convert(self.write_csv(ROWS), self.output)
        lines = self.output.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 31)  # one triple per line
        for line in lines:
            self.assertTrue(line.endswith(" ."), line)
        self.assertIn('_:o1 <http://www.w3.org/ns/sosa/resultTime> '
                      '"2025-01-01T00:00:00Z"^^<http://www.w3.org/2001/XMLSchema#dateTime> .', lines)

    def test_small_batches_give_the_same_triples(self):
        """With one observation per batch, every observation still gets a blank node of its own
        (they don't merge across batches), and the output holds the same triples"""
        path = self.write_csv(ROWS)
        one_batch = tss2rdf.convert(path, self.output)
        expected = observation_shapes(parse_exactly(self.output))
        write_batch = tss2rdf.write_batch
        with patch.object(tss2rdf, "BATCH_SIZE", 1), \
                patch.object(tss2rdf, "write_batch", side_effect=write_batch) as written:
            stats = tss2rdf.convert(path, self.output)
        self.assertEqual(written.call_count, 5)  # 4 batches of one observation, and the last one with the description
        graph = parse_exactly(self.output)
        self.assertEqual(len(set(graph.subjects(RDF.type, SOSA.Observation))), 4)
        self.assertEqual(observation_shapes(graph), expected)
        self.assertEqual((len(graph), stats["triples"]), (one_batch["triples"], one_batch["triples"]))

    def test_the_preview_is_turtle_with_the_first_observations(self):
        self.write_csv(ROWS)
        graph = metadata_graph(False, self.root / "data.csv", TSS.CSV)
        result = tss2rdf.generate(graph, self.root / "data.csv", self.output, preview_limit=3)
        self.assertEqual((result["previewObservations"], result["previewTruncated"]), (3, True))
        # Written by rdflib's Turtle writer, with prefixes, and the texts as they are
        self.assertIn('"2.25"^^xsd:double', result["preview"])
        self.assertIn('"2025-01-01T00:00:00Z"^^xsd:dateTime', result["preview"])
        preview = parse_exactly(result["preview"], "turtle")
        shapes = observation_shapes(preview)
        self.assertEqual(sum(shapes.values()), 3)
        # They are observations of the whole output, and the sensor's description comes along
        everything = observation_shapes(parse_exactly(self.output))
        self.assertEqual(shapes - everything, collections.Counter())
        self.assertIn((EX.sensor, RDFS.label, Literal("Thermometer")), preview)

    def test_window_and_empty_cells(self):
        tss2rdf.convert(self.write_csv(ROWS), self.output)
        graph, found = observations_in(self.output)
        # (rdflib reads "...Z" back as "...+00:00", so compare the moments themselves)
        times = sorted({when.toPython().strftime("%H:%M") for prop, when, value in found})
        self.assertEqual(times, ["00:00", "00:15", "00:30"])
        values = sorted(value.toPython() for prop, when, value in found if prop == EX.temperature)
        self.assertEqual(values, [2.25, 30.0])  # the empty value at 00:15 gave no observation
        flags = sorted(str(value) for prop, when, value in found if prop == EX.flagColour)
        self.assertEqual(flags, ["green", "red"])     # the empty flag at 00:30 neither

    def test_sensor_description_is_copied_without_tss_terms(self):
        tss2rdf.convert(self.write_csv(ROWS), self.output)
        graph, found = observations_in(self.output)
        self.assertIn((EX.sensor, RDFS.label, Literal("Thermometer")), graph)
        # the nested blank nodes came along, so nothing is left dangling
        calibration = graph.value(EX.sensor, EX.calibration)
        self.assertIsInstance(calibration, BNode)
        self.assertEqual(graph.value(calibration, RDFS.label), Literal("Calibrated in 2024"))
        self.assertEqual(graph.value(graph.value(calibration, EX.by), RDFS.label), Literal("Test lab"))
        # no TSS term, and not the blank node behind a TSS term either
        self.assertNotIn(Literal("Internal note"), set(graph.objects()))
        for triple in graph:
            for term in triple:
                self.assertNotIn(term, TSS, triple)

    def test_copied_blank_nodes_never_merge_with_observations(self):
        # The observations are blank nodes _:o1, _:o2, ... (_:o001, ... with a preview). A blank node
        # of the metadata may keep the label its document gave it (a JSON-LD "@id": "_:o1", or a
        # graph made in code). The copied descriptions get new blank nodes, so the two never merge.
        self.write_csv(ROWS)
        graph = metadata_graph(False, self.root / "data.csv", TSS.CSV)
        for label in ["o1", "o001"]:
            graph.add((EX.sensor, EX.checkedBy, BNode(label)))
            graph.add((BNode(label), RDFS.label, Literal(f"Lab {label}")))
        for preview_limit in [0, 200]:  # the command line (_:o1) and the dashboard (_:o001)
            with self.subTest(preview_limit=preview_limit):
                result = tss2rdf.generate(graph, self.root / "data.csv", self.output, preview_limit=preview_limit)
                self.assertEqual(result["observations"], 4)
                written = Graph().parse(self.output, format="nt")
                outputs = [written]
                if preview_limit > 0:
                    outputs.append(Graph().parse(data=result["preview"], format="turtle"))
                for output in outputs:
                    observations = set(output.subjects(RDF.type, SOSA.Observation))
                    self.assertEqual(len(observations), 4)
                    labs = set(output.objects(EX.sensor, EX.checkedBy))
                    self.assertEqual(len(labs), 2)
                    self.assertEqual(labs & observations, set())  # no lab is an observation
                    for observation in observations:
                        self.assertIsNone(output.value(observation, RDFS.label))
                    self.assertEqual({str(output.value(lab, RDFS.label)) for lab in labs}, {"Lab o1", "Lab o001"})

    def test_headerless_csv_uses_column_indexes(self):
        stats = tss2rdf.convert(self.write_csv(ROWS, header=False), self.output)
        self.assertEqual(stats["observations"], 4)

    def test_parquet_keeps_its_values(self):
        data = self.root / "data.parquet"
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE t (time TIMESTAMPTZ, value DOUBLE, flag VARCHAR, code BIGINT)")
            connection.execute("INSERT INTO t VALUES ('2025-01-01T00:00:00Z', 2.25, 'green', 100), ('2025-01-01T00:15:00Z', NULL, 'red', 200)")
            connection.sql("SELECT * FROM t").write_parquet(str(data))
        stats = tss2rdf.convert(self.save(metadata_graph(False, data, TSS.Parquet)), self.output)
        graph, found = observations_in(self.output)
        self.assertEqual(stats["observations"], 3)  # 2 flags + 1 value (the other value is NULL)
        self.assertIn((EX.temperature, Literal("2025-01-01T00:00:00Z", datatype=XSD.dateTime), Literal("2.25", datatype=XSD.double)), found)

    def test_parquet_decimal_time_and_blob_columns(self):
        """Column types that JSON can't write (a DECIMAL, a TIME, a BLOB) convert without a TypeError"""
        data = self.root / "data.parquet"
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE t (time TIMESTAMPTZ, value DECIMAL(10, 2), flag TIME, code BLOB)")
            connection.execute("INSERT INTO t VALUES ('2025-01-01T00:00:00Z', 1.50, '12:30:00', '\\x01\\xFF'::BLOB)")
            connection.sql("SELECT * FROM t").write_parquet(str(data))
        stats = tss2rdf.convert(self.save(metadata_graph(False, data, TSS.Parquet)), self.output)
        graph, found = observations_in(self.output)
        self.assertEqual(stats["observations"], 2)
        values = {}
        for prop, when, value in found:
            values[prop] = value.toPython()
        self.assertEqual(values, {EX.temperature: 1.5, EX.flagColour: "12:30:00"})

    def test_cell_text_of_parquet_values(self):
        self.assertEqual(tss2rdf.cell_text(Decimal("1.50")), "1.50")
        self.assertEqual(tss2rdf.cell_text(time(12, 30)), "12:30:00")
        self.assertEqual(tss2rdf.cell_text(b"\x01\xff"), "01FF")
        self.assertEqual(tss2rdf.cell_text(4797.72), "4797.72")
        self.assertEqual(tss2rdf.cell_text(float("nan")), "NaN")
        self.assertEqual(tss2rdf.cell_text(float("-inf")), "-INF")
        self.assertEqual(tss2rdf.cell_text(True), "true")
        self.assertEqual(tss2rdf.cell_text(datetime(2025, 1, 1, tzinfo=timezone.utc)), "2025-01-01T00:00:00Z")
        self.assertEqual(tss2rdf.cell_text(None), "")

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

    def test_the_window_must_be_typed_date_times(self):
        data = self.root / "data.csv"
        data.write_text("time;value;flag;code\n", encoding="utf-8")
        graph = metadata_graph(False, data, TSS.CSV)
        graph.set((EX.source, TSS["from"], Literal("2025-01-01T00:00:00Z")))  # a plain string
        with self.assertRaises(ValueError) as caught:
            tss2rdf.convert(self.save(graph), self.output)
        self.assertIn("tss:from must be an xsd:dateTime", str(caught.exception))

    def test_command_line(self):
        path = self.write_csv(ROWS)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(tss2rdf.main(["--tss", str(path), "--output", str(self.output)]), 0)
        self.assertTrue(self.output.is_file())
        self.assertIn("Observations: 4", printed.getvalue())


class EmbeddedPoints(TempFolder):
    def test_the_demo_points_are_15_triples(self):
        graph = exact_graph(EMBEDDED_DEMO.read_text(encoding="utf-8"))
        points = tss2rdf.point_graph(graph, EX["zevenaarshaven-source"])
        self.assertEqual(len(points), 15)  # the tss:pointOf links are not in it
        subjects = set(points.subjects())
        self.assertEqual(len(subjects), 5)  # one blank node per point
        for subject in subjects:
            self.assertIsInstance(subject, BNode)
            self.assertEqual(graph.value(subject, TSS.pointOf), EX["zevenaarshaven-source"])

        time_field = str(EX["zevenaarshaven-time-field"])
        ec20_field = str(EX["zevenaarshaven-ec20-value-field"])
        colour_field = str(EX["zevenaarshaven-quality-color-value-field"])
        expected = []
        for when in [datetime(2021, 3, 3, 23, 15), datetime(2021, 3, 3, 23, 30), datetime(2021, 3, 3, 23, 45),
                     datetime(2021, 3, 4, 0, 30), datetime(2021, 3, 4, 1, 0)]:
            expected.append((time_field, when.replace(tzinfo=timezone.utc), XSD.dateTime))
        for value in [4797.72, 4717.8, 4745.36, 4716.1, 4775.96]:
            expected.append((ec20_field, value, XSD.double))
        for count in range(5):
            expected.append((colour_field, "rgba(0, 128, 0, 1.0)", XSD.string))
        found = []
        for subject, predicate, value in points:
            found.append((str(predicate), value.toPython(), value.datatype))
        self.assertEqual(sorted(found), sorted(expected))
        # The times keep their text, as it is in the file
        times = sorted(str(value) for value in points.objects(None, URIRef(time_field)))
        self.assertEqual(times[0], "2021-03-03T23:15:00Z")

    def test_the_demo_file_converts_and_parses_back(self):
        stats = tss2rdf.convert(EMBEDDED_DEMO, self.output)
        self.assertEqual((stats["observations"], stats["triples"]), (10, 65))
        self.assertIsNone(stats["data"])  # the points embedded in the TSS file
        self.assertEqual(per_field(stats), [(str(EX["zevenaarshaven-ec20-value-field"]), 5),
                                            (str(EX["zevenaarshaven-quality-color-value-field"]), 5)])
        graph, found = observations_in(self.output)
        self.assertEqual(len(found), 10)
        self.assertEqual(len(graph), 65)  # 5 EC20 observations (8 triples each), 5 colour ones (5 each)
        # The output keeps the times as they are in the points: ...Z, not ...+00:00
        text = self.output.read_text(encoding="utf-8")
        self.assertIn('<http://www.w3.org/ns/sosa/resultTime> '
                      '"2021-03-03T23:15:00Z"^^<http://www.w3.org/2001/XMLSchema#dateTime>', text)
        self.assertNotIn("+00:00", text)

    def write_points(self, points, more="", metadata=METADATA):
        """A TSS file with these points (Turtle statements, see embedded_text)"""
        path = self.root / "metadata.ttl"
        path.write_text(embedded_text(points, more, metadata), encoding="utf-8")
        return path

    def test_points_become_observations_in_time_order(self):
        path = self.write_points(POINTS)
        graph = exact_graph(path.read_text(encoding="utf-8"))
        rows = list(tss2rdf.read_rows(graph, tss2rdf.read_source(graph), None))
        times = []
        for where, row in rows:
            times.append(Literal(row[EX.time], datatype=XSD.dateTime).toPython().strftime("%H:%M"))
        self.assertEqual(times, ["23:45", "00:00", "00:30"])

        stats = tss2rdf.convert(path, self.output)
        self.assertEqual(per_field(stats), [(str(EX.value), 2), (str(EX.flag), 2)])  # 23:45 is before tss:from
        graph, found = observations_in(self.output)
        values = sorted(value.toPython() for prop, when, value in found if prop == EX.temperature)
        self.assertEqual(values, [2.25, 30.0])
        self.assertEqual(len(graph), stats["triples"])

    def test_appending_a_point_adds_its_observations(self):
        # A new reading is one more statement at the end of the file
        path = self.write_points(POINTS + '[] tss:pointOf ex:source ; ex:time "2025-01-01T00:45:00Z"^^xsd:dateTime ; '
                                          'ex:value "5"^^xsd:double ; ex:flag "red" .\n')
        self.assertEqual(per_field(tss2rdf.convert(path, self.output)), [(str(EX.value), 3), (str(EX.flag), 3)])

    def test_only_the_points_of_the_source_are_read(self):
        # A node of another source, and a node with values but no tss:pointOf, are no points of ex:source
        points = POINTS + (
            '[] tss:pointOf ex:other ; ex:time "2025-01-01T00:15:00Z"^^xsd:dateTime ; ex:value "7"^^xsd:double .\n'
            '[] ex:time "2025-01-01T00:20:00Z"^^xsd:dateTime ; ex:value "8"^^xsd:double .\n')
        graph = exact_graph(embedded_text(points))
        self.assertEqual(len(set(tss2rdf.point_graph(graph, EX.source).subjects())), 3)
        self.assertEqual(per_field(tss2rdf.convert(self.write_points(points), self.output)),
                         [(str(EX.value), 2), (str(EX.flag), 2)])

    def test_a_property_that_is_no_field_gives_no_observation(self):
        path = self.write_points('[] tss:pointOf ex:source ; ex:time "2025-01-01T00:00:00Z"^^xsd:dateTime ; '
                                 'ex:value "1.5"^^xsd:double ; ex:Flag "red" .\n')
        points = tss2rdf.point_graph(exact_graph(path.read_text(encoding="utf-8")), EX.source)
        self.assertEqual(list(points.objects(None, EX.Flag)), [Literal("red")])
        stats = tss2rdf.convert(path, self.output)
        self.assertEqual(per_field(stats), [(str(EX.value), 1), (str(EX.flag), 0)])

    def test_empty_values_give_no_triple_and_no_observation(self):
        points = ('[] tss:pointOf ex:source ; ex:time "2025-01-01T00:00:00Z"^^xsd:dateTime ; ex:value ""^^xsd:double .\n'
                  '[] tss:pointOf ex:source ; ex:time "2025-01-01T00:15:00Z"^^xsd:dateTime ; ex:value "2"^^xsd:double ; '
                  'ex:flag "green" .\n')
        path = self.write_points(points)
        lifted = tss2rdf.point_graph(exact_graph(path.read_text(encoding="utf-8")), EX.source)
        self.assertEqual(len(lifted), 4)  # the first point has only its time
        self.assertEqual(list(lifted.objects(None, EX.flag)), [Literal("green")])
        stats = tss2rdf.convert(path, self.output)
        self.assertEqual(per_field(stats), [(str(EX.value), 1), (str(EX.flag), 1)])

    def test_a_source_without_points_gives_no_observations(self):
        stats = tss2rdf.convert(self.write_points(""), self.output)
        self.assertEqual(stats["observations"], 0)

    def test_a_point_without_any_field_value_is_skipped(self):
        # Like the browser and the backend: a point with only properties that are no field, or
        # with only empty values, has nothing to convert (SHACL reports it: P1, P6)
        points = ('[] tss:pointOf ex:source ; ex:time "2025-01-01T00:00:00Z"^^xsd:dateTime ; ex:value "2.25"^^xsd:double ; '
                  'ex:flag "green" .\n'
                  '[] tss:pointOf ex:source ; ex:Flag "red" .\n'
                  '[] tss:pointOf ex:source ; ex:time ""^^xsd:dateTime ; ex:value ""^^xsd:double ; ex:note "x" .\n')
        path = self.write_points(points)
        graph = exact_graph(path.read_text(encoding="utf-8"))
        self.assertEqual(len(list(tss2rdf.read_rows(graph, tss2rdf.read_source(graph), None))), 1)
        stats = tss2rdf.convert(path, self.output)
        self.assertEqual(per_field(stats), [(str(EX.value), 1), (str(EX.flag), 1)])

    def test_a_time_typed_xsd_date_time_stamp_needs_a_timezone(self):
        # xsd:dateTimeStamp is an xsd:dateTime that must have a timezone
        form = tss2rdf.LEXICAL_FORMS[XSD.dateTimeStamp]
        self.assertIsNotNone(re.fullmatch(form, "2025-01-01T00:30:00Z"))
        self.assertIsNotNone(re.fullmatch(form, "2025-01-01T01:30:00.5+01:00"))
        self.assertIsNone(re.fullmatch(form, "2025-01-01T00:30:00"))
        # The time field's datatype in the test metadata becomes xsd:dateTimeStamp
        stamp = METADATA.replace('tss:columnIndex 0 ; tss:datatype xsd:dateTime .', 'tss:columnIndex 0 ; tss:datatype xsd:dateTimeStamp .')
        self.assertNotEqual(stamp, METADATA)
        stats = tss2rdf.convert(self.write_points(POINTS, metadata=stamp), self.output)
        self.assertEqual(per_field(stats), [(str(EX.value), 2), (str(EX.flag), 2)])
        # Its observations' result times are xsd:dateTime, like those of any other time
        graph_out, found = observations_in(self.output)
        self.assertEqual({when.datatype for prop, when, value in found}, {XSD.dateTime})
        # A time without a timezone stops the conversion (as xsd:dateTime it would be UTC)
        no_zone = '[] tss:pointOf ex:source ; ex:time "2025-01-01T00:30:00"^^xsd:dateTime ; ex:value "1"^^xsd:double .\n'
        with self.assertRaises(ValueError) as caught:
            tss2rdf.convert(self.write_points(no_zone, metadata=stamp), self.output)
        self.assertIn("not an xsd:dateTimeStamp", str(caught.exception))
        self.assertEqual(tss2rdf.convert(self.write_points(no_zone), self.output)["observations"], 1)

    def test_a_point_without_time_stops(self):
        path = self.write_points('[] tss:pointOf ex:source ; ex:time ""^^xsd:dateTime ; ex:value "1"^^xsd:double .\n')
        with self.assertRaises(ValueError) as caught:
            tss2rdf.convert(path, self.output)
        self.assertIn("no time", str(caught.exception))

    def test_a_value_that_does_not_fit_names_the_point(self):
        path = self.write_points('[] tss:pointOf ex:source ; ex:time "2025-01-01T00:30:00Z"^^xsd:dateTime ; '
                                 'ex:value "not a number"^^xsd:double .\n')
        with self.assertRaises(ValueError) as caught:
            tss2rdf.convert(path, self.output)
        self.assertIn("point at 2025-01-01T00:30:00", str(caught.exception))
        self.assertIn("xsd:double", str(caught.exception))

    def test_a_node_or_two_values_for_a_field_stops(self):
        for value, message in (('[ ex:a "1" ]', "A point has a node as its 'value' value, not a literal."),
                               ('"1"^^xsd:double, "2"^^xsd:double', "A point has more than one 'value' value.")):
            with self.subTest(value=value):
                path = self.write_points('[] tss:pointOf ex:source ; ex:time "2025-01-01T00:30:00Z"^^xsd:dateTime ; '
                                         f'ex:value {value} .\n')
                with self.assertRaises(ValueError) as caught:
                    tss2rdf.convert(path, self.output)
                self.assertEqual(str(caught.exception), message)

    def test_values_keep_their_text_even_when_it_is_wrong(self):
        # rdflib would rewrite these while it parses: "12.0", "1000.0", "false", "...+00:00"
        boolean = 'ex:flag tss:datatype xsd:boolean .\n'
        points = ('[] tss:pointOf ex:source ; ex:time "2025-01-01T00:00:00Z"^^xsd:dateTime ; ex:value " 12 "^^xsd:double ; '
                  'ex:flag "yes"^^xsd:boolean .\n'
                  '[] tss:pointOf ex:source ; ex:time "2025-01-01T00:15:00Z"^^xsd:dateTime ; ex:value "1_000"^^xsd:double .\n')
        lifted = tss2rdf.point_graph(exact_graph(embedded_text(points, boolean)), EX.source)
        self.assertEqual(sorted(str(value) for value in lifted.objects(None, EX.value)), [" 12 ", "1_000"])
        self.assertEqual(sorted(str(value) for value in lifted.objects(None, EX.time)),
                         ["2025-01-01T00:00:00Z", "2025-01-01T00:15:00Z"])
        self.assertEqual(list(lifted.objects(None, EX.flag)), [Literal("yes", datatype=XSD.boolean, normalize=False)])
        # rdflib's switch is back on afterwards, also after a document that can't be read
        self.assertTrue(rdflib.NORMALIZE_LITERALS)
        with self.assertRaises(Exception):
            exact_graph(METADATA + "[] tss:pointOf ex:source ; ex:time .")
        self.assertTrue(rdflib.NORMALIZE_LITERALS)
        self.assertEqual(str(Literal("01", datatype=XSD.integer)), "1")  # other literals are normalised again

    def test_parses_at_the_same_time_put_the_switch_back(self):
        errors = []
        text = embedded_text(POINTS)

        def parse_many():
            try:
                for count in range(20):
                    self.assertEqual(len(tss2rdf.point_graph(exact_graph(text), EX.source)), 11)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=parse_many) for count in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertTrue(rdflib.NORMALIZE_LITERALS)

    def test_exact_turtle_keeps_every_text(self):
        points = ('[] tss:pointOf ex:source ; ex:time "2025-01-01T00:00:00Z"^^xsd:dateTime ; ex:value " 12 "^^xsd:double ; '
                  'ex:code "1_0"^^xsd:integer .\n')
        lifted = tss2rdf.point_graph(exact_graph(embedded_text(points)), EX.source)
        lifted.add((EX.x, EX.flag, Literal("yes", datatype=XSD.boolean, normalize=False)))
        text = tss2rdf.exact_turtle(lifted)
        for written in ['" 12 "^^xsd:double', '"1_0"^^xsd:integer', '"yes"^^xsd:boolean',
                        '"2025-01-01T00:00:00Z"^^xsd:dateTime']:
            self.assertIn(written, text)
        self.assertEqual(len(Graph().parse(data=text, format="turtle")), 4)  # valid Turtle


class DemoCsvFile(TempFolder):
    def test_the_demo_csv_file(self):
        """Only the observations per field are checked: the whole file has 1,043,383 triples"""
        stats = tss2rdf.convert(CSV_DEMO, self.output)
        self.assertEqual(per_field(stats), [(str(EX["grootdok-ec20-field"]), 94853),
                                            (str(EX["grootdok-quality-code-color-field"]), 94853)])

    def test_the_demo_csv_preview_parses_back(self):
        graph = Graph().parse(CSV_DEMO, format="turtle", publicID=CSV_DEMO.as_uri())
        data = DEMO / "data" / "waterinfo_289423042.csv"
        result = tss2rdf.generate(graph, data, self.output, preview_limit=200)
        preview = Graph().parse(data=result["preview"], format="turtle")
        observations = list(preview.subjects(RDF.type, SOSA.Observation))
        self.assertEqual(len(observations), 200)
        self.assertEqual(len(preview), 100 * 8 + 100 * 5)  # 100 rows: a value (8 triples) and a colour (5)
        # each observation links back to the source
        self.assertEqual(set(preview.objects(None, DCTERMS.source)), {EX["grootdok-source"]})
        first = None
        for observation in observations:
            if preview.value(observation, SOSA.observedProperty) != EX.qualityCodeColor:
                when = preview.value(observation, SOSA.resultTime).toPython()
                if when == datetime(2021, 3, 3, 23, 45, tzinfo=timezone.utc):
                    first = preview.value(observation, SOSA.hasSimpleResult)
        self.assertEqual((first.toPython(), first.datatype), (902.27, XSD.double))


if __name__ == "__main__":
    unittest.main()
