"""Loading documents and reading the model: classes, defaults, rules reported as issues."""
import pytest

import rdftss
from rdftss import SnippetKind
from rdftss.vocab import XSD

from conftest import DEMO_11, EXAMPLES, PREFIXES, snippet_text


def test_every_example_loads_without_issues(example):
    for path in sorted(EXAMPLES.glob("*.ttl")):
        document = example(path.name)
        assert not document.all_issues, f"{path.name}: {document.all_issues}"
        assert not document.snippet().reading_problems


@pytest.mark.skipif(not DEMO_11.exists(), reason="the demo 11 folder is not next to rdftss")
def test_the_demo_11_documents_load_without_issues():
    documents = [rdftss.load(path) for path in sorted((DEMO_11 / "metadata endpoint").glob("*.ttl"))]
    assert len(documents) == 9
    for document in documents:
        assert not document.all_issues, f"{document.source}: {document.all_issues}"
    kinds = {document.snippet().kind for document in documents}
    assert kinds == {SnippetKind.CSV, SnippetKind.PARQUET, SnippetKind.EMBEDDED_RDF, SnippetKind.EMBEDDED_JSON}


def test_the_class_decides_where_the_points_are(example):
    assert example("embedded_rdf.ttl").snippet().kind == SnippetKind.EMBEDDED_RDF
    assert example("embedded_json.ttl").snippet().kind == SnippetKind.EMBEDDED_JSON
    assert example("readings_parquet.ttl").snippet().kind == SnippetKind.PARQUET
    assert example("readings_arrow.ttl").snippet().kind.is_external


def test_defaults_are_filled_in(make):
    snippet = make(snippet_text(extra='    tss:dataFile <data.csv> ;\n', time_field='; tss:columnName "time" ',
                                value_field='; tss:columnName "value" ')).snippet()
    assert snippet.csv == rdftss.CSVDialect(",", "utf-8", True)
    assert snippet.key is snippet.time_field
    assert snippet.data_file.endswith("/data.csv")


def test_prefixes_do_not_matter(make):
    text = snippet_text(extra='    tss:dataFile <data.csv> ;\n').replace("@prefix tss:", "@prefix t:").replace("tss:", "t:")
    snippet = make(text.replace("@prefix t: <https://w3id.org/tss#>", "@prefix t: <https://w3id.org/tss#>")).snippet()
    assert snippet.kind == SnippetKind.CSV and snippet.time_field.datatype == XSD.dateTimeStamp


def test_fields_are_found_by_name_iri_or_prefixed_name(example):
    snippet = example("readings_csv.ttl").snippet()
    field = snippet.field("value")
    assert snippet.field("ex:csv-value-field") is field
    assert snippet.field("https://example.org/ex#csv-value-field") is field
    assert snippet.field("Electrical conductivity") is field
    assert field.role == "measured" and snippet.field("quality").role == "describing"
    with pytest.raises(KeyError, match="Its fields are"):
        snippet.field("nope")


def test_rules_are_reported_as_issues_not_raised(make):
    text = PREFIXES + """
ex:s a tss:EmbeddedJSONSnippet ; tss:dataFile <x.csv> ; tss:hasPoint [] ;
    tss:timeField ex:t ; tss:key ex:other ; tss:hasField ex:t .
ex:t a tss:Field ; tss:datatype xsd:dateTime .
"""
    document = make(text)
    messages = [issue.message for issue in document.all_issues]
    assert any("must not state tss:hasPoint" in message for message in messages)
    assert any("must not state tss:dataFile" in message for message in messages)
    assert any("exactly one tss:points" in message for message in messages)
    assert any("tss:key" in message for message in messages)
    assert any("xsd:dateTimeStamp" in message for message in messages)
    with pytest.raises(rdftss.DocumentError, match="tss:points"):
        document.snippet().read()


def test_a_snippet_without_a_class_can_not_be_read(make):
    document = make(PREFIXES + "ex:s tss:timeField ex:t ; tss:hasField ex:t . ex:t tss:datatype xsd:dateTimeStamp .")
    assert document.snippet().kind is None
    with pytest.raises(rdftss.DocumentError, match="no snippet class"):
        document.snippet().read()


def test_a_document_must_not_mix_embedded_and_external_snippets(make):
    text = snippet_text(kind="EmbeddedRDFSnippet") + """
ex:s2 a tss:CSVSnippet ; tss:immutable true ;
    tss:dataFile <d.csv> ; tss:timeField ex:t2 ; tss:hasField ex:t2 .
ex:t2 a tss:Field ; tss:columnName "time" ; tss:datatype xsd:dateTimeStamp .
"""
    document = make(text)
    assert len(document.snippets) == 2
    assert any("embedded and external" in issue.message for issue in document.issues)
    with pytest.raises(rdftss.DocumentError, match="2 snippets"):
        document.snippet()
    assert document.snippet("ex:s2").kind == SnippetKind.CSV


def test_time_grid_rules(make):
    text = snippet_text(extra='    tss:dataFile <d.csv> ;\n    tss:timeBase "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;\n'
                              '    tss:timeStep "-PT15M"^^xsd:duration ;\n')
    snippet = make(text).snippet()
    assert snippet.time_grid is None
    assert any("more than zero" in problem for problem in snippet.reading_problems)
    assert any("tss:stepField" in problem for problem in snippet.reading_problems)


def test_loads_resolves_relative_iris_against_the_base():
    document = rdftss.loads(snippet_text(extra="    tss:dataFile <data.csv> ;\n"), base="https://example.org/meta/doc.ttl")
    assert document.snippet().data_file == "https://example.org/meta/data.csv"


def test_other_rdf_syntaxes(tmp_path, example):
    graph = example("weather_rdf.ttl").graph
    for name, format in (("doc.nt", "nt"), ("doc.rdf", "xml"), ("doc.jsonld", "json-ld")):
        path = tmp_path / name
        graph.serialize(path, format=format, encoding="utf-8")
        assert len(rdftss.load(path).snippet().read()) == 3


def test_a_missing_file_is_a_clear_error():
    with pytest.raises(rdftss.DocumentError, match="no file"):
        rdftss.load("there-is-no-such-file.ttl")
