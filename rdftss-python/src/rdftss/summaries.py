"""Summary verification: recompute every stored ``tss:summary`` from the data, and compare.

>>> report = rdftss.verify_summaries(document)      # or document.verify_summaries()  # doctest: +SKIP
>>> report.up_to_date                                                                # doctest: +SKIP
>>> print(report)                                                                    # doctest: +SKIP

A stored summary is not refreshed by itself: its ``tss:generatedAtTime`` says when it was
computed, and a consumer can compute the same numbers from the data to check it. That is what
this module does. For every field with a ``tss:summary``, it reads the points in the snippet's
time window, computes the numbers from the field's decoded values, and names each number the
summary states that the data does not give:

- ``tss:count`` and ``tss:missing`` must be equal;
- ``tss:firstTime`` and ``tss:lastTime`` must be the same moment (``…T10:00:00+02:00`` is
  ``…T08:00:00Z``);
- ``tss:observedMinimum``, ``tss:observedMaximum``, ``tss:mean`` and ``tss:median`` must be
  equal for decimal and integer fields, and equal within a relative ``tolerance`` (``1e-12``)
  for ``xsd:double`` and ``xsd:float`` fields, since a sum of floating-point numbers depends on
  the order of the additions. A numeric attribute on a field that is not numeric is reported:
  the data gives no such number.

A number the summary leaves out is not compared, and ``tss:generatedAtTime`` is not compared.
The numbers follow ``tss:ExcludeEmptyValues``, the one missing-value policy the specification
defines: an empty value is not a value. A summary that states no policy is checked with it; a
summary that states another policy can't be checked, and is reported as not checked.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from .errors import RDFTSSError
from .vocab import TSS

#: The numbers of a summary that are compared, with their properties.
PROPERTY_NAMES = {
    "count": "tss:count", "missing": "tss:missing", "first_time": "tss:firstTime", "last_time": "tss:lastTime",
    "observed_minimum": "tss:observedMinimum", "observed_maximum": "tss:observedMaximum",
    "mean": "tss:mean", "median": "tss:median",
}


def _shown(value) -> str:
    if value is None:
        return "no value"
    return getattr(value, "text", None) or str(value)


@dataclass
class SummaryCheck:
    """The check of one stored summary.

    - ``field``: the field; ``stored``: its :class:`~rdftss.model.Summary`; ``computed``: the
      :class:`~rdftss.analysis.Statistics` of its values in the window (``None`` when not checked).
    - ``compared``: the numbers the summary states, which were compared.
    - ``differences``: ``{number: (stored, computed)}`` for every number that is out of date.
    - ``problem``: why the summary could not be checked, or ``None``.
    """

    field: object
    stored: object
    computed: object
    compared: list
    differences: dict = dataclass_field(default_factory=dict)
    problem: str | None = None

    @property
    def up_to_date(self) -> bool:
        return self.problem is None and not self.differences

    def __str__(self) -> str:
        name = self.field.snippet.document.short(self.field.node)
        if self.problem:
            return f"{name}: not checked: {self.problem}"
        if not self.differences:
            stated = ", ".join(PROPERTY_NAMES[number] for number in self.compared) or "no numbers"
            return f"{name}: up to date ({stated})"
        parts = [f"{PROPERTY_NAMES[number]} is {_shown(stored)}, the data gives {_shown(computed)}"
                 for number, (stored, computed) in self.differences.items()]
        return f"{name}: OUT OF DATE: " + "; ".join(parts)


@dataclass
class SummaryReport:
    """The outcome of :func:`verify_summaries`: one :class:`SummaryCheck` per stored summary.

    ``up_to_date`` is true when every summary was checked and none is out of date;
    ``out_of_date`` and ``not_checked`` list the others."""

    checks: list

    @property
    def up_to_date(self) -> bool:
        return all(check.up_to_date for check in self.checks)

    @property
    def out_of_date(self) -> list:
        return [check for check in self.checks if check.differences]

    @property
    def not_checked(self) -> list:
        return [check for check in self.checks if check.problem]

    def __bool__(self) -> bool:
        return self.up_to_date

    def __str__(self) -> str:
        if not self.checks:
            return "No stored summaries."
        good = len(self.checks) - len(self.out_of_date) - len(self.not_checked)
        count = len(self.checks)
        head = f"{count} stored summar{'y' if count == 1 else 'ies'}: {good} up to date, {len(self.out_of_date)} out of date"
        if self.not_checked:
            head += f", {len(self.not_checked)} not checked"
        return "\n".join([head] + [f"  {check}" for check in self.checks])


def verify_summaries(target, *, strict: bool = True, tolerance: float = 1e-12) -> SummaryReport:
    """Recompute the stored summaries of a document's snippets, of one snippet, or of a list of
    snippets, from their data, and compare them with what the metadata states.

    - ``strict``: read the data strictly; a snippet whose data has an error is reported as not
      checked. With ``strict=False`` the bad values are left out of the numbers.
    - ``tolerance``: the relative difference allowed between floating-point numbers.
    """
    from .document import Document
    from .model import Snippet

    if isinstance(target, Document):
        snippets = target.snippets
    elif isinstance(target, Snippet):
        snippets = [target]
    else:
        snippets = list(target)
    checks = []
    for snippet in snippets:
        fields = [field for field in snippet.fields if field.summary is not None]
        if not fields:
            continue
        try:
            points = snippet.read(strict=strict, fields=fields)
        except RDFTSSError as error:
            checks += [SummaryCheck(field, field.summary, None, [], problem=f"the data can't be read: {error}")
                       for field in fields]
            continue
        for field in fields:
            summary = field.summary
            compared = [number for number in PROPERTY_NAMES if getattr(summary, number) is not None]
            policy = summary.missing_value_policy
            if policy is not None and policy != TSS.ExcludeEmptyValues:
                checks.append(SummaryCheck(field, summary, None, compared, problem=(
                    f"its missing-value policy is {snippet.document.short(policy)}; the specification "
                    "defines only tss:ExcludeEmptyValues")))
                continue
            statistics = points.statistics(field)
            checks.append(SummaryCheck(field, summary, statistics, compared,
                                       statistics.differences(summary, tolerance=tolerance)))
    return SummaryReport(checks)
