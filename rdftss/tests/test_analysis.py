"""Statistics, ranges, gaps, steps, resampling, filters, DataFrames, SQL and discovery."""
from datetime import timedelta
from decimal import Decimal

import pytest

import rdftss

from conftest import DEMO_11


class TestStatistics:
    def test_the_summary_example_of_the_specification(self, example):
        statistics = example("embedded_rdf.ttl").snippet().statistics()
        assert (statistics.count, statistics.missing) == (5, 0)
        assert statistics.first_time_text == "2024-05-12T08:00:00Z"
        assert statistics.last_time_text == "2024-05-12T09:45:00Z"
        assert (statistics.minimum, statistics.maximum) == (4719.3, 4810.5)
        assert statistics.mean == pytest.approx(4757.54)
        assert statistics.median == 4751.9
        assert statistics.differences() == {}  # the stored summary is up to date

    def test_the_missing_value_example_of_the_specification(self, example):
        statistics = example("weather_rdf.ttl").snippet().statistics("humidity-field")
        assert (statistics.count, statistics.missing, statistics.mean, statistics.median) == (2, 1, 60.25, 60.25)

    def test_an_out_of_date_summary_is_found(self, example):
        statistics = example("embedded_rdf.ttl").snippet().read()[:4].statistics()
        assert set(statistics.differences()) == {"count", "last_time", "mean", "median"}

    def test_text_fields_have_no_numbers(self, example):
        statistics = example("readings_csv.ttl").snippet().statistics("quality")
        assert statistics.count == 85 and statistics.mean is None

    def test_decimals_are_exact(self, make):
        from conftest import snippet_text

        text = snippet_text(kind="EmbeddedRDFSnippet").replace("tss:datatype xsd:double", "tss:datatype xsd:decimal") + """
ex:s tss:hasPoint [ ex:time "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ; ex:value "0.1"^^xsd:decimal ] .
ex:s tss:hasPoint [ ex:time "2024-05-12T08:15:00Z"^^xsd:dateTimeStamp ; ex:value "0.2"^^xsd:decimal ] ."""
        statistics = make(text).snippet().statistics()
        assert statistics.mean == Decimal("0.15") and statistics.median == Decimal("0.15")


class TestSelecting:
    def test_between_where_having(self, example):
        points = example("readings_csv.ttl").snippet().read()
        assert len(points.between("2024-05-12T00:00:00Z", "2024-05-12T01:00:00Z")) == 4
        assert len(points.where("quality", equals="suspect")) == 4
        assert all(p["value"] >= 4790 for p in points.where("value", at_least=4790))
        assert len(points.where(lambda point: point["quality"] != "good")) == 4
        assert len(points.having("quality")) == 85

    def test_values_outside_the_expected_range_are_flagged_not_dropped(self, make):
        from conftest import snippet_text

        text = snippet_text(kind="EmbeddedRDFSnippet", value_field='; tss:minimum "0"^^xsd:double ; '
                                                                   'tss:maximum "100"^^xsd:double ') + """
ex:s tss:hasPoint [ ex:time "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ; ex:value "150"^^xsd:double ] .
ex:s tss:hasPoint [ ex:time "2024-05-12T08:15:00Z"^^xsd:dateTimeStamp ; ex:value "100"^^xsd:double ] ."""
        points = make(text).snippet().read()
        flags = points.outside_expected_range()
        assert len(points) == 2 and len(flags) == 1 and flags[0].side == "above" and flags[0].value == 150


class TestGapsAndResampling:
    def test_gaps(self, example):
        report = example("readings_csv.ttl").snippet().gaps()
        assert report.usual_step == timedelta(minutes=15)
        assert report.count == 3
        assert report.gaps[0].length == timedelta(minutes=105)
        assert report.total_missing == timedelta(minutes=165)

    def test_missing_steps_on_a_grid(self, example):
        runs = example("readings_grid.ttl").snippet().read().missing_steps()
        assert [(run.first, run.last) for run in runs] == [(20, 23), (50, 50), (70, 75)]
        with pytest.raises(ValueError, match="no time grid"):
            example("readings_csv.ttl").snippet().read().missing_steps()

    def test_resample(self, example):
        points = example("readings_csv.ttl").snippet().read()
        hours = points.resample("6h", "value")
        assert [item.count for item in hours] == [20, 24, 21, 20]
        assert points.resample("PT6H", "value", how="max")[0].value == max(points[:20].values("value"))
        assert points.resample("1d", "quality", how="count")[0].value == 85
        assert len(points.resample("1h", "value", include_empty=True)) == 24
        with pytest.raises(ValueError, match="numeric"):
            points.resample("1h", "quality", how="mean")

    def test_calendar_months(self, example):
        bins = example("readings_csv.ttl").snippet().read().resample("1 month", "value", how="count")
        assert len(bins) == 1 and bins[0].start.month == 5 and bins[0].end.month == 6


class TestFrames:
    def test_pandas(self, example):
        pytest.importorskip("pandas")
        frame = example("readings_delta.ttl").snippet().to_pandas()
        assert frame.index.name == "time" and len(frame) == 85
        assert frame["value_delta"].iloc[0] == 4747.0  # decoded
        assert str(frame.index.tz) == "UTC"

    def test_arrow_metadata(self, example):
        table = example("readings_csv.ttl").snippet().to_arrow()
        metadata = table.schema.field("value").metadata
        assert metadata[b"unit"] == b"http://qudt.org/vocab/unit/MicroS-PER-CentiM"
        assert table.schema.metadata[b"rdftss.snippet"] == b"https://example.org/ex#csv-snippet"

    def test_polars(self, example):
        pytest.importorskip("polars")
        frame = example("readings_arrow.ttl").snippet().to_polars()
        assert frame.shape == (85, 3)

    def test_nanoseconds_and_names(self, example):
        table = example("readings_csv.ttl").snippet().read().to_arrow(time_unit="ns", names="label")
        assert str(table.schema.field(0).type) == "timestamp[ns, tz=UTC]"
        assert "Electrical conductivity" in table.schema.names


class TestSQL:
    @pytest.fixture(autouse=True)
    def duckdb(self):
        pytest.importorskip("duckdb")

    def test_a_query(self, example):
        result = example("readings_csv.ttl").snippet().sql("SELECT count(*) AS n, round(avg(value), 2) AS mean FROM points")
        assert result.columns == ["n", "mean"] and result.rows[0][0] == 85

    def test_metadata_tables(self, example):
        result = example("embedded_rdf.ttl").sql("SELECT sql_column, role, unit FROM fields ORDER BY role")
        assert ("value_field", "measured", "http://qudt.org/vocab/unit/MicroS-PER-CentiM") in result.rows
        summary = example("embedded_rdf.ttl").sql("SELECT value_count, mean FROM summaries")
        assert summary.rows == [(5, 4757.54)]

    def test_only_one_reading_statement(self, example):
        snippet = example("readings_csv.ttl").snippet()
        from rdftss.sql import SQLError

        with pytest.raises(SQLError, match="exactly one"):
            snippet.sql("SELECT 1; SELECT 2")
        with pytest.raises(SQLError, match="reading queries"):
            snippet.sql("CREATE TABLE x (i INT)")

    def test_no_files_and_no_settings(self, example):
        from rdftss.sql import SQLError

        snippet = example("readings_csv.ttl").snippet()
        with pytest.raises(SQLError):
            snippet.sql("SELECT * FROM read_csv('/etc/hostname')")
        with pytest.raises(SQLError):
            snippet.sql("SET enable_external_access = true")

    def test_row_limit(self, example):
        result = example("readings_csv.ttl").snippet().sql("SELECT * FROM points", max_rows=10)
        assert len(result) == 10 and result.more_rows

    def test_several_snippets(self, example):
        with rdftss.Database([example("readings_csv.ttl").snippet(), example("readings_parquet.ttl").snippet()]) as db:
            assert set(db.tables) == {"csv_snippet", "parquet_snippet"}
            assert "points" not in db.describe().split('View')[0]
            result = db.query("SELECT count(*) FROM csv_snippet c JOIN parquet_snippet p ON c.time = p.time AND c.value = p.value")
            assert result.rows[0][0] == 85
            assert len(db.examples()) >= 3


class TestDiscovery:
    def documents(self, example):
        return [example(name) for name in ("embedded_rdf.ttl", "weather_rdf.ttl", "readings_csv.ttl", "readings_parquet.ttl")]

    def test_by_observed_property_and_feature(self, example):
        matches = rdftss.find(self.documents(example), observed_property="quantitykind:ElectricConductivity",
                              feature_of_interest="ex:feature-1")
        assert len(matches) == 3 and all(len(match.fields) == 1 for match in matches)
        assert rdftss.find(self.documents(example), unit="unit:DEG_C")[0].fields[0].name == "temperature-field"

    def test_windows_that_only_touch_do_not_overlap(self, example):
        documents = self.documents(example)
        assert len(rdftss.find(documents, overlapping=("2024-05-12T09:00:00Z", "2024-05-12T09:30:00Z"))) == 3
        assert len(rdftss.find(documents, overlapping=("2024-05-13T00:00:00Z", None))) == 0
        assert len(rdftss.find(documents, overlapping=(None, "2024-05-12T08:00:00Z"))) == 2

    def test_by_class_and_mutability(self, example):
        documents = self.documents(example)
        assert len(rdftss.find(documents, kind="parquet")) == 1
        assert len(rdftss.find(documents, mutable=True)) == 1
        assert len(rdftss.find(documents[0], label="conductivity")) == 1

    @pytest.mark.skipif(not DEMO_11.exists(), reason="the demo 11 folder is not next to rdftss")
    def test_the_demo_documents(self):
        documents = [rdftss.load(path) for path in sorted((DEMO_11 / "metadata endpoint").glob("*.ttl"))]
        matches = rdftss.find(documents, observed_property="quantitykind:ElectricConductivity",
                              overlapping=("2021-03-04", "2021-03-05"))
        assert len(matches) == 8  # the factory snippet covers 2025 only

    def test_how_to_read(self, example):
        access = example("readings_grid.ttl").snippet().how_to_read()
        assert access["class"] == "https://w3id.org/tss#CSVSnippet"
        assert access["time_grid"] == {"base": "2024-05-12T00:00:00Z", "step": "PT15M",
                                       "step_field": "https://example.org/ex#grid-step-field", "step_column": "step"}
        rdf = example("embedded_rdf.ttl").snippet().how_to_read()
        assert rdf["embedded_predicate"] == "https://w3id.org/tss#hasPoint"
