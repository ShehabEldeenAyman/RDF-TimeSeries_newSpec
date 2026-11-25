"""Value encodings: how a data file states values that are not the readings themselves.

A field of an external source may state ``tss:valueEncoding``. The specification defines one
encoding, ``tss:DeltaFromBaseline``: each value in the file is the difference from the field's
``tss:baseline``, and the reading (the *decoded value*) is the baseline + the value. The sum is
computed exactly on the decimal values of the two texts, then read as the field's datatype:
``4750 + -27.6 = 4722.4``, never ``4722.400000000001``.

To support another encoding, subclass :class:`ValueEncoding` and register it::

    from rdftss.encodings import ValueEncoding, register_encoding

    @register_encoding
    class TimesTen(ValueEncoding):
        iri = URIRef("https://example.org/TimesTen")

        def decode(self, text):
            return decimal_text(Decimal(text) * 10)

        def encode(self, text):
            return decimal_text(Decimal(text) / 10)
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext

from rdflib import Literal, URIRef

from .vocab import TSS
from .xsd import FLOATING_TYPES, INTEGER_RANGES, decimal_text, short_name

#: The registered encodings, by their IRI.
ENCODINGS: dict[URIRef, type["ValueEncoding"]] = {}


def register_encoding(cls: type["ValueEncoding"]) -> type["ValueEncoding"]:
    """Register a :class:`ValueEncoding` subclass by its ``iri`` (usable as a class decorator)."""
    ENCODINGS[URIRef(cls.iri)] = cls
    return cls


class ValueEncoding:
    """How the values of one field are stated in its data file.

    Subclasses set :attr:`iri` and implement :meth:`decode` and :meth:`encode`. Both work on
    texts: the text in the data file, and the lexical form of the reading.
    """

    #: The IRI of the encoding, such as ``tss:DeltaFromBaseline``.
    iri: URIRef = None

    def __init__(self, datatype=None, baseline: Literal | None = None):
        self.datatype = datatype
        self.baseline = baseline

    def decode(self, text: str) -> str:
        """The lexical form of the reading that ``text`` (a value in the data file) stands for."""
        raise NotImplementedError

    def encode(self, text: str) -> str:
        """The text to write in the data file for a reading with lexical form ``text``."""
        raise NotImplementedError

    def statements(self) -> list[tuple[URIRef, object]]:
        """The (property, value) statements that describe this encoding on its field."""
        return [(TSS.valueEncoding, self.iri)]

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


def _number(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{text!r} is not a number") from None


def number_text(value: Decimal, datatype) -> str:
    """The lexical form of an exact number for a datatype: plain digits for ``xsd:decimal`` and
    the integer types, and for ``xsd:double`` and ``xsd:float`` plain digits too, unless the
    number is very large or very small (then ``1.5E+300``)."""
    if value.is_nan():
        if datatype in FLOATING_TYPES:
            return "NaN"
        raise ValueError(f"NaN is not an {short_name(datatype)}")
    if value.is_infinite():
        if datatype in FLOATING_TYPES:
            return "INF" if value > 0 else "-INF"
        raise ValueError(f"infinity is not an {short_name(datatype)}")
    if datatype in INTEGER_RANGES:
        if value != value.to_integral_value():
            raise ValueError(f"{decimal_text(value)} is not a whole number, as {short_name(datatype)} needs")
        return str(int(value))
    if datatype in FLOATING_TYPES and value != 0 and not -20 < value.adjusted() < 20:
        return format(value, "E")
    return decimal_text(value)


@register_encoding
class DeltaFromBaseline(ValueEncoding):
    """``tss:DeltaFromBaseline``: each value is the difference from ``tss:baseline``.

    >>> from rdflib import Literal
    >>> from rdftss.vocab import XSD
    >>> DeltaFromBaseline(XSD.double, Literal("4750", datatype=XSD.double)).decode("-27.6")
    '4722.4'
    """

    iri = TSS.DeltaFromBaseline

    def __init__(self, datatype=None, baseline: Literal | None = None):
        super().__init__(datatype, baseline)
        if baseline is None:
            raise ValueError("tss:DeltaFromBaseline needs a tss:baseline")
        self.baseline_value = _number(str(baseline))

    def decode(self, text: str) -> str:
        with localcontext() as context:
            context.prec = 100
            return number_text(self.baseline_value + _number(text), self.datatype)

    def encode(self, text: str) -> str:
        with localcontext() as context:
            context.prec = 100
            return number_text(_number(text) - self.baseline_value, self.datatype)

    def statements(self) -> list[tuple[URIRef, object]]:
        return [(TSS.valueEncoding, self.iri), (TSS.baseline, self.baseline)]

    def __repr__(self) -> str:
        return f"DeltaFromBaseline(baseline={str(self.baseline)!r})"
