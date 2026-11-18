"""Loading metadata documents: :func:`load`, :func:`loads` and the :class:`Document`.

>>> import rdftss
>>> document = rdftss.load("metadata.ttl")                                    # doctest: +SKIP
>>> document = rdftss.load("https://example.org/metadata.ttl")                # doctest: +SKIP
>>> document = rdftss.loads(turtle_text, base="https://example.org/doc.ttl")  # doctest: +SKIP

How a document is read, without guessing:

1. **Its RDF syntax** is found by rdflib, from the file name (``.ttl``, ``.nt``, ``.rdf``,
   ``.trig``, ``.jsonld``, ...) or the media type a server sends. Turtle when unknown.
2. **Each snippet's storage** is its snippet class (``rdf:type``), which the specification
   requires: exactly one of ``tss:EmbeddedRDFSnippet``, ``tss:EmbeddedJSONSnippet``,
   ``tss:CSVSnippet``, ``tss:ParquetSnippet`` and ``tss:ArrowSnippet``. A table maps that class
   to its reader (see :mod:`rdftss.readers`); the name of a data file is never looked at.

Every literal keeps its text exactly as written (see :mod:`rdftss.rdf` for why).
"""
from __future__ import annotations

import os
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef

from .datafiles import DataAccess
from .errors import DocumentError, Issues
from .model import Snippet, build_snippets, local_name
from .rdf import parse_rdf
from .vocab import WELL_KNOWN_PREFIXES


class Document:
    """A metadata document: an RDF graph that describes one or more snippets.

    - ``snippets``: its :class:`~rdftss.model.Snippet` objects; :meth:`snippet` picks one.
    - ``graph``: the rdflib graph, with every literal's text kept exactly.
    - ``base``: the IRI that relative IRIs were resolved against; ``source``: where it came from.
    - ``issues``: problems of the document as a whole; ``all_issues`` adds those of its snippets.
    """

    def __init__(self, graph: Graph, *, base: str | None = None, source: str | None = None,
                 access: DataAccess | None = None):
        self.graph = graph
        self.base = base
        self.source = source or base or "(text)"
        self.access = access or DataAccess()
        self.downloads: dict[str, Path] = {}  # the data files downloaded for this document, by IRI
        self.issues = Issues()
        self.snippets: list[Snippet] = build_snippets(self)
        if not self.snippets:
            self.issues.error("The document describes no snippet: it must describe at least one")
        embedded = {snippet.kind.is_embedded for snippet in self.snippets if snippet.kind is not None}
        if len(embedded) > 1:
            self.issues.error("The document describes embedded and external snippets: all snippets of a "
                              "document must be embedded, or all external")

    # -- finding snippets ------------------------------------------------------------------------

    def snippet(self, which=None) -> Snippet:
        """One snippet of the document.

        - ``document.snippet()``: the only snippet (an error when there are several);
        - ``document.snippet(0)``: by position;
        - ``document.snippet("ex:snippet")``: by IRI (in full or with a prefix), by the last part
          of its IRI, or by its label.
        """
        if which is None:
            if len(self.snippets) == 1:
                return self.snippets[0]
            if not self.snippets:
                raise DocumentError(f"{self.source} describes no snippet")
            names = ", ".join(repr(self.short(snippet.node)) for snippet in self.snippets)
            raise DocumentError(f"{self.source} describes {len(self.snippets)} snippets ({names}); "
                                "say which one, e.g. document.snippet(0)")
        if isinstance(which, int):
            return self.snippets[which]
        if isinstance(which, Snippet):
            return which
        text = str(which)
        iri = self.curie_to_iri(text) or text
        for test in (lambda s: str(s.node) == iri, lambda s: local_name(s.node) == text,
                     lambda s: s.label is not None and s.label.lower() == text.lower()):
            found = [snippet for snippet in self.snippets if test(snippet)]
            if len(found) == 1:
                return found[0]
        names = ", ".join(repr(self.short(snippet.node)) for snippet in self.snippets)
        raise KeyError(f"{self.source} has no snippet {which!r}. Its snippets are: {names}")

    def __iter__(self):
        return iter(self.snippets)

    def __len__(self) -> int:
        return len(self.snippets)

    @property
    def all_issues(self) -> Issues:
        """The issues of the document and of all its snippets."""
        issues = Issues(self.issues)
        for snippet in self.snippets:
            issues.extend(snippet.issues)
        return issues

    # -- names -----------------------------------------------------------------------------------

    def short(self, term) -> str:
        """A term written with the document's prefixes: ``ex:snippet``, ``"5"^^xsd:integer``."""
        if term is None:
            return "None"
        if isinstance(term, URIRef):
            try:
                return self.graph.namespace_manager.normalizeUri(term)
            except Exception:
                return f"<{term}>"
        if isinstance(term, BNode):
            return f"_:{term}"
        if isinstance(term, Literal):
            return term.n3(self.graph.namespace_manager)
        return str(term)

    def curie_to_iri(self, name: str) -> str | None:
        """The full IRI of a prefixed name such as ``"ex:value-field"``, with the document's
        prefixes (and the well-known ones, such as ``unit:``), or ``None``."""
        if ":" not in name or name.startswith(("http://", "https://", "urn:", "file:")):
            return None
        prefix, rest = name.split(":", 1)
        namespaces = {prefix: str(namespace) for prefix, namespace in self.graph.namespaces()}
        namespace = namespaces.get(prefix) or (str(WELL_KNOWN_PREFIXES[prefix]) if prefix in WELL_KNOWN_PREFIXES else None)
        return namespace + rest if namespace else None

    # -- describing it ---------------------------------------------------------------------------

    def describe(self) -> str:
        """What the document holds, in text: each snippet, and the issues."""
        lines = [f"Document {self.source}: {len(self.snippets)} snippet(s)"]
        for snippet in self.snippets:
            lines.append("")
            lines.append(snippet.describe())
        if self.issues:
            lines.append("")
            lines.append("Document issues:")
            lines.extend(f"  {issue}" for issue in self.issues)
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<Document {self.source} ({len(self.snippets)} snippet(s))>"

    # -- using it --------------------------------------------------------------------------------

    def expand(self, destination=None, **options):
        """Expand every snippet into observations. See :func:`rdftss.expansion.expand`."""
        from .expansion import expand

        return expand(self, destination, **options)

    def sql(self, query: str, **limits):
        """Run one read-only SQL query over all snippets of the document. See :class:`rdftss.sql.Database`."""
        from .sql import Database

        with Database(self.snippets, **limits) as database:
            return database.query(query)

    def to_turtle(self, path=None) -> str:
        """The document as Turtle: literals keep their text, and RDF points come last, one
        statement each (so a new point can be appended at the end). With ``path`` (where the
        text will be written), ``tss:dataFile`` is written relative to it."""
        from .writing import document_turtle

        return document_turtle(self.graph, document_path=path)

    def save(self, path) -> Path:
        """Write the document as Turtle (see :meth:`to_turtle`) and return the path."""
        path = Path(path)
        path.write_text(self.to_turtle(path), encoding="utf-8")
        return path


def _address(source) -> str | None:
    """The IRI of a document source: a file's ``file:`` IRI, or the URL itself."""
    if isinstance(source, str) and "://" in source:
        return source
    if isinstance(source, (str, os.PathLike)):
        return Path(source).resolve().as_uri()
    name = getattr(source, "name", None)
    return Path(name).resolve().as_uri() if isinstance(name, str) and os.path.exists(name) else None


def load(source, *, format: str | None = None, base: str | None = None, allow_network: bool = True,
         data_folder=None, cache_folder=None, timeout: float = 60.0) -> Document:
    """Load a metadata document from a path, a URL, or a file object.

    - ``format``: the RDF syntax, such as ``"turtle"``. By default rdflib finds it from the file
      name or the server's media type.
    - ``base``: the IRI that relative IRIs are resolved against. By default the document's own
      address, so ``tss:dataFile <../data/x.csv>`` is found next to the document, also online.
    - ``allow_network``: download data files that are ``http:``/``https:`` IRIs (on by default).
      Turn it off for documents you do not trust: they could make your program fetch any address.
    - ``data_folder``: only read local data files inside this folder.
    - ``cache_folder``: keep downloaded data files here, between runs (they are checked with the
      server before reuse). By default downloads are kept only as long as the document.

    Raises :class:`~rdftss.errors.DocumentError` when the document can't be read or parsed.
    Problems with what the document says are not raised: see ``document.all_issues``.
    """
    access = DataAccess(allow_network=allow_network, data_folder=Path(data_folder) if data_folder else None,
                        cache_folder=Path(cache_folder) if cache_folder else None, timeout=timeout)
    if isinstance(source, os.PathLike):
        source = str(source)
    graph = parse_rdf(source, format=format, base=base)
    return Document(graph, base=base or _address(source), source=str(getattr(source, "name", source)), access=access)


def loads(text: str | bytes, *, format: str = "turtle", base: str | None = None, **options) -> Document:
    """Load a metadata document from a text (Turtle unless ``format`` says otherwise).

    Without ``base``, relative IRIs (such as ``<data.csv>``) are resolved against the current
    folder. The other options are those of :func:`load`."""
    access = DataAccess(allow_network=options.get("allow_network", True),
                        data_folder=Path(options["data_folder"]) if options.get("data_folder") else None,
                        cache_folder=Path(options["cache_folder"]) if options.get("cache_folder") else None,
                        timeout=options.get("timeout", 60.0))
    graph = parse_rdf(data=text, format=format, base=base)
    return Document(graph, base=base, source=base or "(text)", access=access)
