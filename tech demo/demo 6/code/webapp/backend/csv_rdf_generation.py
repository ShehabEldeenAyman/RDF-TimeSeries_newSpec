"""Dashboard entry point for the shared external CSV RDF converter."""
from pathlib import Path
import sys

# Demo 4's code directory contains the self-contained TSS2RDF converter.
code_directory = str(Path(__file__).resolve().parents[2])
if code_directory not in sys.path:
    sys.path.insert(0, code_directory)

from TSS2RDF.tss2rdf import csvw_delimiter, generate_external as generate
