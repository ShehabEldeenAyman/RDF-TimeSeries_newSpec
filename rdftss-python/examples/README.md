# Examples

Metadata documents for every source class, and the data they describe. Run `python quickstart.py` for a tour of the library with them.

| Document | Class | What it shows |
| --- | --- | --- |
| [`embedded_rdf.ttl`](embedded_rdf.ttl) | `tss:EmbeddedRDFSource` | the specification's embedded example: five rows of one point each, and a stored summary |
| [`weather_rdf.ttl`](weather_rdf.ttl) | `tss:EmbeddedRDFSource` | the specification's multi-sensor example: two value fields, three rows, five points |
| [`embedded_json.ttl`](embedded_json.ttl) | `tss:EmbeddedJSONSource` | the specification's JSON rows example, a growing source (no `tss:immutable true`) |
| [`growing_rdf.ttl`](growing_rdf.ttl) | `tss:EmbeddedRDFSource` | a growing source written as an RDF Message Log: one row per message |
| [`readings_csv.ttl`](readings_csv.ttl) | `tss:CSVSource` | a CSV file with a header line and `;` as delimiter; a quality code that describes the value |
| [`readings_no_header.ttl`](readings_no_header.ttl) | `tss:CSVSource` | a CSV file without a header line: columns by `tss:columnIndex` |
| [`readings_grid.ttl`](readings_grid.ttl) | `tss:CSVSource` | a time grid: a `step` column instead of times |
| [`readings_delta.ttl`](readings_delta.ttl) | `tss:CSVSource` | `tss:DeltaFromBaseline`: values stored as differences from 4750 |
| [`readings_parquet.ttl`](readings_parquet.ttl) | `tss:ParquetSource` | a Parquet file: UTC timestamp, DOUBLE and STRING columns |
| [`readings_arrow.ttl`](readings_arrow.ttl) | `tss:ArrowSource` | an Arrow IPC file: nanosecond UTC timestamps, a dictionary-encoded column |

The six `readings_*` documents describe the same readings: one synthetic day (2024-05-12, UTC) of conductivity every 15 minutes, with three gaps (85 readings). The data is made up and is only there to show and test the library. [`make_data.py`](make_data.py) writes the files in [`sample-data/`](sample-data/) (it needs pyarrow).
