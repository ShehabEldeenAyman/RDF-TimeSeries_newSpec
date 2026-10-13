"""Expand the embedded snippets of a TSS2 metadata endpoint into full RDF.

For every snippet with tss:points, as in TSS v1 ("Expanding data points"):

  1. the points (plain JSON) + the snippet's tss:context (a JSON-LD context)
     -> RDF, with a standard JSON-LD parser: "time" and "value" become the
     properties (and datatypes) the context gives them
  2. every point gets rdf:type <tss:pointType>
  3. every point gets the triples of tss:about (except its rdf:type)

A property the snippet doesn't state itself comes from its group's template (the TSS2 rule).
A snippet without tss:context uses the TSS v1 default context (tss:time and tss:value).

Examples:
    python expand_snippets.py "../../metadata endpoint/waterinfo_289441042_tss2_points_embedded.ttl"
    python expand_snippets.py endpoint.ttl --out observations.ttl
    python expand_snippets.py endpoint.ttl --snippet https://example.org/ex#snippet-zevenaarshaven-ec20
"""
import argparse
import json
import sys
from rdflib import Graph, Namespace, RDF, URIRef

TSS = Namespace("https://w3id.org/tss#")

# The default context of TSS v1, for a snippet without tss:context
DEFAULT_CONTEXT = {
    "id": "@id",
    "time": {"@id": "https://w3id.org/tss#time", "@type": "http://www.w3.org/2001/XMLSchema#dateTime"},
    "value": "https://w3id.org/tss#value",
}


def resolve(graph, snippet, prop):
    """The TSS2 rule: the snippet's own value, otherwise the one in its group's template"""
    own = graph.value(snippet, prop)
    if own is not None:
        return own
    group = graph.value(predicate=TSS.hasSnippet, object=snippet)
    template = graph.value(group, TSS.snippetTemplate) if group is not None else None
    if template is None:
        return None
    return graph.value(template, prop)


def context_of(graph, snippet):
    """The JSON-LD context for the snippet's points: its tss:context, or the TSS v1 default"""
    literal = resolve(graph, snippet, TSS.context)
    if literal is None:
        return DEFAULT_CONTEXT
    context = json.loads(str(literal))
    if "@context" in context:
        return context["@context"]  # written as {"@context": {...}}
    return context                  # written as the context object itself


def expand_snippet(graph, snippet):
    """The RDF of all points of one snippet"""
    points = json.loads(str(graph.value(snippet, TSS.points)))

    # 1. JSON + JSON-LD context -> RDF, with rdflib's JSON-LD parser (no TSS code involved)
    document = {"@context": context_of(graph, snippet), "@graph": points}
    result = Graph()
    result.parse(data=json.dumps(document), format="json-ld")
    entities = set(result.subjects())  # one entity per point (a blank node, or its "id")

    # 2. tss:pointType on every point
    point_type = resolve(graph, snippet, TSS.pointType)
    if point_type is not None:
        for entity in entities:
            result.add((entity, RDF.type, point_type))

    # 3. the tss:about template on every point, except its rdf:type
    about = resolve(graph, snippet, TSS.about)
    if about is not None:
        for prop, value in graph.predicate_objects(about):
            if prop == RDF.type:
                continue
            for entity in entities:
                result.add((entity, prop, value))

    return result, len(points)


def main():
    parser = argparse.ArgumentParser(description="Expand the tss:points of a TSS2 metadata endpoint into RDF observations.")
    parser.add_argument("endpoint", help="the TSS2 Turtle file")
    parser.add_argument("--snippet", help="only this snippet (its full IRI)")
    parser.add_argument("--out", help="write the Turtle to this file (default: print it)")
    args = parser.parse_args()

    graph = Graph()
    graph.parse(args.endpoint, format="turtle")

    if args.snippet:
        snippets = [URIRef(args.snippet)]
    else:
        snippets = sorted(graph.subjects(TSS.points, None))
    if len(snippets) == 0:
        parser.error(f"No snippet with tss:points in {args.endpoint}")

    output = Graph()
    for prefix, namespace in graph.namespaces():  # reuse the endpoint's prefixes, for readable Turtle
        output.bind(prefix, namespace)
    for snippet in snippets:
        if graph.value(snippet, TSS.points) is None:
            parser.error(f"{snippet} has no tss:points")
        triples, count = expand_snippet(graph, snippet)
        output += triples
        print(f"{graph.namespace_manager.normalizeUri(snippet)}: {count} points -> {len(triples)} triples", file=sys.stderr)

    turtle = output.serialize(format="turtle")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(turtle)
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(turtle)


if __name__ == "__main__":
    main()
