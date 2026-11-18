"""rdftss: one place to work with RDF Time Series Snippets (TSS).

Load a metadata document, read its snippets in any of the five storage classes, query and
analyse the points, discover snippets across documents, expand them into observations and
convert them to other storage classes::

    import rdftss

    document = rdftss.load("metadata.ttl")
    snippet = document.snippet()
    print(snippet.describe())

    points = snippet.read()                    # in time order, decoded, inside the window
    points.statistics("value")                 # count, mean, median, ... like tss:summary
    frame = snippet.to_pandas()                # a DataFrame

    snippet.expand("observations.nt")          # SOSA observations, streamed to a file
    snippet.convert("parquet", document="out/readings.ttl")   # data: out/readings.parquet

    documents = [rdftss.load(path) for path in ["a.ttl", "b.ttl"]]
    rdftss.find(documents, observed_property="quantitykind:ElectricConductivity")

The package implements the specification of demo 11, vocabulary version :data:`SPEC_VERSION`.
See the README and docs/ for a guide.
"""
from __future__ import annotations

from .document import Document, load, loads
from .errors import (DataError, DataFileError, DocumentError, Issue, Issues, MissingDependencyError,
                     RDFTSSError, UnsupportedError)
from .model import CSVDialect, Field, Snippet, SnippetKind, Summary, TimeGrid, TimeWindow
from .points import Point, PointReader, Points
from .times import Duration, Time
from .vocab import SPEC_URL, SPEC_VERSION, TSS

__version__ = "0.1.0"

# Imported when first used, so that "import rdftss" stays fast and optional packages stay optional.
_LAZY = {
    "find": ("rdftss.discovery", "find"),
    "Match": ("rdftss.discovery", "Match"),
    "Statistics": ("rdftss.analysis", "Statistics"),
    "Database": ("rdftss.sql", "Database"),
    "expand": ("rdftss.expansion", "expand"),
    "observations": ("rdftss.expansion", "observations"),
    "ObservationMapping": ("rdftss.expansion", "ObservationMapping"),
    "SOSAMapping": ("rdftss.expansion", "SOSAMapping"),
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
    "load", "loads", "Document", "Snippet", "SnippetKind", "Field", "Summary", "TimeWindow", "TimeGrid",
    "CSVDialect", "Point", "Points", "PointReader", "Time", "Duration",
    "RDFTSSError", "DocumentError", "DataError", "DataFileError", "UnsupportedError",
    "MissingDependencyError", "Issue", "Issues", "TSS", "SPEC_VERSION", "SPEC_URL", "__version__",
    *_LAZY,
]
