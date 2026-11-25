"""Loading documents and reading the model: classes, defaults, rules reported as issues."""
import pytest

import rdftss
from rdftss import SourceKind
from rdftss.vocab import XSD

from conftest import DEMO_11, DEMO_DOCUMENTS, EXAMPLES, PREFIXES, source_text


def test_every_example_loads_without_issues(example):
    for path in sorted(EXAMPLES.glob("*.ttl")):
        document = example(path.name)
        assert not document.all_issues, f"{path.name}: {document.all_issues}"
        assert not document.source().reading_problems


@pytest.mark.skipif(not DEMO_11.exists(), reason="the demo 11 folder is not next to rdftss")
def test_the_demo_11_documents_load_without_issues():
    documents = [rdftss.load(path) for path in DEMO_DOCUMENTS]
    assert len(documents) == 8
    for document in documents:
        assert not document.all_issues, f"{document.location}: {document.all_issues}"
    kinds = {document.source().kind for document in documents}
    assert kinds == {SourceKind.CSV, SourceKind.PARQUET, SourceKind.EMBEDDED_RDF, SourceKind.EMBEDDED_JSON}


def test_the_class_decides_where_the_points_are(example):
    assert example("embedded_rdf.ttl").source().kind == SourceKind.EMBEDDED_RDF
    assert example("embedded_json.ttl").source().kind == SourceKind.EMBEDDED_JSON
    assert example("readings_parquet.ttl").source().kind == SourceKind.PARQUET
    assert example("readings_arrow.ttl").source().kind.is_external


def test_defaults_are_filled_in(make):
    source = make(source_text(extra='    tss:dataFile <data.csv> ;\n', time_field='; tss:columnName "time" ',
                                value_field='; tss:columnName "value" ')).source()
    assert source.csv == rdftss.CSVDialect(",", "utf-8", True)
    assert source.key is source.time_field
    assert source.data_file.endswith("/data.csv")


def test_prefixes_do_not_matter(make):
    text = source_text(extra='    tss:dataFile <data.csv> ;\n').replace("@prefix tss:", "@prefix t:").replace("tss:", "t:")
    source = make(text.replace("@prefix t: <https://w3id.org/tss#>", "@prefix t: <https://w3id.org/tss#>")).source()
    assert source.kind == SourceKind.CSV and source.time_field.datatype == XSD.dateTime


def test_fields_are_found_by_name_iri_or_prefixed_name(example):
    source = example("readings_csv.ttl").source()
    field = source.field("value")
    assert source.field("ex:csv-value-field") is field
    assert source.field("https://example.org/ex#csv-value-field") is field
    assert source.field("Electrical conductivity") is field
    assert field.role == "value" and source.field("quality").role == "describing"
    assert field.kind == "value" and source.field("quality").kind == "context" and source.time_field.kind == "context"
    with pytest.raises(KeyError, match="Its fields are"):
        source.field("nope")


def test_rules_are_reported_as_issues_not_raised(make):
    text = PREFIXES + """
ex:s a tss:EmbeddedJSONSource ; tss:dataFile <x.csv> ; tss:hasPoint [] ;
    tss:timeField ex:t ; tss:key ex:other ; tss:hasField ex:t .
ex:t a tss:Field ; tss:datatype xsd:date .
"""
    document = make(text)
    messages = [issue.message for issue in document.all_issues]
    assert any("must not state tss:hasPoint" in message for message in messages)
    assert any("must not state tss:dataFile" in message for message in messages)
    assert any("exactly one tss:points" in message for message in messages)
    assert any("tss:key" in message for message in messages)
    assert any("xsd:dateTime or xsd:dateTimeStamp" in message for message in messages)
    assert any("neither tss:ValueField nor tss:ContextField" in message for message in messages)
    with pytest.raises(rdftss.DocumentError, match="tss:points"):
        document.source().read()


def test_a_source_without_a_class_can_not_be_read(make):
    document = make(PREFIXES + "ex:s tss:timeField ex:t ; tss:hasField ex:t . ex:t tss:datatype xsd:dateTimeStamp .")
    assert document.source().kind is None
    with pytest.raises(rdftss.DocumentError, match="no source class"):
        document.source().read()


def test_a_document_must_not_mix_embedded_and_external_sources(make):
    text = source_text(kind="EmbeddedRDFSource") + """
ex:s2 a tss:CSVSource ; tss:immutable true ;
    tss:dataFile <d.csv> ; tss:timeField ex:t2 ; tss:hasField ex:t2 .
ex:t2 a tss:ContextField ; tss:columnName "time" ; tss:datatype xsd:dateTime .
"""
    document = make(text)
    assert len(document.sources) == 2
    assert any("embedded and external" in issue.message for issue in document.issues)
    with pytest.raises(rdftss.DocumentError, match="2 sources"):
        document.source()
    assert document.source("ex:s2").kind == SourceKind.CSV


def test_time_grid_rules(make):
    text = source_text(extra='    tss:dataFile <d.csv> ;\n    tss:timeBase "2024-05-12T08:00:00Z"^^xsd:dateTime ;\n'
                              '    tss:timeStep "-PT15M"^^xsd:duration ;\n')
    source = make(text).source()
    assert source.time_grid is None
    assert any("more than zero" in problem for problem in source.reading_problems)


def test_a_time_grid_without_a_step_field_is_by_position(make):
    text = source_text(extra='    tss:dataFile <d.csv> ;\n    tss:timeBase "2024-05-12T08:00:00Z"^^xsd:dateTime ;\n'
                              '    tss:timeStep "PT15M"^^xsd:duration ;\n', value_field='; tss:columnName "value" ')
    source = make(text, files={"d.csv": "value\n1.5\n\n2.5\n"}).source()
    assert not source.issues and source.time_grid.by_position and source.step_field is None
    rows = source.read()
    assert [row.time_text for row in rows] == ["2024-05-12T08:00:00Z", "2024-05-12T08:15:00Z", "2024-05-12T08:30:00Z"]
    assert [row["value"] for row in rows] == [1.5, None, 2.5]     # a blank line is a step without readings


def test_loads_resolves_relative_iris_against_the_base():
    document = rdftss.loads(source_text(extra="    tss:dataFile <data.csv> ;\n"), base="https://example.org/meta/doc.ttl")
    assert document.source().data_file == "https://example.org/meta/data.csv"


def test_other_rdf_syntaxes(tmp_path, example):
    graph = example("weather_rdf.ttl").graph
    for name, format in (("doc.nt", "nt"), ("doc.rdf", "xml"), ("doc.jsonld", "json-ld")):
        path = tmp_path / name
        graph.serialize(path, format=format, encoding="utf-8")
        assert len(rdftss.load(path).source().read()) == 3


def test_a_missing_file_is_a_clear_error():
    with pytest.raises(rdftss.DocumentError, match="no file"):
        rdftss.load("there-is-no-such-file.ttl")


def _messages(document) -> list:
    return [issue.message for issue in document.all_issues]


def test_every_field_is_a_value_field_or_a_context_field(make):
    text = PREFIXES + """
ex:s a tss:EmbeddedRDFSource ; tss:immutable true ; tss:timeField ex:t ;
    tss:hasField ex:t, ex:v, ex:both, ex:none, ex:quality .
ex:t a tss:ValueField ; tss:datatype xsd:dateTime .
ex:v a tss:ValueField ; tss:datatype xsd:double .
ex:both a tss:ValueField, tss:ContextField ; tss:datatype xsd:double .
ex:none a tss:MetaField ; tss:datatype xsd:double .
ex:quality a tss:ContextField ; tss:datatype xsd:string ; tss:observedProperty ex:p ;
    tss:describesField ex:v ; tss:property ex:quality .
"""
    messages = _messages(make(text))
    assert any("'t' is the time field, so it must be a context field" in message for message in messages)
    assert any("both tss:ValueField and tss:ContextField" in message for message in messages)
    assert any("tss:MetaField is from an earlier draft, it is now tss:ContextField" in message for message in messages)
    assert any("is a context field, so it must not state tss:observedProperty" in message for message in messages)


def test_describing_fields_and_their_field_property(make):
    text = PREFIXES + """
ex:s a tss:CSVSource ; tss:immutable true ; tss:dataFile <https://example.org/data.csv> ;
    tss:timeField ex:t ; tss:hasField ex:t, ex:v, ex:status, ex:flag, ex:note .
ex:t a tss:ContextField ; tss:columnName "time" ; tss:datatype xsd:dateTime .
ex:v a tss:ValueField ; tss:columnName "value" ; tss:datatype xsd:double ; tss:property ex:wrong .
ex:status a tss:ContextField ; tss:columnName "status code" ; tss:datatype xsd:string ; tss:describesField ex:v .
ex:flag a tss:ContextField ; tss:columnName "flag" ; tss:datatype xsd:string ; tss:describesField ex:v ;
    tss:property tss:value .
ex:note a tss:ContextField ; tss:columnName "note" ; tss:datatype xsd:string ; tss:describesField ex:t .
"""
    document = make(text)
    source = document.source()
    status = source.field("status code")
    assert str(status.field_property) == "https://example.org/data.csv#status%20code"   # the default property
    assert status.describes == [source.field("value")] and status in source.field("value").described_by
    messages = _messages(document)
    assert any("'value' states tss:property, but only a describing field" in message for message in messages)
    assert any("must not be rdf:type or a term of the TSS vocabulary" in message for message in messages)
    assert any("must be a value field (tss:ValueField)" in message for message in messages)


def test_a_describing_field_of_rdf_points_needs_a_property_or_a_column_name(make):
    text = PREFIXES + """
ex:s a tss:EmbeddedRDFSource ; tss:immutable true ; tss:timeField ex:t ; tss:hasField ex:t, ex:v, ex:q .
ex:t a tss:ContextField ; tss:datatype xsd:dateTime .
ex:v a tss:ValueField ; tss:datatype xsd:double .
ex:q a tss:ContextField ; tss:datatype xsd:string ; tss:describesField ex:v .
"""
    source = make(text).source()
    assert any("must state tss:property or tss:columnName" in issue.message for issue in source.issues)
    with pytest.raises(rdftss.DocumentError, match="no field property"):
        source.read()


@pytest.mark.parametrize("kind, allowed", [("CSVSource", True), ("ArrowSource", True), ("EmbeddedRDFSource", True),
                                           ("ParquetSource", False), ("EmbeddedJSONSource", False)])
def test_range_requests_only_where_the_bytes_can_only_grow(make, kind, allowed):
    storage = "    tss:dataFile <d.csv> ;\n" if kind in ("CSVSource", "ArrowSource", "ParquetSource") else \
        '    tss:points "[]"^^rdf:JSON ;\n' if kind == "EmbeddedJSONSource" else ""
    text = source_text(kind=kind, extra=storage + "    tss:rangeRequests true ;\n",
                       time_field='; tss:columnName "time" ', value_field='; tss:columnName "value" ')
    source = make(text).source()
    assert source.range_requests
    refused = any("must not state tss:rangeRequests true" in issue.message for issue in source.issues)
    assert refused is not allowed


def test_about_url(make):
    def source_with(template, kind="CSVSource", storage="    tss:dataFile <d.csv> ;\n"):
        return make(source_text(kind=kind, extra=storage, time_field='; tss:columnName "time" ',
                                value_field=f'; tss:columnName "value" ; tss:aboutUrl "{template}" ')).source()

    good = source_with("https://example.org/obs/{time}")
    assert good.field("value").about_url == "https://example.org/obs/{time}" and not good.issues
    assert any("not the tss:columnName of a field" in issue.message for issue in source_with("{station}").issues)
    assert any("not a valid URI template" in issue.message for issue in source_with("{time").issues)
    rdf = source_with("{time}", kind="EmbeddedRDFSource", storage="")
    assert any("do not state tss:aboutUrl" in issue.message for issue in rdf.issues)


def test_classes_of_earlier_drafts_are_named(make):
    text = source_text(kind="CSVSnippet", extra="    tss:dataFile <d.csv> ;\n")
    source = make(text).source()
    assert source.kind is None
    assert any("tss:CSVSnippet is from an earlier draft, it is now tss:CSVSource" in issue.message
               for issue in source.issues)


def test_a_parquet_source_may_grow(make):
    text = source_text(kind="ParquetSource", extra="    tss:dataFile <d.parquet> ;\n",
                       time_field='; tss:columnName "time" ', value_field='; tss:columnName "value" ')
    source = make(text.replace("    tss:immutable true ;\n", "")).source()
    assert source.growing and not source.issues
