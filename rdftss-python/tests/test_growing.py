"""Growing and immutable snippets: tss:immutable, RDF Message Logs, data files that grow, and the
rules that come with them."""
import pytest

import rdftss

from conftest import EXAMPLES, PREFIXES, snippet_text

GROWING = (EXAMPLES / "growing_rdf.ttl").read_text(encoding="utf-8")
LOG = 'VERSION "1.2-messages"\n' + PREFIXES


def point(time: str, value: str, label: str = "") -> str:
    node = f"_:{label} " if label else "[ "
    if label:
        return (f'ex:s tss:hasPoint {node}.\n{node}ex:time "{time}"^^xsd:dateTimeStamp ; '
                f'ex:value "{value}"^^xsd:double .\n')
    return f'ex:s tss:hasPoint [ ex:time "{time}"^^xsd:dateTimeStamp ; ex:value "{value}"^^xsd:double ] .\n'


def growing_rdf(*points: str, delimiter: str = "MESSAGE") -> str:
    description = snippet_text(kind="EmbeddedRDFSnippet").replace(PREFIXES, "").replace("    tss:immutable true ;\n", "")
    return LOG + description + delimiter + "\n" + "".join(text + delimiter + "\n" for text in points)


def messages_of(document) -> list:
    return [issue.message for issue in document.all_issues]


class TestImmutable:
    def test_the_default_is_growing(self):
        snippet = rdftss.loads(snippet_text(kind="EmbeddedJSONSnippet", extra='    tss:points "[]"^^rdf:JSON ;')
                               .replace("    tss:immutable true ;\n", "")).snippet()
        assert snippet.growing and not snippet.immutable
        assert "growing" in snippet.describe()

    def test_immutable(self, example):
        snippet = example("readings_csv.ttl").snippet()
        assert snippet.immutable and not snippet.growing
        assert "immutable: final" in snippet.describe()

    def test_a_parquet_snippet_is_always_immutable(self):
        document = rdftss.loads(snippet_text(kind="ParquetSnippet", extra="    tss:dataFile <d.parquet> ;")
                                .replace("tss:immutable true", "tss:immutable false"))
        assert any("must state tss:immutable true" in message for message in messages_of(document))

    def test_a_growing_snippet_has_no_summaries(self, example):
        text = (EXAMPLES / "readings_csv.ttl").read_text(encoding="utf-8")
        assert "tss:summary" not in text
        document = rdftss.loads(snippet_text(value_field='; tss:summary [ tss:count "3"^^xsd:integer ] ',
                                             extra="    tss:dataFile <d.csv> ;").replace("    tss:immutable true ;\n", ""))
        assert any("only an immutable snippet has stored summaries" in message for message in messages_of(document))

    def test_terms_of_earlier_drafts_are_warnings(self):
        text = snippet_text(extra='    tss:dataFile <d.csv> ; tss:mutable false ;\n'
                                  '    tss:committedUntil "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;',
                            time_field='; tss:columnName "time" ', value_field='; tss:columnName "value" ')
        issues = rdftss.loads(text).all_issues
        assert not issues.errors and len(issues.warnings) == 2
        assert "tss:mutable false is now tss:immutable true" in str(issues)

    def test_find(self, example):
        documents = [example("readings_csv.ttl"), example("embedded_json.ttl")]
        assert [match.snippet.name for match in rdftss.find(documents, immutable=False)] == ["json-snippet"]


class TestMessageLogs:
    def test_the_example(self, example):
        document = example("growing_rdf.ttl")
        assert document.message_log and document.messages == 4 and not document.all_issues
        assert len(document.snippet().read()) == 3
        assert document.validate().conforms

    def test_both_delimiters(self):
        for delimiter in ("MESSAGE", "@message .", "message"):
            document = rdftss.loads(growing_rdf(point("2024-05-12T08:00:00Z", "1"), delimiter=delimiter))
            assert document.messages == 2 and len(document.snippet().read()) == 1, delimiter

    def test_blank_node_labels_belong_to_their_message(self):
        document = rdftss.loads(growing_rdf(point("2024-05-12T08:00:00Z", "1", "p"), point("2024-05-12T08:15:00Z", "2", "p")))
        assert [p["value"] for p in document.snippet().read()] == [1.0, 2.0]

    def test_prefixes_stay_in_force(self):
        text = growing_rdf() + "PREFIX later: <https://example.org/ex#>\n" + point("2024-05-12T08:00:00Z", "1") \
            + "MESSAGE\n" + point("2024-05-12T08:15:00Z", "2").replace("ex:s ", "later:s ") + "MESSAGE\n"
        assert len(rdftss.loads(text).snippet().read()) == 2

    def test_a_message_that_no_delimiter_ends_is_not_read(self):
        text = growing_rdf(point("2024-05-12T08:00:00Z", "1")) + 'ex:s tss:hasPoint [ ex:time "2024-05-12T08:15:00Z'
        document = rdftss.loads(text)
        assert len(document.snippet().read()) == 1
        assert "still being written" in str(document.all_issues)

    def test_a_delimiter_inside_a_long_string_is_text(self):
        text = growing_rdf().replace('ex:s a tss:EmbeddedRDFSnippet ;',
                                     'ex:s a tss:EmbeddedRDFSnippet ;\n    rdfs:comment """two\nMESSAGE\nlines""" ;')
        document = rdftss.loads(text)
        assert document.messages == 1 and "MESSAGE" in str(document.snippet().comments[0])

    def test_a_growing_rdf_snippet_in_plain_turtle(self):
        text = snippet_text(kind="EmbeddedRDFSnippet").replace("    tss:immutable true ;\n", "")
        assert any("RDF Message Log" in message for message in messages_of(rdftss.loads(text)))

    def test_a_log_does_not_state_immutable_false(self):
        text = growing_rdf().replace("ex:s a tss:EmbeddedRDFSnippet ;", "ex:s a tss:EmbeddedRDFSnippet ; tss:immutable false ;")
        assert any("must not state tss:immutable false" in message for message in messages_of(rdftss.loads(text)))

    def test_ending_a_log(self):
        text = growing_rdf(point("2024-05-12T08:00:00Z", "1")) \
            + 'ex:s tss:immutable true .\nex:value tss:summary [ tss:count "1"^^xsd:integer ] .\nMESSAGE\n'
        document = rdftss.loads(text)
        assert document.snippet().immutable and not document.all_issues and document.verify_summaries()

    def test_a_growing_rdf_snippet_is_saved_as_a_log(self, tmp_path, example):
        path = example("growing_rdf.ttl").save(tmp_path / "copy.ttl")
        text = path.read_text(encoding="utf-8")
        assert text.startswith('VERSION "1.2-messages"\n') and text.count("MESSAGE\n") == 4
        appended = text + point("2024-05-12T09:00:00Z", "4700").replace("ex:s ", "ex:growing-snippet ").replace(
            "ex:time", "ex:growing-time-field").replace("ex:value", "ex:growing-value-field") + "MESSAGE\n"
        path.write_text(appended, encoding="utf-8")
        assert len(rdftss.load(path).snippet().read()) == 4

    def test_an_immutable_snippet_is_saved_as_plain_turtle(self, example):
        assert "MESSAGE" not in example("embedded_rdf.ttl").to_turtle()

    def test_a_shapes_file_or_any_other_document_is_not_a_log(self):
        assert rdftss.load(EXAMPLES / "readings_csv.ttl").message_log is False


class TestGrowingDataFiles:
    CSV = "time,value\n2024-05-12T08:00:00Z,1\n2024-05-12T08:15:00Z,2"

    def csv(self, make, immutable: bool):
        text = snippet_text(extra="    tss:dataFile <d.csv> ;", time_field='; tss:columnName "time" ',
                            value_field='; tss:columnName "value" ')
        if not immutable:
            text = text.replace("    tss:immutable true ;\n", "")
        return make(text, files={"d.csv": self.CSV}).snippet()

    def test_a_csv_record_without_its_line_break_is_still_being_written(self, make):
        snippet = self.csv(make, immutable=False)
        points = snippet.read(strict=False)
        assert len(points) == 1 and "still being written" in str(points.issues)

    def test_an_immutable_csv_file_may_end_without_a_line_break(self, make):
        assert len(self.csv(make, immutable=True).read()) == 2

    def arrow(self, make, tmp_path, *, stream: bool, immutable: bool, cut: int = 0):
        pa = pytest.importorskip("pyarrow")
        import pyarrow.ipc

        table = pa.table({"time": ["2024-05-12T08:00:00Z", "2024-05-12T08:15:00Z"], "value": [1.0, 2.0]})
        path = tmp_path / "d.arrows"
        with pa.OSFile(str(path), "wb") as sink:
            with (pyarrow.ipc.new_stream if stream else pyarrow.ipc.new_file)(sink, table.schema) as writer:
                for batch in table.to_batches(max_chunksize=1):
                    writer.write_batch(batch)
        if cut:
            path.write_bytes(path.read_bytes()[:-cut])
        text = snippet_text(kind="ArrowSnippet", extra="    tss:dataFile <d.arrows> ;", time_field='; tss:columnName "time" ',
                            value_field='; tss:columnName "value" ')
        if not immutable:
            text = text.replace("    tss:immutable true ;\n", "")
        return make(text).snippet()

    def test_an_arrow_stream(self, make, tmp_path):
        points = self.arrow(make, tmp_path, stream=True, immutable=False).read(strict=False)
        assert len(points) == 2 and not points.issues

    def test_an_arrow_file_can_not_grow(self, make, tmp_path):
        points = self.arrow(make, tmp_path, stream=False, immutable=False).read(strict=False)
        assert len(points) == 2 and "IPC file format" in str(points.issues)

    def test_the_last_message_of_a_growing_stream_may_be_incomplete(self, make, tmp_path):
        points = self.arrow(make, tmp_path, stream=True, immutable=False, cut=12).read(strict=False)
        assert len(points) == 1 and "not complete" in str(points.issues)


class TestConversion:
    def growing_csv(self, make):
        text = snippet_text(extra="    tss:dataFile <d.csv> ;", time_field='; tss:columnName "time" ',
                            value_field='; tss:columnName "value" ').replace("    tss:immutable true ;\n", "")
        return make(text, files={"d.csv": "time,value\n2024-05-12T08:00:00Z,1\n2024-05-12T08:15:00Z,2\n"}).snippet()

    def test_parquet_is_immutable(self, make, tmp_path):
        pytest.importorskip("pyarrow")
        converted = rdftss.convert(self.growing_csv(make), "parquet", document=tmp_path / "out" / "p.ttl")
        assert converted.snippet().immutable and not converted.all_issues

    def test_a_growing_arrow_snippet_is_a_stream(self, make, tmp_path):
        pytest.importorskip("pyarrow")
        converted = rdftss.convert(self.growing_csv(make), "arrow", document=tmp_path / "out" / "a.ttl")
        snippet = converted.snippet()
        assert snippet.growing and snippet.data_file.endswith(".arrows") and not converted.all_issues
        assert (tmp_path / "out" / "a.arrows").read_bytes()[:4] == b"\xff\xff\xff\xff"
        assert len(snippet.read()) == 2

    def test_a_growing_rdf_snippet_becomes_a_log(self, make, tmp_path):
        converted = rdftss.convert(self.growing_csv(make), "rdf", document=tmp_path / "out" / "r.ttl")
        assert converted.message_log and converted.messages == 3 and not converted.all_issues

    def test_a_growing_snippet_gets_no_summaries(self, make, tmp_path):
        converted = rdftss.merge([self.growing_csv(make)], document=tmp_path / "out" / "m.ttl")
        snippet = converted.snippet()
        assert snippet.growing and all(field.summary is None for field in snippet.fields)
