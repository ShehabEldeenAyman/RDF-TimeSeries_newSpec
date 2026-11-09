"""The data files of the bundled endpoints: files in demo 7's data folder, named by their location
inside demo 7 (e.g. "data/x.csv", see source_reading.iri_to_location). Find them, read their lines,
send them to the browser. Used by the data, RDF and SQL routes.
This backend never downloads anything: the data of a user endpoint (an uploaded or online
metadata file) is read by the browser itself, and never reaches this backend."""
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pathlib import PurePosixPath

import paths


def local_path(location):
    """The file on this computer for a location inside demo 7, e.g. "data/x.csv".
    DuckDB (the Parquet files, the SQL box) and the converter read the file from there."""
    return paths.DEMO_DIR / location


def file_exists(location):
    if location is None:
        return False
    return local_path(location).exists()


def read_lines(location):
    """The lines of a file, one at a time, so a big CSV never has to fit in memory.
    (A function with "yield" hands out its values one by one, whenever the loop
    that uses it asks for the next one.)"""
    with open(local_path(location), encoding="utf-8", newline="") as f:
        for line in f:
            yield line.rstrip("\r\n")


def file_name(location):
    """The last part of a location, e.g. waterinfo_289423042.csv"""
    return PurePosixPath(location).name


def send_as_download(location, media_type):
    """Answer with a whole file and tell the browser to save it"""
    if not file_exists(location):
        raise HTTPException(status_code=404, detail=f"File not found: {location}")
    # filename=... tells the browser to save the file instead of showing it
    return FileResponse(local_path(location), media_type=media_type, filename=file_name(location))
