"""Dashboard entry point for the shared Parquet RDF converter and its Parquet helpers."""
from pathlib import Path
import sys

# The code directory contains the self-contained TSS2RDF converter.
code_directory = str(Path(__file__).resolve().parents[2])
if code_directory not in sys.path:
    sys.path.insert(0, code_directory)

from TSS2RDF.tss2rdf import FILE_TYPES, data_format, generate_parquet as generate, parquet_column
