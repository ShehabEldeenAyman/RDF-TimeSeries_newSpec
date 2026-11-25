"""URI templates (RFC 6570), for ``tss:aboutUrl``: how the IRI of each point of a value field is made.

>>> from rdftss.templates import expand_template, template_variables
>>> template_variables("https://example.org/obs/value/{time}")
['time']
>>> expand_template("https://example.org/obs/value/{time}", {"time": "2024-05-01T00:00:00Z"})
'https://example.org/obs/value/2024-05-01T00%3A00%3A00Z'

Every expression of the RFC is supported for text values: ``{var}``, ``{+var}``, ``{#var}``,
``{.var}``, ``{/var}``, ``{;var}``, ``{?var}`` and ``{&var}``, several variables per expression,
and the prefix modifier ``{var:3}``. An explode modifier (``{var*}``) changes nothing for a text.
A variable without a value (``None``) is undefined: the expression leaves it out.
"""
from __future__ import annotations

import re

UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
RESERVED = ":/?#[]@!$&'()*+,;="

#: operator: (first, separator, named, if empty, allow reserved characters)
OPERATORS = {
    "": ("", ",", False, "", False),
    "+": ("", ",", False, "", True),
    "#": ("#", ",", False, "", True),
    ".": (".", ".", False, "", False),
    "/": ("/", "/", False, "", False),
    ";": (";", ";", True, "", False),
    "?": ("?", "&", True, "=", False),
    "&": ("&", "&", True, "=", False),
}
_EXPRESSION = re.compile(r"\{([^{}]*)\}")
_VARSPEC = re.compile(r"^([A-Za-z0-9_]|%[0-9A-Fa-f]{2})(\.?([A-Za-z0-9_]|%[0-9A-Fa-f]{2}))*(:[1-9][0-9]{0,3}|\*)?$")


class TemplateError(ValueError):
    """A URI template that does not follow RFC 6570."""


def _parse(template: str) -> list:
    """The parts of a template: texts, and (operator, [(name, prefix length or None)]) expressions."""
    parts, position = [], 0
    for match in _EXPRESSION.finditer(template):
        parts.append(template[position:match.start()])
        body = match.group(1)
        operator = body[:1] if body[:1] in OPERATORS and body[:1] != "" else ""
        specs = []
        for spec in body[len(operator):].split(","):
            if not _VARSPEC.match(spec):
                raise TemplateError(f"{{{body}}} is not a valid expression of a URI template")
            name, _, modifier = spec.partition(":")
            if name.endswith("*"):
                name = name[:-1]
            specs.append((name, int(modifier) if modifier and modifier != "*" else None))
        parts.append((operator, specs))
        position = match.end()
    rest = template[position:]
    if "{" in rest or "}" in rest or any("{" in part or "}" in part for part in parts if isinstance(part, str)):
        raise TemplateError("the braces of the URI template do not match")
    parts.append(rest)
    return parts


def template_variables(template: str) -> list[str]:
    """The names of the variables of a URI template, in order. Raises :class:`TemplateError`."""
    names = []
    for part in _parse(template):
        if isinstance(part, tuple):
            names.extend(name for name, _ in part[1] if name not in names)
    return names


def _encode(text: str, reserved: bool) -> str:
    allowed = UNRESERVED + (RESERVED if reserved else "")
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if reserved and char == "%" and re.match(r"%[0-9A-Fa-f]{2}", text[index:index + 3]):
            out.append(text[index:index + 3])
            index += 3
            continue
        if char in allowed:
            out.append(char)
        else:
            out.extend(f"%{byte:02X}" for byte in char.encode("utf-8"))
        index += 1
    return "".join(out)


def expand_template(template: str, values: dict) -> str:
    """The URI that a template gives for ``values`` (name -> text, or ``None`` when undefined)."""
    out = []
    for part in _parse(template):
        if isinstance(part, str):
            out.append(part)
            continue
        operator, specs = part
        first, separator, named, if_empty, reserved = OPERATORS[operator]
        defined = []
        for name, prefix in specs:
            value = values.get(name)
            if value is None:
                continue
            value = str(value)
            if prefix is not None:
                value = value[:prefix]
            encoded = _encode(value, reserved)
            if named:
                defined.append(name + (if_empty if value == "" else "=" + encoded))
            else:
                defined.append(encoded)
        if defined:
            out.append(first + separator.join(defined))
    return "".join(out)
