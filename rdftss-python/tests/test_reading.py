"""Reading points from the five source classes, with the rules of the specification."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import rdftss
from rdftss import DataError

from conftest import source_text

pa = pytest.importorskip("pyarrow")
import pyarrow.ipc  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

CSV_COLUMNS = dict(extra="    tss:dataFile <data.csv> ;\n", time_field='; tss:columnName "time" ',
                   value_field='; tss:columnName "value" ')


def csv_source(make, data: str, extra: str = "", **options):
    text = source_text(extra=CSV_COLUMNS["extra"] + extra, time_field=CSV_COLUMNS["time_field"],
                        value_field=CSV_COLUMNS["value_field"])
    return make(text, {"data.csv": data}, **options).source()


class TestEveryClass:
    def test_all_examples_give_the_same_readings(self, example):
        reference = [(p.instant, p["value"]) for p in example("readings_csv.ttl").source().read()]
        for name in ("readings_no_header.ttl", "readings_grid.ttl", "readings_parquet.ttl", "readings_arrow.ttl"):
            points = example(name).source().read()
            assert [(p.instant, p["value"]) for p in points] == reference, name
        delta = example("readings_delta.ttl").source().read()
        assert [(p.instant, p["value_delta"]) for p in delta] == reference

    def test_rdf_points(self, example):
        points = example("embedded_rdf.ttl").source().read()
        assert [p.time_text for p in points] == ["2024-05-12T08:00:00Z", "2024-05-12T08:15:00Z",
                                                 "2024-05-12T08:30:00Z", "2024-05-12T09:15:00Z", "2024-05-12T09:45:00Z"]
        assert points[0]["value-field"] == 4810.5
        assert points[0].text("value-field") == "4810.5"

    def test_json_points(self, example):
        points = example("embedded_json.ttl").source().read()
        assert [p["value"] for p in points] == [4810.5, 4722.4, 4751.9, 4719.3, 4783.6]

    def test_a_missing_value_is_an_empty_value(self, example):
        points = example("weather_rdf.ttl").source().read()
        assert points[1]["humidity-field"] is None and not points[1].has("humidity-field")
        assert points[1]["temperature-field"] == 18.4

    def test_grid_times_and_steps(self, example):
        points = example("readings_grid.ttl").source().read()
        assert points[0].step == 0 and points[0].time_text == "2024-05-12T00:00:00Z"
        assert points[20].step == 24 and points[20].time_text == "2024-05-12T06:00:00Z"

    def test_delta_values_are_decoded_exactly(self, example):
        point = example("readings_delta.ttl").source().read()[0]
        assert point.raw("value_delta") == "-3.00"
        assert point.text("value_delta") == "4747.00"
        assert point["value_delta"] == 4747.0


class TestRules:
    def test_points_come_in_time_order(self, make):
        source = csv_source(make, "time,value\n2024-05-12T08:30:00Z,3\n2024-05-12T08:00:00Z,1\n")
        assert [p["value"] for p in source.read()] == [1.0, 3.0]

    def test_rows_outside_the_window_are_skipped(self, make):
        data = "time,value\n2024-05-12T07:59:59Z,0\n2024-05-12T08:00:00Z,1\n2024-05-12T10:00:00Z,2\n"
        source = csv_source(make, data)
        points = source.read()
        assert [p["value"] for p in points] == [1.0] and points.outside_window == 2
        assert len(source.read(window=False)) == 3

    def test_an_embedded_point_outside_the_window_is_an_error(self, make):
        text = source_text(kind="EmbeddedRDFSource") + """
ex:s tss:hasPoint [ tss:time "2024-05-12T11:00:00Z"^^xsd:dateTime ; tss:value "1"^^xsd:double ] ."""
        with pytest.raises(DataError, match="outside the time window"):
            make(text).source().read()

    def test_two_points_at_the_same_moment(self, make):
        source = csv_source(make, "time,value\n2024-05-12T08:00:00Z,1\n2024-05-12T10:00:00+02:00,2\n")
        with pytest.raises(DataError, match="same time") as error:
            source.read()
        assert error.value.where == "CSV line 3"

    def test_a_point_without_a_time(self, make):
        with pytest.raises(DataError, match="no time"):
            csv_source(make, "time,value\n,1\n").read()

    def test_a_time_without_a_timezone(self, make):
        with pytest.raises(DataError, match="timezone"):
            csv_source(make, "time,value\n2024-05-12T08:00:00,1\n").read()

    def test_a_value_keeps_its_text_and_must_be_in_its_lexical_form(self, make):
        source = csv_source(make, "time,value\n2024-05-12T08:00:00Z, 12 \n")
        with pytest.raises(DataError, match="' 12 ' is not an xsd:double"):
            source.read()

    def test_lenient_reading_lists_the_problems(self, make):
        data = "time,value\n2024-05-12T08:00:00Z,1\n2024-05-12T08:15:00Z,yes\n,2\n2024-05-12T08:00:00Z,3\n"
        points = csv_source(make, data).read(strict=False)
        assert len(points) == 2 and points[1]["value"] is None
        assert len(points.issues.errors) == 3

    def test_an_empty_text_is_no_value_but_spaces_are(self, make):
        text = source_text(kind="EmbeddedRDFSource") + """
ex:quality a tss:ContextField ; tss:datatype xsd:string ; tss:describesField ex:value ; tss:property ex:q .
ex:s tss:hasField ex:quality .
ex:s tss:hasPoint [ tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "1"^^xsd:double ; ex:q ""^^xsd:string ] .
ex:s tss:hasPoint [ tss:time "2024-05-12T08:15:00Z"^^xsd:dateTime ; tss:value "2"^^xsd:double ; ex:q " "^^xsd:string ] ."""
        points = make(text).source().read()
        assert points[0]["quality"] is None and points[1]["quality"] == " "


class TestRDFPoints:
    def make_points(self, make, point: str):
        text = source_text(kind="EmbeddedRDFSource") + f"\nex:s tss:hasPoint [ {point} ] ."
        return make(text).source()

    def test_a_node_as_value(self, make):
        source = self.make_points(make, 'tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value ex:x')
        with pytest.raises(DataError, match="must be a literal"):
            source.read()

    def test_two_values_for_one_point(self, make):
        source = self.make_points(make, 'tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; '
                                         'tss:value "1"^^xsd:double, "2"^^xsd:double')
        with pytest.raises(DataError, match="states 2 tss:value; a point states exactly one"):
            source.read()

    def test_the_literal_must_have_the_datatype_of_its_field(self, make):
        source = self.make_points(make, 'tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "1"^^xsd:decimal')
        with pytest.raises(DataError, match="datatype xsd:double"):
            source.read()

    def test_a_property_that_is_no_field_property_is_a_warning(self, make):
        source = self.make_points(make, 'tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "1"^^xsd:double ; '
                                         'ex:other "x"')
        rows = source.read()
        assert len(rows) == 1 and "not the field property of a describing field" in rows.issues.warnings[0].message

    def test_the_value_field_of_a_point_defaults_to_the_only_one(self, make):
        source = self.make_points(make, 'tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "1"^^xsd:double')
        point = next(source.points())
        assert point.field is source.field("value") and str(point.value) == "1"

    def test_points_of_several_value_fields(self, make):
        text = source_text(kind="EmbeddedRDFSource", fields=", ex:other") + """
ex:other a tss:ValueField ; tss:datatype xsd:double .
ex:s tss:hasPoint [ tss:field ex:value ; tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "1"^^xsd:double ],
    [ tss:field ex:other ; tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "2"^^xsd:double ] .
ex:s tss:hasPoint [ tss:field ex:other ; tss:time "2024-05-12T08:15:00Z"^^xsd:dateTime ; tss:value "3"^^xsd:double ] ."""
        source = make(text).source()
        rows = source.read()
        assert [(row["value"], row["other"]) for row in rows] == [(1.0, 2.0), (None, 3.0)]   # two rows, three points
        assert len(list(source.points())) == 3
        with pytest.raises(DataError, match="states no tss:field"):
            make(text.replace("tss:field ex:other ; tss:time \"2024-05-12T08:15", "tss:time \"2024-05-12T08:15")).source().read()

    def test_two_points_of_one_field_at_the_same_time(self, make):
        text = source_text(kind="EmbeddedRDFSource") + """
ex:s tss:hasPoint [ tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "1"^^xsd:double ] .
ex:s tss:hasPoint [ tss:time "2024-05-12T10:00:00+02:00"^^xsd:dateTime ; tss:value "2"^^xsd:double ] ."""
        with pytest.raises(DataError, match="second point of field 'value'"):
            make(text).source().read()

    def test_tss_field_names_a_value_field_of_the_source(self, make):
        source = self.make_points(make, 'tss:field ex:time ; tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; '
                                         'tss:value "1"^^xsd:double')
        with pytest.raises(DataError, match="is not a value field of the source"):
            source.read()

    def test_describing_values(self, make):
        text = source_text(kind="EmbeddedRDFSource", fields=", ex:other, ex:quality") + """
ex:other a tss:ValueField ; tss:datatype xsd:double .
ex:quality a tss:ContextField ; tss:datatype xsd:string ; tss:describesField ex:value, ex:other ; tss:property ex:q .
ex:s tss:hasPoint [ tss:field ex:value ; tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "1"^^xsd:double ;
        ex:q "good" ],
    [ tss:field ex:other ; tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; tss:value "2"^^xsd:double ; ex:q "good" ] ."""
        source = make(text).source()
        assert source.read()[0]["quality"] == "good"
        assert all(str(point.describing[source.field("quality")]) == "good" for point in source.points())
        with pytest.raises(DataError, match="two values in the row"):
            make(text.replace('ex:q "good" ] .', 'ex:q "bad" ] .')).source().read()
        only_value = text.replace("tss:describesField ex:value, ex:other", "tss:describesField ex:value")
        with pytest.raises(DataError, match="which does not describe field 'other'"):
            make(only_value).source().read()


class TestJSONPoints:
    def make_points(self, make, points: str):
        text = source_text(kind="EmbeddedJSONSource", extra=f"    tss:points '''{points}'''^^rdf:JSON ;\n",
                            time_field='; tss:columnName "time" ', value_field='; tss:columnName "value" ')
        return make(text).source()

    def test_null_empty_and_missing_keys_are_empty_values(self, make):
        source = self.make_points(make, '[{"time": "2024-05-12T08:00:00Z", "value": null},'
                                         ' {"time": "2024-05-12T08:15:00Z", "value": ""},'
                                         ' {"time": "2024-05-12T08:30:00Z"}]')
        assert [p["value"] for p in source.read()] == [None, None, None]

    def test_keys_must_not_start_with_an_at_sign(self, make):
        source = self.make_points(make, '[{"@id": "x", "time": "2024-05-12T08:00:00Z"}]')
        with pytest.raises(DataError, match="must not start with @"):
            source.read()

    def test_json_numbers_keep_their_text_with_a_warning(self, make):
        points = self.make_points(make, '[{"time": "2024-05-12T08:00:00Z", "value": 2.50}]').read()
        assert points[0].text("value") == "2.50"
        assert any("JSON numbers" in issue.message for issue in points.issues.warnings)

    def test_an_unknown_key_is_a_warning(self, make):
        points = self.make_points(make, '[{"time": "2024-05-12T08:00:00Z", "other": "1"}]').read()
        assert any("'other'" in issue.message for issue in points.issues.warnings)


class TestCSV:
    def test_no_delimiter_guessing(self, make):
        with pytest.raises(DataError, match="no column 'time'"):
            csv_source(make, "time;value\n2024-05-12T08:00:00Z;1\n").read()

    def test_delimiter_quotes_and_encoding(self, make):
        data = 'time;value\n"2024-05-12T08:00:00Z";"1.5"\n'.encode("utf-16")
        text = source_text(extra=CSV_COLUMNS["extra"] + '    tss:delimiter ";" ;\n    tss:encoding "utf-16" ;\n',
                            time_field=CSV_COLUMNS["time_field"], value_field=CSV_COLUMNS["value_field"])
        assert make(text, {"data.csv": data}).source().read()[0]["value"] == 1.5

    def test_a_byte_order_mark_is_not_part_of_the_first_column_name(self, make):
        source = csv_source(make, "﻿time,value\n2024-05-12T08:00:00Z,1\n")
        assert len(source.read()) == 1

    def test_without_a_header_the_columns_are_found_by_index(self, example):
        assert example("readings_no_header.ttl").source().read()[0]["quality"] == "suspect"

    def test_a_missing_data_file(self, make):
        text = source_text(extra="    tss:dataFile <missing.csv> ;\n", time_field=CSV_COLUMNS["time_field"],
                            value_field=CSV_COLUMNS["value_field"])
        with pytest.raises(rdftss.DataFileError, match="does not exist"):
            make(text).source().read()

    def test_the_data_folder_keeps_reading_inside_it(self, make, tmp_path):
        text = source_text(extra="    tss:dataFile <../secret.csv> ;\n", time_field=CSV_COLUMNS["time_field"],
                            value_field=CSV_COLUMNS["value_field"])
        (tmp_path.parent / "secret.csv").write_text("time,value\n")
        with pytest.raises(rdftss.DataFileError, match="outside the allowed data folder"):
            make(text, data_folder=tmp_path).source().read()

    def test_set_data_file(self, make, tmp_path):
        source = csv_source(make, "time,value\n2024-05-12T08:00:00Z,1\n")
        (tmp_path / "other.csv").write_text("time,value\n2024-05-12T08:00:00Z,7\n")
        assert source.set_data_file(tmp_path / "other.csv").read()[0]["value"] == 7.0


class TestParquetAndArrow:
    def write(self, tmp_path, table, kind):
        if kind == "parquet":
            pq.write_table(table, tmp_path / "data.parquet")
        else:
            with pa.OSFile(str(tmp_path / "data.arrow"), "wb") as sink, pyarrow.ipc.new_file(sink, table.schema) as writer:
                writer.write_table(table)

    def source(self, make, tmp_path, table, kind, value_datatype="xsd:double"):
        self.write(tmp_path, table, kind)
        cls = "ParquetSource" if kind == "parquet" else "ArrowSource"
        text = source_text(kind=cls, extra=f"    tss:dataFile <data.{kind}> ;\n", time_field='; tss:columnName "time" ',
                            value_field='; tss:columnName "value" ').replace("tss:datatype xsd:double", f"tss:datatype {value_datatype}")
        return make(text).source()

    @pytest.mark.parametrize("kind", ["parquet", "arrow"])
    @pytest.mark.parametrize("arrow_type,datatype,values,texts", [
        (pa.float64(), "xsd:double", [1.5, float("inf"), float("nan")], ["1.5", "INF", "NaN"]),
        (pa.float32(), "xsd:float", [0.1, None, -2.0], ["0.1", None, "-2.0"]),
        (pa.decimal128(6, 2), "xsd:decimal", [Decimal("1.5"), Decimal("-0.25"), None], ["1.50", "-0.25", None]),
        (pa.int32(), "xsd:int", [1, -2, 3], ["1", "-2", "3"]),
        (pa.uint8(), "xsd:unsignedByte", [1, 2, 255], ["1", "2", "255"]),
        (pa.bool_(), "xsd:boolean", [True, False, None], ["true", "false", None]),
        (pa.string(), "xsd:decimal", ["1.50", "2", None], ["1.50", "2", None]),
        (pa.date32(), "xsd:date", [date(2024, 5, 12), None, date(2024, 2, 29)], ["2024-05-12", None, "2024-02-29"]),
    ])
    def test_lexical_forms_of_the_types(self, make, tmp_path, kind, arrow_type, datatype, values, texts):
        times = pa.array([1715500800, 1715501700, 1715502600], type=pa.int64()).cast(pa.timestamp("s", tz="UTC"))
        table = pa.table({"time": times, "value": pa.array(values, type=arrow_type)})
        points = self.source(make, tmp_path, table, kind, datatype).read()
        assert [p.text("value") for p in points] == texts
        assert points[0].time_text == "2024-05-12T08:00:00Z"

    def test_nanosecond_timestamps(self, make, tmp_path):
        times = pa.array([1715500800_000000001, 1715500800_000000002], type=pa.int64()).cast(pa.timestamp("ns", tz="UTC"))
        table = pa.table({"time": times, "value": pa.array([1.0, 2.0])})
        points = self.source(make, tmp_path, table, "arrow").read()
        assert [p.time_text for p in points] == ["2024-05-12T08:00:00.000000001Z", "2024-05-12T08:00:00.000000002Z"]

    def test_a_timestamp_without_timezone_can_not_be_the_time_column(self, make, tmp_path):
        table = pa.table({"time": pa.array([datetime(2024, 5, 12, 8)], type=pa.timestamp("us")), "value": [1.0]})
        with pytest.raises(DataError, match="no timezone"):
            self.source(make, tmp_path, table, "parquet").read()

    def test_the_datatype_must_be_allowed_for_the_column_type(self, make, tmp_path):
        times = pa.array([datetime(2024, 5, 12, 8, tzinfo=timezone.utc)], type=pa.timestamp("us", tz="UTC"))
        table = pa.table({"time": times, "value": pa.array([1], type=pa.int64())})
        with pytest.raises(DataError, match="does not allow"):
            self.source(make, tmp_path, table, "parquet").read()

    def test_unsupported_types(self, make, tmp_path):
        times = pa.array([datetime(2024, 5, 12, 8, tzinfo=timezone.utc)], type=pa.timestamp("us", tz="UTC"))
        table = pa.table({"time": times, "value": pa.array([[1.0]], type=pa.list_(pa.float64()))})
        with pytest.raises(rdftss.UnsupportedError):
            self.source(make, tmp_path, table, "arrow").read()

    def test_dictionary_columns_are_read_as_their_values(self, example):
        assert {p["quality"] for p in example("readings_arrow.ttl").source().read()} == {"good", "suspect"}


def test_iter_rows_streams(example):
    reader = example("readings_csv.ttl").source().iter_rows()
    assert sum(1 for _ in reader) == 85 and reader.outside_window == 0


def test_documents_and_data_online(tmp_path):
    """A document loaded from a URL finds its data file next to it, on the web."""
    import functools
    import http.server
    import threading

    (tmp_path / "meta").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "d.csv").write_text("time,value\n2024-05-12T08:00:00Z,1\n")
    text = source_text(extra="    tss:dataFile <../data/d.csv> ;\n", time_field='; tss:columnName "time" ',
                        value_field='; tss:columnName "value" ')
    (tmp_path / "meta" / "doc.ttl").write_text(text)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/meta/doc.ttl"
        document = rdftss.load(url)
        assert document.source().data_file.endswith("/data/d.csv")
        assert document.source().read()[0]["value"] == 1.0
        with pytest.raises(rdftss.DataFileError, match="downloads are off"):
            rdftss.load(url, allow_network=False).source().read()
        cache = tmp_path / "cache"
        assert len(rdftss.load(url, cache_folder=cache).source().read()) == 1
        assert len(rdftss.load(url, cache_folder=cache).source().read()) == 1
    finally:
        server.shutdown()
