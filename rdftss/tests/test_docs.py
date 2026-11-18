"""The documentation's code runs, and the bundled vocabulary is the one of demo 11."""
import contextlib
import io
import re
import runpy
import shutil
import tempfile

import pytest
from rdflib import Literal, URIRef
from rdflib.namespace import OWL

from rdftss.vocab import SPEC_VERSION, vocabulary_graph, vocabulary_path

from conftest import DEMO_11, EXAMPLES, ROOT


def code_blocks():
    for document in ("README.md", "docs/guide.md"):
        text = (ROOT / document).read_text(encoding="utf-8")
        for number, block in enumerate(re.findall(r"```python\n(.*?)```", text, flags=re.S), 1):
            yield f"{document}, block {number}", block


def test_the_code_in_the_readme_and_the_guide_runs(tmp_path, monkeypatch):
    for package in ("pandas", "polars", "duckdb"):
        pytest.importorskip(package)
    shutil.copytree(EXAMPLES, tmp_path / "examples")
    (tmp_path / "out").mkdir()
    monkeypatch.chdir(tmp_path)
    namespace: dict = {}
    for name, code in code_blocks():
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(code, name, "exec"), namespace)


def test_the_quickstart_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **options: str(tmp_path))
    with contextlib.redirect_stdout(io.StringIO()) as output:
        runpy.run_path(str(EXAMPLES / "quickstart.py"), run_name="__main__")
    assert "Same points: True" in output.getvalue()


def test_the_bundled_vocabulary():
    graph = vocabulary_graph()
    assert (URIRef("https://w3id.org/tss#"), OWL.versionInfo, Literal(SPEC_VERSION)) in graph


@pytest.mark.skipif(not DEMO_11.exists(), reason="the demo 11 folder is not next to rdftss")
def test_the_bundled_vocabulary_is_the_one_of_demo_11():
    assert vocabulary_path().read_bytes() == (DEMO_11 / "vocabulary" / "tss.ttl").read_bytes(), \
        "the vocabulary of demo 11 changed: copy it to src/rdftss/vocabulary/tss.ttl"
