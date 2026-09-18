import csv
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import paho.mqtt.client as mqtt
from rdflib import Graph, Namespace
from rdflib.collection import Collection

BROKER = "test.mosquitto.org"
PORT = 1883
TOPIC = "example/sensors/temperature"

TTL_PATH = Path(__file__).resolve().parent / "data_endpoint.ttl"

CSVW = Namespace("http://www.w3.org/ns/csvw#")
TSS2 = Namespace("https://example.org/tss2#")


def file_uri_to_path(uri) -> Path:
    parsed = urlparse(str(uri))
    return Path(url2pathname(parsed.path))


def get_csv_path(graph: Graph) -> Path:
    url = next(graph.objects(predicate=CSVW.url))
    return file_uri_to_path(url)


def get_columns(graph: Graph) -> tuple[list[str], str]:
    """Returns (column names in declared order, name of the timestamp column)."""
    schema = next(graph.objects(predicate=CSVW.tableSchema))
    column_list_head = next(graph.objects(subject=schema, predicate=CSVW.column))
    columns = []
    timestamp_col = None
    for col in Collection(graph, column_list_head):
        name = str(next(graph.objects(subject=col, predicate=CSVW.name)))
        columns.append(name)
        is_timestamp = next(graph.objects(subject=col, predicate=TSS2.isTimestampColumn), None)
        if is_timestamp is not None and str(is_timestamp).lower() == "true":
            timestamp_col = name
    return columns, timestamp_col


def sniff_dialect(csv_path: Path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        sample = f.read(2048)
    return csv.Sniffer().sniff(sample, delimiters=",;\t")


def append_row(csv_path: Path, columns: list[str], timestamp_col: str,
               timestamp_str: str, value: float, dialect) -> None:
    row = [timestamp_str if name == timestamp_col else value for name in columns]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f, dialect=dialect).writerow(row)


def update_committed_until(ttl_path: Path, new_timestamp: str) -> None:
    text = ttl_path.read_text(encoding="utf-8")
    updated = re.sub(
        r'(TSS2:committedUntil\s+")[^"]*("\^\^xsd:dateTime)',
        rf"\g<1>{new_timestamp}\g<2>",
        text,
    )
    ttl_path.write_text(updated, encoding="utf-8")


def main():
    graph = Graph()
    graph.parse(TTL_PATH, format="turtle")

    csv_path = get_csv_path(graph)
    columns, timestamp_col = get_columns(graph)
    dialect = sniff_dialect(csv_path)
    print(f"Appending to: {csv_path}")
    print(f"Columns (in order): {columns}, timestamp column: {timestamp_col}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(BROKER, PORT, keepalive=60)
    client.loop_start()  # runs network loop in background thread

    try:
        while True:
            value = round(random.uniform(18.0, 25.0), 2)
            timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            append_row(csv_path, columns, timestamp_col, timestamp_str, value, dialect)
            update_committed_until(TTL_PATH, timestamp_str)

            client.publish(TOPIC, payload=str(value), qos=0)
            print(f"Published {value} at {timestamp_str} to {TOPIC}")
            time.sleep(2)
    except KeyboardInterrupt:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
