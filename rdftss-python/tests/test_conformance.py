"""The conformance test suite (conformance/manifest.ttl), run against rdftss.

Every test of the manifest is one pytest test, named after it, such as
``embedded-S23-from-without-timezone``. docs/conformance.md explains the suite: the test types,
their expected results and how results are compared.
"""
import csv
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from rdflib import Graph, Namespace, URIRef
from rdflib.collection import Collection
from rdflib import namespace

import rdftss
from rdftss.summaries import PROPERTY_NAMES
from rdftss.times import parse_time
from rdftss.xsd import is_numeric

from conftest import ROOT

SUITE = ROOT / "conformance"
MF = Namespace("http://www.w3.org/2001/sw/DataAccess/tests/test-manifest#")
TSST = Namespace("https://w3id.org/tss/tests#")
FILE_FORMATS = {rdftss.SnippetKind.PARQUET, rdftss.SnippetKind.ARROW}


def manifest():
    graph = Graph().parse(SUITE / "manifest.ttl")
    node = next(graph.subjects(MF.entries, None))
    return graph, list(Collection(graph, graph.value(node, MF.entries)))


def path_of(iri) -> Path:
    return Path(unquote(urlparse(str(iri)).path))


def instant(text: str):
    return parse_time(text).instant


def same_value(actual, text: str, datatype, exact_text=None) -> bool:
    """Whether a value is the expected one: numbers by value, other values by their text."""
    if text == "":
        return actual is None
    if actual is None:
        return False
    if datatype is not None and is_numeric(datatype):
        if isinstance(actual, float):
            return actual == float(text)
        return Decimal(str(actual)) == Decimal(text)
    if datatype == namespace.XSD.boolean:
        return actual == (text in ("true", "1"))
    return (exact_text if exact_text is not None else str(actual)) == text


# --- One function per test type -------------------------------------------------------------------

def run_metadata_test(graph, test):
    pytest.importorskip("pyshacl")
    report = rdftss.load(path_of(graph.value(test, MF.action))).validate()
    conforms = graph.value(test, TSST.conforms).toPython()
    violations = {str(check) for check in graph.objects(test, TSST.violation)}
    warnings = {str(check) for check in graph.objects(test, TSST.warning)}
    assert sorted(report.shapes) == sorted(str(kind) for kind in graph.objects(test, TSST.shapes))
    assert report.conforms == conforms, str(report)
    assert violations <= {result.check for result in report.violations}, str(report)
    assert warnings <= {result.check for result in report.warnings}, str(report)
    if conforms and not warnings:
        assert not report.results, str(report)


def run_read_test(graph, test):
    snippet = rdftss.load(path_of(graph.value(test, MF.action))).snippet()
    if snippet.kind in FILE_FORMATS:
        pytest.importorskip("pyarrow")
    result = graph.value(test, MF.result)
    if result == TSST.Error:
        with pytest.raises(rdftss.DataError):
            snippet.read()
        return
    with open(path_of(result), newline="", encoding="utf-8") as file:
        header, *rows = list(csv.reader(file))
    fields = [snippet.field(URIRef(iri)) for iri in header[1:]]
    points = sorted(snippet.read(), key=lambda point: point.instant)
    rows.sort(key=lambda row: instant(row[0]))
    assert [point.instant for point in points] == [instant(row[0]) for row in rows]
    for point, row in zip(points, rows):
        for field, text in zip(fields, row[1:]):
            assert same_value(point[field], text, field.datatype, point.text(field)), \
                f"{point.time_text}, {field.name}: {point[field]!r}, expected {text!r}"


def run_summary_test(graph, test):
    report = rdftss.load(path_of(graph.value(test, MF.action))).verify_summaries()
    assert not report.not_checked, str(report)
    assert report.up_to_date == graph.value(test, TSST.upToDate).toPython(), str(report)
    found = {PROPERTY_NAMES[number] for check in report.checks for number in check.differences}
    assert found == {str(name) for name in graph.objects(test, TSST.outOfDate)}, str(report)


def run_expansion_test(graph, test):
    snippet = rdftss.load(path_of(graph.value(test, MF.action))).snippet()
    with open(path_of(graph.value(test, MF.result)), newline="", encoding="utf-8") as file:
        expected = list(csv.DictReader(file))
    remaining = list(snippet.observations())
    assert len(remaining) == len(expected)

    def matches(observation, row) -> bool:
        iris = [(observation.snippet.node, "snippet"), (observation.observed_property, "observed_property"),
                (observation.sensor, "sensor"), (observation.feature_of_interest, "feature_of_interest"),
                (observation.unit, "unit")]
        if any(str(iri or "") != row[column] for iri, column in iris):
            return False
        result = observation.result
        if observation.time.instant != instant(row["time"]) or str(result.datatype) != row["datatype"]:
            return False
        if row["match"] == "text":
            return str(result) == row["result"]
        return same_value(result.toPython(), row["result"], result.datatype)

    for row in expected:
        found = next((observation for observation in remaining if matches(observation, row)), None)
        assert found is not None, f"No observation like {row}"
        remaining.remove(found)


RUNNERS = {
    TSST.MetadataTest: run_metadata_test,
    TSST.ReadTest: run_read_test,
    TSST.SummaryTest: run_summary_test,
    TSST.ExpansionTest: run_expansion_test,
}

GRAPH, ENTRIES = manifest()


@pytest.mark.parametrize("test", ENTRIES, ids=[str(GRAPH.value(test, MF.name)) for test in ENTRIES])
def test_conformance(test):
    kind = next(kind for kind in RUNNERS if (test, namespace.RDF.type, kind) in GRAPH)
    RUNNERS[kind](GRAPH, test)


def test_the_suite_is_up_to_date(tmp_path):
    """make_suite.py writes the files of the suite; they must be what it writes now."""
    import shutil
    import subprocess
    import sys

    pytest.importorskip("pyarrow")
    copy = tmp_path / "conformance"
    shutil.copytree(SUITE, copy)
    subprocess.run([sys.executable, str(copy / "make_suite.py")], check=True, capture_output=True)
    def texts(folder):
        return {path.relative_to(folder): path.read_text(encoding="utf-8")
                for path in folder.rglob("*") if path.suffix in (".ttl", ".csv")}

    written, committed = texts(copy), texts(SUITE)
    assert set(written) == set(committed), "make_suite.py writes other files: run it again"
    for path, text in committed.items():
        assert written[path] == text, f"{path} differs from what make_suite.py writes: run it again"
