"""Where things are: the demo 7 folders, and the addresses the browser uses for the RDF documents.
Every module reads these as paths.X, so a test can point one somewhere else with patch.object(paths, ...).
Constants only, no functions."""
from pathlib import Path


# This file lives in demo 7/code/webapp/backend/, so demo 7/ is three folders up
DEMO_DIR = Path(__file__).resolve().parents[3]
METADATA_DIR = DEMO_DIR / "metadata endpoint"
SHACL_DIR = DEMO_DIR / "SHACL"

# The RDF documents are also served as plain files (see the end of main.py), at these addresses:
#   /files/metadata%20endpoint/<id>.ttl   the local metadata endpoints
#   /files/SHACL/<name>.ttl               the SHACL shapes
# (%20 is how a space is written in a web address)
METADATA_FILES_PATH = "/files/metadata endpoint"
SHACL_FILES_PATH = "/files/SHACL"
# The data folder is NOT served (some of its data may not be shared). But a local metadata
# endpoint's <../data/x.csv> is read against its address, so it becomes
# /files/data/x.csv, in the browser and here. source_reading.iri_to_location maps such an
# IRI back to the file demo 7/data/x.csv.
DATA_FILES_PATH = "/files/data/"

# The address of this backend as the browser sees it, for code that has no request to ask
# (a route called from a test or a script): the Vite dev server, which passes requests on
# without changing their Host. See documents.browser_base.
DEFAULT_BASE = "http://localhost:5174/"

# The online endpoints the user adds are remembered in this file, as RDF (see online_registry.py):
#   <https://.../x_tss2.ttl> a dcat:Dataset ; dcterms:identifier "online-1" ; rdfs:label "..."@en ; ...
ONLINE_ENDPOINTS_FILE = Path(__file__).parent / "online_endpoints.ttl"

# The built frontend (npm run build): main.py serves it at "/" when it is there
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
