"""Materialize embedded RDFTSS points with JSON-LD and their effective template.

TSS is an input encoding only. The export contains domain observations and
their referenced resources, with no TSS vocabulary or source container nodes.
"""
import json
from uuid import uuid4

from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef
from rdflib.plugins.shared.jsonld.context import Context

from tss2 import TSS


def single(graph, node, prop, required=False):
    values = list(graph.objects(node, prop))
    if len(values) > 1 or required and not values:
        raise ValueError(f"{node} must state {'exactly' if required else 'at most'} one {prop}.")
    return values[0] if values else None


def effective(graph, snippet, template, prop):
    own = single(graph, snippet, prop)
    return own if own is not None else single(graph, template, prop)


def read_json(literal, name):
    if not isinstance(literal, Literal):
        raise ValueError(f"{name} must be a JSON literal.")
    try:
        return json.loads(str(literal))
    except (ValueError, TypeError) as error:
        raise ValueError(f"Invalid JSON in {name}: {error}") from error


def generate(graph, base):
    groups = sorted(set(graph.subjects(RDF.type, TSS.SnippetGroup)), key=str)
    if not groups:
        raise ValueError("No tss:SnippetGroup found.")
    members = []
    for group in groups:
        flag = single(graph, group, TSS.embedded, required=True)
        if not isinstance(flag, Literal) or flag.toPython() is not True:
            raise ValueError("RDF generation currently supports only groups with tss:embedded true.")
        template = single(graph, group, TSS.snippetTemplate, required=True)
        snippets = sorted(set(graph.objects(group, TSS.hasSnippet)), key=str)
        if not snippets:
            raise ValueError(f"{group} has no member snippets.")
        for snippet in snippets:
            if (snippet, RDF.type, TSS.Snippet) not in graph:
                raise ValueError(f"{snippet} is not described as a tss:Snippet.")
            if len(set(graph.subjects(TSS.hasSnippet, snippet))) != 1:
                raise ValueError(f"{snippet} must belong to exactly one group.")
            members.append((snippet, template))

    output = Graph(bind_namespaces="none")
    for prefix, namespace in graph.namespaces():
        if not str(namespace).startswith(str(TSS)) and prefix != "tss":
            output.bind(prefix, namespace)
    controls = (set(groups) | {template for _, template in members}
                | set(graph.subjects(RDF.type, TSS.Snippet)) | set(graph.objects(None, TSS.about)))
    referenced_resources = set()

    def is_tss(term):
        return (isinstance(term, URIRef) and str(term).startswith(str(TSS))
                or isinstance(term, Literal) and term.datatype is not None
                and str(term.datatype).startswith(str(TSS)))

    def add_domain_triple(subject, predicate, value):
        if any(is_tss(term) for term in (subject, predicate, value)):
            return
        if any(term in controls for term in (subject, predicate, value)):
            return
        output.add((subject, predicate, value))
        if isinstance(value, (BNode, URIRef)):
            referenced_resources.add(value)

    series = []
    for snippet, template in members:
        points = read_json(single(graph, snippet, TSS.points, required=True), f"tss:points on {snippet}")
        if not isinstance(points, list) or any(
            not isinstance(point, dict) or "time" not in point or "value" not in point
            for point in points
        ):
            raise ValueError(f"tss:points on {snippet} must be an array of objects with time and value keys.")
        context = read_json(effective(graph, snippet, template, TSS.context), f"tss:context on {snippet}")
        if isinstance(context, dict) and "@context" in context:
            context = context["@context"]
        try:
            active = Context(context, base=base)
            for key in ("time", "value"):
                predicate = active.expand(key)
                if not predicate or predicate.startswith("@") or ":" not in predicate:
                    raise ValueError(f"tss:context on {snippet} does not map the {key!r} key to an RDF property.")
                if predicate.startswith(str(TSS)):
                    raise ValueError(f"The {key!r} mapping on {snippet} uses the TSS vocabulary. Map it to a domain RDF property to generate a TSS-free graph.")
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(f"Could not read the JSON-LD context on {snippet}: {error}") from error

        point_type = effective(graph, snippet, template, TSS.pointType)
        if not isinstance(point_type, URIRef):
            raise ValueError(f"{snippet} needs a tss:pointType class IRI, own or inherited.")
        if is_tss(point_type):
            raise ValueError(f"The point type on {snippet} must be a domain class, not a TSS class.")
        about = effective(graph, snippet, template, TSS.about)
        if not isinstance(about, (URIRef, BNode)):
            raise ValueError(f"{snippet} needs a tss:about node, own or inherited.")

        # A temporary wrapper identifies only the top-level point. Decorating all
        # JSON-LD subjects would also decorate nested results and other resources.
        root_property = URIRef(f"urn:rdftss-generation:{uuid4()}:root")
        for index, point in enumerate(points, 1):
            wrapper = URIRef(f"urn:rdftss-generation:{uuid4()}:wrapper")
            document = {"@context": context, "@id": str(wrapper), str(root_property): point}
            expanded = Graph()
            try:
                expanded.parse(data=json.dumps(document), format="json-ld", publicID=base)
            except Exception as error:
                raise ValueError(f"Could not expand point {index} of {snippet}: {error}") from error
            roots = list(expanded.objects(wrapper, root_property))
            if len(roots) != 1 or not isinstance(roots[0], (URIRef, BNode)):
                raise ValueError(f"Point {index} of {snippet} did not expand into one RDF node.")
            root = roots[0]
            if is_tss(root) or root in controls:
                raise ValueError(f"Point {index} of {snippet} uses a TSS container or vocabulary IRI as its identifier.")
            expanded.remove((wrapper, root_property, root))
            for subject, predicate, value in expanded:
                if isinstance(value, Literal) and value.ill_typed:
                    raise ValueError(f"Point {index} of {snippet} has an invalid {value.datatype} literal: {value}.")
                add_domain_triple(subject, predicate, value)
            add_domain_triple(root, RDF.type, point_type)
            for predicate, value in graph.predicate_objects(about):
                if predicate != RDF.type:
                    add_domain_triple(root, predicate, value)
        series.append({"label": str(graph.value(snippet, RDFS.label) or "Series"),
                       "points": len(points)})

    # Keep descriptions of referenced sensors/features/nested metadata, rather
    # than copying the complete source graph and its TSS scaffolding.
    visited = set()
    while referenced_resources - visited:
        resource = next(iter(referenced_resources - visited))
        visited.add(resource)
        for predicate, value in graph.predicate_objects(resource):
            add_domain_triple(resource, predicate, value)

    return {"turtle": output.serialize(format="turtle"), "triples": len(output),
            "points": sum(entry["points"] for entry in series), "series": series}
