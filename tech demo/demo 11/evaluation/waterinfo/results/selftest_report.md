# Self-test of check_waterinfo.py

12 of 12 deliberate errors were found.

| Case | Document | Error | Expected | Failed checks | Result |
|---|---|---|---|---|---|
| W1 | `waterinfo_289423042_tss2.ttl` | the value field names a column that is not in the CSV file | D1 | D1 | found |
| W2 | `waterinfo_289423042_tss2.ttl` | the value field is typed xsd:integer, but the readings have decimals | D2 | D2 | found |
| W3 | `waterinfo_289441042_tss2_points_embedded_json.ttl` | two JSON rows have the same time | D3 | D3, D4 | found |
| W4 | `waterinfo_289441042_tss2_points_embedded.ttl` | an RDF point states a reading that Waterinfo does not have | D4 | D4, D6 | found |
| W5 | `waterinfo_289441042_tss2_points_embedded.ttl` | an RDF point states another quality colour | D4 | D4 | found |
| W6 | `waterinfo_289441042_tss2_points_embedded_json.ttl` | a JSON row states another quality colour | D4 | D4 | found |
| W7 | `waterinfo_289423042_tss2.ttl` | the quality code field reads the column of the interpolation type (CSV) | D4 | D4 | found |
| W8 | `waterinfo_289429042_tss2_parquet.ttl` | the quality code name field reads the column of the quality colour (Parquet) | D4 | D4 | found |
| W9 | `waterinfo_289441042_tss2_value_delta.ttl` | the baseline is off by one, so every decoded reading is | D4 | D4, D6 | found |
| W10 | `waterinfo_289441042_tss2_time_step.ttl` | the time step is doubled, so the times of the rows are wrong | D4 | D4, D5, D6 | found |
| W11 | `waterinfo_289423042_tss2.ttl` | a stored count is off by one | D6 | D6 | found |
| W12 | `waterinfo_289429042_tss2_parquet.ttl` | the window starts later, so the stored summaries no longer describe it | D6 | D6 | found |
