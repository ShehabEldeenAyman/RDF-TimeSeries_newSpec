"""Dashboard entry point for the shared converter in code/TSS2RDF/tss2rdf.py,
so the dashboard and the command line make the same RDF."""
from pathlib import Path
import sys

# The code directory (two folders up) contains the self-contained TSS2RDF converter.
code_directory = str(Path(__file__).resolve().parents[2])
if code_directory not in sys.path:
    sys.path.insert(0, code_directory)

from TSS2RDF.tss2rdf import generate
