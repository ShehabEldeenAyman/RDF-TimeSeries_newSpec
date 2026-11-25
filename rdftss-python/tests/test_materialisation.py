"""Expansion into points, what the alignment derives, and conversion between storage classes."""
import pytest
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

import rdftss
from rdftss import SourceKind
from rdftss.vocab import QUDT, SAREF, SOSA, TSS, XSD

from conftest import EXAMPLES, PREFIXES, source_text

EX = "https://example.org/ex#"


def point_at(graph, source, time):
    """The point of a source at a time, in an expansion."""
    return next(point for point in graph.objects(URIRef(source), TSS.hasPoint)
                if str(graph.value(point, TSS.time)) == time)


class TestExpansion:
    def test_the_expanded_point_of_the_specification(self, example):
        document = example("embedded_rdf.ttl")
        graph = document.source().expand().graph
        assert len(list(graph.objects(URIRef(EX + "source"), TSS.hasPoint))) == 5
        first = point_at(graph, EX + "source", "2024-05-12T08:00:00Z")
        assert graph.value(first, TSS.field) == URIRef(EX + "value-field")       # added: the source has one value field
        assert graph.value(first, TSS.time) == Literal("2024-05-12T08:00:00Z", datatype=XSD.dateTime, normalize=False)
        assert str(graph.value(first, TSS.value)) == "4810.5"
        assert first in set(document.graph.objects(URIRef(EX + "source"), TSS.hasPoint))   # the RDF point itself

    def test_the_expansion_is_the_metadata_with_the_points(self, example):
        graph = example("embedded_json.ttl").source().expand().graph
        assert (URIRef(EX + "json-source"), TSS.points, None) not in graph            # without tss:points
        assert (URIRef(EX + "json-value-field"), TSS.datatype, XSD.double) in graph   # the metadata
        assert len(list(graph.objects(URIRef(EX + "json-source"), TSS.hasPoint))) == 5

    def test_what_the_alignment_derives(self, example):
        graph = example("embedded_rdf.ttl").source().expand(derive=True).graph
        first = point_at(graph, EX + "source", "2024-05-12T08:00:00Z")
        assert {SOSA.Observation, SAREF.Measurement, TSS.Point} <= set(graph.objects(first, RDF.type))
        assert str(graph.value(first, SOSA.resultTime)) == "2024-05-12T08:00:00Z"
        assert str(graph.value(first, SOSA.hasSimpleResult)) == "4810.5"
        assert str(graph.value(first, SAREF.hasValue)) == "4810.5"
        assert graph.value(first, SOSA.observedProperty) == URIRef("http://qudt.org/vocab/quantitykind/ElectricConductivity")
        assert graph.value(first, SOSA.madeBySensor) == URIRef(EX + "sensor-1")
        assert graph.value(first, SOSA.hasFeatureOfInterest) == URIRef(EX + "feature-1")
        assert graph.value(first, QUDT.hasUnit) == URIRef("http://qudt.org/vocab/unit/MicroS-PER-CentiM")
        assert graph.value(first, SAREF.isMeasuredIn) == URIRef("http://qudt.org/vocab/unit/MicroS-PER-CentiM")

    def test_derive_applies_the_axioms_of_the_vocabulary(self, example):
        expansion = example("weather_rdf.ttl").source().expand().graph
        derived = rdftss.derive(expansion)
        assert len(set(derived.subjects(RDF.type, SOSA.Observation))) == 5
        assert all(triple not in expansion for triple in derived)                # only what is new

    def test_the_weather_example_gives_five_points(self, example):
        result = example("weather_rdf.ttl").source().expand()
        assert result.points == 5 and set(result.per_field.values()) == {3, 2}

    def test_describing_values_with_their_field_property(self, example):
        source = example("readings_csv.ttl").source()
        result = source.expand()
        assert result.points == 85 and list(result.per_field) == [EX + "csv-value-field"]
        graph = result.graph
        prop = graph.value(URIRef(EX + "csv-quality-field"), TSS.property)     # the default property, stated
        assert prop == URIRef(source.data_file + "#quality")
        first = point_at(graph, EX + "csv-source", "2024-05-12T00:00:00Z")
        assert graph.value(first, prop) == Literal("suspect", datatype=XSD.string)

    def test_decoded_values_and_grid_times(self, example):
        delta = list(example("readings_delta.ttl").source().points())
        assert str(delta[0].value) == "4747.00" and delta[0].time.text == "2024-05-12T00:00:00Z"
        grid = list(example("readings_grid.ttl").source().points())
        assert grid[20].time.text == "2024-05-12T06:00:00Z"

    def test_point_iris_from_about_url(self, make):
        text = source_text(extra="    tss:dataFile <d.csv> ;\n", time_field='; tss:columnName "time" ',
                           value_field='; tss:columnName "value" ; tss:aboutUrl "obs/value/{time}" ')
        source = make(text, files={"d.csv": "time,value\n2024-05-12T08:00:00Z,1\n"}).source()
        point = next(source.points())
        assert str(point.node) == source.data_file.rsplit("/", 1)[0] + "/obs/value/2024-05-12T08%3A00%3A00Z"
        twice = make(text.replace("{time}", "x"), files={"d.csv": "time,value\n2024-05-12T08:00:00Z,1\n"
                                                                    "2024-05-12T08:15:00Z,2\n"}).source()
        with pytest.raises(rdftss.DataError, match="to two points"):
            list(twice.points())

    def test_files_are_streamed_and_replaced_only_on_success(self, example, tmp_path, make):
        target = tmp_path / "out.nt"
        result = example("readings_csv.ttl").source().expand(target, batch_size=10)
        assert result.path == target and len(Graph().parse(target, format="nt")) == result.triples
        bad = make(source_text(extra="    tss:dataFile <d.csv> ;\n", time_field='; tss:columnName "time" ',
                                value_field='; tss:columnName "value" '), {"d.csv": "time,value\n2024-05-12T08:00:00Z,x\n"})
        with pytest.raises(rdftss.DataError):
            bad.source().expand(target)
        assert len(Graph().parse(target, format="nt")) == result.triples  # the old output stays

    def test_nquads_have_a_graph_per_source(self, example, tmp_path):
        example("embedded_rdf.ttl").source().expand(tmp_path / "out.nq")
        lines = (tmp_path / "out.nq").read_text().splitlines()
        assert any(line.endswith(f"<{EX}source> .") and "hasPoint" in line for line in lines)

    def test_only_the_points(self, example):
        graph = example("embedded_rdf.ttl").source().expand(metadata=False).graph
        assert len(graph) == 5 * 4 and (None, RDFS.label, None) not in graph


def _readings(source, name):
    quality = [field for field in source.fields if field.name == "quality"]
    return [(point.instant, point[name], point[quality[0]] if quality else None) for point in source.read()]


class TestConversion:
    @pytest.mark.parametrize("to", ["rdf", "json", "csv", "parquet", "arrow"])
    def test_every_class_keeps_the_points(self, example, tmp_path, to):
        original = example("readings_delta.ttl").source()
        document = rdftss.convert(original, to, document=tmp_path / "out.ttl")
        converted = document.source()
        assert converted.kind == SourceKind.parse(to) and not document.all_issues
        assert _readings(converted, "value_delta") == _readings(original, "value_delta")
        assert converted.iri == EX + f"delta-source-{to}"
        assert converted.field("value_delta").summary is None and converted.window == original.window

    def test_embedded_targets_decode_and_have_no_grid(self, example, tmp_path):
        rdf = rdftss.convert(example("readings_grid.ttl").source(), "rdf", document=tmp_path / "rdf.ttl").source()
        assert rdf.time_grid is None and rdf.step_field is None
        assert rdf.read()[20].time_text == "2024-05-12T06:00:00Z"
        text = (tmp_path / "rdf.ttl").read_text()
        assert text.index("tss:hasPoint [") > text.index("tss:hasField")  # the points come last

    def test_the_data_file_is_written_relative_to_the_document(self, example, tmp_path):
        rdftss.convert(example("readings_csv.ttl").source(), "parquet", document=tmp_path / "x.ttl")
        assert "tss:dataFile <x.parquet> ." in (tmp_path / "x.ttl").read_text()

    def test_time_grids(self, example, tmp_path):
        original = example("readings_csv.ttl").source()
        grid = rdftss.convert(original, "csv", document=tmp_path / "g.ttl", time_grid="auto").source()
        assert (grid.time_grid.base.text, grid.time_grid.step.text) == ("2024-05-12T00:00:00Z", "PT15M")
        assert _readings(grid, "value") == _readings(original, "value")
        back = rdftss.convert(grid, "csv", document=tmp_path / "t.ttl", time_grid=False).source()
        assert back.time_grid is None and _readings(back, "value") == _readings(original, "value")
        with pytest.raises(rdftss.DataError, match="not on the time grid"):
            rdftss.convert(original, "csv", document=tmp_path / "h.ttl", time_grid=("2024-05-12T00:00:00Z", "PT1H"))

    def test_value_encodings(self, example, tmp_path):
        original = example("readings_csv.ttl").source()
        encoded = rdftss.convert(original, "parquet", document=tmp_path / "e.ttl", encode={"value": "4750"}).source()
        assert encoded.field("value").value_encoding is not None
        assert encoded.read()[0].raw("value") == "-3.0" and _readings(encoded, "value") == _readings(original, "value")
        decoded = rdftss.convert(encoded, "csv", document=tmp_path / "d.ttl", decode=True).source()
        assert decoded.field("value").value_encoding is None and decoded.read()[0].raw("value") == "4747.0"
        with pytest.raises(ValueError, match="embedded"):
            rdftss.convert(original, "json", document=tmp_path / "j.ttl", encode={"value": "1"})

    def test_keep_texts(self, example, tmp_path):
        original = example("readings_csv.ttl").source()
        plain = rdftss.convert(original, "arrow", document=tmp_path / "a.ttl").source()
        assert plain.read()[0].text("value") == "4747.0"            # an Arrow double
        kept = rdftss.convert(original, "arrow", document=tmp_path / "b.ttl", keep_texts=True).source()
        assert kept.read()[0].text("value") == "4747.00"            # the text of the CSV file

    def test_headerless_csv(self, example, tmp_path):
        original = example("embedded_rdf.ttl").source()
        csv = rdftss.convert(original, "csv", document=tmp_path / "c.ttl", csv={"header": False, "delimiter": ";"}).source()
        assert csv.csv.header is False and csv.field("value-field").column_index == 1
        assert _readings(csv, "value-field") == _readings(original, "value-field")

    def test_subset(self, example, tmp_path):
        document = rdftss.subset(example("readings_csv.ttl").source(), ["value"], document=tmp_path / "s.ttl")
        assert [field.name for field in document.source().fields] == ["time", "value"]

    def test_split_and_merge(self, example, tmp_path):
        original = example("embedded_rdf.ttl").source()
        parts = rdftss.split(original, "1h", document=tmp_path / "parts.ttl")
        assert [len(part.read()) for part in parts.sources] == [3, 2]
        first = parts.sources[0]
        assert str(first.window) == "[2024-05-12T08:00:00Z, 2024-05-12T09:00:00Z)"
        assert first.field("value-field").summary.count == 3  # a new summary per part
        assert not parts.all_issues
        merged = rdftss.merge(parts.sources, iri=EX + "whole", document=tmp_path / "whole.ttl").source()
        assert _readings(merged, "value-field") == _readings(original, "value-field")
        # Only periods with points became parts, so the merged window is the two hours with points
        assert str(merged.window) == "[2024-05-12T08:00:00Z, 2024-05-12T10:00:00Z)"
        assert merged.statistics().differences() == {}

    def test_split_external_sources_into_data_files(self, example, tmp_path):
        parts = rdftss.split(example("readings_csv.ttl").source(), "6h", document=tmp_path / "parts.ttl")
        assert len(parts.sources) == 4 and sum(len(part.read()) for part in parts.sources) == 85
        assert sorted(path.name for path in tmp_path.glob("*.csv"))[0] == "parts-2024-05-12T0000.csv"

    def test_from_observations(self, example, tmp_path):
        original = example("weather_rdf.ttl").source()
        observations = original.expand(derive=True).graph            # the SOSA observations the alignment gives
        document = rdftss.from_observations(observations, iri=EX + "again", to="parquet", document=tmp_path / "again.ttl")
        source = document.source()
        assert source.kind == SourceKind.PARQUET and not document.all_issues
        assert len(source.fields) == 3 and len(source.read()) == 3
        temperature = rdftss.find(document, unit="unit:DEG_C")[0].fields[0]
        assert [point[temperature] for point in source.read()] == [18.2, 18.4, 18.9]
        assert temperature.summary.count == 3
        assert URIRef(EX + "thermometer") in set(source.graph.objects(None, TSS.sensor))


class TestEdgeCases:
    BLANK = PREFIXES + '''_:s a tss:EmbeddedJSONSource ; tss:immutable true ;
    tss:timeField _:t ; tss:hasField _:t, _:v ;
    tss:points """[{"time": "2024-05-12T08:00:00Z", "v": "1.5"}, {"time": "2024-05-13T08:00:00Z", "v": "2.5"}]"""^^rdf:JSON .
_:t a tss:ContextField ; tss:columnName "time" ; tss:datatype xsd:dateTime .
_:v a tss:ValueField ; tss:columnName "v" ; tss:datatype xsd:double .'''

    @pytest.mark.parametrize("to", ["rdf", "json", "csv"])
    def test_blank_node_sources_and_fields(self, make, tmp_path, to):
        source = make(self.BLANK).source()
        converted = rdftss.convert(source, to, document=tmp_path / "c.ttl")
        assert not converted.all_issues and [p["v"] for p in converted.source().read()] == [1.5, 2.5]
        parts = rdftss.split(source, "day", to=to, document=tmp_path / "parts.ttl")
        assert len(parts.sources) == 2 and not parts.all_issues  # the parts share no node

    def test_converting_one_source_of_a_document_with_several(self, tmp_path):
        weather = (EXAMPLES / "weather_rdf.ttl").read_text()
        body = "\n".join(line for line in weather.splitlines() if not line.startswith(("@prefix", "#")))
        (tmp_path / "both.ttl").write_text((EXAMPLES / "embedded_rdf.ttl").read_text() + "\n" + body)
        both = rdftss.load(tmp_path / "both.ttl")
        assert len(both.sources) == 2
        converted = rdftss.convert(both.source("ex:weather-source"), "json", document=tmp_path / "weather.ttl")
        assert [source.name for source in converted.sources] == ["weather-source-json"]
        assert "ex:value-field" not in (tmp_path / "weather.ttl").read_text()
