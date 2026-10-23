"""The dashboard's backend: one FastAPI app. Start it from this folder with
    python -m uvicorn main:app --port 8001
and open the dashboard through the frontend (npm run dev, http://localhost:5174).

This file only makes the app. The code is in the modules next to it:
- paths.py            where things are: the demo 7 folders, and the addresses of the RDF documents
- files.py            files on this computer or online: download, read lines, send to the browser
- online_registry.py  the online endpoints the user added (online_endpoints.ttl, read with rdflib)
- documents.py        the RDF documents: which endpoints there are, read with the browser's base IRI
- source_reading.py   an endpoint's tss:Source and its fields, read from the metadata graph
- value_reading.py    one value (a data cell's text or a point's literal) as a time or a number
- data_reading.py     the data: a CSV file, a Parquet file or the embedded points (read as RDF)
- query_helpers.py    the query form's summary numbers, gaps and hourly/daily values
- sql_box.py          the SQL box's DuckDB part
- rdf_generation.py   the door to the shared code in code/ (the converter and the SHACL command line)
The routes (the addresses the browser calls) are in the *_routes.py modules:
- catalog_routes.py   /api/catalog, adding and removing online endpoints, an endpoint's document
- rdf_routes.py       /api/endpoints/{id}/rdf, /rdf/preview and /rdf/download: the data as SOSA observations
- shacl_routes.py     /api/endpoints/{id}/shacl: the SHACL validation report
- data_routes.py      /api/endpoints/{id}/data, /data/download, /rows, /filter-columns and /query
- sql_routes.py       /api/endpoints/{id}/sql/tables and /sql

How the modules use each other: each one is imported whole (import documents) and used as
documents.load_graph(...), so you always see which file a function lives in, and a test that
patches documents.load_graph changes it for every caller. The one exception is
rdf_generation.py, the door to code/, whose names are imported with "from rdf_generation import".
Never name a local variable like a module (no variable called documents or paths), or Python
will mix them up.
"""
import logging
import mimetypes

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import paths
import catalog_routes
import rdf_routes
import shacl_routes
import data_routes
import sql_routes

# rdflib logs a long traceback for every value that does not fit its datatype; bad values are reported by SHACL instead
logging.getLogger("rdflib.term").setLevel(logging.ERROR)

# Python does not know the .ttl extension, so without this the RDF documents below would be
# sent as text/plain instead of text/turtle
mimetypes.add_type("text/turtle", ".ttl")


app = FastAPI()


# If a route crashes, answer with the error message as JSON ({"detail": ...}), like the
# errors the routes raise themselves, instead of a bare "500 Internal Server Error"
# that tells the browser nothing.
@app.middleware("http")
async def crash_to_message(request, call_next):
    try:
        return await call_next(request)
    except Exception as error:
        return JSONResponse(status_code=500, content={"detail": f"The backend crashed: {type(error).__name__}: {error}"})


# The routes. Each *_routes.py file makes an APIRouter: a group of routes that is not an app by
# itself. It writes its routes as @router.get("/catalog"), @router.post(...), ..., and
# APIRouter(prefix="/api") puts /api in front of each of them (so /api/catalog).
# app.include_router(...) adds the routes of one group to the app. They are added in this order,
# which is also their order in the API documentation (http://localhost:8001/docs).
app.include_router(catalog_routes.router)  # the catalogue, online endpoints, an endpoint's document
app.include_router(rdf_routes.router)      # the RDF generation tab
app.include_router(shacl_routes.router)    # the SHACL tab
app.include_router(data_routes.router)     # the data file, the inspector's rows and the query form
app.include_router(sql_routes.router)      # the SQL box

# The RDF documents as plain files, for the browser to fetch and parse (frontend/src/rdf.js):
#   /files/metadata%20endpoint/<id>.ttl   the local metadata endpoints (the catalogue lists them)
#   /files/SHACL/<name>.ttl               the SHACL shapes
# The data/ folder is not served: some of its data may not be shared (the factory data);
# the /api data routes read it. These two must come before the "/" below, which would
# otherwise answer every address that is not an /api route.
app.mount(paths.METADATA_FILES_PATH, StaticFiles(directory=paths.METADATA_DIR), name="metadata-endpoints")
app.mount(paths.SHACL_FILES_PATH, StaticFiles(directory=paths.SHACL_DIR), name="shacl-shapes")

# The built frontend (npm run build) is in frontend/dist. Every address that is not
# an /api route or one of the files above is looked up as a file in there, and "/" gives index.html.
# Not built yet? Then we skip this, and you use the dashboard through npm run dev.
if paths.FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=paths.FRONTEND_DIST, html=True), name="frontend")
