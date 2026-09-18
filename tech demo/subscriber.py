"""Reads data_endpoint.ttl and uses its metadata to load the actual CSV data.

1) Parses data_endpoint.ttl with rdflib.
2) Extracts the csvw:url value (the location of the historical CSV).
3) Extracts hctl:hasTarget and splits it into broker + topic.
4) Reads csvw:column to learn the CSV's column names/order/datatypes, then
   uses that to load the CSV into memory and print the first 5 records.
"""

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname
import csv

import paho.mqtt.client as mqtt
from rdflib import Graph, Namespace
from rdflib.collection import Collection
from rdflib.namespace import XSD

TTL_PATH = Path(__file__).resolve().parent / "data_endpoint.ttl"

CSVW = Namespace("http://www.w3.org/ns/csvw#")
TSS2 = Namespace("https://example.org/tss2#")
HCTL = Namespace("https://www.w3.org/2019/wot/hypermedia#")
LDES = Namespace("https://w3id.org/ldes#")

# how to turn a CSV cell (always a string) into a Python value, based on
# the xsd: datatype declared for that column in csvw:datatype
XSD_CASTERS = {
    XSD.dateTime: lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
    XSD.double: float,
    XSD.float: float,
    XSD.integer: int,
    XSD.boolean: lambda s: s.strip().lower() == "true",
    XSD.string: str,
}


def file_uri_to_path(uri: str) -> Path:
    parsed = urlparse(str(uri))
    return Path(url2pathname(parsed.path))


def get_csv_url(graph: Graph) -> Path:
    url = next(graph.objects(predicate=CSVW.url))
    return file_uri_to_path(url)


def get_broker_and_topic(graph: Graph) -> tuple[str, int, str]:
    """Returns (host, port, topic), parsed from hctl:hasTarget so the actual
    MQTT connection is driven by the spec, not hardcoded."""
    target = next(graph.objects(predicate=HCTL.hasTarget))
    parsed = urlparse(str(target))
    host = parsed.hostname
    port = parsed.port or 1883
    topic = parsed.path.lstrip("/")
    return host, port, topic


def parse_datetime(literal) -> datetime:
    return datetime.fromisoformat(str(literal).replace("Z", "+00:00"))


def get_committed_until(graph: Graph) -> datetime:
    value = next(graph.objects(predicate=TSS2.committedUntil))
    return parse_datetime(value)


def get_retention_starting_from(graph: Graph) -> datetime:
    policy = next(graph.objects(predicate=LDES.retentionPolicy))
    value = next(graph.objects(subject=policy, predicate=LDES.startingFrom))
    return parse_datetime(value)


def get_column_schema(graph: Graph) -> list[dict]:
    schema = next(graph.objects(predicate=CSVW.tableSchema))
    column_list_head = next(graph.objects(subject=schema, predicate=CSVW.column))
    columns = []
    for col in Collection(graph, column_list_head):
        name = str(next(graph.objects(subject=col, predicate=CSVW.name)))
        datatype = next(graph.objects(subject=col, predicate=CSVW.datatype))
        columns.append({"name": name, "datatype": datatype})
    return columns


def load_csv(csv_path: Path, columns: list[dict]) -> list[dict]:
    expected_names = [c["name"] for c in columns]
    casters = {c["name"]: XSD_CASTERS.get(c["datatype"], str) for c in columns}

    records = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        sample = f.read(2048)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        reader = csv.DictReader(f, dialect=dialect)
        if reader.fieldnames != expected_names:
            print(f"WARNING: CSV header {reader.fieldnames} does not match "
                  f"csvw:column order {expected_names}")
        for row in reader:
            records.append({name: casters[name](value) for name, value in row.items()})
    return records


def listen_for_latest(host: str, port: int, topic: str) -> None:
    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Connected to {host}:{port} (result code {reason_code}), subscribing to {topic}")
        client.subscribe(topic)

    def on_message(client, userdata, msg):
        print(f"Latest point via MQTT [{msg.topic}]: {msg.payload.decode()}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(host, port, keepalive=60)
    print("Listening for live updates (Ctrl+C to stop)...")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        client.disconnect()


def main():
    graph = Graph()
    graph.parse(TTL_PATH, format="turtle")

    csv_path = get_csv_url(graph)
    print(f"csvw:url resolves to: {csv_path}")

    host, port, topic = get_broker_and_topic(graph)
    print(f"broker: mqtt://{host}:{port}")
    print(f"topic:  {topic}")

    committed_until = get_committed_until(graph)
    print(f"TSS2:committedUntil: {committed_until}")

    starting_from = get_retention_starting_from(graph)
    print(f"ldes:startingFrom:   {starting_from}")

    columns = get_column_schema(graph)
    print(f"columns: {[c['name'] for c in columns]}")

    records = load_csv(csv_path, columns)
    print(f"loaded {len(records)} historical records, first 5:")
    for record in records[:5]:
        print(record)

    listen_for_latest(host, port, topic)


if __name__ == "__main__":
    main()
