# TSS2 tech demo

A working proof-of-concept of the TSS2 spec design: historical readings live in a
plain CSV, new readings arrive live over MQTT, and a single RDF metadata file
(`data_endpoint.ttl`) describes both — location, column structure, units, live
endpoint, and retention — so that neither Python script hardcodes anything about
the data itself. Both scripts parse `data_endpoint.ttl` at startup and act on
whatever it says.

## Files

- **`data_endpoint.ttl`** — the metadata. Points at the CSV (`csvw:url`),
  describes its columns (`csvw:tableSchema`/`csvw:column`, with unit/observed
  property/sensor per column), declares the live MQTT endpoint
  (`td:isObservable`/`td:hasForm`/`hctl:hasTarget`), and tracks how far the data
  is safely committed (`TSS2:committedUntil`) and how far back it's retained
  (`ldes:retentionPolicy`).
- **`generate_historic_data.py`** — one-time seed script. Writes synthetic
  hourly temperature readings (seasonal + daily variation + noise) into
  `../data/sample_data.csv`. Only needs to be run once, or again if you want to
  regenerate the history from scratch.
- **`publisher.py`** — simulates the sensor. Reads `data_endpoint.ttl` to find
  the CSV location and column structure, then in a loop: generates a random
  reading, appends it to the CSV, updates `TSS2:committedUntil` in the `.ttl`
  file to match, and publishes the same reading over MQTT.
- **`subscriber.py`** — simulates a consumer. Reads `data_endpoint.ttl` to find
  the CSV location, column structure, retention/commit boundaries, and the live
  MQTT endpoint. Loads and prints the first 5 historical records from the CSV,
  then connects and listens for live readings from `publisher.py`.
- **`unused/`** — the original standalone MQTT publisher/subscriber scripts,
  kept for reference, from before they were wired up to read `data_endpoint.ttl`.

## Requirements

```
pip install rdflib paho-mqtt
```

Both scripts connect to the public test broker `test.mosquitto.org:1883` (via
`data_endpoint.ttl`'s `hctl:hasTarget`) — it's a shared public broker, so
messages on the topic could in principle be seen by anyone else using it too.

## How to run

1. **(once) Generate the historical CSV:**
   ```
   python generate_historic_data.py
   ```

2. **In one terminal, start the subscriber** (loads history, then listens live):
   ```
   python subscriber.py
   ```

3. **In a second terminal, start the publisher** (appends + publishes new readings):
   ```
   python publisher.py
   ```

4. Watch the subscriber's terminal — every ~2 seconds a new line should appear:
   ```
   Latest point via MQTT [example/sensors/temperature]: 22.4
   ```
   and each of those readings has also been appended as a new row in
   `../data/sample_data.csv`, with `TSS2:committedUntil` in `data_endpoint.ttl`
   updated to match.

Stop either script with Ctrl+C.
