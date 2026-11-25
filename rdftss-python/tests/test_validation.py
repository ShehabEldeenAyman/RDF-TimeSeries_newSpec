"""SHACL validation and summary verification, as the API gives them. The conformance test suite
(test_conformance.py) checks the rules themselves."""
import pytest

import rdftss

from conftest import EXAMPLES, ROOT

EMBEDDED = (EXAMPLES / "embedded_rdf.ttl").read_text(encoding="utf-8")


class TestValidation:
    @pytest.fixture(autouse=True)
    def pyshacl(self):
        pytest.importorskip("pyshacl")

    def test_the_source_classes_choose_the_shapes(self, example):
        assert example("embedded_json.ttl").validate().shapes == ["embedded"]
        assert example("readings_parquet.ttl").validate().shapes == ["external"]

    def test_a_report(self):
        report = rdftss.loads(EMBEDDED.replace('tss:from "2024-05-12T08:00:00Z"', 'tss:from "2024-05-12T08:00:00"')).validate()
        assert not report and report.checks == {"S23"} and report.counts["Violation"] == 1
        result = report.violations[0]
        assert (result.check, result.name, result.where, result.shapes) == ("S23", "S23 timezone", "ex:source", "embedded")
        assert "DOES NOT CONFORM (1 violation, 0 warnings)" in str(report)
        assert "sh:ValidationReport" in report.to_turtle() and "S23 timezone" in report.to_turtle()

    def test_property_checks_have_a_group(self):
        report = rdftss.loads(EMBEDDED.replace("tss:immutable true ;", 'tss:immutable "yes" ;')).validate()
        result = next(result for result in report.violations if result.check == "S2")
        assert result.group == "Optional" and result.name == "S2 immutable"

    def test_values_are_checked_as_written(self):
        text = EMBEDDED.replace('tss:value "4810.5"^^xsd:double', 'tss:value " 4810.5 "^^xsd:double')
        assert text != EMBEDDED
        assert "P4" in rdftss.loads(text).validate().checks

    def test_one_bundled_file(self, example):
        assert "S1" in example("readings_csv.ttl").validate(shapes="embedded").checks

    def test_your_own_shapes(self, tmp_path, example):
        shapes = tmp_path / "shapes.ttl"
        shapes.write_text("""@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix tss: <https://w3id.org/tss#> .
<#CommentShape> a sh:NodeShape ;
    sh:targetClass tss:CSVSource ;
    sh:property [ sh:name "X1 comment" ; sh:path rdfs:comment ; sh:minCount 1 ] .
""", encoding="utf-8")
        report = example("readings_csv.ttl").validate(shapes=shapes)
        assert report.checks == {"X1"} and report.shapes == ["shapes.ttl"]

    def test_a_path_or_url(self):
        assert rdftss.validate(EXAMPLES / "readings_arrow.ttl").conforms


class TestSummaryVerification:
    def test_up_to_date(self, example):
        report = example("embedded_rdf.ttl").verify_summaries()
        assert report and len(report.checks) == 1 and report.checks[0].computed.count == 5
        assert str(report).startswith("1 stored summary: 1 up to date")

    def test_out_of_date(self):
        report = rdftss.loads(EMBEDDED.replace('tss:count "5"', 'tss:count "4"')).verify_summaries()
        assert not report and report.out_of_date[0].differences == {"count": (4, 5)}
        assert "tss:count is 4, the data gives 5" in str(report)

    def test_a_tolerance(self):
        document = rdftss.loads(EMBEDDED.replace('tss:mean "4757.54"', 'tss:mean "4757.5"'))
        assert not document.verify_summaries().up_to_date
        assert document.verify_summaries(tolerance=1e-4).up_to_date

    def test_another_missing_value_policy(self):
        text = EMBEDDED.replace("tss:missingValuePolicy tss:ExcludeEmptyValues", "tss:missingValuePolicy ex:OtherPolicy")
        report = rdftss.loads(text).verify_summaries()
        assert not report and report.not_checked and "ExcludeEmptyValues" in str(report)

    def test_data_that_cannot_be_read(self):
        report = rdftss.load(ROOT / "conformance" / "metadata" / "external" / "valid-csv.ttl").verify_summaries()
        assert report.not_checked and "can't be read" in report.not_checked[0].problem

    def test_one_source(self, example):
        assert example("embedded_rdf.ttl").source().verify_summaries().up_to_date
        assert str(example("readings_csv.ttl").verify_summaries()) == "No stored summaries."
