"""Write the TSS conformance test suite: test documents, data files, expected results and manifest.ttl.

Run from anywhere (it needs pyarrow for the Parquet and Arrow files):

    python conformance/make_suite.py

The tables in this file are the source of the suite; the files next to it are written from them.
To change a test or add one, edit a table and run this script again. docs/conformance.md explains
what the suite holds and how to run it.

- METADATA_TESTS: a valid base document and edits that break exactly one rule. The SHACL shapes
  must find it, with the check ID the test names.
- READ_TESTS, SUMMARY_TESTS, EXPANSION_TESTS: small documents and data files with their
  expected results, written out by hand from the specification.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = "https://knowledgeonwebscale.github.io/rdf-timeseriessnippets/"

PREFIXES = """@prefix tss: <https://w3id.org/tss#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
@prefix quantitykind: <http://qudt.org/vocab/quantitykind/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix ex: <https://example.org/ex#> .
"""

# --- The base documents ---------------------------------------------------------------------------

RDF_SNIPPET = """
ex:snippet
    a tss:EmbeddedRDFSnippet ;
    rdfs:label "Conductivity readings"@en ;
    tss:immutable true ;
    tss:producedBy ex:producer ;
    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;
    tss:until "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;
    tss:timeField ex:time-field ;
    tss:key ex:time-field ;
    tss:hasField ex:time-field, ex:value-field .

ex:time-field
    a tss:Field ;
    rdfs:label "Observation time"@en ;
    tss:datatype xsd:dateTimeStamp .
"""

RDF_VALUE_FIELD = """
ex:value-field
    a tss:Field ;
    rdfs:label "Electrical conductivity"@en ;
    tss:datatype xsd:double ;
    tss:minimum "0"^^xsd:double ;
    tss:maximum "10000"^^xsd:double ;
    tss:observedProperty quantitykind:ElectricConductivity ;
    tss:unit unit:MicroS-PER-CentiM ;
    tss:sensor ex:sensor-1 ;
    tss:featureOfInterest ex:feature-1 ;
    tss:summary [
        tss:missingValuePolicy tss:ExcludeEmptyValues ;
        tss:count "3"^^xsd:integer ;
        tss:missing "0"^^xsd:integer ;
        tss:firstTime "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;
        tss:lastTime "2024-05-12T08:30:00Z"^^xsd:dateTimeStamp ;
        tss:observedMinimum "4722.4"^^xsd:double ;
        tss:observedMaximum "4810.5"^^xsd:double ;
        tss:mean "4761.6"^^xsd:double ;
        tss:median "4751.9"^^xsd:double ;
        tss:generatedAtTime "2024-05-12T09:46:10Z"^^xsd:dateTimeStamp
    ] .

ex:sensor-1 a sosa:Sensor ;
    rdfs:label "Conductivity sensor 1"@en .

ex:feature-1 a sosa:FeatureOfInterest ;
    rdfs:label "Water of basin 1"@en .
"""

RDF_POINTS = """
ex:snippet tss:hasPoint [
    ex:time-field "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;
    ex:value-field "4810.5"^^xsd:double
] .

ex:snippet tss:hasPoint [
    ex:time-field "2024-05-12T08:15:00Z"^^xsd:dateTimeStamp ;
    ex:value-field "4722.4"^^xsd:double
] .

ex:snippet tss:hasPoint [
    ex:time-field "2024-05-12T08:30:00Z"^^xsd:dateTimeStamp ;
    ex:value-field "4751.9"^^xsd:double
] .
"""

#: The specification's embedded example, with three points and a stored summary.
RDF = PREFIXES + RDF_SNIPPET + RDF_VALUE_FIELD + RDF_POINTS
#: The stored summary of the value field, with every attribute.
SUMMARY = RDF_VALUE_FIELD[RDF_VALUE_FIELD.index("\n    tss:summary ["):RDF_VALUE_FIELD.index("\n    ] .") + len("\n    ]")]

#: The embedded example as a growing snippet: an RDF Message Log, without tss:immutable and without
#: the summary; the description is the first message, and each point a message of its own.
RDF_LOG = ('VERSION "1.2-messages"\n' + PREFIXES + RDF_SNIPPET.replace("    tss:immutable true ;\n", "")
           + RDF_VALUE_FIELD.replace(" ;" + SUMMARY, "") + "MESSAGE\n"
           + "".join(part.strip() + "\nMESSAGE\n" for part in RDF_POINTS.split("\n\n")))
#: The last message of a log that ends the snippet: tss:immutable true, and the summary.
RDF_LOG_END = "ex:snippet tss:immutable true .\n\nex:value-field" + SUMMARY + " .\nMESSAGE\n"

JSON_ARRAY = """[
  { "time": "2024-05-12T08:00:00Z", "value": "4810.5" },
  { "time": "2024-05-12T08:15:00Z", "value": "4722.4" },
  { "time": "2024-05-12T08:30:00Z", "value": "4751.9" }
]"""


def json_document(array: str = JSON_ARRAY, extra_fields: str = "", more: str = "", immutable: bool = False) -> str:
    """A tss:EmbeddedJSONSnippet with these points: growing, unless ``immutable``."""
    final = "    tss:immutable true ;\n" if immutable else ""
    return PREFIXES + f"""
ex:snippet
    a tss:EmbeddedJSONSnippet ;
    rdfs:label "Conductivity readings"@en ;
{final}    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;
    tss:until "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;
    tss:timeField ex:time-field ;
    tss:hasField ex:time-field, ex:value-field{extra_fields} ;
    tss:points \"\"\"{array}\"\"\"^^rdf:JSON .

ex:time-field
    a tss:Field ;
    tss:columnName "time" ;
    tss:datatype xsd:dateTimeStamp .

ex:value-field
    a tss:Field ;
    tss:columnName "value" ;
    tss:datatype xsd:double ;
    tss:observedProperty quantitykind:ElectricConductivity ;
    tss:unit unit:MicroS-PER-CentiM{more} .
"""


JSON = json_document()


def external_document(kind: str = "CSVSnippet", data_file: str = "readings.csv", snippet_extra: str = "",
                      window: bool = True, value_extra: str = "", summary: str | None = None,
                      immutable: bool = True) -> str:
    """An external snippet (CSV, Parquet or Arrow) with a time field and a value field: immutable,
    unless ``immutable=False`` (then give ``summary=""``: a growing snippet has no summary)."""
    final = "    tss:immutable true ;\n" if immutable else ""
    bounds = ('    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;\n'
              '    tss:until "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;\n') if window else ""
    summary = ("""
    tss:summary [
        tss:missingValuePolicy tss:ExcludeEmptyValues ;
        tss:count "3"^^xsd:integer
    ]""" if summary is None else summary)
    return PREFIXES + f"""
ex:snippet
    a tss:{kind} ;
    rdfs:label "Conductivity readings"@en ;
{final}    tss:producedBy ex:producer ;
{bounds}    tss:dataFile <{data_file}> ;{snippet_extra}
    tss:timeField ex:time-field ;
    tss:key ex:time-field ;
    tss:hasField ex:time-field, ex:value-field .

ex:time-field
    a tss:Field ;
    tss:columnName "time" ;
    tss:datatype xsd:dateTimeStamp .

ex:value-field
    a tss:Field ;
    tss:columnName "value" ;
    tss:datatype xsd:double ;
    tss:minimum "0"^^xsd:double ;
    tss:maximum "10000"^^xsd:double ;
    tss:observedProperty quantitykind:ElectricConductivity ;
    tss:unit unit:MicroS-PER-CentiM{value_extra}{(" ;" + summary) if summary else ""} .
"""


CSV = external_document()
PARQUET = external_document("ParquetSnippet", "readings.parquet")
ARROW = external_document("ArrowSnippet", "readings.arrow")


def grid_document(time_base: str = "2024-05-12T08:00:00Z", time_step: str = "PT15M", value_extra: str = "") -> str:
    """A CSV snippet on a time grid: a step column instead of a time column."""
    return PREFIXES + f"""
ex:snippet
    a tss:CSVSnippet ;
    tss:immutable true ;
    tss:dataFile <readings.csv> ;
    tss:timeBase "{time_base}"^^xsd:dateTimeStamp ;
    tss:timeStep "{time_step}"^^xsd:duration ;
    tss:stepField ex:step-field ;
    tss:timeField ex:time-field ;
    tss:hasField ex:time-field, ex:step-field, ex:value-field .

ex:time-field
    a tss:Field ;
    tss:datatype xsd:dateTimeStamp .

ex:step-field
    a tss:Field ;
    tss:columnName "step" ;
    tss:datatype xsd:integer .

ex:value-field
    a tss:Field ;
    tss:columnName "value" ;
    tss:datatype xsd:double ;
    tss:observedProperty quantitykind:ElectricConductivity{value_extra} .
"""


GRID = grid_document()
DELTA_ENCODING = """ ;
    tss:valueEncoding tss:DeltaFromBaseline ;
    tss:baseline "4750"^^xsd:double"""
DELTA = external_document(window=False, value_extra=DELTA_ENCODING, summary="")

SECOND_EXTERNAL_SNIPPET = """
ex:csv-snippet
    a tss:CSVSnippet ;
    tss:immutable true ;
    tss:dataFile <readings.csv> ;
    tss:timeField ex:csv-time-field ;
    tss:hasField ex:csv-time-field .

ex:csv-time-field
    a tss:Field ;
    tss:columnName "time" ;
    tss:datatype xsd:dateTimeStamp .
"""


# --- Edits ----------------------------------------------------------------------------------------

def replace(old: str, new: str):
    def change(text: str) -> str:
        if text.count(old) != 1:
            raise ValueError(f"The base must hold {old!r} exactly once (found {text.count(old)}).")
        return text.replace(old, new)
    return change


def remove(old: str):
    return replace(old, "")


def append(more: str):
    return lambda text: text + more


def add_to_snippet(line: str):
    """Add a statement about the snippet, after its tss:immutable."""
    return replace("    tss:immutable true ;", "    tss:immutable true ;\n" + line)


def add_json_line(line: str):
    """Add a statement about a JSON snippet, after its label."""
    return replace('    rdfs:label "Conductivity readings"@en ;', '    rdfs:label "Conductivity readings"@en ;\n' + line)


def point(time: str | None, value: str | None, extra: str = "") -> str:
    parts = ([f'    ex:time-field "{time}"^^xsd:dateTimeStamp'] if time else []) + \
            ([f'    ex:value-field {value}'] if value else []) + ([extra] if extra else [])
    return "\nex:snippet tss:hasPoint [\n" + " ;\n".join(parts) + "\n] .\n"


VALUE_LINE = '    ex:value-field "4722.4"^^xsd:double'
SUMMARY_POLICY = '        tss:missingValuePolicy tss:ExcludeEmptyValues ;'


class M:
    """A metadata test: ``base`` with ``edits`` applied must give these results."""

    def __init__(self, name: str, base: str, edits=(), comment: str = "", rule: str = "", violations=None,
                 warnings=(), conforms=None):
        self.name, self.base, self.edits, self.comment, self.rule = name, base, edits, comment, rule
        self.warnings = list(warnings)
        if violations is None:
            violations = [] if name.startswith("valid") or warnings else [name.split("-")[0]]
        self.violations = list(violations)
        self.conforms = not self.violations if conforms is None else conforms

    def text(self) -> str:
        text = self.base
        for edit in self.edits:
            text = edit(text)
        return text


EMBEDDED_TESTS = [
    M("valid-rdf-points", RDF, comment="The specification's embedded example: RDF points and a stored summary.",
      rule="embedded-example"),
    M("valid-json-points", JSON, comment="The same readings as JSON points.", rule="json-points"),
    M("valid-open-window", RDF, [remove('    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;\n'),
                                 remove('    tss:until "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;\n')],
      "A snippet without tss:from and tss:until: the window is open on both sides.", "window"),
    M("valid-times-with-offsets", RDF, [replace('ex:time-field "2024-05-12T08:15:00Z"',
                                                'ex:time-field "2024-05-12T10:15:00+02:00"')],
      "A point's time with an offset instead of Z.", "times"),
    M("valid-empty-text-is-no-value", RDF, [replace(VALUE_LINE, '    ex:value-field ""^^xsd:double')],
      'An empty text ("") is no value, so it is not checked against xsd:double.', "points"),
    M("valid-no-points-yet", PREFIXES + RDF_SNIPPET + RDF_VALUE_FIELD[:RDF_VALUE_FIELD.index(" ;\n    tss:summary [")]
      + " .\n" + RDF_VALUE_FIELD[RDF_VALUE_FIELD.index("\nex:sensor-1"):],
      comment="A snippet may have no points yet.", rule="rdf-points"),
    M("valid-empty-json-array", json_document("[]"), comment='"[]" holds no points.', rule="json-points"),
    M("valid-growing-json-points", JSON, comment="A growing snippet: it states no tss:immutable, and has no summary.",
      rule="change"),
    M("valid-growing-message-log", RDF_LOG,
      comment="A growing tss:EmbeddedRDFSnippet: an RDF Message Log, one point per message, no summary.", rule="rdf-points"),
    M("valid-ended-message-log", RDF_LOG + RDF_LOG_END,
      comment="A log whose last message ends the snippet: tss:immutable true and the summary.", rule="ending"),
    M("S2-immutable-not-a-boolean", RDF, [replace("tss:immutable true ;", 'tss:immutable "yes" ;')],
      'tss:immutable is the string "yes", not an xsd:boolean (so the snippet is growing, and its summary is S26).',
      "change", violations=["S2", "S26"]),
    M("S2-two-immutable-values", RDF, [replace("tss:immutable true ;", "tss:immutable true, false ;")],
      "The snippet states tss:immutable twice.", "change"),
    M("S26-summary-of-a-growing-snippet", json_document(more=" ;" + SUMMARY),
      comment="A growing snippet (no tss:immutable) with a stored summary.", rule="summaries"),
    M("S26-summary-with-immutable-false", json_document(more=" ;" + SUMMARY), [add_json_line("    tss:immutable false ;")],
      "A snippet that states tss:immutable false, with a stored summary.", "summaries"),
    M("S3-two-snippet-classes", RDF, [replace("    a tss:EmbeddedRDFSnippet ;",
                                              "    a tss:EmbeddedRDFSnippet, tss:EmbeddedJSONSnippet ;")],
      "The snippet states two snippet classes.", "storage"),
    M("S4-time-field-missing", RDF, [remove("    tss:timeField ex:time-field ;\n")],
      "The snippet has no tss:timeField.", "time-and-key"),
    M("S4-time-field-not-a-field-of-the-snippet", RDF,
      [replace("    tss:timeField ex:time-field ;", "    tss:timeField ex:other-time-field ;"),
       append("\nex:other-time-field a tss:Field ;\n    tss:datatype xsd:dateTimeStamp .\n")],
      "The time field is not one of the snippet's fields.", "time-and-key"),
    M("S5-no-fields", RDF, [replace("    tss:key ex:time-field ;\n    tss:hasField ex:time-field, ex:value-field .",
                                    "    tss:key ex:time-field .")], "The snippet has no tss:hasField.", "fields"),
    M("S6-value-outside-a-point", RDF, [append('\nex:loose-value ex:value-field "4800.1"^^xsd:double .\n')],
      "A value of a field is on a node that the snippet does not link to with tss:hasPoint.", "rdf-points"),
    M("S6-json-points-not-rdf-json", JSON, [replace('"""^^rdf:JSON', '"""')],
      "tss:points is a plain string, not an rdf:JSON literal.", "json-points"),
    M("S6-json-points-not-an-array", json_document('{ "time": "2024-05-12T08:00:00Z", "value": "4810.5" }'),
      comment="tss:points holds a JSON object, not an array.", rule="json-points"),
    M("S6-json-points-missing", JSON, [replace(f' ;\n    tss:points """{JSON_ARRAY}"""^^rdf:JSON .', " .")],
      "A tss:EmbeddedJSONSnippet without tss:points.", "json-points"),
    M("S7-label-not-a-literal", RDF, [replace('    rdfs:label "Conductivity readings"@en ;', "    rdfs:label ex:readings ;")],
      "The snippet's label is an IRI.", "provenance"),
    M("S8-produced-by-a-literal", RDF, [replace("    tss:producedBy ex:producer ;", '    tss:producedBy "the producer" ;')],
      "tss:producedBy is a literal, not an IRI.", "provenance"),
    M("S9-key-not-the-time-field", RDF, [replace("    tss:key ex:time-field ;", "    tss:key ex:value-field ;")],
      "The key is not the time field.", "time-and-key"),
    M("S9-two-keys", RDF, [replace("    tss:key ex:time-field ;", "    tss:key ex:time-field, ex:value-field ;")],
      "The snippet states two keys.", "time-and-key"),
    M("S10-from-a-date", RDF, [replace('    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;',
                                       '    tss:from "2024-05-12"^^xsd:date ;')],
      "tss:from is an xsd:date, not an xsd:dateTimeStamp.", "window"),
    M("S11-until-before-from", RDF, [replace('    tss:until "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;',
                                             '    tss:until "2024-05-12T07:00:00Z"^^xsd:dateTimeStamp ;')],
      "tss:until is before tss:from.", "window"),
    *[M(f"S12-{name}", RDF, [add_to_snippet(line)], f"An embedded snippet states {line.split()[0]}.", "storage")
      for name, line in [("data-file", "    tss:dataFile <readings.csv> ;"),
                         ("delimiter", '    tss:delimiter ";" ;'),
                         ("encoding", '    tss:encoding "utf-8" ;'),
                         ("header", "    tss:header true ;"),
                         ("time-base", '    tss:timeBase "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;'),
                         ("time-step", '    tss:timeStep "PT15M"^^xsd:duration ;'),
                         ("step-field", "    tss:stepField ex:value-field ;"),
                         ("points-on-an-rdf-snippet", '    tss:points "[]"^^rdf:JSON ;')]],
    M("S12-has-point-on-a-json-snippet", JSON, [append(point("2024-05-12T08:45:00Z", None))],
      "A tss:EmbeddedJSONSnippet with an RDF point.", "storage"),
    M("S20-time-field-datatype", RDF, [replace('    rdfs:label "Observation time"@en ;\n    tss:datatype xsd:dateTimeStamp .',
                                               '    rdfs:label "Observation time"@en ;\n    tss:datatype xsd:dateTime .')],
      "The time field's datatype is xsd:dateTime.", "time-and-key"),
    M("S23-from-without-timezone", RDF, [replace('    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;',
                                                 '    tss:from "2024-05-12T08:00:00"^^xsd:dateTimeStamp ;')],
      "tss:from has no timezone.", "times"),
    M("S23-point-time-without-timezone", RDF, [replace('ex:time-field "2024-05-12T08:15:00Z"',
                                                       'ex:time-field "2024-05-12T08:15:00"')],
      "The time of a point has no timezone.", "times"),
    M("S23-summary-time-without-timezone", RDF, [replace('tss:generatedAtTime "2024-05-12T09:46:10Z"',
                                                         'tss:generatedAtTime "2024-05-12T09:46:10"')],
      "The tss:generatedAtTime of a summary has no timezone.", "times"),
    M("S25-json-field-without-column-name", JSON, [remove('    tss:columnName "value" ;\n')],
      "A field of a tss:EmbeddedJSONSnippet has no tss:columnName, its key in the JSON points.", "json-points"),
    M("F1-field-not-typed", RDF, [replace("ex:value-field\n    a tss:Field ;\n", "ex:value-field\n")],
      "A field is not typed tss:Field.", "fields"),
    M("F1-field-of-two-snippets", RDF, [append("""
ex:snippet-2
    a tss:EmbeddedRDFSnippet ;
    tss:immutable true ;
    tss:timeField ex:time-field-2 ;
    tss:hasField ex:time-field-2, ex:value-field .

ex:time-field-2 a tss:Field ;
    tss:datatype xsd:dateTimeStamp .
""")], "One field is a field of two snippets.", "fields"),
    M("F2-empty-column-name", JSON, [replace('tss:columnName "value"', 'tss:columnName ""')],
      "A column name is empty.", "fields"),
    M("F2-duplicate-column-names", JSON, [replace('tss:columnName "value"', 'tss:columnName "time"')],
      "Two fields of a snippet have the same column name.", "fields"),
    M("F3-datatype-missing", RDF, [replace('    rdfs:label "Electrical conductivity"@en ;\n    tss:datatype xsd:double ;\n',
                                           '    rdfs:label "Electrical conductivity"@en ;\n')],
      "A field has no tss:datatype.", "datatypes"),
    M("F3-datatype-a-literal", RDF, [replace("    tss:datatype xsd:double ;", '    tss:datatype "double" ;')],
      "tss:datatype is a literal, not an IRI.", "datatypes"),
    M("F4-negative-column-index", RDF, [replace("    tss:datatype xsd:double ;",
                                                '    tss:datatype xsd:double ;\n    tss:columnIndex "-1"^^xsd:integer ;')],
      "A column index is negative.", "fields"),
    M("F4-duplicate-column-indexes", RDF,
      [replace("    tss:datatype xsd:double ;", '    tss:datatype xsd:double ;\n    tss:columnIndex "0"^^xsd:integer ;'),
       replace("    tss:datatype xsd:dateTimeStamp .", '    tss:datatype xsd:dateTimeStamp ;\n    tss:columnIndex "0"^^xsd:integer .')],
      "Two fields of a snippet have the same column index.", "fields"),
    M("F5-label-not-a-literal", RDF, [replace('    rdfs:label "Electrical conductivity"@en ;', "    rdfs:label ex:conductivity ;")],
      "A field's label is an IRI.", "fields"),
    M("F6-comment-not-a-literal", RDF, [replace('    rdfs:label "Electrical conductivity"@en ;',
                                                '    rdfs:label "Electrical conductivity"@en ;\n    rdfs:comment ex:comment ;')],
      "A field's comment is an IRI.", "fields"),
    M("F7-minimum-not-a-number", RDF, [replace('tss:minimum "0"^^xsd:double', 'tss:minimum "low"')],
      "tss:minimum is a string, not a number.", "meaning"),
    M("F8-maximum-below-minimum", RDF, [replace('tss:maximum "10000"^^xsd:double', 'tss:maximum "-1"^^xsd:double')],
      "tss:maximum is below tss:minimum (and so every value is above it: P5).", "meaning", violations=["F8"],
      warnings=["P5"]),
    M("F9-field-without-values", RDF, [replace("    tss:hasField ex:time-field, ex:value-field .",
                                               "    tss:hasField ex:time-field, ex:value-field, ex:note-field ."),
                                       append("\nex:note-field a tss:Field ;\n    tss:datatype xsd:string .\n")],
      "No point has a value for one of the fields (a warning).", "rdf-points", warnings=["F9"]),
    M("F10-summary-a-literal", RDF, [replace("    tss:datatype xsd:dateTimeStamp .",
                                             '    tss:datatype xsd:dateTimeStamp ;\n    tss:summary "none" .')],
      "tss:summary is a literal, not a node.", "summaries"),
    M("D3-value-encoding-on-an-embedded-field", RDF,
      [replace("    tss:datatype xsd:double ;",
               '    tss:datatype xsd:double ;\n    tss:valueEncoding tss:DeltaFromBaseline ;\n    tss:baseline "4750"^^xsd:double ;')],
      "A field of an embedded snippet states a value encoding.", "value-encoding"),
    M("D3-baseline-on-an-embedded-field", RDF,
      [replace("    tss:datatype xsd:double ;", '    tss:datatype xsd:double ;\n    tss:baseline "4750"^^xsd:double ;')],
      "A field of an embedded snippet states a baseline.", "value-encoding"),
    M("P1-point-without-time", RDF, [append(point(None, '"4700.1"^^xsd:double'))], "A point has no time.", "rdf-points"),
    M("P2-point-before-from", RDF, [append(point("2024-05-12T07:45:00Z", '"4700.1"^^xsd:double'))],
      "A point lies before tss:from.", "window"),
    M("P2-point-at-until", RDF, [append(point("2024-05-13T00:00:00Z", '"4700.1"^^xsd:double'))],
      "A point lies at tss:until, which is not in the window.", "window"),
    M("P3-two-points-at-the-same-moment", RDF, [append(point("2024-05-12T10:15:00+02:00", '"4700.1"^^xsd:double'))],
      "Two points have the same time, written in two ways (08:15Z and 10:15+02:00).", "time-and-key"),
    M("P4-value-not-in-lexical-form", RDF, [replace(VALUE_LINE, '    ex:value-field " 4722.4 "^^xsd:double')],
      'A value " 4722.4 " with spaces is not in the lexical form of xsd:double.', "datatypes"),
    M("P4-value-with-another-datatype", RDF, [replace(VALUE_LINE, '    ex:value-field "4722.4"^^xsd:decimal')],
      "A value is an xsd:decimal literal, but the field's datatype is xsd:double.", "datatypes"),
    M("P5-value-above-maximum", RDF, [replace(VALUE_LINE, '    ex:value-field "12000"^^xsd:double')],
      "A value is above tss:maximum: suspicious, but valid (a warning).", "meaning", warnings=["P5"]),
    M("P6-point-with-a-type", RDF, [replace('ex:snippet tss:hasPoint [\n    ex:time-field "2024-05-12T08:15:00Z"',
                                            'ex:snippet tss:hasPoint [\n    a ex:Reading ;\n    ex:time-field "2024-05-12T08:15:00Z"')],
      "A point states an rdf:type, which is not a field (a warning).", "rdf-points", warnings=["P6"]),
    M("P7-two-values-for-one-field", RDF, [replace(VALUE_LINE, '    ex:value-field "4722.4"^^xsd:double, "4722.5"^^xsd:double')],
      "A point has two values for one field.", "rdf-points"),
    M("Q1-summary-with-from", RDF, [replace(SUMMARY_POLICY, SUMMARY_POLICY + '\n        tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;')],
      "A summary states its own tss:from.", "summaries"),
    M("Q2-summary-with-until", RDF, [replace(SUMMARY_POLICY, SUMMARY_POLICY + '\n        tss:until "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;')],
      "A summary states its own tss:until.", "summaries"),
    M("Q3-policy-a-literal", RDF, [replace("tss:missingValuePolicy tss:ExcludeEmptyValues", 'tss:missingValuePolicy "exclude"')],
      "tss:missingValuePolicy is a literal, not an IRI.", "summaries"),
    M("Q4-count-a-decimal", RDF, [replace('tss:count "3"^^xsd:integer', 'tss:count "3.0"^^xsd:decimal')],
      "tss:count is an xsd:decimal.", "summaries"),
    M("Q4-count-negative", RDF, [replace('tss:count "3"^^xsd:integer', 'tss:count "-3"^^xsd:integer')],
      "tss:count is negative.", "summaries"),
    M("Q5-missing-negative", RDF, [replace('tss:missing "0"^^xsd:integer', 'tss:missing "-1"^^xsd:integer')],
      "tss:missing is negative.", "summaries"),
    M("Q6-first-time-a-date", RDF, [replace('tss:firstTime "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp',
                                            'tss:firstTime "2024-05-12"^^xsd:date')], "tss:firstTime is an xsd:date.", "summaries"),
    M("Q7-last-time-a-date", RDF, [replace('tss:lastTime "2024-05-12T08:30:00Z"^^xsd:dateTimeStamp',
                                           'tss:lastTime "2024-05-12"^^xsd:date')], "tss:lastTime is an xsd:date.", "summaries"),
    *[M(f"Q{number}-{name}-a-string", RDF, [replace(f'tss:{prop} "{value}"^^xsd:double', f'tss:{prop} "{value}"')],
        f"tss:{prop} is a string, not a number.", "summaries")
      for number, name, prop, value in [(8, "observed-minimum", "observedMinimum", "4722.4"),
                                        (9, "observed-maximum", "observedMaximum", "4810.5"),
                                        (10, "mean", "mean", "4761.6"), (11, "median", "median", "4751.9")]],
    M("Q12-generated-at-time-a-date", RDF, [replace('tss:generatedAtTime "2024-05-12T09:46:10Z"^^xsd:dateTimeStamp',
                                                    'tss:generatedAtTime "2024-05-12"^^xsd:date')],
      "tss:generatedAtTime is an xsd:date.", "summaries"),
]

EXTERNAL_TESTS = [
    M("valid-csv", CSV, comment="A CSV snippet with a window, a key and a stored summary.", rule="csv"),
    M("valid-csv-no-header", CSV, [replace("    tss:dataFile <readings.csv> ;",
                                           '    tss:dataFile <readings.csv> ;\n    tss:delimiter ";" ;\n    tss:header false ;'),
                                   replace('    tss:columnName "time" ;', '    tss:columnName "time" ;\n    tss:columnIndex "0"^^xsd:integer ;'),
                                   replace('    tss:columnName "value" ;', '    tss:columnName "value" ;\n    tss:columnIndex "1"^^xsd:integer ;')],
      "A CSV file without a header line, with ';' as delimiter: columns by tss:columnIndex.", "csv"),
    M("valid-parquet", PARQUET, comment="A Parquet snippet.", rule="parquet"),
    M("valid-arrow", ARROW, comment="An Arrow snippet.", rule="arrow"),
    M("valid-time-grid", GRID, comment="A time grid: tss:timeBase, tss:timeStep and a step field.", rule="time-grid"),
    M("valid-delta-from-baseline", DELTA, comment="A value encoding: differences from a baseline.", rule="value-encoding"),
    M("valid-open-window", external_document(window=False), comment="No tss:from and no tss:until.", rule="window"),
    M("valid-growing-csv", external_document(immutable=False, summary=""),
      comment="A growing CSV snippet: it states no tss:immutable, and has no summary.", rule="appending-data-files"),
    M("valid-growing-arrow-stream", external_document("ArrowSnippet", "readings.arrows", immutable=False, summary=""),
      comment="A growing Arrow snippet, whose data file is an Arrow IPC stream.", rule="arrow"),
    M("S3-immutable-not-a-boolean", CSV, [replace("tss:immutable true ;", 'tss:immutable "yes" ;')],
      'tss:immutable is the string "yes", not an xsd:boolean (so the snippet is growing, and its summary is S26).',
      "change", violations=["S3", "S26"]),
    M("S26-summary-of-a-growing-snippet", CSV, [remove("    tss:immutable true ;\n")],
      "A growing CSV snippet (no tss:immutable) with a stored summary.", "summaries"),
    M("S27-parquet-not-immutable", external_document("ParquetSnippet", "readings.parquet", immutable=False, summary=""),
      comment="A Parquet snippet that does not state tss:immutable true: rows can't be appended to a Parquet file.",
      rule="parquet"),
    M("S27-parquet-immutable-false", external_document("ParquetSnippet", "readings.parquet", summary=""),
      [replace("tss:immutable true ;", "tss:immutable false ;")],
      "A Parquet snippet that states tss:immutable false.", "parquet"),
    M("S4-time-field-missing", CSV, [remove("    tss:timeField ex:time-field ;\n")], "The snippet has no tss:timeField.",
      "time-and-key"),
    M("S4-time-field-not-a-field-of-the-snippet", CSV,
      [replace("    tss:timeField ex:time-field ;", "    tss:timeField ex:other-time-field ;"),
       append('\nex:other-time-field a tss:Field ;\n    tss:columnName "other" ;\n    tss:datatype xsd:dateTimeStamp .\n')],
      "The time field is not one of the snippet's fields.", "time-and-key"),
    M("S5-key-not-the-time-field", CSV, [replace("    tss:key ex:time-field ;", "    tss:key ex:value-field ;")],
      "The key is not the time field.", "time-and-key"),
    M("S6-no-fields", CSV, [replace("    tss:key ex:time-field ;\n    tss:hasField ex:time-field, ex:value-field .",
                                    "    tss:key ex:time-field .")], "The snippet has no tss:hasField.", "fields"),
    M("S7-two-snippet-classes", CSV, [replace("    a tss:CSVSnippet ;", "    a tss:CSVSnippet, tss:ParquetSnippet ;")],
      "The snippet states two snippet classes.", "storage"),
    M("S8-data-file-missing", CSV, [remove("    tss:dataFile <readings.csv> ;\n")], "The snippet has no tss:dataFile.",
      "data-files"),
    M("S8-data-file-a-literal", CSV, [replace("    tss:dataFile <readings.csv> ;", '    tss:dataFile "readings.csv" ;')],
      "tss:dataFile is a string, not an IRI.", "data-files"),
    M("S10-delimiter-two-characters", CSV, [replace("    tss:dataFile <readings.csv> ;",
                                                    '    tss:dataFile <readings.csv> ;\n    tss:delimiter ";;" ;')],
      "tss:delimiter has two characters.", "csv"),
    M("S11-encoding-empty", CSV, [replace("    tss:dataFile <readings.csv> ;", '    tss:dataFile <readings.csv> ;\n    tss:encoding "" ;')],
      "tss:encoding is empty.", "csv"),
    M("S12-label-not-a-literal", CSV, [replace('    rdfs:label "Conductivity readings"@en ;', "    rdfs:label ex:readings ;")],
      "The snippet's label is an IRI.", "provenance"),
    M("S13-produced-by-a-literal", CSV, [replace("    tss:producedBy ex:producer ;", '    tss:producedBy "the producer" ;')],
      "tss:producedBy is a literal, not an IRI.", "provenance"),
    M("S14-until-before-from", CSV, [replace('    tss:until "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;',
                                             '    tss:until "2024-05-12T07:00:00Z"^^xsd:dateTimeStamp ;')],
      "tss:until is before tss:from.", "window"),
    M("S15-delimiter-on-parquet", PARQUET, [replace("    tss:dataFile <readings.parquet> ;",
                                                    '    tss:dataFile <readings.parquet> ;\n    tss:delimiter ";" ;')],
      "tss:delimiter on a Parquet snippet has no effect (a warning).", "parquet", warnings=["S15"]),
    M("S15-encoding-on-arrow", ARROW, [replace("    tss:dataFile <readings.arrow> ;",
                                               '    tss:dataFile <readings.arrow> ;\n    tss:encoding "utf-8" ;')],
      "tss:encoding on an Arrow snippet has no effect (a warning).", "arrow", warnings=["S15"]),
    M("S19-time-base-without-time-step", GRID, [remove('    tss:timeStep "PT15M"^^xsd:duration ;\n')],
      "A tss:timeBase without a tss:timeStep: the time step is missing.", "time-grid"),
    M("S18-time-base-a-date", GRID, [replace('tss:timeBase "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp',
                                             'tss:timeBase "2024-05-12"^^xsd:date')], "tss:timeBase is an xsd:date.", "time-grid"),
    M("S18-time-step-without-time-base", GRID, [remove('    tss:timeBase "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;\n')],
      "A tss:timeStep without a tss:timeBase: the time base is missing.", "time-grid"),
    M("S19-time-step-zero", grid_document(time_step="PT0S"), comment="The time step is zero.", rule="time-grid"),
    M("S19-time-step-negative", grid_document(time_step="-PT15M"), comment="The time step is negative.", rule="time-grid"),
    M("S20-time-field-datatype", CSV, [replace('    tss:columnName "time" ;\n    tss:datatype xsd:dateTimeStamp .',
                                               '    tss:columnName "time" ;\n    tss:datatype xsd:dateTime .')],
      "The time field's datatype is xsd:dateTime.", "time-and-key"),
    M("S21-grid-without-step-field", GRID, [remove("    tss:stepField ex:step-field ;\n")],
      "A time grid without a tss:stepField.", "time-grid"),
    M("S21-step-field-not-an-integer", GRID, [replace('    tss:columnName "step" ;\n    tss:datatype xsd:integer .',
                                                      '    tss:columnName "step" ;\n    tss:datatype xsd:decimal .')],
      "The step field's datatype is xsd:decimal.", "time-grid"),
    M("S21-step-field-without-a-grid", CSV, [replace("    tss:dataFile <readings.csv> ;",
                                                     "    tss:dataFile <readings.csv> ;\n    tss:stepField ex:value-field ;")],
      "A tss:stepField without a time grid.", "time-grid"),
    M("S22-has-point", CSV, [append(point("2024-05-12T08:00:00Z", '"4810.5"^^xsd:double'))],
      "An external snippet with an RDF point.", "storage"),
    M("S22-points", CSV, [replace("    tss:dataFile <readings.csv> ;", '    tss:dataFile <readings.csv> ;\n    tss:points "[]"^^rdf:JSON ;')],
      "An external snippet with JSON points.", "storage"),
    M("S23-until-without-timezone", CSV, [replace('tss:until "2024-05-13T00:00:00Z"', 'tss:until "2024-05-13T00:00:00"')],
      "tss:until has no timezone.", "times"),
    M("S23-time-base-without-timezone", grid_document(time_base="2024-05-12T08:00:00"),
      comment="tss:timeBase has no timezone.", rule="times"),
    M("F1-field-not-typed", CSV, [replace("ex:value-field\n    a tss:Field ;\n", "ex:value-field\n")],
      "A field is not typed tss:Field.", "fields"),
    M("F2-column-name-missing", CSV, [remove('    tss:columnName "value" ;\n')],
      "A field of an external snippet has no tss:columnName.", "data-files"),
    M("F2-duplicate-column-names", CSV, [replace('tss:columnName "value"', 'tss:columnName "time"')],
      "Two fields of a snippet have the same column name.", "data-files"),
    M("F2-grid-time-field-with-a-column-name", GRID, [replace("ex:time-field\n    a tss:Field ;\n",
                                                              'ex:time-field\n    a tss:Field ;\n    tss:columnName "time" ;\n')],
      "On a time grid, the time field has no column, so no tss:columnName.", "time-grid"),
    M("F3-datatype-missing", CSV, [remove("    tss:datatype xsd:double ;\n")], "A field has no tss:datatype.", "datatypes"),
    M("F4-duplicate-column-indexes", CSV,
      [replace('    tss:columnName "time" ;', '    tss:columnName "time" ;\n    tss:columnIndex "0"^^xsd:integer ;'),
       replace('    tss:columnName "value" ;', '    tss:columnName "value" ;\n    tss:columnIndex "0"^^xsd:integer ;')],
      "Two fields of a snippet have the same column index.", "data-files"),
    M("F8-maximum-below-minimum", CSV, [replace('tss:maximum "10000"^^xsd:double', 'tss:maximum "-1"^^xsd:double')],
      "tss:maximum is below tss:minimum.", "meaning"),
    M("D1-unknown-value-encoding", DELTA, [replace("tss:valueEncoding tss:DeltaFromBaseline", "tss:valueEncoding ex:ScaledValues")],
      "A value encoding the specification does not define.", "value-encoding"),
    M("D1-delta-without-baseline", DELTA, [remove(' ;\n    tss:baseline "4750"^^xsd:double')],
      "tss:DeltaFromBaseline without a tss:baseline.", "value-encoding"),
    M("D2-baseline-with-another-datatype", DELTA, [replace('tss:baseline "4750"^^xsd:double', 'tss:baseline "4750"^^xsd:integer')],
      "The baseline is an xsd:integer, but the field's datatype is xsd:double.", "value-encoding"),
    M("D3-baseline-without-value-encoding", DELTA, [remove("    tss:valueEncoding tss:DeltaFromBaseline ;\n")],
      "A tss:baseline without tss:valueEncoding tss:DeltaFromBaseline.", "value-encoding"),
    M("D3-value-encoding-on-the-time-field", CSV,
      [replace('    tss:columnName "time" ;\n    tss:datatype xsd:dateTimeStamp .',
               '    tss:columnName "time" ;\n    tss:datatype xsd:dateTimeStamp ;\n    tss:valueEncoding tss:DeltaFromBaseline .')],
      "The time field states a value encoding.", "value-encoding"),
    M("Q2-summary-with-until", CSV, [replace(SUMMARY_POLICY, SUMMARY_POLICY + '\n        tss:until "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;')],
      "A summary states its own tss:until.", "summaries"),
]

#: The checks of fields and summaries are the same in both shapes files: the external file gets
#: the embedded tests' edits, on a CSV snippet whose value field has a label and a full summary.
SHARED_CHECKS = ("F5", "F6", "F7", "F10", "Q1", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q10", "Q11", "Q12")
CSV_FULL = external_document(value_extra=' ;\n    rdfs:label "Electrical conductivity"@en', summary=SUMMARY)
EXTERNAL_TESTS += [M(test.name, CSV_FULL, test.edits, test.comment, test.rule, violations=test.violations,
                     warnings=test.warnings) for test in EMBEDDED_TESTS if test.name.split("-")[0] in SHARED_CHECKS]
EXTERNAL_TESTS.append(M("S2-from-a-date", CSV, [replace('    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;',
                                                        '    tss:from "2024-05-12"^^xsd:date ;')],
                        "tss:from is an xsd:date, not an xsd:dateTimeStamp.", "window"))

MIXED_TESTS = [
    M("S1-no-snippet", PREFIXES + "\nex:time-field a tss:Field ;\n    tss:datatype xsd:dateTimeStamp .\n",
      comment="A document that describes no snippet; both shapes files report it.", rule="the-snippet"),
    M("S24-embedded-and-external-snippets", RDF + SECOND_EXTERNAL_SNIPPET,
      comment="A document with an embedded and an external snippet.", rule="storage"),
]


# --- Reading, summary and expansion tests ----------------------------------------------------------

VALUE = "https://example.org/ex#value-field"
NOTE = "https://example.org/ex#note-field"


def csv_rows(*rows) -> str:
    return "\n".join(rows) + "\n"


READ_TESTS = [
    dict(name="rdf-points", comment="RDF points: one node per point, the field IRIs as predicates.", rule="rdf-points",
         files={"metadata.ttl": RDF},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4",
                           "2024-05-12T08:30:00Z,4751.9")),
    dict(name="json-empty-values", comment='JSON points: "", null and a missing key are empty values.', rule="json-points",
         files={"metadata.ttl": json_document("""[
  { "time": "2024-05-12T08:00:00Z", "value": "" },
  { "time": "2024-05-12T08:15:00Z", "value": null },
  { "time": "2024-05-12T08:30:00Z" },
  { "time": "2024-05-12T08:45:00Z", "value": "4751.9" }
]""")},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,", "2024-05-12T08:15:00Z,", "2024-05-12T08:30:00Z,",
                           "2024-05-12T08:45:00Z,4751.9")),
    dict(name="csv-delimiter", comment="A CSV file with ';' as delimiter and a header line.", rule="csv",
         files={"metadata.ttl": external_document(snippet_extra='\n    tss:delimiter ";" ;', summary=""),
                "readings.csv": csv_rows("time;value", "2024-05-12T08:00:00Z;4810.5", "2024-05-12T08:15:00Z;4722.4")},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4")),
    dict(name="csv-no-header", comment="A CSV file without a header line: columns by tss:columnIndex.", rule="csv",
         files={"metadata.ttl": external_document(snippet_extra="\n    tss:header false ;", summary="").replace(
             '    tss:columnName "value" ;', '    tss:columnName "value" ;\n    tss:columnIndex "1"^^xsd:integer ;').replace(
             '    tss:columnName "time" ;', '    tss:columnName "time" ;\n    tss:columnIndex "0"^^xsd:integer ;'),
                "readings.csv": csv_rows("2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4")},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4")),
    dict(name="csv-time-window", comment="Only the rows in [tss:from, tss:until) are points; times compare as moments.",
         rule="window",
         files={"metadata.ttl": external_document(summary=""),
                "readings.csv": csv_rows("time,value", "2024-05-12T07:45:00Z,4700.1", "2024-05-12T09:59:00+02:00,4700.2",
                                         "2024-05-12T08:00:00Z,4810.5", "2024-05-12T23:59:59Z,4722.4",
                                         "2024-05-13T00:00:00Z,4700.3")},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T23:59:59Z,4722.4")),
    dict(name="csv-time-offsets", comment="Times with offsets are the same moments as in Z.", rule="times",
         files={"metadata.ttl": external_document(summary=""),
                "readings.csv": csv_rows("time,value", "2024-05-12T10:00:00+02:00,4810.5", "2024-05-12T03:15:00-05:00,4722.4")},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4")),
    dict(name="time-grid", comment="On a time grid, the time of a row is tss:timeBase + step × tss:timeStep.",
         rule="time-grid",
         files={"metadata.ttl": GRID, "readings.csv": csv_rows("step,value", "0,4810.5", "1,4722.4", "3,4751.9")},
         expected=csv_rows(f"time,https://example.org/ex#step-field,{VALUE}", "2024-05-12T08:00:00Z,0,4810.5",
                           "2024-05-12T08:15:00Z,1,4722.4", "2024-05-12T08:45:00Z,3,4751.9")),
    dict(name="time-grid-calendar-months", rule="time-grid",
         comment="A step of P1M from 31 January: n months are added at once, and the day is clamped to the month.",
         files={"metadata.ttl": grid_document(time_base="2024-01-31T00:00:00Z", time_step="P1M"),
                "readings.csv": csv_rows("step,value", "0,4810.5", "1,4722.4", "2,4751.9")},
         expected=csv_rows(f"time,https://example.org/ex#step-field,{VALUE}", "2024-01-31T00:00:00Z,0,4810.5",
                           "2024-02-29T00:00:00Z,1,4722.4", "2024-03-31T00:00:00Z,2,4751.9")),
    dict(name="delta-from-baseline", comment="The specification's delta example: 4750 + 60.5 and 4750 + (-27.6).",
         rule="value-encoding",
         files={"metadata.ttl": DELTA, "readings.csv": csv_rows("time,value", "2024-05-12T08:00:00Z,60.5",
                                                                "2024-05-12T08:15:00Z,-27.6")},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4")),
    dict(name="texts-kept", rule="datatypes",
         comment="A text keeps its spaces, and a text of only spaces is a value, not an empty value.",
         files={"metadata.ttl": external_document(summary="").replace(
             "    tss:hasField ex:time-field, ex:value-field .", "    tss:hasField ex:time-field, ex:value-field, ex:note-field .")
             + '\nex:note-field a tss:Field ;\n    tss:columnName "note" ;\n    tss:datatype xsd:string .\n',
                "readings.csv": csv_rows("time,value,note", '2024-05-12T08:00:00Z,4810.5," good "',
                                         '2024-05-12T08:15:00Z,4722.4,"   "', "2024-05-12T08:30:00Z,4751.9,")},
         expected=csv_rows(f"time,{VALUE},{NOTE}", '2024-05-12T08:00:00Z,4810.5," good "',
                           '2024-05-12T08:15:00Z,4722.4,"   "', "2024-05-12T08:30:00Z,4751.9,")),
    dict(name="parquet", comment="A Parquet file: a UTC timestamp column and a DOUBLE column.", rule="parquet",
         files={"metadata.ttl": PARQUET}, parquet="readings.parquet",
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4",
                           "2024-05-12T08:30:00Z,4751.9")),
    dict(name="arrow", comment="An Arrow IPC file: nanosecond UTC timestamps and a DOUBLE column.", rule="arrow",
         files={"metadata.ttl": ARROW}, arrow="readings.arrow",
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4",
                           "2024-05-12T08:30:00Z,4751.9")),
    dict(name="rdf-message-log", rule="rdf-points",
         comment="A growing snippet's RDF Message Log is read as the merge of its messages.",
         files={"metadata.ttl": RDF_LOG},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4",
                           "2024-05-12T08:30:00Z,4751.9")),
    dict(name="rdf-message-log-unfinished-message", rule="range-requests",
         comment="Text after the last message delimiter is a message still being written: it is not read.",
         files={"metadata.ttl": RDF_LOG + 'ex:snippet tss:hasPoint [\n    ex:time-field "2024-05-12T08:45:00Z"^^xsd:dateTimeStamp ;\n'},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4",
                           "2024-05-12T08:30:00Z,4751.9")),
    dict(name="rdf-message-log-blank-node-labels", rule="rdf-points",
         comment="Blank node labels are scoped to their message: _:p in two messages are two points.",
         files={"metadata.ttl": RDF_LOG.split("ex:snippet tss:hasPoint")[0] + "".join(
             f'ex:snippet tss:hasPoint _:p .\n_:p ex:time-field "{time}"^^xsd:dateTimeStamp ; ex:value-field "{value}"^^xsd:double .\nMESSAGE\n'
             for time, value in [("2024-05-12T08:00:00Z", "4810.5"), ("2024-05-12T08:15:00Z", "4722.4")])},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4")),
    dict(name="csv-growing-unfinished-record", rule="appending-data-files",
         comment="In a growing CSV snippet, a last record without its line break is still being written: it is not read.",
         files={"metadata.ttl": external_document(immutable=False, summary=""),
                "readings.csv": "time,value\n2024-05-12T08:00:00Z,4810.5\n2024-05-12T08:15:00Z,4722.4"},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5")),
    dict(name="csv-immutable-last-record-without-line-break", rule="csv",
         comment="In an immutable CSV snippet, a last record without a line break is read.",
         files={"metadata.ttl": external_document(summary=""),
                "readings.csv": "time,value\n2024-05-12T08:00:00Z,4810.5\n2024-05-12T08:15:00Z,4722.4"},
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4")),
    dict(name="arrow-stream", rule="arrow",
         comment="A growing Arrow snippet: its data file is an Arrow IPC stream, told apart by its first bytes.",
         files={"metadata.ttl": external_document("ArrowSnippet", "readings.arrows", immutable=False, summary="")},
         arrow_stream="readings.arrows",
         expected=csv_rows(f"time,{VALUE}", "2024-05-12T08:00:00Z,4810.5", "2024-05-12T08:15:00Z,4722.4",
                           "2024-05-12T08:30:00Z,4751.9")),
    dict(name="error-csv-point-without-time", comment="A row without a time is an error.", rule="expanding",
         files={"metadata.ttl": external_document(summary=""),
                "readings.csv": csv_rows("time,value", "2024-05-12T08:00:00Z,4810.5", ",4722.4")}, error=True),
    dict(name="error-csv-value-not-in-lexical-form", rule="expanding",
         comment="A value that is not in the lexical form of its datatype (a decimal comma) is an error.",
         files={"metadata.ttl": external_document(snippet_extra='\n    tss:delimiter ";" ;', summary=""),
                "readings.csv": csv_rows("time;value", "2024-05-12T08:00:00Z;4810,5")}, error=True),
    dict(name="error-json-point-without-time", comment="A JSON point without a time is an error.", rule="expanding",
         files={"metadata.ttl": json_document('[\n  { "value": "4810.5" }\n]')}, error=True),
    dict(name="error-json-value-not-in-lexical-form", rule="expanding",
         comment='A JSON value "high" is not in the lexical form of xsd:double: an error.',
         files={"metadata.ttl": json_document('[\n  { "time": "2024-05-12T08:00:00Z", "value": "high" }\n]')}, error=True),
]

SUMMARY_TESTS = [
    dict(name="up-to-date", comment="The summary of the embedded example matches its three points.", rule="summaries",
         files={"metadata.ttl": RDF}, up_to_date=True),
    dict(name="out-of-date", comment="A point was added, but tss:count and tss:mean were not recomputed.", rule="summaries",
         files={"metadata.ttl": RDF.replace('tss:count "3"', 'tss:count "2"').replace('tss:mean "4761.6"', 'tss:mean "4766.45"')},
         up_to_date=False, out_of_date=["tss:count", "tss:mean"]),
    dict(name="out-of-date-first-time", comment="tss:firstTime is not the time of the first point with a value.",
         rule="summaries", files={"metadata.ttl": RDF.replace('tss:firstTime "2024-05-12T08:00:00Z"', 'tss:firstTime "2024-05-12T07:00:00Z"')},
         up_to_date=False, out_of_date=["tss:firstTime"]),
    dict(name="empty-values", rule="summaries",
         comment="tss:ExcludeEmptyValues: an empty value is counted in tss:missing and left out of the numbers.",
         files={"metadata.ttl": json_document("""[
  { "time": "2024-05-12T08:00:00Z", "value": "4810.5" },
  { "time": "2024-05-12T08:15:00Z", "value": "" },
  { "time": "2024-05-12T08:30:00Z", "value": "4751.9" }
]""", immutable=True, more=""" ;
    tss:summary [
        tss:missingValuePolicy tss:ExcludeEmptyValues ;
        tss:count "2"^^xsd:integer ;
        tss:missing "1"^^xsd:integer ;
        tss:firstTime "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;
        tss:lastTime "2024-05-12T08:30:00Z"^^xsd:dateTimeStamp ;
        tss:observedMinimum "4751.9"^^xsd:double ;
        tss:observedMaximum "4810.5"^^xsd:double ;
        tss:mean "4781.2"^^xsd:double ;
        tss:median "4781.2"^^xsd:double
    ]""")}, up_to_date=True),
    dict(name="same-moment", comment="tss:lastTime written with an offset is the same moment as the last point's time.",
         rule="times", files={"metadata.ttl": RDF.replace('tss:lastTime "2024-05-12T08:30:00Z"', 'tss:lastTime "2024-05-12T10:30:00+02:00"')},
         up_to_date=True),
    dict(name="decoded-values", comment="The numbers of a summary are computed from the decoded values.", rule="value-encoding",
         files={"metadata.ttl": external_document(window=False, value_extra=DELTA_ENCODING, summary="""
    tss:summary [
        tss:count "2"^^xsd:integer ;
        tss:observedMinimum "4722.4"^^xsd:double ;
        tss:observedMaximum "4810.5"^^xsd:double ;
        tss:mean "4766.45"^^xsd:double ;
        tss:median "4766.45"^^xsd:double
    ]"""), "readings.csv": csv_rows("time,value", "2024-05-12T08:00:00Z,60.5", "2024-05-12T08:15:00Z,-27.6")},
         up_to_date=True),
    dict(name="window", comment="Only the points in the window count: rows outside it are left out.", rule="summaries",
         files={"metadata.ttl": external_document(summary="""
    tss:summary [
        tss:count "2"^^xsd:integer ;
        tss:firstTime "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;
        tss:lastTime "2024-05-12T08:15:00Z"^^xsd:dateTimeStamp
    ]""").replace('tss:until "2024-05-13T00:00:00Z"', 'tss:until "2024-05-12T09:00:00Z"'),
                "readings.csv": csv_rows("time,value", "2024-05-12T07:45:00Z,4700.1", "2024-05-12T08:00:00Z,4810.5",
                                         "2024-05-12T08:15:00Z,4722.4", "2024-05-12T09:00:00Z,4700.2")},
         up_to_date=True),
]

OBSERVATION_HEADER = "snippet,observed_property,sensor,feature_of_interest,unit,time,result,datatype,match"
SNIPPET = "https://example.org/ex#snippet"
CONDUCTIVITY = "http://qudt.org/vocab/quantitykind/ElectricConductivity"
MICROSIEMENS = "http://qudt.org/vocab/unit/MicroS-PER-CentiM"
DOUBLE = "http://www.w3.org/2001/XMLSchema#double"

EXPANSION_TESTS = [
    dict(name="rdf-points", comment="The embedded example: one observation per value, with what its field says.",
         rule="expanding", files={"metadata.ttl": RDF},
         expected=csv_rows(OBSERVATION_HEADER, *[
             f"{SNIPPET},{CONDUCTIVITY},https://example.org/ex#sensor-1,https://example.org/ex#feature-1,{MICROSIEMENS},"
             f"{time},{value},{DOUBLE},text"
             for time, value in [("2024-05-12T08:00:00Z", "4810.5"), ("2024-05-12T08:15:00Z", "4722.4"),
                                 ("2024-05-12T08:30:00Z", "4751.9")]])),
    dict(name="measured-fields-only", rule="expanding",
         comment="An empty value gives no observation, and neither does a field without tss:observedProperty.",
         files={"metadata.ttl": json_document("""[
  { "time": "2024-05-12T08:00:00Z", "value": "4810.5", "quality": "good" },
  { "time": "2024-05-12T08:15:00Z", "value": "", "quality": "bad" },
  { "time": "2024-05-12T08:30:00Z", "value": "4751.9", "quality": "good" }
]""", extra_fields=", ex:quality-field") + """
ex:quality-field
    a tss:Field ;
    tss:columnName "quality" ;
    tss:datatype xsd:string ;
    tss:describesField ex:value-field .
"""},
         expected=csv_rows(OBSERVATION_HEADER,
                           f"{SNIPPET},{CONDUCTIVITY},,,{MICROSIEMENS},2024-05-12T08:00:00Z,4810.5,{DOUBLE},text",
                           f"{SNIPPET},{CONDUCTIVITY},,,{MICROSIEMENS},2024-05-12T08:30:00Z,4751.9,{DOUBLE},text")),
    dict(name="time-grid-and-delta", rule="value-encoding",
         comment="On a time grid with a value encoding: the time of each step, and the decoded value.",
         files={"metadata.ttl": grid_document(value_extra=DELTA_ENCODING),
                "readings.csv": csv_rows("step,value", "0,60.5", "1,-27.6")},
         expected=csv_rows(OBSERVATION_HEADER,
                           f"{SNIPPET},{CONDUCTIVITY},,,,2024-05-12T08:00:00Z,4810.5,{DOUBLE},value",
                           f"{SNIPPET},{CONDUCTIVITY},,,,2024-05-12T08:15:00Z,4722.4,{DOUBLE},value")),
]


# --- Writing it all --------------------------------------------------------------------------------

def turtle_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_binary_files(folder: Path, test: dict) -> None:
    if not {"parquet", "arrow", "arrow_stream"} & set(test):
        return
    import pyarrow as pa
    import pyarrow.ipc
    import pyarrow.parquet as pq

    times = [datetime(2024, 5, 12, 8, minute, tzinfo=timezone.utc) for minute in (0, 15, 30)]
    values = [4810.5, 4722.4, 4751.9]
    if "parquet" in test:
        table = pa.table({"time": pa.array(times, type=pa.timestamp("us", tz="UTC")), "value": pa.array(values, pa.float64())})
        pq.write_table(table, folder / test["parquet"])
    if "arrow" in test:
        table = pa.table({"time": pa.array(times, type=pa.timestamp("ns", tz="UTC")), "value": pa.array(values, pa.float64())})
        with pa.OSFile(str(folder / test["arrow"]), "wb") as sink, pyarrow.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
    if "arrow_stream" in test:  # a stream to which one record batch per row was appended
        table = pa.table({"time": pa.array(times, type=pa.timestamp("ns", tz="UTC")), "value": pa.array(values, pa.float64())})
        with pa.OSFile(str(folder / test["arrow_stream"]), "wb") as sink, pyarrow.ipc.new_stream(sink, table.schema) as writer:
            for batch in table.to_batches(max_chunksize=1):
                writer.write_batch(batch)


def main() -> None:
    for part in ("metadata", "reading", "summaries", "expansion"):
        shutil.rmtree(HERE / part, ignore_errors=True)
    entries = []

    def entry(kind: str, name: str, comment: str, rule: str, action: str, properties: list) -> None:
        identifier = f"{action.split('/')[0]}-{name}" if kind != "MetadataTest" else f"{action.split('/')[1]}-{name}"
        lines = [f"<#{identifier}> a tsst:{kind} ;", f"    mf:name {turtle_string(identifier)} ;",
                 f"    rdfs:comment {turtle_string(comment)} ;", f"    tsst:rule <{SPEC}#{rule}> ;",
                 f"    mf:action <{action}>"]
        lines += [f"    {prop}" for prop in properties]
        entries.append((identifier, " ;\n".join(lines) + " ."))

    for folder, tests, shapes in (("embedded", EMBEDDED_TESTS, ["embedded"]), ("external", EXTERNAL_TESTS, ["external"]),
                                  ("mixed", MIXED_TESTS, ["embedded", "external"])):
        target = HERE / "metadata" / folder
        target.mkdir(parents=True, exist_ok=True)
        for test in tests:
            (target / f"{test.name}.ttl").write_text(f"# {test.comment}\n" + test.text(), encoding="utf-8")
            properties = [f"tsst:shapes {', '.join(turtle_string(kind) for kind in shapes)}",
                          f"tsst:conforms {'true' if test.conforms else 'false'}"]
            properties += [f"tsst:violation {turtle_string(check)}" for check in test.violations]
            properties += [f"tsst:warning {turtle_string(check)}" for check in test.warnings]
            entry("MetadataTest", test.name, test.comment, test.rule, f"metadata/{folder}/{test.name}.ttl", properties)

    for part, tests, kind in (("reading", READ_TESTS, "ReadTest"), ("summaries", SUMMARY_TESTS, "SummaryTest"),
                              ("expansion", EXPANSION_TESTS, "ExpansionTest")):
        for test in tests:
            folder = HERE / part / test["name"]
            folder.mkdir(parents=True, exist_ok=True)
            for name, content in test["files"].items():
                (folder / name).write_text(content, encoding="utf-8")
            write_binary_files(folder, test)
            properties = []
            if "expected" in test:
                (folder / "expected.csv").write_text(test["expected"], encoding="utf-8")
                properties.append(f"mf:result <{part}/{test['name']}/expected.csv>")
            if test.get("error"):
                properties.append("mf:result tsst:Error")
            if "up_to_date" in test:
                properties.append(f"tsst:upToDate {'true' if test['up_to_date'] else 'false'}")
                properties += [f"tsst:outOfDate {turtle_string(name)}" for name in test.get("out_of_date", [])]
            entry(kind, test["name"], test["comment"], test["rule"], f"{part}/{test['name']}/metadata.ttl", properties)

    manifest = f"""# The TSS conformance test suite: every test, its input and its expected result.
# Written by make_suite.py; docs/conformance.md explains the test types and how to run them.
@prefix mf: <http://www.w3.org/2001/sw/DataAccess/tests/test-manifest#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix tsst: <https://w3id.org/tss/tests#> .

<> a mf:Manifest ;
    rdfs:label "TSS conformance tests" ;
    rdfs:comment "Tests of conforming metadata documents (SHACL), consumers (reading), stored summaries and expanders." ;
    rdfs:seeAlso <{SPEC}> ;
    mf:entries (
{chr(10).join(f'        <#{identifier}>' for identifier, _ in entries)}
    ) .

""" + "\n\n".join(text for _, text in entries) + "\n"
    (HERE / "manifest.ttl").write_text(manifest, encoding="utf-8")
    counts = {}
    for identifier, text in entries:
        kind = text.split(" a tsst:")[1].split(" ")[0]
        counts[kind] = counts.get(kind, 0) + 1
    print(f"Wrote {len(entries)} tests: " + ", ".join(f"{count} {kind}" for kind, count in counts.items()))


if __name__ == "__main__":
    main()
