"""Statistics, ranges, gaps, steps, resampling, filters, DataFrames, SPARQL and discovery."""
from datetime import timedelta
from decimal import Decimal

import pytest

import rdftss

from conftest import DEMO_11, snippet_text


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


class TestSPARQL:
    def test_a_query(self, example):
        result = example("readings_csv.ttl").snippet().sparql(
            "SELECT (COUNT(?point) AS ?n) (ROUND(AVG(?value) * 100) / 100 AS ?mean) "
            "WHERE { ?point ex:csv-value-field ?value }")
        assert result.columns == ["n", "mean"] and result.rows == [(85, Decimal("4751.34"))]

    def test_values_keep_their_text_and_are_decoded(self, example):
        result = example("readings_delta.ttl").snippet().sparql(
            "SELECT ?value WHERE { ?point ex:delta-value-field ?value ; ex:delta-time-field ?time } ORDER BY ?time LIMIT 1")
        assert str(result.terms[0][0]) == "4747.00" and result.rows[0][0] == 4747.0

    def test_every_storage_class_gives_the_same_points(self, example):
        query = """SELECT (COUNT(?point) AS ?n) (MIN(?value) AS ?low) (MAX(?value) AS ?high) (MIN(?time) AS ?first)
                   WHERE { ?snippet tss:hasPoint ?point ; tss:hasField ?field ; tss:timeField ?time_field .
                           ?field tss:observedProperty quantitykind:ElectricConductivity .
                           ?point ?field ?value ; ?time_field ?time }"""
        answers = {str(example(name).snippet().sparql(query).rows) for name in (
            "readings_csv.ttl", "readings_no_header.ttl", "readings_grid.ttl", "readings_delta.ttl",
            "readings_parquet.ttl", "readings_arrow.ttl")}
        assert len(answers) == 1 and "85" in answers.pop()

    def test_times_compare_as_moments(self, make):
        document = make(snippet_text("EmbeddedRDFSnippet") + """
ex:s tss:hasPoint [ ex:time "2024-05-12T10:30:00+02:00"^^xsd:dateTimeStamp ; ex:value "1"^^xsd:double ] ,
                  [ ex:time "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ; ex:value "2"^^xsd:double ] .
""")
        ordered = document.sparql("SELECT ?value WHERE { ?point ex:time ?time ; ex:value ?value } ORDER BY ?time")
        assert [row[0] for row in ordered] == [2.0, 1.0]
        later = document.sparql('SELECT ?value WHERE { ?point ex:time ?time ; ex:value ?value '
                                'FILTER(?time > "2024-05-12T08:15:00Z"^^xsd:dateTime) }')
        assert later.rows == [(1.0,)]

    def test_the_metadata_and_the_stored_summaries(self, example):
        result = example("embedded_rdf.ttl").sparql(
            "SELECT ?count ?mean WHERE { ?field tss:summary ?summary . ?summary tss:count ?count ; tss:mean ?mean }")
        assert result.rows == [(5, 4757.54)]
        fields = example("readings_csv.ttl").sparql("SELECT ?field WHERE { ?field tss:unit unit:MicroS-PER-CentiM }")
        assert [str(row[0]) for row in fields] == ["https://example.org/ex#csv-value-field"]

    def test_embedded_points_are_there_once(self, example):
        result = example("embedded_rdf.ttl").sparql("SELECT (COUNT(?point) AS ?n) WHERE { ?snippet tss:hasPoint ?point }")
        assert result.rows == [(5,)]

    def test_ask_and_construct(self, example):
        snippet = example("readings_csv.ttl").snippet()
        assert snippet.sparql("ASK { ?point ex:csv-value-field ?value FILTER(?value > 4800) }") is True
        graph = snippet.sparql("CONSTRUCT { ?field ?p ?o } WHERE { ?field a tss:Field ; ?p ?o }")
        assert len(graph) > 10

    def test_only_reading_queries(self, example):
        with pytest.raises(rdftss.QueryError, match="can't be read"):
            example("readings_csv.ttl").snippet().sparql("INSERT DATA { <a:b> <a:c> <a:d> }")

    def test_no_other_servers_and_no_downloads(self, example):
        snippet = example("readings_csv.ttl").snippet()
        with pytest.raises(rdftss.QueryError, match="SERVICE"):
            snippet.sparql("SELECT * WHERE { SERVICE <http://localhost:9/sparql> { ?s ?p ?o } }")
        with pytest.raises(rdftss.QueryError, match="FROM"):
            snippet.sparql("SELECT * FROM <http://localhost:9/graph.ttl> WHERE { ?s ?p ?o }")

    def test_row_limit(self, example):
        result = example("readings_csv.ttl").snippet().sparql("SELECT * WHERE { ?s ?p ?o }", max_rows=10)
        assert len(result) == 10 and result.more_rows

    def test_time_limit(self, example):
        import threading

        with pytest.raises(rdftss.QueryError, match="stopped after"):
            example("readings_csv.ttl").snippet().sparql(
                "SELECT (COUNT(*) AS ?n) WHERE { ?a ?b ?c . ?d ?e ?f . ?g ?h ?i }", timeout=0.5)
        assert not any(thread.name == "rdftss-sparql" and thread.is_alive() for thread in threading.enumerate())

    def test_several_snippets(self, example):
        snippets = [example("readings_csv.ttl").snippet(), example("readings_parquet.ttl").snippet()]
        with rdftss.TripleStore(snippets) as store:
            assert [count for _, count in store.loaded] == [85, 85]
            assert "ex:parquet-value-field" in store.describe()
            result = store.query("""SELECT (COUNT(*) AS ?n) WHERE {
                ex:csv-snippet tss:hasPoint ?a . ?a ex:csv-time-field ?time ; ex:csv-value-field ?value .
                ex:parquet-snippet tss:hasPoint ?b . ?b ex:parquet-time-field ?other ; ex:parquet-value-field ?number .
                FILTER(?time = ?other && ?value = ?number) }""")
            assert result.rows == [(85,)]     # the same values: "4747.00" in the CSV file is 4747.0 in Parquet
            for query in store.examples():
                store.query(query)

    def test_a_period_and_some_fields(self, example):
        snippet = example("readings_csv.ttl").snippet()
        with rdftss.TripleStore([snippet], between=("2024-05-12T06:00:00Z", "2024-05-12T12:00:00Z"),
                                fields=["value"]) as store:
            assert store.loaded[0][1] == 24
            assert not store.query("ASK { ?point ex:csv-quality-field ?quality }")


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

    def test_by_class_and_immutability(self, example):
        documents = self.documents(example)
        assert len(rdftss.find(documents, kind="parquet")) == 1
        assert len(rdftss.find(documents, immutable=True)) == 4 and not rdftss.find(documents, immutable=False)
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
