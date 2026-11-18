"""Finding and opening the data files of external snippets, locally or on the web.

``tss:dataFile`` is an IRI, resolved against the IRI of the metadata document. So a relative
``<../data/x.csv>`` is found next to a local document, and next to an online one too: a document
loaded from ``https://example.org/meta/doc.ttl`` with ``<data.csv>`` reads
``https://example.org/meta/data.csv``. rdftss opens:

- ``file:`` IRIs (and plain paths): the local file;
- ``http:`` and ``https:`` IRIs: downloaded (on by default; ``allow_network=False`` when loading
  turns it off). A download is kept as long as its document, so reading a snippet twice reads
  the same data. With a ``cache_folder``, downloads are kept between runs, and checked with the
  server (ETag, Last-Modified) before they are reused, so a growing file is never stale;
- a file or stream you give yourself: ``snippet.set_data_file(path_or_stream)``.

With ``data_folder``, a local data file must lie inside that folder: a document can then not make
rdftss read other files on your computer (``../../secret.csv`` is refused).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import weakref
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .errors import DataFileError


@dataclass
class DataAccess:
    """The rules for opening data files, set when a document is loaded (see :func:`rdftss.load`).

    - ``allow_network``: download ``http:`` and ``https:`` data files (on by default).
    - ``data_folder``: when set, only local data files inside this folder can be read.
    - ``cache_folder``: keep downloads here between runs (checked with the server before reuse).
    - ``max_download_bytes``: the largest download (default: 2 GB).
    - ``timeout``: seconds to wait for a server.
    """

    allow_network: bool = True
    data_folder: Path | None = None
    cache_folder: Path | None = None
    max_download_bytes: int = 2_000_000_000
    timeout: float = 60.0


def data_source(snippet):
    """What to read the data of an external snippet from: a local :class:`~pathlib.Path`, or the
    file object given with ``snippet.set_data_file``. Raises :class:`DataFileError`."""
    if snippet._data_override is not None:
        source = snippet._data_override
        if isinstance(source, (str, os.PathLike)):
            return _checked_path(snippet, Path(source))
        return source
    iri = snippet.data_file
    if iri is None:
        raise DataFileError(f"{snippet.name} states no tss:dataFile")
    scheme = urlparse(iri).scheme
    if scheme in ("http", "https"):
        return download(iri, snippet.document)
    if scheme == "file":
        parsed = urlparse(iri)
        if parsed.netloc not in ("", "localhost"):
            raise DataFileError(f"Can't open {iri}: the file is on another computer ({parsed.netloc})")
        return _checked_path(snippet, Path(unquote(parsed.path)))
    if scheme == "" or (len(scheme) == 1 and os.name == "nt"):  # a plain path (or a Windows drive)
        return _checked_path(snippet, Path(iri))
    raise DataFileError(f"Can't open the data file {iri}: rdftss reads file:, http: and https: data files")


def _checked_path(snippet, path: Path) -> Path:
    access = snippet.document.access
    if access.data_folder is not None:
        folder = Path(access.data_folder).resolve()
        resolved = path.resolve()
        if resolved != folder and folder not in resolved.parents:
            raise DataFileError(f"The data file {path} of {snippet.name} is outside the allowed data folder {folder}")
    if not path.is_file():
        raise DataFileError(f"The data file {path} of {snippet.name} does not exist")
    return path


def download(url: str, document) -> Path:
    """The local copy of an online data file of ``document``, downloaded when needed."""
    access = document.access
    if not access.allow_network:
        raise DataFileError(f"The data file {url} is online, and downloads are off (allow_network=False). "
                            "Give a local copy with snippet.set_data_file(path), or load the document with "
                            "allow_network=True.")
    known = document.downloads.get(url)
    if known is not None and known.is_file():
        return known
    if access.cache_folder is not None:
        path = _cached_download(url, Path(access.cache_folder), access)
    else:
        path = _document_folder(document) / _file_name(url)
        _fetch(url, path, access)
    document.downloads[url] = path
    return path


def _file_name(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix[:12]
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + suffix


def _document_folder(document) -> Path:
    """A temporary folder for the downloads of one document, removed with the document."""
    folder = getattr(document, "_download_folder", None)
    if folder is None:
        folder = Path(tempfile.mkdtemp(prefix="rdftss-"))
        document._download_folder = folder
        weakref.finalize(document, shutil.rmtree, folder, True)
    return folder


def _fetch(url: str, target: Path, access: DataAccess, headers: dict | None = None):
    """Download ``url`` to ``target``. Returns the response headers, or ``None`` when the server
    answered 304 Not Modified."""
    request = urllib.request.Request(url, headers={"User-Agent": "rdftss", **(headers or {})})
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(handle, "wb") as output:
            try:
                response = urllib.request.urlopen(request, timeout=access.timeout)
            except urllib.error.HTTPError as error:
                if error.code == 304:
                    return None
                raise DataFileError(f"Can't download the data file {url}: the server answered {error.code} "
                                    f"{error.reason}") from None
            with response:
                size = 0
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > access.max_download_bytes:
                        raise DataFileError(f"The data file {url} is larger than {access.max_download_bytes} bytes")
                    output.write(chunk)
                found = dict(response.headers.items())
        shutil.move(temporary, target)
        return found
    except DataFileError:
        raise
    except OSError as error:
        raise DataFileError(f"Can't download the data file {url}: {error}") from None
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _cached_download(url: str, folder: Path, access: DataAccess) -> Path:
    """Download into a lasting cache, reusing a cached copy only when the server says it is
    still current (ETag or Last-Modified)."""
    target = folder / _file_name(url)
    notes = target.with_name(target.name + ".json")
    validators = {}
    if target.is_file() and notes.is_file():
        stored = json.loads(notes.read_text(encoding="utf-8"))
        if stored.get("etag"):
            validators["If-None-Match"] = stored["etag"]
        if stored.get("last_modified"):
            validators["If-Modified-Since"] = stored["last_modified"]
    headers = _fetch(url, target, access, validators if validators else None)
    if headers is None:  # 304: the cached copy is current
        return target
    lowered = {key.lower(): value for key, value in headers.items()}
    notes.write_text(json.dumps({"url": url, "etag": lowered.get("etag"),
                                 "last_modified": lowered.get("last-modified")}), encoding="utf-8")
    return target


def source_name(source) -> str:
    """A name for a data source in messages."""
    if isinstance(source, Path):
        return source.name
    return getattr(source, "name", "the data file")
