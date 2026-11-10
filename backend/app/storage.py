"""Controlled file store.

Files are content-addressed: the storage key contains the SHA-256 of the bytes, files are written
once (temporary name, then an atomic rename) and never overwritten. Every read can be verified
against the hash recorded in the database, so a file changed on disk is detected.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile

from .config import settings

MAGIC = {
    b"%PDF": ("application/pdf", ".pdf"),
    b"\x89PNG": ("image/png", ".png"),
    b"\xff\xd8\xff": ("image/jpeg", ".jpg"),
    b"II*\x00": ("image/tiff", ".tif"),
    b"MM\x00*": ("image/tiff", ".tif"),
}
OFFICE = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class StorageError(Exception):
    pass


def root() -> str:
    return os.path.abspath(settings.data_dir)


def sniff(data: bytes, filename: str) -> tuple[str, str]:
    for magic, (mime, ext) in MAGIC.items():
        if data.startswith(magic):
            return mime, ext
    if data[:12].startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data.startswith(b"PK\x03\x04"):
        ext = os.path.splitext(filename.lower())[1]
        if ext in OFFICE:
            return OFFICE[ext], ext
    raise StorageError("Upload a PDF, photo (JPEG, PNG, WebP, TIFF), Excel (.xlsx) or Word (.docx) file.")


def key_for(tenant_id: str, sha: str, ext: str) -> str:
    return f"tenants/{tenant_id}/files/{sha[:2]}/{sha}{ext}"


def put(tenant_id: str, data: bytes, ext: str) -> tuple[str, str]:
    sha = hashlib.sha256(data).hexdigest()
    key = key_for(tenant_id, sha, ext)
    path = os.path.join(root(), key)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o440)
        os.replace(tmp, path)
    return sha, key


def path_of(key: str) -> str:
    p = os.path.abspath(os.path.join(root(), key))
    if not p.startswith(root() + os.sep):
        raise StorageError("Invalid storage key")
    return p


def verify(key: str, sha: str) -> str:
    """Return the path after checking the bytes still match the recorded SHA-256."""
    p = path_of(key)
    if not os.path.exists(p):
        raise StorageError("The stored file is missing from the file store. Upload the same file again to restore it "
                           "(it is accepted only if its SHA-256 matches the record), then run again.")
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != sha:
        raise StorageError("The stored file no longer matches its recorded SHA-256; it was changed outside ComMach.")
    return p


def describe(data: bytes, mime: str) -> int | None:
    """Page count of PDFs (read without OCR); 1 for images; None otherwise."""
    if mime == "application/pdf":
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(io.BytesIO(data)))
    if mime.startswith("image/"):
        return 1
    return None
