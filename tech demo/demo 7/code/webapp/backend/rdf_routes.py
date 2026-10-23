"""Routes for the RDF generation tab: the data as SOSA observations (code/TSS2RDF/tss2rdf.py),
written to temporary files to preview and download. main.py adds this router to the app."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
import tempfile

import files
import documents
from rdf_generation import generate

# The routes of this file, all under /api (main.py adds them to the app with include_router)
router = APIRouter(prefix="/api")


# The generated RDF (the RDF generation tab) is written to temporary files: the whole
# graph to download (N-Triples), and a preview with its first observations (Turtle).
# token -> those files.
RDF_EXPORT_DIRECTORY = tempfile.TemporaryDirectory(prefix="rdf-observations-")
RDF_EXPORTS = {}
# The name the downloaded file gets (N-Triples: one triple per line, see tss2rdf.write_batch)
GENERATED_RDF_FILENAME = "observations.nt"


@router.post("/endpoints/{endpoint_id}/rdf")
def generate_rdf(endpoint_id, request: Request = None):
    """Turn the source's data into SOSA observations (see code/TSS2RDF/tss2rdf.py).
    The whole graph is written to a temporary file to download (N-Triples), and its first
    observations to a preview file (Turtle). The answer holds the counts and the addresses
    of those two files."""
    graph, source = documents.load_source(endpoint_id, request)

    # The data file, as a file on this computer (embedded points are in the graph itself)
    data_path = None
    if source["embedded"] is not True:
        if source["dataFile"] is None:
            raise HTTPException(status_code=400, detail="The source has no tss:dataFile.")
        if not files.file_exists(source["dataFile"]):
            raise HTTPException(status_code=404, detail=f"Data file not found: {source['dataFile']}")
        data_path = files.local_copy(source["dataFile"])

    token = uuid4().hex
    target = Path(RDF_EXPORT_DIRECTORY.name) / f"{token}.nt"
    try:
        result = generate(graph, data_path, target)
    except ValueError as error:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(error))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    # The preview (the first 200 observations, written as Turtle by rdflib) gets a file and
    # an address of its own: the browser parses it as RDF
    preview = Path(RDF_EXPORT_DIRECTORY.name) / f"{token}-preview.ttl"
    preview.write_text(result["preview"], encoding="utf-8", newline="\n")
    RDF_EXPORTS[token] = {"endpoint": endpoint_id, "file": target, "preview": preview}

    address = f"/api/endpoints/{quote(endpoint_id)}/rdf"
    return {
        "observations": result["observations"],
        "triples": result["triples"],
        "series": result["series"],                  # [{"field": <the field's IRI>, "observations": n}]
        "previewUrl": f"{address}/preview/{token}",
        "previewObservations": result["previewObservations"],
        "previewTruncated": result["previewTruncated"],
        "bytes": result["bytes"],                    # the size of the whole file
        "downloadUrl": f"{address}/download/{token}",
        "filename": GENERATED_RDF_FILENAME,
    }


def generated_rdf(endpoint_id, token):
    """The files of one RDF generation (see generate_rdf), or 404 when they are gone
    (they only live as long as the backend runs)"""
    export = RDF_EXPORTS.get(token)
    if export is None or export["endpoint"] != endpoint_id or not export["file"].is_file():
        raise HTTPException(status_code=404, detail="This generated RDF is no longer available. Generate it again.")
    return export


@router.get("/endpoints/{endpoint_id}/rdf/preview/{token}")
def preview_generated_rdf(endpoint_id, token):
    """The preview of generated RDF: the first 200 observations, as Turtle"""
    return FileResponse(generated_rdf(endpoint_id, token)["preview"], media_type="text/turtle")


@router.get("/endpoints/{endpoint_id}/rdf/download/{token}")
def download_generated_rdf(endpoint_id, token):
    """All the generated RDF, as an N-Triples file to save"""
    return FileResponse(generated_rdf(endpoint_id, token)["file"], media_type="application/n-triples",
                        filename=GENERATED_RDF_FILENAME)
