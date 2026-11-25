"""rdftss: one place to work with RDF Time Series Sources (TSS).

Load a metadata document, read its sources in any of the five storage classes, query and
analyse the rows, discover sources across documents, expand them into points as RDF, and
convert them to other storage classes::

    import rdftss

    document = rdftss.load("metadata.ttl")
    source = document.source()
    print(source.describe())

    rows = source.read()                      # in time order, decoded, inside the window
    rows.statistics("value")                  # count, mean, median, ... like tss:summary
    frame = source.to_pandas()                # a DataFrame

    source.expand("points.nt")                # the points as RDF, streamed to a file
    source.convert("parquet", document="out/readings.ttl")   # data: out/readings.parquet

    documents = [rdftss.load(path) for path in ["a.ttl", "b.ttl"]]
    rdftss.find(documents, observed_property="quantitykind:ElectricConductivity")

The package implements the RDF Time Series Sources specification, vocabulary version
:data:`SPEC_VERSION`. See the README and docs/ for a guide.
"""
from __future__ import annotations

from .document import Document, load, loads
from .errors import (DataError, DataFileError, DocumentError, Issue, Issues, MissingDependencyError,
                     RDFTSSError, UnsupportedError)
from .model import CSVDialect, Field, Source, SourceKind, Summary, TimeGrid, TimeWindow
from .rows import Row, RowReader, Rows
from .times import Duration, Time
from .vocab import SPEC_URL, SPEC_VERSION, TSS

__version__ = "0.2.0"

# Imported when first used, so that "import rdftss" stays fast and optional packages stay optional.
_LAZY = {
    "find": ("rdftss.discovery", "find"),
    "Match": ("rdftss.discovery", "Match"),
    "Statistics": ("rdftss.analysis", "Statistics"),
    "TripleStore": ("rdftss.sparql", "TripleStore"),
    "validate": ("rdftss.validation", "validate"),
    "ValidationReport": ("rdftss.validation", "ValidationReport"),
    "verify_summaries": ("rdftss.summaries", "verify_summaries"),
    "SummaryReport": ("rdftss.summaries", "SummaryReport"),
    "QueryError": ("rdftss.sparql", "QueryError"),
    "expand": ("rdftss.expansion", "expand"),
    "Point": ("rdftss.expansion", "Point"),
    "derive": ("rdftss.alignment", "derive"),
    "convert": ("rdftss.conversion", "convert"),
    "split": ("rdftss.conversion", "split"),
    "merge": ("rdftss.conversion", "merge"),
    "subset": ("rdftss.conversion", "subset"),
    "from_observations": ("rdftss.conversion", "from_observations"),
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module, attribute = _LAZY[name]
        return getattr(importlib.import_module(module), attribute)
    raise AttributeError(f"module 'rdftss' has no attribute {name!r}")


__all__ = [
    "load", "loads", "Document", "Source", "SourceKind", "Field", "Summary", "TimeWindow", "TimeGrid",
    "CSVDialect", "Row", "Rows", "RowReader", "Time", "Duration",
    "RDFTSSError", "DocumentError", "DataError", "DataFileError", "UnsupportedError",
    "MissingDependencyError", "Issue", "Issues", "TSS", "SPEC_VERSION", "SPEC_URL", "__version__",
    *_LAZY,
]
