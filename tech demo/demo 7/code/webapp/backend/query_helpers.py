"""Helper functions for the query form (data_routes.query_series): the times and numbers typed in the
form, the summary numbers, the gaps in the data and the hourly/daily values. No routes."""
from fastapi import HTTPException
from datetime import datetime, timezone


def parse_time(text):
    """Read a date-time typed in the query form, such as 2021-03-04T00:00.
    A time without a timezone is taken as UTC. (Times in the RDF are read by rdflib instead,
    see value_reading.literal_time, and times in the data by value_reading.data_time.)"""
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Not a valid date-time: '{text}' (expected something like 2021-03-04T00:00)")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def parse_number(text, what):
    """A number typed in the form, or None when the field was left empty"""
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{what} must be a number, not '{text}'")


def time_text(moment):
    """A date-time as text, always in UTC, e.g. 2021-03-04T01:00:00Z"""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def duration_text(seconds):
    """A number of seconds as text people can read, e.g. 2 d 3 h 15 min"""
    minutes = int(seconds // 60)
    days = minutes // (24 * 60)
    hours = (minutes // 60) % 24
    minutes = minutes % 60
    parts = []
    if days > 0:
        parts.append(f"{days} d")
    if hours > 0:
        parts.append(f"{hours} h")
    if minutes > 0 or len(parts) == 0:
        parts.append(f"{minutes} min")
    return " ".join(parts)


def summarize(readings):
    """Count, mean, min and max of the readings (empty cells are counted apart).
    Each reading is {"moment": its time (a datetime), "value": its value}."""
    count = 0
    empty = 0
    total = 0
    lowest = None
    highest = None
    for reading in readings:
        value = reading["value"]
        if value is None:
            empty += 1
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        count += 1
        total += value
        if lowest is None or value < lowest["value"]:
            lowest = reading
        if highest is None or value > highest["value"]:
            highest = reading

    summary = {"count": count, "empty": empty, "mean": None, "min": None, "max": None}
    if count > 0:
        summary["mean"] = round(total / count, 3)
        summary["min"] = {"value": lowest["value"], "time": time_text(lowest["moment"])}
        summary["max"] = {"value": highest["value"], "time": time_text(highest["moment"])}
    return summary


def find_gaps(moments):
    """Gaps: places where the time between two readings is longer than usual.
    "Usual" is the step that occurs most often (15 minutes in our files).
    moments must be in time order, like the rows of our CSV files."""
    if len(moments) < 3:
        return {"step": None, "count": 0, "missing": None, "longest": []}

    # 1. How often does each step occur? {900.0: 80000, 1800.0: 4000, ...} (in seconds)
    steps = {}
    for i in range(1, len(moments)):
        seconds = (moments[i] - moments[i - 1]).total_seconds()
        steps[seconds] = steps.get(seconds, 0) + 1
    usual = None
    for seconds, how_often in steps.items():
        if usual is None or how_often > steps[usual]:
            usual = seconds

    # 2. Every step longer than the usual one is a gap
    gaps = []
    missing = 0
    for i in range(1, len(moments)):
        seconds = (moments[i] - moments[i - 1]).total_seconds()
        if seconds > usual:
            gaps.append((seconds, moments[i - 1], moments[i]))
            missing += seconds - usual
    gaps.sort(reverse=True)  # longest first, because the length comes first in each gap

    longest = []
    for seconds, before, after in gaps[:5]:
        longest.append({"from": time_text(before), "until": time_text(after), "length": duration_text(seconds)})

    return {
        "step": duration_text(usual),
        "count": len(gaps),
        "missing": duration_text(missing),
        "longest": longest,
    }


def resample_readings(readings, period, aggregate):
    """Put the readings in groups per hour or per day, and turn each group into
    one value: the mean, the min or the max of the values in it"""
    groups = {}  # start of the hour (or day) -> the values in it. A dict keeps the time order.
    for reading in readings:
        moment = reading["moment"].astimezone(timezone.utc)
        if period == "hour":
            group_start = moment.replace(minute=0, second=0, microsecond=0)
        else:
            group_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        if group_start not in groups:
            groups[group_start] = []
        if reading["value"] is not None:
            groups[group_start].append(reading["value"])

    rows = []
    for group_start, values in groups.items():
        if len(values) == 0:
            value = None
        elif aggregate == "min":
            value = min(values)
        elif aggregate == "max":
            value = max(values)
        else:
            value = round(sum(values) / len(values), 3)
        rows.append({"time": time_text(group_start), "value": value, "count": len(values)})
    return rows
