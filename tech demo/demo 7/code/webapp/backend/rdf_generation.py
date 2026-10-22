"""Dashboard entry point for the shared converter in code/TSS2RDF/tss2rdf.py,
so the dashboard and the command line make the same RDF and read embedded points the same way:
- generate(graph, data_path, target): the SOSA observations of a source, as Turtle;
- lift_points(graph, source): a source's tss:points as an RDF graph (JSON-LD, with a context made from its fields);
- points_context(graph, source): that JSON-LD context."""
from pathlib import Path
import sys

# The code directory (two folders up) contains the self-contained TSS2RDF converter.
code_directory = str(Path(__file__).resolve().parents[2])
if code_directory not in sys.path:
    sys.path.insert(0, code_directory)

from TSS2RDF.tss2rdf import generate, lift_points, points_context
