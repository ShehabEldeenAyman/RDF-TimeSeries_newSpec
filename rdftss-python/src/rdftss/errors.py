"""Errors and issues.

rdftss raises an error when it cannot do what you asked, and reports an :class:`Issue` when it
can, but found something that breaks a rule of the specification.

Every error is an :class:`RDFTSSError`, so ``except rdftss.RDFTSSError`` catches them all:

- :class:`DocumentError`: the metadata document cannot be parsed, or lacks what reading a
  source needs (no source class, no time field, ...).
- :class:`DataError`: the data breaks a rule that the specification calls an error, such as a
  row without a time, two rows with the same time, or a value that is not in the lexical
  form of its datatype. It says where: ``error.where`` is, for example, ``"CSV line 12"``.
- :class:`DataFileError`: the data file of an external source cannot be found, opened or
  downloaded.
- :class:`UnsupportedError`: something this library or the specification does not support,
  such as a Parquet column type that is not in the specification's table.
- :class:`MissingDependencyError`: a feature needs an optional package, such as ``pyarrow``.
"""
from __future__ import annotations

from dataclasses import dataclass


class RDFTSSError(Exception):
    """The base class of every error that rdftss raises."""


class DocumentError(RDFTSSError):
    """The metadata document cannot be read, or does not say what is needed to read a source."""


class DataError(RDFTSSError):
    """The data of a source breaks a rule of the specification.

    ``where`` says where the problem is, such as ``"CSV line 12"``, ``"Parquet row 7"`` or
    ``"the row at 2024-05-12T08:00:00Z"``.
    """

    def __init__(self, message: str, where: str | None = None):
        self.message = message
        self.where = where
        super().__init__(f"{where}: {message}" if where else message)


class DataFileError(RDFTSSError):
    """The data file of an external source cannot be found, opened or downloaded."""


class UnsupportedError(RDFTSSError):
    """Something that this library, or the specification, does not support."""


class MissingDependencyError(RDFTSSError, ImportError):
    """A feature needs an optional package that is not installed."""


def require(module: str, feature: str, extra: str):
    """Import an optional package, or raise a :class:`MissingDependencyError` that says how to
    install it."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as error:
        package = module.split(".")[0]
        raise MissingDependencyError(
            f"{feature} needs the package {package!r}. Install it with: "
            f"pip install {package}  (or: pip install 'rdftss[{extra}]')"
        ) from error


@dataclass(frozen=True)
class Issue:
    """Something that breaks a rule of the specification, found while reading.

    - ``severity`` is ``"error"`` for a rule the specification states with MUST, and
      ``"warning"`` for a SHOULD, or for something that is allowed but probably a mistake.
    - ``message`` says what is wrong, in words.
    - ``where`` says where: a source, a field, ``"CSV line 12"``, ...
    """

    severity: str
    message: str
    where: str | None = None

    def __str__(self) -> str:
        where = f"{self.where}: " if self.where else ""
        return f"{self.severity}: {where}{self.message}"


class Issues(list):
    """A list of :class:`Issue` objects, with a few helpers.

    To keep memory bounded on large files, it keeps at most ``limit`` issues and counts the
    rest in :attr:`not_listed`.
    """

    def __init__(self, issues=(), limit: int = 1000):
        super().__init__(issues)
        self.limit = limit
        self.not_listed = 0

    def add(self, severity: str, message: str, where: str | None = None) -> Issue:
        """Add an issue (or count it, when the list is full) and return it."""
        issue = Issue(severity, message, where)
        if len(self) < self.limit:
            self.append(issue)
        else:
            self.not_listed += 1
        return issue

    def error(self, message: str, where: str | None = None) -> Issue:
        """Add an issue with severity ``"error"``."""
        return self.add("error", message, where)

    def warning(self, message: str, where: str | None = None) -> Issue:
        """Add an issue with severity ``"warning"``."""
        return self.add("warning", message, where)

    @property
    def errors(self) -> list[Issue]:
        """The issues with severity ``"error"``."""
        return [issue for issue in self if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        """The issues with severity ``"warning"``."""
        return [issue for issue in self if issue.severity == "warning"]

    def __str__(self) -> str:
        if not self:
            return "No issues."
        lines = [str(issue) for issue in self]
        if self.not_listed:
            lines.append(f"... and {self.not_listed} more issues, not listed.")
        return "\n".join(lines)
