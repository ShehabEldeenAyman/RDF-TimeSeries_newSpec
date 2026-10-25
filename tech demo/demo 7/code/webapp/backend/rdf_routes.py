"""Routes for the RDF generation tab: the data as SOSA observations (code/TSS2RDF/tss2rdf.py),
written to files to preview and download. main.py adds this router to the app."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from urllib.parse import quote
from uuid import uuid4
import threading

import paths
import files
import documents
from rdf_generation import generate

# The routes of this file, all under /api (main.py adds them to the app with include_router)
router = APIRouter(prefix="/api")


# The generated RDF (the RDF generation tab) is written to two files in one fixed folder,
# paths.RDF_EXPORT_DIR: the whole graph to download (N-Triples, <token>.nt) and a preview with
# its first observations (Turtle, <token>-preview.ttl). The token is a random name, and the
# addresses in the answer carry it. So that no generated data is left behind on this computer:
# - each endpoint keeps only its latest export: a new generation deletes the files of the one before;
# - the folder is emptied when the backend starts (that also removes what a backend that was
#   killed left behind) and when it stops: see empty_export_folder, which main.py calls;
# - a token only works for its own endpoint (see generated_rdf).
# endpoint id -> its latest export: {"token": ..., "file": <the .nt file>, "preview": <the .ttl file>}
RDF_EXPORTS = {}
# FastAPI runs routes like these in several threads at the same time. The lock makes sure that
# two generations of one endpoint that end together can't both stay (one would never be deleted).
RDF_EXPORTS_LOCK = threading.Lock()
# The name the downloaded file gets (N-Triples: one triple per line, see tss2rdf.write_batch)
GENERATED_RDF_FILENAME = "observations.nt"


def delete_export_files(export):
    """Delete the two files of one export. On Windows a file that is being sent to a browser right
    now can't be deleted: it then stays until the folder is emptied (see empty_export_folder)."""
    for path in (export["file"], export["preview"]):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def empty_export_folder():
    """Forget every export and delete every file in the export folder (paths.RDF_EXPORT_DIR).
    main.py calls this when the backend starts and when it stops."""
    with RDF_EXPORTS_LOCK:
        RDF_EXPORTS.clear()
        if not paths.RDF_EXPORT_DIR.is_dir():
            return
        for path in paths.RDF_EXPORT_DIR.iterdir():
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass  # still in use (see delete_export_files): deleted the next time


@router.post("/endpoints/{endpoint_id}/rdf")
def generate_rdf(endpoint_id, request: Request = None):
    """Turn the source's data into SOSA observations (see code/TSS2RDF/tss2rdf.py).
    The whole graph is written to a file to download (N-Triples), and its first observations
    to a preview file (Turtle), both in the export folder (see RDF_EXPORTS). The answer holds
    the counts and the addresses of those two files."""
    graph, source = documents.load_source(endpoint_id, request)

    # The data file, a file in demo 7's data folder (embedded points are in the graph itself)
    data_path = None
    if source["embedded"] is not True:
        if source["dataFile"] is None:
            raise HTTPException(status_code=400, detail="The source has no tss:dataFile.")
        if not files.file_exists(source["dataFile"]):
            raise HTTPException(status_code=404, detail=f"Data file not found: {source['dataFile']}")
        data_path = files.local_path(source["dataFile"])

    token = uuid4().hex
    paths.RDF_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    export = {
        "token": token,
        "file": paths.RDF_EXPORT_DIR / f"{token}.nt",
        # The preview (the first 200 observations, written as Turtle by rdflib) gets a file and
        # an address of its own: the browser parses it as RDF
        "preview": paths.RDF_EXPORT_DIR / f"{token}-preview.ttl",
    }
    try:
        result = generate(graph, data_path, export["file"])
        export["preview"].write_text(result["preview"], encoding="utf-8", newline="\n")
    except ValueError as error:
        delete_export_files(export)
        raise HTTPException(status_code=400, detail=str(error))
    except Exception:
        delete_export_files(export)
        raise

    # This export takes the place of the endpoint's previous one, whose files are deleted
    with RDF_EXPORTS_LOCK:
        previous = RDF_EXPORTS.get(endpoint_id)
        RDF_EXPORTS[endpoint_id] = export
    if previous is not None:
        delete_export_files(previous)

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
    """The files of the endpoint's latest RDF generation (see generate_rdf), when the token is
    its token. 404 for any other token: an older generation of this endpoint (its files are
    deleted), a generation of another endpoint, or one from before the backend (re)started."""
    export = RDF_EXPORTS.get(endpoint_id)
    if export is None or export["token"] != token or not export["file"].is_file() or not export["preview"].is_file():
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
