"""Expansion into observations, and conversion between storage classes."""
import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, RDF, RDFS

import rdftss
from rdftss import SnippetKind
from rdftss.vocab import QUDT, SOSA, TSS, XSD

from conftest import EXAMPLES, PREFIXES, snippet_text

EX = "https://example.org/ex#"


class TestExpansion:
    def test_the_observations_example_of_the_specification(self, example):
        graph = example("embedded_rdf.ttl").snippet().expand().graph
        observations = list(graph.subjects(RDF.type, SOSA.Observation))
        assert len(observations) == 5
        first = next(node for node in observations if str(graph.value(node, SOSA.resultTime)) == "2024-05-12T08:00:00Z")
        assert graph.value(first, SOSA.resultTime).datatype == XSD.dateTime
        assert graph.value(first, DCTERMS.source) == URIRef(EX + "snippet")
        assert graph.value(first, SOSA.observedProperty) == URIRef("http://qudt.org/vocab/quantitykind/ElectricConductivity")
        assert graph.value(first, SOSA.madeBySensor) == URIRef(EX + "sensor-1")
        assert graph.value(first, SOSA.hasFeatureOfInterest) == URIRef(EX + "feature-1")
        assert graph.value(first, QUDT.hasUnit) == URIRef("http://qudt.org/vocab/unit/MicroS-PER-CentiM")
        assert str(graph.value(first, SOSA.hasSimpleResult)) == "4810.5"

    def test_no_tss_vocabulary_but_the_descriptions_of_used_iris(self, example):
        graph = example("embedded_rdf.ttl").snippet().expand().graph
        assert not any(str(term).startswith(str(TSS)) for triple in graph for term in triple)
        assert graph.value(URIRef(EX + "sensor-1"), RDFS.label) == Literal("Conductivity sensor 1", lang="en")
        assert (URIRef(EX + "snippet"), RDFS.label, None) not in graph  # no description of the snippet

    def test_the_weather_example_gives_five_observations(self, example):
        assert example("weather_rdf.ttl").snippet().expand().observations == 5

    def test_describing_fields_without_observed_property_give_no_observations(self, example):
        result = example("readings_csv.ttl").snippet().expand()
        assert result.observations == 85 and list(result.per_field) == [EX + "csv-value-field"]

    def test_decoded_values_and_grid_times(self, example):
        delta = list(example("readings_delta.ttl").snippet().observations())
        assert str(delta[0].result) == "4747.00" and delta[0].time.text == "2024-05-12T00:00:00Z"
        grid = list(example("readings_grid.ttl").snippet().observations())
        assert grid[20].time.text == "2024-05-12T06:00:00Z"

    def test_files_are_streamed_and_replaced_only_on_success(self, example, tmp_path, make):
        target = tmp_path / "out.nt"
        result = example("readings_csv.ttl").snippet().expand(target, batch_size=10)
        assert result.path == target and len(Graph().parse(target, format="nt")) == result.triples
        bad = make(snippet_text(extra="    tss:dataFile <d.csv> ;\n", time_field='; tss:columnName "time" ',
                                value_field='; tss:columnName "value" '), {"d.csv": "time,value\n2024-05-12T08:00:00Z,x\n"})
        with pytest.raises(rdftss.DataError):
            bad.snippet().expand(target)
        assert len(Graph().parse(target, format="nt")) == result.triples  # the old output stays

    def test_nquads_have_a_graph_per_snippet(self, example, tmp_path):
        example("embedded_rdf.ttl").snippet().expand(tmp_path / "out.nq")
        line = (tmp_path / "out.nq").read_text().splitlines()[0]
        assert line.endswith(f"<{EX}snippet> .")

    def test_another_vocabulary(self, example):
        class Simple(rdftss.ObservationMapping):
            def triples(self, observation, node):
                yield node, URIRef(EX + "value"), observation.result

        graph = example("embedded_rdf.ttl").snippet().expand(mapping=Simple()).graph
        assert len(list(graph.objects(None, URIRef(EX + "value")))) == 5


def _readings(snippet, name):
    quality = [field for field in snippet.fields if field.name == "quality"]
    return [(point.instant, point[name], point[quality[0]] if quality else None) for point in snippet.read()]


class TestConversion:
    @pytest.mark.parametrize("to", ["rdf", "json", "csv", "parquet", "arrow"])
    def test_every_class_keeps_the_points(self, example, tmp_path, to):
        source = example("readings_delta.ttl").snippet()
        document = rdftss.convert(source, to, document=tmp_path / "out.ttl")
        converted = document.snippet()
        assert converted.kind == SnippetKind.parse(to) and not document.all_issues
        assert _readings(converted, "value_delta") == _readings(source, "value_delta")
        assert converted.iri == EX + f"delta-snippet-{to}"
        assert converted.field("value_delta").summary is None and converted.window == source.window

    def test_embedded_targets_decode_and_have_no_grid(self, example, tmp_path):
        rdf = rdftss.convert(example("readings_grid.ttl").snippet(), "rdf", document=tmp_path / "rdf.ttl").snippet()
        assert rdf.time_grid is None and rdf.step_field is None
        assert rdf.read()[20].time_text == "2024-05-12T06:00:00Z"
        text = (tmp_path / "rdf.ttl").read_text()
        assert text.index("tss:hasPoint [") > text.index("tss:hasField")  # the points come last

    def test_the_data_file_is_written_relative_to_the_document(self, example, tmp_path):
        rdftss.convert(example("readings_csv.ttl").snippet(), "parquet", document=tmp_path / "x.ttl")
        assert "tss:dataFile <x.parquet> ." in (tmp_path / "x.ttl").read_text()

    def test_time_grids(self, example, tmp_path):
        source = example("readings_csv.ttl").snippet()
        grid = rdftss.convert(source, "csv", document=tmp_path / "g.ttl", time_grid="auto").snippet()
        assert (grid.time_grid.base.text, grid.time_grid.step.text) == ("2024-05-12T00:00:00Z", "PT15M")
        assert _readings(grid, "value") == _readings(source, "value")
        back = rdftss.convert(grid, "csv", document=tmp_path / "t.ttl", time_grid=False).snippet()
        assert back.time_grid is None and _readings(back, "value") == _readings(source, "value")
        with pytest.raises(rdftss.DataError, match="not on the time grid"):
            rdftss.convert(source, "csv", document=tmp_path / "h.ttl", time_grid=("2024-05-12T00:00:00Z", "PT1H"))

    def test_value_encodings(self, example, tmp_path):
        source = example("readings_csv.ttl").snippet()
        encoded = rdftss.convert(source, "parquet", document=tmp_path / "e.ttl", encode={"value": "4750"}).snippet()
        assert encoded.field("value").value_encoding is not None
        assert encoded.read()[0].raw("value") == "-3.0" and _readings(encoded, "value") == _readings(source, "value")
        decoded = rdftss.convert(encoded, "csv", document=tmp_path / "d.ttl", decode=True).snippet()
        assert decoded.field("value").value_encoding is None and decoded.read()[0].raw("value") == "4747.0"
        with pytest.raises(ValueError, match="embedded"):
            rdftss.convert(source, "json", document=tmp_path / "j.ttl", encode={"value": "1"})

    def test_keep_texts(self, example, tmp_path):
        source = example("readings_csv.ttl").snippet()
        plain = rdftss.convert(source, "arrow", document=tmp_path / "a.ttl").snippet()
        assert plain.read()[0].text("value") == "4747.0"            # an Arrow double
        kept = rdftss.convert(source, "arrow", document=tmp_path / "b.ttl", keep_texts=True).snippet()
        assert kept.read()[0].text("value") == "4747.00"            # the text of the CSV file

    def test_headerless_csv(self, example, tmp_path):
        source = example("embedded_rdf.ttl").snippet()
        csv = rdftss.convert(source, "csv", document=tmp_path / "c.ttl", csv={"header": False, "delimiter": ";"}).snippet()
        assert csv.csv.header is False and csv.field("value-field").column_index == 1
        assert _readings(csv, "value-field") == _readings(source, "value-field")

    def test_subset(self, example, tmp_path):
        document = rdftss.subset(example("readings_csv.ttl").snippet(), ["value"], document=tmp_path / "s.ttl")
        assert [field.name for field in document.snippet().fields] == ["time", "value"]

    def test_split_and_merge(self, example, tmp_path):
        source = example("embedded_rdf.ttl").snippet()
        parts = rdftss.split(source, "1h", document=tmp_path / "parts.ttl")
        assert [len(part.read()) for part in parts.snippets] == [3, 2]
        first = parts.snippets[0]
        assert str(first.window) == "[2024-05-12T08:00:00Z, 2024-05-12T09:00:00Z)"
        assert first.field("value-field").summary.count == 3  # a new summary per part
        assert not parts.all_issues
        merged = rdftss.merge(parts.snippets, iri=EX + "whole", document=tmp_path / "whole.ttl").snippet()
        assert _readings(merged, "value-field") == _readings(source, "value-field")
        # Only periods with points became parts, so the merged window is the two hours with points
        assert str(merged.window) == "[2024-05-12T08:00:00Z, 2024-05-12T10:00:00Z)"
        assert merged.statistics().differences() == {}

    def test_split_external_snippets_into_data_files(self, example, tmp_path):
        parts = rdftss.split(example("readings_csv.ttl").snippet(), "6h", document=tmp_path / "parts.ttl")
        assert len(parts.snippets) == 4 and sum(len(part.read()) for part in parts.snippets) == 85
        assert sorted(path.name for path in tmp_path.glob("*.csv"))[0] == "parts-2024-05-12T0000.csv"

    def test_from_observations_is_the_reverse_of_expansion(self, example, tmp_path):
        source = example("weather_rdf.ttl").snippet()
        observations = source.expand().graph
        document = rdftss.from_observations(observations, iri=EX + "again", to="parquet", document=tmp_path / "again.ttl")
        snippet = document.snippet()
        assert snippet.kind == SnippetKind.PARQUET and not document.all_issues
        assert len(snippet.fields) == 3 and len(snippet.read()) == 3
        temperature = rdftss.find(document, unit="unit:DEG_C")[0].fields[0]
        assert [point[temperature] for point in snippet.read()] == [18.2, 18.4, 18.9]
        assert temperature.summary.count == 3
        assert URIRef(EX + "thermometer") in set(snippet.graph.objects(None, TSS.sensor))


class TestEdgeCases:
    BLANK = PREFIXES + '''_:s a tss:EmbeddedJSONSnippet ; tss:immutable true ;
    tss:timeField _:t ; tss:hasField _:t, _:v ;
    tss:points """[{"time": "2024-05-12T08:00:00Z", "v": "1.5"}, {"time": "2024-05-13T08:00:00Z", "v": "2.5"}]"""^^rdf:JSON .
_:t a tss:Field ; tss:columnName "time" ; tss:datatype xsd:dateTimeStamp .
_:v a tss:Field ; tss:columnName "v" ; tss:datatype xsd:double .'''

    @pytest.mark.parametrize("to", ["rdf", "json", "csv"])
    def test_blank_node_snippets_and_fields(self, make, tmp_path, to):
        snippet = make(self.BLANK).snippet()
        converted = rdftss.convert(snippet, to, document=tmp_path / "c.ttl")
        assert not converted.all_issues and [p["v"] for p in converted.snippet().read()] == [1.5, 2.5]
        parts = rdftss.split(snippet, "day", to=to, document=tmp_path / "parts.ttl")
        assert len(parts.snippets) == 2 and not parts.all_issues  # the parts share no node

    def test_converting_one_snippet_of_a_document_with_several(self, tmp_path):
        weather = (EXAMPLES / "weather_rdf.ttl").read_text()
        body = "\n".join(line for line in weather.splitlines() if not line.startswith(("@prefix", "#")))
        (tmp_path / "both.ttl").write_text((EXAMPLES / "embedded_rdf.ttl").read_text() + "\n" + body)
        both = rdftss.load(tmp_path / "both.ttl")
        assert len(both.snippets) == 2
        converted = rdftss.convert(both.snippet("ex:weather-snippet"), "json", document=tmp_path / "weather.ttl")
        assert [snippet.name for snippet in converted.snippets] == ["weather-snippet-json"]
        assert "ex:value-field" not in (tmp_path / "weather.ttl").read_text()
