"""Reading and writing RDF with rdflib, keeping the text of every literal exactly as written.

rdflib finds the syntax of a document itself: from the file name (``.ttl``, ``.nt``, ``.rdf``,
``.trig``, ``.jsonld``, ...) or from the media type a server sends. rdftss only changes one thing
about how rdflib parses.

By default, rdflib rewrites the text of a literal while it parses:

=============================  ===================  =================
Written in the document        rdflib's default     rdftss
=============================  ===================  =================
``" 12 "^^xsd:double``         ``12.0``             `` 12 ``
``"yes"^^xsd:boolean``         ``false``            ``yes``
``"1_000"^^xsd:double``        ``1000.0``           ``1_000``
``"3.0E1"^^xsd:double``        ``30.0``             ``3.0E1``
``"...00Z"^^xsd:dateTime``     ``...00+00:00``      ``...00Z``
=============================  ===================  =================

The specification says that a value keeps its text exactly as written, and that a value that is
not in the lexical form of its datatype is an error. With rdflib's default, wrong values would
become valid (or, like ``"yes"``, silently ``false``), and observations would get other texts
than the data. So :func:`parse_rdf` parses with ``rdflib.NORMALIZE_LITERALS`` switched off.

That switch is global in rdflib (pyshacl's ``validate()`` flips it too), so :data:`NORMALIZE_LOCK`
makes the code that flips it take turns. :func:`exact_turtle` writes Turtle in which numbers and
booleans keep their text as well.
"""
from __future__ import annotations

import io
import threading
import warnings

import rdflib
from rdflib import Dataset, Graph, Literal
from rdflib.plugins.serializers.turtle import TurtleSerializer

from .errors import DocumentError
from .vocab import XSD

#: Taken while ``rdflib.NORMALIZE_LITERALS`` is switched off (see the module documentation).
NORMALIZE_LOCK = threading.RLock()


def parse_rdf(source=None, *, data=None, format: str | None = None, base: str | None = None) -> Graph:
    """Parse RDF into an rdflib :class:`~rdflib.Graph`, keeping every literal's text exactly.

    - ``source``: a path, a URL, or a file object; or ``data``: the RDF text itself.
    - ``format``: an rdflib format name (``"turtle"``, ``"nt"``, ``"xml"``, ``"trig"``,
      ``"json-ld"``, ...). By default rdflib finds it from the file name or media type.
    - ``base``: the IRI that relative IRIs are resolved against. By default, the document's
      own address.

    Documents with named graphs (TriG, N-Quads) are read as the union of their graphs.
    Raises :class:`~rdftss.errors.DocumentError` when the document can't be read or parsed.
    """
    dataset = Dataset(default_union=True)
    with NORMALIZE_LOCK:
        normalize = rdflib.NORMALIZE_LITERALS
        rdflib.NORMALIZE_LITERALS = False
        try:
            with warnings.catch_warnings():  # rdflib 7.6 uses its own deprecated Dataset.default_context
                warnings.filterwarnings("ignore", message=".*default_context is deprecated", category=DeprecationWarning)
                warnings.filterwarnings("ignore", message="ConjunctiveGraph is deprecated", category=DeprecationWarning)
                dataset.parse(source=source, data=data, format=format, publicID=base)
        except FileNotFoundError:
            hint = ""
            if isinstance(source, str) and ("\n" in source or "@prefix" in source):
                hint = " To read RDF text, use rdftss.loads(text)."
            raise DocumentError(f"There is no file {str(source)[:120]!r}.{hint}") from None
        except Exception as error:  # rdflib and urllib raise many kinds of errors
            what = "the text" if data is not None else repr(str(source))
            raise DocumentError(f"Can't read {what} as RDF: {error}") from None
        finally:
            rdflib.NORMALIZE_LITERALS = normalize
    graph = Graph()
    for prefix, namespace in dataset.namespaces():
        graph.bind(prefix, namespace, override=True, replace=True)
    for subject, predicate, value, _ in dataset.quads((None, None, None, None)):
        graph.add((subject, predicate, value))
    return graph


#: Datatypes whose literals rdflib's Turtle writer writes in a short form made from their value.
SHORT_FORM_DATATYPES = (XSD.integer, XSD.boolean, XSD.double, XSD.decimal)


class ExactTurtleSerializer(TurtleSerializer):
    """rdflib's Turtle writer, except that literals of :data:`SHORT_FORM_DATATYPES` are written
    in full (``"text"^^datatype``), so their text stays exactly as it is."""

    def label(self, node, position):
        if isinstance(node, Literal) and node.datatype in SHORT_FORM_DATATYPES:
            return node.n3(self.store.namespace_manager)
        return super().label(node, position)


def exact_turtle(graph: Graph) -> str:
    """A graph as Turtle text in which every literal keeps its text: ``"4797.72"^^xsd:double``,
    ``"yes"^^xsd:boolean`` (which is wrong, but stays visible)."""
    stream = io.BytesIO()
    ExactTurtleSerializer(graph).serialize(stream, encoding="utf-8")
    return stream.getvalue().decode("utf-8")
