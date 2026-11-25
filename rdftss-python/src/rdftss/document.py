"""Loading metadata documents: :func:`load`, :func:`loads` and the :class:`Document`.

>>> import rdftss
>>> document = rdftss.load("metadata.ttl")                                    # doctest: +SKIP
>>> document = rdftss.load("https://example.org/metadata.ttl")                # doctest: +SKIP
>>> document = rdftss.loads(turtle_text, base="https://example.org/doc.ttl")  # doctest: +SKIP

How a document is read, without guessing:

1. **Its RDF syntax** is found by rdflib, from the file name (``.ttl``, ``.nt``, ``.rdf``,
   ``.trig``, ``.jsonld``, ...) or the media type a server sends. Turtle when unknown.
2. **Each source's storage** is its source class (``rdf:type``), which the specification
   requires: exactly one of ``tss:EmbeddedRDFSource``, ``tss:EmbeddedJSONSource``,
   ``tss:CSVSource``, ``tss:ParquetSource`` and ``tss:ArrowSource``. A table maps that class
   to its reader (see :mod:`rdftss.readers`); the name of a data file is never looked at.

Every literal keeps its text exactly as written (see :mod:`rdftss.rdf` for why). An RDF Message
Log, the document of a growing ``tss:EmbeddedRDFSource``, is read as the merge of its messages.
"""
from __future__ import annotations

import os
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef

from .datafiles import DataAccess
from .errors import DocumentError, Issues
from .model import Source, build_sources, local_name
from .rdf import RDFDocument, read_rdf
from .vocab import WELL_KNOWN_PREFIXES


class Document:
    """A metadata document: an RDF graph that describes one or more sources.

    - ``sources``: its :class:`~rdftss.model.Source` objects; :meth:`source` picks one.
    - ``graph``: the rdflib graph, with every literal's text kept exactly.
    - ``base``: the IRI that relative IRIs were resolved against; ``location``: where it came from.
    - ``messages``: for an RDF Message Log, how many complete messages it has; ``None`` for a
      document that is not a log, or whose text rdftss did not read (a graph given in code).
    - ``issues``: problems of the document as a whole; ``all_issues`` adds those of its sources.
    """

    def __init__(self, graph: Graph, *, base: str | None = None, location: str | None = None,
                 access: DataAccess | None = None, read: RDFDocument | None = None):
        self.graph = graph
        self.base = base
        self.location = location or base or "(text)"
        self.access = access or DataAccess()
        self.downloads: dict[str, Path] = {}  # the data files downloaded for this document, by IRI
        self.issues = Issues()
        #: ``True`` or ``False`` when rdftss read the document's text; ``None`` for a graph given in code.
        self.message_log: bool | None = read.is_message_log if read is not None else None
        self.messages: int | None = read.messages if read is not None else None
        for note in (read.notes if read is not None else []):
            self.issues.warning(note)
        self.sources: list[Source] = build_sources(self)
        if not self.sources:
            self.issues.error("The document describes no source: it must describe at least one")
        embedded = {source.kind.is_embedded for source in self.sources if source.kind is not None}
        if len(embedded) > 1:
            self.issues.error("The document describes embedded and external sources: all sources of a "
                              "document must be embedded, or all external")

    # -- finding sources ------------------------------------------------------------------------

    def source(self, which=None) -> Source:
        """One source of the document.

        - ``document.source()``: the only source (an error when there are several);
        - ``document.source(0)``: by position;
        - ``document.source("ex:source")``: by IRI (in full or with a prefix), by the last part
          of its IRI, or by its label.
        """
        if which is None:
            if len(self.sources) == 1:
                return self.sources[0]
            if not self.sources:
                raise DocumentError(f"{self.location} describes no source")
            names = ", ".join(repr(self.short(source.node)) for source in self.sources)
            raise DocumentError(f"{self.location} describes {len(self.sources)} sources ({names}); "
                                "say which one, e.g. document.source(0)")
        if isinstance(which, int):
            return self.sources[which]
        if isinstance(which, Source):
            return which
        text = str(which)
        iri = self.curie_to_iri(text) or text
        for test in (lambda s: str(s.node) == iri, lambda s: local_name(s.node) == text,
                     lambda s: s.label is not None and s.label.lower() == text.lower()):
            found = [source for source in self.sources if test(source)]
            if len(found) == 1:
                return found[0]
        names = ", ".join(repr(self.short(source.node)) for source in self.sources)
        raise KeyError(f"{self.location} has no source {which!r}. Its sources are: {names}")

    def __iter__(self):
        return iter(self.sources)

    def __len__(self) -> int:
        return len(self.sources)

    @property
    def all_issues(self) -> Issues:
        """The issues of the document and of all its sources."""
        issues = Issues(self.issues)
        for source in self.sources:
            issues.extend(source.issues)
        return issues

    # -- names -----------------------------------------------------------------------------------

    def short(self, term) -> str:
        """A term written with the document's prefixes: ``ex:source``, ``"5"^^xsd:integer``."""
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
        """What the document holds, in text: each source, and the issues."""
        lines = [f"Document {self.location}: {len(self.sources)} source(s)"]
        for source in self.sources:
            lines.append("")
            lines.append(source.describe())
        if self.issues:
            lines.append("")
            lines.append("Document issues:")
            lines.extend(f"  {issue}" for issue in self.issues)
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<Document {self.location} ({len(self.sources)} source(s))>"

    # -- using it --------------------------------------------------------------------------------

    def expand(self, destination=None, **options):
        """Expand every source into its points as RDF. See :func:`rdftss.expansion.expand`."""
        from .expansion import expand

        return expand(self, destination, **options)

    def validate(self, **options):
        """Validate the document with the specification's SHACL shapes (or yours). See
        :func:`rdftss.validation.validate`."""
        from .validation import validate

        return validate(self, **options)

    def verify_summaries(self, **options):
        """Recompute every stored summary of the document's sources from the data, and compare.
        See :func:`rdftss.summaries.verify_summaries`."""
        from .summaries import verify_summaries

        return verify_summaries(self, **options)

    def sparql(self, query: str, **options):
        """Run one read-only SPARQL query over the document and the points of all its sources.
        See :class:`rdftss.sparql.TripleStore`."""
        from .sparql import TripleStore

        with TripleStore(self.sources, **options) as store:
            return store.query(query)

    def to_turtle(self, path=None) -> str:
        """The document as Turtle: literals keep their text, and the RDF points come last, one
        statement per row. A document with a growing ``tss:EmbeddedRDFSource`` is written as an
        RDF Message Log, one message per row, so a new row is appended as one more message. With
        ``path`` (where the text will be written), ``tss:dataFile`` is written relative to it."""
        from .writing import document_turtle

        return document_turtle(self.graph, document_path=path)

    def save(self, path) -> Path:
        """Write the document as Turtle (see :meth:`to_turtle`) and return the path."""
        path = Path(path)
        path.write_text(self.to_turtle(path), encoding="utf-8")
        return path


def _address(location) -> str | None:
    """The IRI of a document location: a file's ``file:`` IRI, or the URL itself."""
    if isinstance(location, str) and "://" in location:
        return location
    if isinstance(location, (str, os.PathLike)):
        return Path(location).resolve().as_uri()
    name = getattr(location, "name", None)
    return Path(name).resolve().as_uri() if isinstance(name, str) and os.path.exists(name) else None


def load(location, *, format: str | None = None, base: str | None = None, allow_network: bool = True,
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
    if isinstance(location, os.PathLike):
        location = str(location)
    read = read_rdf(location, format=format, base=base)
    return Document(read.graph, base=base or _address(location), location=str(getattr(location, "name", location)),
                    access=access, read=read)


def loads(text: str | bytes, *, format: str = "turtle", base: str | None = None, **options) -> Document:
    """Load a metadata document from a text (Turtle unless ``format`` says otherwise).

    Without ``base``, relative IRIs (such as ``<data.csv>``) are resolved against the current
    folder. The other options are those of :func:`load`."""
    access = DataAccess(allow_network=options.get("allow_network", True),
                        data_folder=Path(options["data_folder"]) if options.get("data_folder") else None,
                        cache_folder=Path(options["cache_folder"]) if options.get("cache_folder") else None,
                        timeout=options.get("timeout", 60.0))
    read = read_rdf(data=text, format=format, base=base)
    return Document(read.graph, base=base, location=base or "(text)", access=access, read=read)
