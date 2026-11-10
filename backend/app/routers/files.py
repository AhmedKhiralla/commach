"""Controlled files: photos, nameplates, manuals, certificates and import workbooks.

Upload is a raw body (?filename=...&purpose=...). Files are content-addressed and write-once; every
download re-checks the SHA-256 recorded at upload, so a file changed on disk is refused.
"""
from __future__ import annotations

import re

import anyio.from_thread
from starlette.responses import FileResponse

from .. import storage
from ..api import ApiError, Call, endpoint, not_found
from ..config import settings

PURPOSES = ("photo", "nameplate", "manual", "certificate", "import", "attachment", "form", "logo")


def save_upload(call: Call, data: bytes, filename: str, purpose: str) -> dict:
    if not settings.storage_persistent:
        raise ApiError(503, "no_storage", "Uploads are switched off because this server has no persistent volume: "
                       "files would be lost at the next deploy. Attach a volume to the ComMach service in Railway.")
    if not data:
        raise ApiError(422, "no_file", "Choose a file to upload.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ApiError(413, "too_large", f"The file is larger than {settings.max_upload_mb} MB.")
    try:
        mime, ext = storage.sniff(data, filename)
        pages = storage.describe(data, mime)
    except storage.StorageError as e:
        raise ApiError(422, "bad_file", str(e))
    except Exception:
        raise ApiError(422, "bad_file", "This file could not be opened. Check that it is not damaged or password-protected.")
    sha, key = storage.put(call.tenant_id, data, ext)
    row = call.tx.one(
        "INSERT INTO files (tenant_id, sha256, filename, mime, size_bytes, page_count, purpose, storage_key)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id, sha256, filename, mime, size_bytes, page_count, purpose, created_at",
        call.tenant_id, sha, filename, mime, len(data), pages, purpose, key)
    call.tx.event("FILE_UPLOADED", "files", str(row["id"]), filename, {"sha256": sha, "bytes": len(data), "purpose": purpose})
    return row


@endpoint(auth="tenant_user")
def upload_file(call: Call):
    filename = (call.query.get("filename") or "").strip()[:200]
    if not filename:
        raise ApiError(422, "invalid", "The file name is missing.")
    purpose = call.query.get("purpose") or "attachment"
    if purpose not in PURPOSES:
        raise ApiError(422, "invalid", "Unknown file purpose.")
    data = anyio.from_thread.run(call.request.body)
    return save_upload(call, data, filename, purpose)


def read_bytes(call: Call, file_id: str) -> tuple[dict, bytes]:
    f = call.tx.one("SELECT * FROM files WHERE id = %s::uuid", str(file_id))
    if not f:
        raise not_found("File")
    try:
        path = storage.verify(f["storage_key"], f["sha256"])
    except storage.StorageError as e:
        raise ApiError(409, "integrity", str(e))
    with open(path, "rb") as fh:
        return f, fh.read()


@endpoint(auth="tenant_user")
def file_content(call: Call):
    f = call.tx.one("SELECT * FROM files WHERE id = %s::uuid", call.path["file_id"])
    if not f:
        raise not_found("File")
    try:
        path = storage.verify(f["storage_key"], f["sha256"])
    except storage.StorageError as e:
        raise ApiError(409, "integrity", str(e))
    if call.query.get("inline") != "1" or not f["mime"].startswith("image/"):
        call.tx.event("FILE_DOWNLOADED", "files", str(f["id"]), f["filename"], {"sha256": f["sha256"]})
    disp = "inline" if call.query.get("inline") == "1" else "attachment"
    safe = re.sub(r'[^\w.\- ()]', "_", f["filename"])
    return FileResponse(path, media_type=f["mime"], headers={"Content-Disposition": f'{disp}; filename="{safe}"',
                                                              "Cache-Control": "private, max-age=3600"})
