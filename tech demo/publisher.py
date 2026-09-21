import csv
import json
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import cbor2
import paho.mqtt.client as mqtt
from rdflib import Graph, Namespace
from rdflib.collection import Collection

TTL_PATH = Path(__file__).resolve().parent / "data_endpoint.ttl"
JSON_CONTENT_TYPE = "application/senml+json"
CBOR_CONTENT_TYPE = "application/senml+cbor"

# SenML CBOR integer labels (RFC 8428) for the fields this publisher uses
SENML_LABEL_NAME = 0
SENML_LABEL_VALUE = 2
SENML_LABEL_TIME = 6

CSVW = Namespace("http://www.w3.org/ns/csvw#")
TSS2 = Namespace("https://example.org/tss2#")
TD = Namespace("https://www.w3.org/2019/wot/td#")
HCTL = Namespace("https://www.w3.org/2019/wot/hypermedia#")


def get_form_target(graph: Graph, content_type: str) -> tuple[str, int, str]:
    """Finds the td:hasForm entry whose hctl:forContentType matches content_type
    and returns (host, port, topic) parsed from that Form's hctl:hasTarget -
    so the publisher only ever sends to the endpoint the spec actually declares
    for this encoding, never a hardcoded broker/topic."""
    for form in graph.objects(predicate=TD.hasForm):
        form_content_type = next(graph.objects(subject=form, predicate=HCTL.forContentType), None)
        if form_content_type is not None and str(form_content_type) == content_type:
            target = next(graph.objects(subject=form, predicate=HCTL.hasTarget))
            parsed = urlparse(str(target))
            return parsed.hostname, parsed.port or 1883, parsed.path.lstrip("/")
    raise ValueError(f"No td:hasForm found with hctl:forContentType {content_type!r}")


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


def append_row(csv_path: Path, columns: list[str], timestamp_col: str,
               timestamp_str: str, value: float) -> None:
    row = [timestamp_str if name == timestamp_col else value for name in columns]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


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
    value_col = next(name for name in columns if name != timestamp_col)
    print(f"Appending to: {csv_path}")
    print(f"Columns (in order): {columns}, timestamp column: {timestamp_col}")

    host, port, topic = get_form_target(graph, JSON_CONTENT_TYPE)
    print(f"selected Form with hctl:forContentType={JSON_CONTENT_TYPE!r}")
    print(f"broker: mqtt://{host}:{port}")
    print(f"topic:  {topic}")

    cbor_host, cbor_port, cbor_topic = get_form_target(graph, CBOR_CONTENT_TYPE)
    print(f"selected Form with hctl:forContentType={CBOR_CONTENT_TYPE!r}")
    print(f"broker: mqtt://{cbor_host}:{cbor_port}")
    print(f"topic:  {cbor_topic}")
    if (cbor_host, cbor_port) != (host, port):
        print("WARNING: JSON and CBOR Forms point at different brokers - "
              "this publisher only opens one MQTT connection, using the JSON broker.")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(host, port, keepalive=60)
    client.loop_start()  # runs network loop in background thread

    try:
        while True:
            value = round(random.uniform(18.0, 25.0), 2)
            now = datetime.now(timezone.utc)
            timestamp_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

            append_row(csv_path, columns, timestamp_col, timestamp_str, value)
            update_committed_until(TTL_PATH, timestamp_str)

            senml_record = {"n": value_col, "t": now.timestamp(), "v": value}
            payload = json.dumps(senml_record)
            client.publish(topic, payload=payload, qos=0)
            print(f"Published {payload} at {timestamp_str} to {topic}")

            # same data point, binary-compressed via SenML CBOR - published only,
            # never written to the CSV (that's what append_row above already did)
            cbor_record = {
                SENML_LABEL_NAME: value_col,
                SENML_LABEL_TIME: now.timestamp(),
                SENML_LABEL_VALUE: value,
            }
            cbor_payload = cbor2.dumps(cbor_record)
            client.publish(cbor_topic, payload=cbor_payload, qos=0)
            print(f"Published (CBOR, {len(cbor_payload)} bytes) {cbor_payload.hex()} "
                  f"at {timestamp_str} to {cbor_topic}")

            time.sleep(60)
    except KeyboardInterrupt:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
