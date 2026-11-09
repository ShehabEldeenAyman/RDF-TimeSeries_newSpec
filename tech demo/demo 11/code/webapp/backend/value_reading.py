"""Reading one value: the text of a data cell, or the literal of an embedded point (or of tss:from /
tss:until), after the check that it has the XSD form of its datatype. Gives datetimes and numbers.
Used by data_reading.py and by the data and SQL routes."""
from fastapi import HTTPException
from rdflib import Literal
from rdflib.namespace import XSD
from datetime import datetime, timezone
from decimal import Decimal
import math
import re

import source_reading
import sql_box
from rdf_generation import TSS, LEXICAL_FORMS, TIME_DATATYPES


# The cells of a data file (the text of a CSV cell, or a Parquet value written as text) are
# read by Python itself, after a check that the text has the XSD form of the field's datatype
# (LEXICAL_FORMS): see data_time and read_number. A data file can have hundreds of thousands
# of cells, and an rdflib Literal for each of them would make the query several times slower.
# The embedded points are RDF already: their values are rdflib Literals (see data_reading.point_rows), so
# rdflib reads them (Literal.toPython()), after the same check of their text: see
# literal_time and literal_number. So does the metadata.
# The XSD forms, each made into a pattern once (re.compile), to check many cells quickly
DATA_FORMS = {}
for datatype_iri, form_text in LEXICAL_FORMS.items():
    DATA_FORMS[datatype_iri] = re.compile(form_text)


def time_form(datatype):
    """The XSD form a time of this datatype must have: an xsd:dateTimeStamp must have a timezone,
    an xsd:dateTime may leave it out. Any other datatype is read as xsd:dateTime (see TIME_DATATYPES)."""
    if datatype in TIME_DATATYPES:
        return DATA_FORMS[datatype]
    return DATA_FORMS[XSD.dateTime]


def data_time(text, datatype=XSD.dateTime):
    """The text of a time cell as a datetime, or None when it is not an xsd:dateTime like
    2021-03-03T23:45:00Z. A time without a timezone is taken as UTC.
    datatype: the time field's tss:datatype (an xsd:dateTimeStamp time needs a timezone, see time_form)"""
    if time_form(datatype).fullmatch(text) is None:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:  # e.g. 30 February, or a year Python can't hold
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def literal_time(literal):
    """A time literal (an rdflib Literal: an embedded point's time, or tss:from/tss:until) as a
    datetime in UTC, read by rdflib, or None when it is not an xsd:dateTime (or xsd:dateTimeStamp)
    literal with the XSD form, like "2021-03-03T23:45:00Z". A time without a timezone is taken
    as UTC; an xsd:dateTimeStamp must have one."""
    if literal.datatype not in TIME_DATATYPES or time_form(literal.datatype).fullmatch(str(literal)) is None:
        return None
    # rdflib reads an xsd:dateTime literal (Literal.toPython()), but gives an xsd:dateTimeStamp
    # literal back unread. It is an xsd:dateTime with a timezone, so its text is read as one.
    moment = Literal(str(literal), datatype=XSD.dateTime).toPython()
    if not isinstance(moment, datetime):
        return None  # e.g. 30 February: rdflib gives the literal back
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)  # a time without a timezone is UTC
    return moment.astimezone(timezone.utc)


def window_moment(graph, source, name):
    """tss:from or tss:until of the source as a datetime in UTC, or None when it is not stated.
    It must be an xsd:dateTime literal, read by literal_time like every other time here.
    (Only xsd:dateTime: the converter, tss2rdf.window_moment, refuses an xsd:dateTimeStamp too.
    That is the converter's own copy: it raises ValueError instead of an HTTP error.)"""
    value = graph.value(source, TSS[name])
    if value is None:
        return None
    moment = None
    if isinstance(value, Literal) and value.datatype == XSD.dateTime:
        moment = literal_time(value)
    if moment is None:
        raise HTTPException(status_code=400, detail=f'tss:{name} must be an xsd:dateTime like '
                                                    f'"2021-03-03T23:45:00Z"^^xsd:dateTime, not {value.n3(graph.namespace_manager)}.')
    return moment


def read_number(text, datatype, graph):
    """The text of a numeric field's value as a number (None when it is empty):
    "4797.72" of an xsd:double field -> 4797.72. The text must have the XSD form of the
    datatype, so " 12 ", "1_000" or "4,5" are refused, as SHACL refuses them."""
    if text == "":
        return None
    form = DATA_FORMS.get(datatype)  # None: a datatype without a known form
    if form is not None and form.fullmatch(text) is None:
        raise HTTPException(status_code=400, detail=f"The field declares numeric values "
                                                    f"({source_reading.message_text(graph, datatype)}), but contains {text!r}.")
    try:
        if sql_box.duckdb_type(datatype) == "BIGINT":
            value = int(text)
        else:
            value = float(text)  # also for xsd:decimal ("1.50" -> 1.5)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"The field declares numeric values "
                                                    f"({source_reading.message_text(graph, datatype)}), but contains {text!r}.")
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail="The field contains a non-finite numeric value.")
    return value


def literal_number(literal, datatype, graph):
    """The value of an embedded point (an rdflib Literal) for a numeric field, read by rdflib:
    "4797.72"^^xsd:double -> 4797.72. As with read_number, the literal must have the field's
    datatype, and its text the XSD form of that datatype: " 12 " or "1_000" are refused, as SHACL
    refuses them (rdflib alone would read them as 12.0 and 1000.0)."""
    value = literal.toPython()  # an int, a float or a Decimal; the literal itself when rdflib can't read it
    form = DATA_FORMS.get(datatype)  # None: a datatype without a known form
    wrong_form = form is not None and form.fullmatch(str(literal)) is None
    if (literal.datatype != datatype or wrong_form or isinstance(value, bool)
            or not isinstance(value, (int, float, Decimal))):
        raise HTTPException(status_code=400, detail=f"The field declares numeric values "
                                                    f"({source_reading.message_text(graph, datatype)}), but contains {str(literal)!r}.")
    if isinstance(value, Decimal):
        value = float(value)  # xsd:decimal, like read_number: "1.50" -> 1.5
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail="The field contains a non-finite numeric value.")
    return value


def value_text(row, field):
    """The text of a field's value in a row of data_reading.data_rows: a cell's text as it is, or the text
    (lexical form) of an embedded point's literal. "" when the row has no value for the field."""
    return str(row.get(field, ""))


def is_numeric(datatype):
    """Whether a field's tss:datatype is a number type: only numeric fields have value
    ranges and mean/min/max. (The same map of XSD types as the SQL box.)"""
    return sql_box.duckdb_type(datatype) in sql_box.NUMERIC_TYPES
