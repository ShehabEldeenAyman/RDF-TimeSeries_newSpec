"""Times, durations, the time grid, datatypes and value encodings."""
from datetime import timezone
from decimal import Decimal

import pytest
from rdflib import Literal

from rdftss.encodings import DeltaFromBaseline
from rdftss.times import Duration, TimeGridMath, add_duration, parse_time, time_from_epoch, to_instant
from rdftss.vocab import XSD
from rdftss.xsd import lexical_form, read_value


class TestTimes:
    def test_times_are_compared_as_moments(self):
        assert parse_time("2024-05-12T10:00:00+02:00") == parse_time("2024-05-12T08:00:00Z")
        assert parse_time("2024-05-12T08:00:00Z") == parse_time("2024-05-12T08:00:00+00:00")
        assert parse_time("2024-05-12T08:00:00Z") < parse_time("2024-05-12T08:00:01Z")

    def test_the_text_is_kept(self):
        assert parse_time("2024-05-12T10:00:00+02:00").text == "2024-05-12T10:00:00+02:00"

    def test_a_timezone_is_required(self):
        with pytest.raises(ValueError, match="no timezone"):
            parse_time("2024-05-12T08:00:00")
        assert parse_time("2024-05-12T08:00:00", require_timezone=False).instant is None

    @pytest.mark.parametrize("text", ["2021-02-30T00:00:00Z", "2024-13-01T00:00:00Z", "2024-05-12T25:00:00Z",
                                      "2024-05-12T08:60:00Z", "2024-05-12T08:00:00+15:00", "2024-05-12 08:00:00Z",
                                      " 2024-05-12T08:00:00Z", "2024-05-12T24:00:01Z"])
    def test_invalid_times(self, text):
        with pytest.raises(ValueError):
            parse_time(text)

    def test_leap_day_and_end_of_day(self):
        assert parse_time("2024-02-29T00:00:00Z").datetime.day == 29
        end = parse_time("2024-12-31T24:00:00Z")
        assert end.datetime.year == 2025 and end == parse_time("2025-01-01T00:00:00Z")

    def test_fraction_digits_beyond_microseconds_are_kept_in_the_instant(self):
        a = parse_time("2024-05-12T08:00:00.123456789Z")
        b = parse_time("2024-05-12T08:00:00.123456780Z")
        assert a != b
        assert a.datetime.microsecond == 123456

    def test_to_instant_accepts_texts_dates_and_datetimes(self):
        expected = parse_time("2024-05-12T00:00:00Z").instant
        assert to_instant("2024-05-12") == expected
        assert to_instant("2024-05-12T00:00") == expected
        assert to_instant(parse_time("2024-05-12T00:00:00Z").datetime.astimezone(timezone.utc)) == expected

    def test_epoch_counts_as_parquet_and_arrow_store_them(self):
        assert time_from_epoch(1_715_500_800_000_000, 10**6, utc=True) == "2024-05-12T08:00:00Z"
        assert time_from_epoch(1_715_500_800_500_000_000, 10**9, utc=True) == "2024-05-12T08:00:00.5Z"
        assert time_from_epoch(1_715_500_800, 1, utc=False) == "2024-05-12T08:00:00"


class TestDurationsAndGrids:
    def test_durations(self):
        assert Duration.parse("PT15M").total_seconds == 900
        assert Duration.parse("P1Y2M").total_months == 14
        assert Duration.parse("P1DT2H").total_seconds == 93600
        assert not Duration.parse("-PT1M").is_positive
        assert not Duration.parse("PT0S").is_positive
        for text in ("P", "PT", "P1H", "15M", "P1.5D"):
            with pytest.raises(ValueError):
                Duration.parse(text)

    def test_shortest_form(self):
        assert Duration.of(seconds=900).text == "PT15M"
        assert Duration.of(seconds=Decimal("0.5")).text == "PT0.5S"
        assert Duration.of(months=13).text == "P1Y1M"

    def test_calendar_months_end_on_the_last_day(self):
        # The note of the specification: 2021-01-31 + P1M is 2021-02-28
        assert add_duration(parse_time("2021-01-31T00:00:00Z"), 1, 0).text == "2021-02-28T00:00:00Z"
        grid = TimeGridMath(parse_time("2021-01-31T00:00:00+01:00"), Duration.parse("P1M"))
        assert [grid.time_of(n).text for n in range(4)] == [
            "2021-01-31T00:00:00+01:00", "2021-02-28T00:00:00+01:00",
            "2021-03-31T00:00:00+01:00", "2021-04-30T00:00:00+01:00"]
        assert grid.step_of(grid.time_of(3)) == 3

    def test_the_grid_example_of_the_specification(self):
        grid = TimeGridMath(parse_time("2024-05-12T08:00:00Z"), Duration.parse("PT15M"))
        assert grid.time_of(5).text == "2024-05-12T09:15:00Z"
        assert grid.step_of(parse_time("2024-05-12T09:15:00Z")) == 5
        assert grid.step_of(parse_time("2024-05-12T09:16:00Z")) is None
        assert grid.step_of(parse_time("2024-05-12T07:45:00Z")) is None


class TestDatatypes:
    @pytest.mark.parametrize("text,datatype,value", [
        ("4810.5", XSD.double, 4810.5), ("INF", XSD.double, float("inf")), ("-INF", XSD.float, float("-inf")),
        ("1.50", XSD.decimal, Decimal("1.50")), ("-42", XSD.integer, -42), ("255", XSD.unsignedByte, 255),
        ("true", XSD.boolean, True), ("0", XSD.boolean, False), ("anything at all", XSD.string, "anything at all"),
    ])
    def test_valid_values(self, text, datatype, value):
        assert read_value(text, datatype) == value

    @pytest.mark.parametrize("text,datatype", [
        (" 12 ", XSD.double), ("1_000", XSD.double), ("1,5", XSD.decimal), ("yes", XSD.boolean),
        ("256", XSD.unsignedByte), ("2147483648", XSD.int), ("-1", XSD.nonNegativeInteger), ("0", XSD.positiveInteger),
        ("1.0", XSD.integer), ("2021-02-30", XSD.date), ("2024-05-12T08:00:00", XSD.dateTimeStamp), ("nan", XSD.double),
    ])
    def test_invalid_values_are_not_read(self, text, datatype):
        with pytest.raises(ValueError):
            read_value(text, datatype)

    def test_nan(self):
        value = read_value("NaN", XSD.double)
        assert value != value

    def test_lexical_forms(self):
        assert lexical_form(float("nan")) == "NaN"
        assert lexical_form(Decimal("1E+3")) == "1000"
        assert lexical_form(read_value("2024-05-12T08:00:00Z", XSD.dateTimeStamp)) == "2024-05-12T08:00:00Z"


class TestDeltaFromBaseline:
    def test_exact_decimal_sums(self):
        delta = DeltaFromBaseline(XSD.double, Literal("4750", datatype=XSD.double))
        assert delta.decode("60.5") == "4810.5"
        assert delta.decode("-27.6") == "4722.4"   # not 4722.400000000001
        assert delta.encode("4722.4") == "-27.6"
        assert delta.decode("INF") == "INF"

    def test_integers_stay_whole(self):
        delta = DeltaFromBaseline(XSD.integer, Literal("100", datatype=XSD.integer))
        assert delta.decode("-5") == "95"
        with pytest.raises(ValueError):
            delta.decode("0.5")
