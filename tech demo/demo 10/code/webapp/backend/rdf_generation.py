"""Dashboard entry point for the shared code in code/, so the dashboard and the command lines make
the same RDF, read embedded points the same way and validate the same way:
- code/TSS2RDF/tss2rdf.py (the converter):
  generate(graph, data_path, target): the SOSA observations of a source, as N-Triples (and a Turtle preview);
  point_graph(graph, source): a source's embedded points (the nodes with tss:pointOf) as an RDF graph;
  parse_exact(graph, **arguments): graph.parse, with every literal's text kept exactly;
  TSS (the vocabulary), LEXICAL_FORMS, TIME_DATATYPES, NORMALIZE_LOCK, exact_turtle, column_index, embedded_mode;
- shacl_report (code/shacl_report/shacl_report.py, the SHACL command line): the SHACL tab runs its validation_report.
The backend modules import these names from here (from rdf_generation import ...), never from code/ themselves."""
from pathlib import Path
import sys

# The code directory (two folders up) contains the self-contained TSS2RDF converter and the SHACL command line.
code_directory = str(Path(__file__).resolve().parents[2])
if code_directory not in sys.path:
    sys.path.insert(0, code_directory)

from TSS2RDF.tss2rdf import (LEXICAL_FORMS, NORMALIZE_LOCK, TIME_DATATYPES, TSS, column_index, embedded_mode,
                             exact_turtle, generate, parse_exact, point_graph)
from shacl_report import shacl_report
