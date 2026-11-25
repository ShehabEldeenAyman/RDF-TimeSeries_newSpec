"""The documentation's code runs, requirements.txt lists every dependency, and the bundled
vocabulary and shapes are those of the specification (checked against the repository's working
copy of the specification, when rdftss is next to it)."""
import contextlib
import io
import re
import runpy
import shutil
import tempfile

import pytest
from rdflib import Literal, URIRef
from rdflib.namespace import DCTERMS

from rdftss.vocab import SPEC_VERSION, XSD, vocabulary_graph, vocabulary_path

from conftest import DEMO_11, EXAMPLES, ROOT


def code_blocks():
    for document in ("README.md", "docs/guide.md"):
        text = (ROOT / document).read_text(encoding="utf-8")
        for number, block in enumerate(re.findall(r"```python\n(.*?)```", text, flags=re.S), 1):
            yield f"{document}, block {number}", block


def test_the_code_in_the_readme_and_the_guide_runs(tmp_path, monkeypatch):
    for package in ("pandas", "polars"):
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
    assert "Same rows: True" in output.getvalue()


def test_the_requirements_file_lists_every_dependency():
    tomllib = pytest.importorskip("tomllib")    # Python 3.11 and newer
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wanted = set(project["dependencies"]).union(*project["optional-dependencies"].values())
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    lines = [line.split("#")[0].strip() for line in text.splitlines()]
    assert {line for line in lines if line and not line.startswith("-")} == wanted, \
        "requirements.txt and pyproject.toml list different packages"
    assert "-e ." in lines, "requirements.txt should also install rdftss itself"


def test_the_bundled_vocabulary():
    graph = vocabulary_graph()
    assert (URIRef("https://w3id.org/tss#"), DCTERMS.modified, Literal(SPEC_VERSION, datatype=XSD.date)) in graph


@pytest.mark.skipif(not DEMO_11.exists(), reason="the demo 11 folder is not next to rdftss")
@pytest.mark.parametrize("name", ["embedded_shapes.ttl", "external_shapes.ttl"])
def test_the_bundled_shapes_are_those_of_demo_11(name):
    from rdftss.validation import SHAPES_FOLDER

    assert (SHAPES_FOLDER / name).read_bytes() == (DEMO_11 / "SHACL" / name).read_bytes(), \
        f"the shapes of demo 11 changed: copy SHACL/{name} to src/rdftss/shapes/"


@pytest.mark.skipif(not DEMO_11.exists(), reason="the demo 11 folder is not next to rdftss")
def test_the_bundled_vocabulary_is_the_one_of_demo_11():
    assert vocabulary_path().read_bytes() == (DEMO_11 / "vocabulary" / "tss.ttl").read_bytes(), \
        "the vocabulary of demo 11 changed: copy it to src/rdftss/vocabulary/tss.ttl"
