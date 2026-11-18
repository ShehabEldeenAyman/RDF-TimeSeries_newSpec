"""Shared helpers for the tests."""
from pathlib import Path

import pytest

import rdftss

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
DEMO_11 = ROOT.parent / "tech demo" / "demo 11"

PREFIXES = """@prefix tss: <https://w3id.org/tss#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
@prefix quantitykind: <http://qudt.org/vocab/quantitykind/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix ex: <https://example.org/ex#> .
"""


def snippet_text(kind="CSVSnippet", extra="", time_field="", value_field="", window=True, fields="") -> str:
    """A small snippet description: a time field and a double value field."""
    bounds = ('    tss:from "2024-05-12T08:00:00Z"^^xsd:dateTimeStamp ;\n'
              '    tss:until "2024-05-12T10:00:00Z"^^xsd:dateTimeStamp ;\n') if window else ""
    return PREFIXES + f"""
ex:s a tss:{kind} ;
    tss:mutable false ;
    tss:committedUntil "2024-05-13T00:00:00Z"^^xsd:dateTimeStamp ;
{bounds}{extra}
    tss:timeField ex:time ;
    tss:hasField ex:time, ex:value{fields} .
ex:time a tss:Field ; tss:datatype xsd:dateTimeStamp {time_field}.
ex:value a tss:Field ; tss:datatype xsd:double ;
    tss:observedProperty quantitykind:ElectricConductivity ; tss:unit unit:MicroS-PER-CentiM {value_field}.
"""


@pytest.fixture
def make(tmp_path):
    """make(turtle, files={"data.csv": "..."}) writes a document (and data files) in a temporary
    folder and loads it."""

    def _make(text: str, files: dict | None = None, name: str = "doc.ttl", **options):
        for file_name, content in (files or {}).items():
            target = tmp_path / file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return rdftss.load(path, **options)

    return _make


@pytest.fixture
def example():
    """example("readings_csv.ttl") loads an example document."""

    def _example(name: str):
        return rdftss.load(EXAMPLES / name)

    return _example
