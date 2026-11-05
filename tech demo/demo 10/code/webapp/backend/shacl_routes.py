"""Routes for the SHACL tab: validate the metadata (and its embedded points) with pyshacl, with the
bundled shapes or with uploaded ones. The validation itself is the SHACL command line's own code
(code/shacl_report/shacl_report.py, through rdf_generation.py). main.py adds this router to the app."""
from fastapi import APIRouter, Depends, HTTPException, Request
from rdflib import Graph, URIRef, RDF
from rdflib.namespace import DCTERMS, SH
from typing import Annotated
from urllib.parse import quote, urljoin

import paths
import documents
from rdf_generation import shacl_report

# The routes of this file, all under /api (main.py adds them to the app with include_router)
router = APIRouter(prefix="/api")


# An uploaded shapes file may be at most this big (5 MB)
MAX_SHAPES_BYTES = 5_000_000


def run_validation(graph, shapes, name):
    """Validate an endpoint's metadata (and its embedded points) with these shapes: the SHACL
    command line's own code (shacl_report.validation_report: the metadata, with its embedded
    points in it, and pyshacl while holding NORMALIZE_LOCK).
    Gives the report graph and its sh:ValidationReport node.
    name: how the error message calls the shapes"""
    try:
        report, report_node, point_triples = shacl_report.validation_report(graph, shapes)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"Could not validate with {name}: {error}")
    return report, report_node


async def request_body(request: Request):
    """The body of the request, as bytes. FastAPI runs this before the route that asks for it
    (Depends), because reading the body has to wait for it to arrive (await)."""
    return await request.body()


def has_shapes(shapes):
    """Whether a graph holds SHACL shapes: a shape (sh:NodeShape, sh:PropertyShape),
    or a SHACL target or property"""
    for kind in (SH.NodeShape, SH.PropertyShape):
        if (None, RDF.type, kind) in shapes:
            return True
    for prop in (SH.targetClass, SH.targetNode, SH.targetSubjectsOf, SH.targetObjectsOf, SH.property, SH.path):
        if (None, prop, None) in shapes:
            return True
    return False


@router.get("/endpoints/{endpoint_id}/shacl")
def check_shacl(endpoint_id, request: Request):
    """Validate with the bundled shapes for the source's storage profile (tss:embedded).
    Answers with the SHACL validation report (an sh:ValidationReport, as Turtle)."""
    graph = documents.load_graph(endpoint_id, documents.browser_base(request))  # the report names the IRIs the browser knows
    # The bundled shapes for tss:embedded true or false (SHACL/embedded_shapes.ttl or
    # SHACL/external_shapes.ttl), picked like the SHACL command line picks them
    shapes_path = shacl_report.default_shapes(graph)
    if shapes_path is None:
        raise HTTPException(status_code=400, detail="Cannot pick the default shapes: the metadata needs a tss:Source "
                                                    "with tss:embedded true or false. You can upload your own shapes instead.")
    shapes = Graph().parse(shapes_path, format="turtle")
    report, report_node = run_validation(graph, shapes, shapes_path.name)
    # Which shapes were used: their address on this backend (/files/SHACL/<name>)
    address = urljoin(str(request.base_url), quote(f"{paths.SHACL_FILES_PATH.lstrip('/')}/{shapes_path.name}"))
    report.add((report_node, DCTERMS.source, URIRef(address)))
    return documents.turtle_response(report)


@router.post("/endpoints/{endpoint_id}/shacl")
def check_custom_shacl(endpoint_id, request: Request, body: Annotated[bytes, Depends(request_body)]):
    """Validate with your own shapes instead of the bundled ones. The request body is the
    shapes file itself, in the RDF syntax its Content-Type says (text/turtle, application/rdf+xml, ...;
    not JSON-LD, see RDF_FORMATS). Answers with the SHACL validation report (Turtle), like the GET above."""
    graph = documents.load_graph(endpoint_id, documents.browser_base(request))
    if len(body) > MAX_SHAPES_BYTES:
        raise HTTPException(status_code=413, detail="The shapes file must be at most 5 MB.")
    content_type = request.headers.get("content-type")
    if documents.bare_media_type(content_type) == documents.JSON_LD:
        raise HTTPException(status_code=400, detail=f"The shapes were sent as JSON-LD. {documents.JSON_LD_REFUSED}")
    media_type = documents.rdf_media_type(content_type)
    if media_type is None:
        raise HTTPException(status_code=415, detail=f"The shapes were sent as {content_type}, which is not an RDF syntax "
                                                    "this dashboard reads (Turtle, RDF/XML, N-Triples or N3)")

    shapes = Graph()
    try:
        # utf-8-sig: a byte order mark at the start of the file (some editors write one) is skipped.
        # Relative IRIs in the shapes are read against the address they were sent to.
        text = body.decode("utf-8-sig")
        shapes.parse(data=text, format=documents.RDF_FORMATS[media_type], publicID=str(request.url))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not read the shapes as {media_type}: {error}")
    if not has_shapes(shapes):
        raise HTTPException(status_code=400, detail="The uploaded file contains no SHACL shapes or targets.")

    report, report_node = run_validation(graph, shapes, "the uploaded shapes")
    return documents.turtle_response(report)
