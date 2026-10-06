"""The TSS2 inheritance rule, in one place.

A snippet (one time series) belongs to a group (TSS2:SnippetGroup). The group
has a snippet template (TSS2:snippetTemplate) that holds default values.
The rule:

    a snippet uses the template's value for a property,
    unless the snippet states its own value for that property.

rdf:type is never inherited: the template is a TSS2:SnippetTemplate, the
snippet stays a TSS2:Snippet.
"""
from rdflib import Namespace

TSS = Namespace("https://w3id.org/tss#")
TSS2 = Namespace("https://example.org/tss2#")
SOSA = Namespace("http://www.w3.org/ns/sosa/")
QUDT = Namespace("http://qudt.org/schema/qudt/")
CSVW_TERMS = Namespace("http://www.w3.org/ns/csvw#")  # not called CSVW: the csvw library already uses that name
WDRS = Namespace("http://www.w3.org/2007/05/powder-s#")


def value_of(graph, node, prop):
    """graph.value(node, prop), but None when there is no node"""
    if node is None:
        return None
    return graph.value(node, prop)


def find_group(graph, snippet):
    """The group a snippet belongs to, and that group's template.
    (None, None) when the snippet is not in a group."""
    group = graph.value(predicate=TSS2.hasSnippet, object=snippet)
    template = value_of(graph, group, TSS2.snippetTemplate)
    return group, template


def resolve_with_source(graph, snippet, template, prop):
    """Apply the rule to one property, and remember where the value came from.

    Returns (value, source, group_value). source is one of:
      "own"      - the snippet states it (and the template doesn't, or says the same)
      "group"    - only the template states it, so the snippet inherits it
      "override" - both state it, with different values: the snippet's value wins
      "missing"  - nobody states it
    group_value is the template's value. It is only filled in for "override".
    """
    own = graph.value(snippet, prop)
    from_group = value_of(graph, template, prop)

    if own is not None and from_group is not None and own != from_group:
        return own, "override", from_group
    if own is not None:
        return own, "own", None
    if from_group is not None:
        return from_group, "group", None
    return None, "missing", None


def resolve(graph, snippet, template, prop):
    """Apply the rule to one property: only the value the snippet ends up with"""
    value, source, group_value = resolve_with_source(graph, snippet, template, prop)
    return value
