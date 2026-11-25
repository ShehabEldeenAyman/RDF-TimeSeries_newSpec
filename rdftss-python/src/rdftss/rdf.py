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

**RDF Message Logs.** The metadata document of a growing ``tss:EmbeddedRDFSource`` is an RDF
Message Log: Turtle or TriG that announces the version label ``1.2-messages`` and ends every
message with a delimiter, ``MESSAGE`` or ``@message .``, on a line of its own::

    VERSION "1.2-messages"
    PREFIX ex: <https://example.org/>
    ex:source a tss:EmbeddedRDFSource ; ...
    MESSAGE
    ex:source tss:hasPoint [ tss:time "2024-05-12T08:00:00Z"^^xsd:dateTime ; ... ] .
    MESSAGE

rdflib can't read the version directive or the delimiters, so :func:`parse_rdf` reads such a
document itself, as the specification says: as the RDF merge of its messages. Each message is
parsed on its own, with the prefixes and base in force at the end of the messages before it,
so the blank node labels of two messages are never the same node. A last message that no
delimiter ends is still being written: it is left out, and :func:`read_rdf` says so.
"""
from __future__ import annotations

import io
import logging
import re
import threading
import warnings
from dataclasses import dataclass, field as dataclass_field

import rdflib
from rdflib import Dataset, Graph, Literal
from rdflib.plugins.serializers.turtle import TurtleSerializer

from .errors import DocumentError
from .vocab import XSD

#: Taken while ``rdflib.NORMALIZE_LITERALS`` is switched off (see the module documentation).
NORMALIZE_LOCK = threading.RLock()

#: The version label of an RDF Message Log.
MESSAGE_LOG_VERSION = "1.2-messages"
#: A version directive: ``VERSION "1.2-messages"`` or ``@version "1.2-messages" .``
_VERSION = re.compile(r"""^[ \t]*(?:VERSION|@version)[ \t]+(?:"([^"\n]*)"|'([^'\n]*)')[ \t]*\.?[ \t]*(?:#.*)?$""",
                      re.IGNORECASE | re.MULTILINE)
#: A message delimiter, on a line of its own: ``MESSAGE`` or ``@message .``
_DELIMITER = re.compile(r"^[ \t]*(?:MESSAGE|@message[ \t]*\.)[ \t]*(?:#.*)?$", re.IGNORECASE)
#: Prefix and base directives, which stay in force in the messages after the one that states them.
_DIRECTIVE = re.compile(r"^[ \t]*(?:@prefix|@base|PREFIX|BASE)\b.*$", re.IGNORECASE)


@dataclass
class RDFDocument:
    """What :func:`read_rdf` read: the ``graph``, and for an RDF Message Log, ``messages``: how
    many complete messages it has (``None`` for a document that is not a log), and ``incomplete``:
    whether text after the last delimiter was left out because no delimiter ends it yet."""

    graph: Graph
    messages: int | None = None
    incomplete: bool = False
    notes: list = dataclass_field(default_factory=list)

    @property
    def is_message_log(self) -> bool:
        return self.messages is not None


def is_message_log(text: str) -> bool:
    """Whether a Turtle or TriG text announces the version label ``1.2-messages``."""
    return any((match.group(1) or match.group(2)) == MESSAGE_LOG_VERSION for match in _VERSION.finditer(text))


def split_messages(text: str) -> tuple[list[str], str]:
    """The messages of an RDF Message Log, and the text after the last delimiter.

    A delimiter is a line that holds only ``MESSAGE`` or ``@message .`` (and maybe a comment),
    outside a long string (``\"\"\"...\"\"\"``). Version directives are left out: rdflib can't read
    them. A delimiter right before the end of the text does not start a new message."""
    messages, current, quote = [], [], None
    for line in text.splitlines(keepends=True):
        if quote is None and _DELIMITER.match(line.rstrip("\r\n")):
            messages.append("".join(current))
            current = []
            continue
        if quote is None and _VERSION.match(line.rstrip("\r\n")):
            continue
        current.append(line)
        for long_quote in re.findall(r'"""|\'\'\'', line):  # a delimiter inside a long string is text
            if quote is None:
                quote = long_quote
            elif long_quote == quote:
                quote = None
    return messages, "".join(current)


def _directives(text: str) -> list[str]:
    return [match.group(0).strip() for line in text.splitlines() if (match := _DIRECTIVE.match(line))]


def read_rdf(location=None, *, data=None, format: str | None = None, base: str | None = None) -> RDFDocument:
    """Like :func:`parse_rdf`, and also says whether the document is an RDF Message Log."""
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    if text is None and location is not None and not hasattr(location, "read"):
        try:
            return RDFDocument(parse_rdf(location, format=format, base=base))
        except DocumentError as error:
            text = _text_of(location)  # maybe an RDF Message Log, which rdflib can't read
            if text is None or not is_message_log(text):
                raise error from None
            base = base or _address_of(location)
            format = format or ("trig" if str(location).lower().endswith(".trig") else "turtle")
    if text is None or not is_message_log(text):
        return RDFDocument(parse_rdf(location, data=data, format=format, base=base))

    messages, rest = split_messages(text)
    notes = []
    if rest.strip() and not all(line.strip() == "" or line.strip().startswith("#") for line in rest.splitlines()):
        notes.append("the text after the last message delimiter is not read: a message that no delimiter ends "
                     "is still being written")
    dataset = Dataset(default_union=True)
    in_force: list[str] = []
    for number, message in enumerate(messages, start=1):
        try:
            _parse_into(dataset, data="\n".join(in_force) + "\n" + message, format=format or "turtle", base=base)
        except DocumentError as error:
            raise DocumentError(f"Message {number} of the RDF Message Log: {error}") from None
        in_force += _directives(message)
    return RDFDocument(_as_graph(dataset), messages=len(messages), incomplete=bool(notes), notes=notes)


def _text_of(location) -> str | None:
    """The text of a local file or a URL, or ``None`` when it can't be fetched."""
    try:
        from rdflib.parser import create_input_source

        stream = create_input_source(source=location).getByteStream()
        content = stream.read()
        stream.close()
        return content.decode("utf-8") if isinstance(content, bytes) else content
    except Exception:
        return None


def _address_of(location) -> str | None:
    from pathlib import Path

    text = str(location)
    return text if "://" in text else Path(text).resolve().as_uri()


def parse_rdf(location=None, *, data=None, format: str | None = None, base: str | None = None) -> Graph:
    """Parse RDF into an rdflib :class:`~rdflib.Graph`, keeping every literal's text exactly.

    - ``location``: a path, a URL, or a file object; or ``data``: the RDF text itself.
    - ``format``: an rdflib format name (``"turtle"``, ``"nt"``, ``"xml"``, ``"trig"``,
      ``"json-ld"``, ...). By default rdflib finds it from the file name or media type.
    - ``base``: the IRI that relative IRIs are resolved against. By default, the document's
      own address.

    Documents with named graphs (TriG, N-Quads) are read as the union of their graphs. An RDF
    Message Log given as ``data`` is read as the merge of its messages (see :func:`read_rdf`).
    Raises :class:`~rdftss.errors.DocumentError` when the document can't be read or parsed.
    """
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    if text is not None and is_message_log(text):
        return read_rdf(data=text, format=format, base=base).graph
    dataset = Dataset(default_union=True)
    _parse_into(dataset, location=location, data=data, format=format, base=base)
    return _as_graph(dataset)


def _parse_into(dataset: Dataset, *, location=None, data=None, format=None, base=None) -> None:
    with NORMALIZE_LOCK:
        normalize = rdflib.NORMALIZE_LITERALS
        rdflib.NORMALIZE_LITERALS = False
        # rdflib logs a traceback for every literal that misses its datatype's lexical form;
        # rdftss reports such values itself, where they matter.
        term_logger = logging.getLogger("rdflib.term")
        level = term_logger.level
        term_logger.setLevel(logging.CRITICAL)
        try:
            with warnings.catch_warnings():  # rdflib 7.6 uses its own deprecated Dataset.default_context
                warnings.filterwarnings("ignore", message=".*default_context is deprecated", category=DeprecationWarning)
                warnings.filterwarnings("ignore", message="ConjunctiveGraph is deprecated", category=DeprecationWarning)
                dataset.parse(source=location, data=data, format=format, publicID=base)
        except FileNotFoundError:
            hint = ""
            if isinstance(location, str) and ("\n" in location or "@prefix" in location):
                hint = " To read RDF text, use rdftss.loads(text)."
            raise DocumentError(f"There is no file {str(location)[:120]!r}.{hint}") from None
        except Exception as error:  # rdflib and urllib raise many kinds of errors
            what = "the text" if data is not None else repr(str(location))
            raise DocumentError(f"Can't read {what} as RDF: {error}") from None
        finally:
            rdflib.NORMALIZE_LITERALS = normalize
            term_logger.setLevel(level)


def _as_graph(dataset: Dataset) -> Graph:
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
