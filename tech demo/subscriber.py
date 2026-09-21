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
import json

import paho.mqtt.client as mqtt
from rdflib import Graph, Namespace
from rdflib.collection import Collection
from rdflib.namespace import XSD

TTL_PATH = Path(__file__).resolve().parent / "data_endpoint.ttl"
JSON_CONTENT_TYPE = "application/senml+json"

CSVW = Namespace("http://www.w3.org/ns/csvw#")
TSS2 = Namespace("https://example.org/tss2#")
TD = Namespace("https://www.w3.org/2019/wot/td#")
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


def get_form_target(graph: Graph, content_type: str) -> tuple[str, int, str, object]:
    """Finds the td:hasForm entry whose hctl:forContentType matches content_type
    (there can be several Forms for the same data, e.g. JSON vs CBOR) and
    returns (host, port, topic, sequence_field) parsed from that Form's
    hctl:hasTarget/TSS2:sequenceField."""
    for form in graph.objects(predicate=TD.hasForm):
        form_content_type = next(graph.objects(subject=form, predicate=HCTL.forContentType), None)
        if form_content_type is not None and str(form_content_type) == content_type:
            target = next(graph.objects(subject=form, predicate=HCTL.hasTarget))
            parsed = urlparse(str(target))
            seq_field = next(graph.objects(subject=form, predicate=TSS2.sequenceField), None)
            seq_field = seq_field.toPython() if seq_field is not None else None
            return parsed.hostname, parsed.port or 1883, parsed.path.lstrip("/"), seq_field
    raise ValueError(f"No td:hasForm found with hctl:forContentType {content_type!r}")


def resolve_senml_record(record: dict, base: dict, seq_field: str) -> dict:
    """Applies SenML base-field inheritance (RFC 8428): bn/bt/bu declared in an
    earlier record apply to every following record until overridden. seq_field
    is the TSS2:sequenceField name declared for this Form - not a standard
    SenML field, just read straight off the record."""
    return {
        "name": base.get("bn", "") + record.get("n", ""),
        "time": base.get("bt", 0) + record.get("t", 0),
        "unit": record.get("u", base.get("bu")),
        "value": record.get("v"),
        "seq": record.get(seq_field),
    }


def parse_senml_payload(payload: bytes, seq_field: str) -> list[dict]:
    """A SenML Pack is a JSON array of records; a single reading may also
    arrive as one bare record object rather than a one-element array."""
    data = json.loads(payload.decode("utf-8"))
    pack = data if isinstance(data, list) else [data]
    base = {}
    resolved = []
    for record in pack:
        for key in ("bn", "bt", "bu"):
            if key in record:
                base[key] = record[key]
        resolved.append(resolve_senml_record(record, base, seq_field))
    return resolved


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


def listen_for_latest(host: str, port: int, topic: str, seq_field: str, start_seq: int) -> None:
    last_seq = start_seq
    is_first_reading = True

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Connected to {host}:{port} (result code {reason_code}), subscribing to {topic}")
        client.subscribe(topic)

    def on_message(client, userdata, msg):
        nonlocal last_seq, is_first_reading
        size_bytes = len(msg.payload)
        try:
            records = parse_senml_payload(msg.payload, seq_field)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"Could not parse SenML JSON on [{msg.topic}]: {msg.payload!r} ({e})")
            return
        for record in records:
            seq = record["seq"]
            mismatch_note = ""
            if seq is not None and last_seq is not None and seq != last_seq + 1:
                missing = seq - last_seq - 1
                if is_first_reading:
                    mismatch_note = (f" [SEQUENCE NUMBER MISMATCH: CSV ends at seq {last_seq}, "
                                      f"first live reading is seq {seq} - missing {missing} reading(s)]")
                else:
                    mismatch_note = (f" [SEQUENCE NUMBER MISMATCH: expected seq {last_seq + 1}, "
                                      f"got {seq} - missing {missing} reading(s)]")
            if seq is not None:
                last_seq = seq
            is_first_reading = False
            print(f"Latest point via MQTT [{msg.topic}] ({size_bytes} bytes): "
                  f"name={record['name']} time={record['time']} "
                  f"value={record['value']} unit={record['unit']} seq={seq}{mismatch_note}")

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

    host, port, topic, seq_field = get_form_target(graph, JSON_CONTENT_TYPE)
    print(f"selected Form with hctl:forContentType={JSON_CONTENT_TYPE!r}")
    print(f"broker: mqtt://{host}:{port}")
    print(f"topic:  {topic}")
    print(f"sequence field: {seq_field!r}")

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

    start_seq = len(records)
    print(f"expecting first live seq to be {start_seq + 1}")

    listen_for_latest(host, port, topic, seq_field, start_seq)


if __name__ == "__main__":
    main()
