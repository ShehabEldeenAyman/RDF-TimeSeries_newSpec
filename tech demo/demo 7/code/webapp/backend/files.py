"""Files that are either on this computer (a path inside demo 7, e.g. "data/x.csv") or online (a web
address): download them, read their lines, check that they exist, send them to the browser.
Used by documents.py (online RDF documents) and by the data, RDF and SQL routes (data files)."""
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from urllib.parse import urlparse, unquote
import re
import tempfile
import requests

import paths

# A file "location" is either a path inside the demo 7 folder (e.g. "data/x.csv")
# or a web address (e.g. "https://example.org/data/x.csv").
# The functions below work for both, so the routes don't have to care which one it is.
#
# Security: this backend downloads every http(s) address an online endpoint names (its
# document, and its tss:dataFile), also addresses on this computer (localhost) and on the
# private network, and passes what it gets on to the browser. Anyone who can reach port 8001
# can register such an endpoint. So never make this backend reachable from other computers
# (and never run tunnel.py with it). See "Security" in code/webapp/README.md.


def is_url(location):
    return location.startswith("http://") or location.startswith("https://")


def download(url, stream=False, accept=None):
    """GET an online file. A network problem becomes a clear error message.
    stream=True: don't download everything at once (for big CSV files).
    accept: the media types we would like (the Accept header), e.g. documents.RDF_ACCEPT."""
    headers = {}
    if accept is not None:
        headers["Accept"] = accept
    try:
        response = requests.get(url, stream=stream, timeout=30, headers=headers)
        response.raise_for_status()  # turns "404 Not Found" and the like into an error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Could not download {url}: {error}")
    response.encoding = "utf-8"  # our files are UTF-8, even when the server doesn't say so
    return response


def file_exists(location):
    if location is None:
        return False
    if is_url(location):
        try:
            response = requests.get(location, stream=True, timeout=30)  # stream: only look, don't download
            response.close()
            return response.status_code < 400
        except requests.RequestException:
            return False
    return (paths.DEMO_DIR / location).exists()


def read_lines(location):
    """The lines of a file, one at a time, so a big CSV never has to fit in memory.
    (A function with "yield" hands out its values one by one, whenever the loop
    that uses it asks for the next one.)"""
    if is_url(location):
        with download(location, stream=True) as response:
            for line in response.iter_lines(decode_unicode=True):
                yield line
    else:
        with open(paths.DEMO_DIR / location, encoding="utf-8", newline="") as f:
            for line in f:
                yield line.rstrip("\r\n")


def file_name(location):
    """The last part of a location, e.g. waterinfo_289423042.csv"""
    return unquote(urlparse(location).path.split("/")[-1])


# Online files that are needed as a file on this computer (DuckDB reads files,
# not web addresses): web address -> downloaded copy. Kept while the backend
# runs; restart the backend to download fresh copies.
DOWNLOADED = {}


def local_copy(location):
    """A file on this computer with the content of a location"""
    if not is_url(location):
        return paths.DEMO_DIR / location
    if location not in DOWNLOADED or not DOWNLOADED[location].exists():
        # The copy gets the name of the online file (e.g. waterinfo_289429042.parquet), so messages
        # show a name people know. Odd characters become "_": DuckDB reads * ? [ ] as patterns.
        name = re.sub(r"[^A-Za-z0-9._-]", "_", file_name(location))
        if name.strip("._") == "":
            name = "data"
        target = Path(tempfile.mkdtemp(prefix="tss2-")) / name
        with download(location, stream=True) as response:
            with open(target, "wb") as f:
                for piece in response.iter_content(chunk_size=65536):
                    f.write(piece)
        DOWNLOADED[location] = target
    return DOWNLOADED[location]


def send_as_download(location, media_type):
    """Answer with a whole file (local or online) and tell the browser to save it"""
    if not file_exists(location):
        raise HTTPException(status_code=404, detail=f"File not found: {location}")

    if is_url(location):
        # Pass the online file on to the browser piece by piece
        response = download(location, stream=True)
        return StreamingResponse(
            response.iter_content(chunk_size=65536),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{file_name(location)}"'},
        )
    # filename=... tells the browser to save the file instead of showing it
    return FileResponse(paths.DEMO_DIR / location, media_type=media_type, filename=file_name(location))
