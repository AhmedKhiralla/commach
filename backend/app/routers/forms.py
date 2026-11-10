"""Forms and records: controlled print templates (block designer or Word with merge fields), approval and versions,
previews, and controlled-copy printing of plans, calibration and PM records, history cards and status labels."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from starlette.responses import Response

from .. import esign, forms
from ..api import ApiError, Call, endpoint, not_found
from ..common import bad, clean, code
from ..perms import has_any, is_admin
from .files import read_bytes

Source = Literal["calibration_plan", "pm_plan", "calibration_record", "pm_record", "history_card", "status_label"]
EDITORS = ("qa_approver", "qa_calibration_officer", "engineering_manager")
SQL = """
SELECT f.id, f.code, f.version, f.title, f.source, f.engine, f.layout, f.file_id, f.format_no, f.revision, f.is_default, f.status,
       f.based_on, f.approved_at, f.approved_by_name, f.source_kind, f.updated_at, fl.filename,
       (SELECT count(*) FROM form_issues i WHERE i.template_id = f.id) AS issued
  FROM form_templates f LEFT JOIN files fl ON fl.id = f.file_id
"""


def can_edit(call: Call) -> bool:
    return is_admin(call) or has_any(call, *EDITORS)


def can_approve(call: Call) -> bool:
    return has_any(call, "qa_approver")


def _form(call: Call, fid: str) -> dict:
    f = call.tx.one(SQL + " WHERE f.id = %s::uuid", fid)
    if not f:
        raise not_found("Form")
    f["versions"] = call.tx.q("SELECT id, version, status FROM form_templates WHERE code = %s ORDER BY version DESC", f["code"])
    f["signature"] = call.tx.one("SELECT s.user_name, s.user_email, s.signed_at, s.statement, s.record_hash FROM signatures s"
                                 " JOIN form_templates t ON t.approved_signature_id = s.id WHERE t.id = %s", f["id"])
    f["can_edit"] = f["status"] == "draft" and can_edit(call)
    f["can_approve"] = f["status"] == "draft" and can_approve(call)
    f["can_manage"] = can_edit(call)
    if f["engine"] == "docx" and f["file_id"]:
        _, data = read_bytes(call, str(f["file_id"]))
        used = forms.placeholders(data)
        f["placeholders"] = used
        f["unknown"] = forms.unknown_fields(f["source"], used)
    return f


@endpoint(auth="tenant_user")
def list_forms(call: Call):
    w, p = "", []
    if call.query.get("source"):
        w, p = " WHERE f.source = %s", [call.query["source"]]
    if call.query.get("all") != "1":
        w += (" AND" if w else " WHERE") + " f.status IN ('draft', 'approved')"
    return {"items": call.tx.q(SQL + w + " ORDER BY f.source, f.code, f.version DESC", *p), "can_edit": can_edit(call),
            "can_approve": can_approve(call)}


@endpoint(auth="tenant_user")
def form_meta(call: Call):
    return {"sources": list(forms.SOURCES), "fields": forms.FIELDS, "lists": list(forms.LISTS),
            "defaults": {s: forms.default_layout(s) for s in forms.SOURCES}}


class Layout(BaseModel):
    page: dict = Field(default_factory=dict)
    header: dict = Field(default_factory=dict)
    blocks: list[dict] = Field(default_factory=list, max_length=60)
    footer: dict = Field(default_factory=dict)


class FormIn(BaseModel):
    code: str
    title: str = Field(min_length=1, max_length=200)
    source: Source
    engine: Literal["blocks", "docx"] = "blocks"
    format_no: str | None = Field(default=None, max_length=60)
    revision: str | None = Field(default=None, max_length=20)
    layout: Layout | None = None
    file_id: str | None = None
    _c = field_validator("code")(lambda v: code(v, upper=True))


class FormPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    format_no: str | None = Field(default=None, max_length=60)
    revision: str | None = Field(default=None, max_length=20)
    layout: Layout | None = None
    file_id: str | None = None


def _check_docx(call: Call, file_id: str) -> None:
    f = call.tx.one("SELECT mime, filename FROM files WHERE id = %s::uuid", file_id)
    if not f or not f["mime"].endswith("wordprocessingml.document"):
        raise bad("file_id", "Upload the template as a Word (.docx) file.")


@endpoint(auth="tenant_user", body=FormIn)
def create_form(call: Call):
    if not can_edit(call):
        raise ApiError(403, "forbidden", "QA or the engineering manager writes forms.")
    b: FormIn = call.body
    if call.tx.val("SELECT 1 FROM form_templates WHERE code = %s", b.code):
        raise bad("code", "This code is already used.")
    if b.engine == "docx":
        if not b.file_id:
            raise bad("file_id", "Upload the Word template.")
        _check_docx(call, b.file_id)
    layout = (b.layout.model_dump() if b.layout else forms.default_layout(b.source)) if b.engine == "blocks" else {}
    fid = call.tx.val("INSERT INTO form_templates (tenant_id, code, title, source, engine, layout, file_id, format_no, revision)"
                      " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::uuid, %s, %s) RETURNING id", call.tenant_id, b.code, b.title.strip(),
                      b.source, b.engine, json.dumps(layout), b.file_id if b.engine == "docx" else None, clean(b.format_no), clean(b.revision))
    return _form(call, str(fid))


@endpoint(auth="tenant_user")
def get_form(call: Call):
    return _form(call, call.path["form_id"])


@endpoint(auth="tenant_user", body=FormPatch)
def update_form(call: Call):
    f = _form(call, call.path["form_id"])
    if not f["can_edit"]:
        raise ApiError(409, "approved", "An approved form cannot be changed. Make a new version.")
    b: FormPatch = call.body
    data = b.model_dump(exclude_unset=True)
    if "title" in data and data["title"]:
        call.tx.x("UPDATE form_templates SET title = %s WHERE id = %s", data["title"].strip(), f["id"])
    for k in ("format_no", "revision"):
        if k in data:
            call.tx.x(f"UPDATE form_templates SET {k} = %s WHERE id = %s", clean(data[k]), f["id"])
    if "layout" in data and f["engine"] == "blocks" and data["layout"] is not None:
        call.tx.x("UPDATE form_templates SET layout = %s::jsonb WHERE id = %s", json.dumps(data["layout"]), f["id"])
    if data.get("file_id") and f["engine"] == "docx":
        _check_docx(call, data["file_id"])
        call.tx.x("UPDATE form_templates SET file_id = %s::uuid WHERE id = %s", data["file_id"], f["id"])
    return _form(call, str(f["id"]))


class SignIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=500)


@endpoint(auth="tenant_user", body=SignIn)
def approve_form(call: Call):
    f = _form(call, call.path["form_id"])
    if not f["can_approve"]:
        raise ApiError(403, "forbidden", "A QA approver approves forms as controlled documents.")
    if f.get("unknown"):
        raise ApiError(422, "unknown_fields", "The Word template uses merge fields this record does not have.",
                       extra={"problems": f["unknown"]})
    b: SignIn = call.body
    label = f"{f['code']} v{f['version']}"
    content = {"code": f["code"], "version": f["version"], "title": f["title"], "source": f["source"], "engine": f["engine"],
               "format_no": f["format_no"], "revision": f["revision"], "layout": f["layout"]}
    if f["file_id"]:
        content["file_sha256"] = call.tx.val("SELECT sha256 FROM files WHERE id = %s", f["file_id"])
    signer = esign.verify_signer(call, b.email, b.password, entity="form", record_id=str(f["id"]), label=label, meaning="form_approved")
    sig = esign.record(call, signer=signer, entity="form", record_id=str(f["id"]), label=label, meaning="form_approved",
                       rhash=esign.record_hash(content), reason=b.reason)
    was_default = False
    for o in call.tx.q("SELECT id, is_default FROM form_templates WHERE code = %s AND status = 'approved'", f["code"]):
        was_default |= o["is_default"]
        call.tx.x("UPDATE form_templates SET status = 'superseded', is_default = false WHERE id = %s", o["id"])
    first = not call.tx.val("SELECT 1 FROM form_templates WHERE source = %s AND status = 'approved' AND is_default", f["source"])
    call.tx.x("UPDATE form_templates SET status = 'approved', approved_signature_id = %s, approved_at = now(), approved_by_name = %s,"
              " is_default = %s WHERE id = %s", sig["id"], call.user["full_name"], was_default or first, f["id"])
    return _form(call, str(f["id"]))


@endpoint(auth="tenant_user")
def new_form_version(call: Call):
    if not can_edit(call):
        raise ApiError(403, "forbidden", "QA or the engineering manager writes forms.")
    f = _form(call, call.path["form_id"])
    if call.tx.val("SELECT 1 FROM form_templates WHERE code = %s AND status = 'draft'", f["code"]):
        raise ApiError(409, "draft_exists", "A draft version of this form already exists.")
    v = call.tx.val("SELECT max(version) FROM form_templates WHERE code = %s", f["code"]) + 1
    nid = call.tx.val("INSERT INTO form_templates (tenant_id, code, version, title, source, engine, layout, file_id, format_no, revision,"
                      " based_on) SELECT tenant_id, code, %s, title, source, engine, layout, file_id, format_no, revision, id"
                      " FROM form_templates WHERE id = %s RETURNING id", v, f["id"])
    return _form(call, str(nid))


@endpoint(auth="tenant_user")
def make_default(call: Call):
    f = _form(call, call.path["form_id"])
    if not can_edit(call):
        raise ApiError(403, "forbidden", "QA or the engineering manager manages forms.")
    if f["status"] != "approved":
        raise ApiError(409, "not_approved", "Only an approved form can be the default.")
    call.tx.x("UPDATE form_templates SET is_default = false WHERE source = %s AND is_default AND id <> %s", f["source"], f["id"])
    call.tx.x("UPDATE form_templates SET is_default = true WHERE id = %s", f["id"])
    return _form(call, str(f["id"]))


class RetireIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@endpoint(auth="tenant_user", body=RetireIn)
def retire_form(call: Call):
    f = _form(call, call.path["form_id"])
    if not can_approve(call) or f["status"] != "approved":
        raise ApiError(403, "forbidden", "A QA approver retires approved forms.")
    call.tx.reason(call.body.reason)
    call.tx.x("UPDATE form_templates SET status = 'retired', is_default = false WHERE id = %s", f["id"])
    return _form(call, str(f["id"]))


# ------------------------------------------------------------------ rendering
def _record(call: Call, source: str) -> tuple[dict, str, str | None]:
    q = call.query
    if source in ("calibration_plan", "pm_plan"):
        site = q.get("site_id") or call.tx.val("SELECT id FROM sites WHERE active ORDER BY code LIMIT 1")
        if not site:
            raise bad("site_id", "Choose a site.")
        try:
            year = int(q.get("year") or dt.date.today().year)
        except ValueError:
            raise bad("year", "Give the year.")
        ctx, ref = forms.plan_context(call, str(site), "calibration" if source == "calibration_plan" else "pm", year)
        pid = call.tx.val("SELECT id FROM plans WHERE site_id = %s::uuid AND kind = %s AND year = %s", str(site),
                          "calibration" if source == "calibration_plan" else "pm", year)
        return ctx, ref, str(pid) if pid else None
    if source in ("calibration_record", "pm_record"):
        kind = "calibration" if source == "calibration_record" else "pm"
        tid = q.get("task_id") or call.tx.val("SELECT id FROM tasks WHERE kind = %s AND status = 'completed' ORDER BY completed_at DESC"
                                              " LIMIT 1", kind) or call.tx.val("SELECT id FROM tasks WHERE kind = %s LIMIT 1", kind)
        if not tid:
            raise ApiError(404, "no_record", "There is no task to print yet.")
        ctx, ref, real = forms.task_context(call, str(tid))
        if real != kind:
            raise bad("task_id", "This form prints the other kind of task.")
        return ctx, ref, str(tid)
    aid = q.get("asset_id") or call.tx.val("SELECT asset_id FROM tasks WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 1") \
        or call.tx.val("SELECT id FROM assets LIMIT 1")
    if not aid:
        raise ApiError(404, "no_record", "There is no asset to print yet.")
    ctx, ref = forms.asset_context(call, str(aid))
    return ctx, ref, str(aid)


def _logo(call: Call, layout: dict) -> bytes | None:
    fid = (layout.get("header") or {}).get("logo_file_id")
    if not fid:
        return None
    try:
        return read_bytes(call, fid)[1]
    except ApiError:
        return None


def _render(call: Call, f: dict, ctx: dict) -> tuple[bytes, str, str]:
    if f["engine"] == "docx":
        _, data = read_bytes(call, str(f["file_id"]))
        return forms.render_docx(data, ctx), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    return forms.render_blocks(f["layout"] or {}, ctx, _logo(call, f["layout"] or {})), "application/pdf", "pdf"


def _ctx(call: Call, f: dict, number: str) -> tuple[dict, str, str | None]:
    rec, ref, rid = _record(call, f["source"])
    ctx = {**forms._common(call, f, number, rec.pop("_site", None)), **rec}
    return ctx, ref, rid


@endpoint(auth="tenant_user")
def preview_form(call: Call):
    f = _form(call, call.path["form_id"])
    ctx, ref, _ = _ctx(call, f, "PREVIEW")
    ctx["form"]["status"] = "draft" if f["status"] != "approved" else "approved"
    data, mime, ext = _render(call, f, ctx)
    return Response(data, media_type=mime, headers={"Content-Disposition": f'inline; filename="preview-{f["code"]}.{ext}"'})


class PreviewIn(BaseModel):
    source: Source
    layout: Layout
    title: str = "Preview"
    format_no: str | None = None
    revision: str | None = None


@endpoint(auth="tenant_user", body=PreviewIn)
def preview_layout(call: Call):
    """Live preview of an unsaved block layout in the designer."""
    b: PreviewIn = call.body
    f = {"code": "PREVIEW", "version": 0, "title": b.title, "format_no": b.format_no, "revision": b.revision, "status": "draft",
         "source": b.source, "engine": "blocks", "layout": b.layout.model_dump()}
    ctx, _, _ = _ctx(call, f, "PREVIEW")
    pdf = forms.render_blocks(f["layout"], ctx, _logo(call, f["layout"]))
    if call.query.get("as") == "png":
        # page images: every browser shows them, including phones and tablets that cannot show a PDF in a frame
        return {"pages": forms.pdf_pages(pdf, max_pages=4), "count": forms.pdf_page_count(pdf)}
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="preview.pdf"'})


def _issue(call: Call, f: dict) -> Response:
    number = call.tx.val("SELECT cm_next_number(%s::uuid, 'CC', 5)", call.tenant_id)
    ctx, ref, rid = _ctx(call, f, number)
    data, mime, ext = _render(call, f, ctx)
    sha = hashlib.sha256(data).hexdigest()
    call.tx.x("INSERT INTO form_issues (tenant_id, number, template_id, template_code, template_version, template_status, source,"
              " record_ref, record_id, format, sha256, issued_by, issued_by_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s, %s, %s, %s)",
              call.tenant_id, number, f["id"], f["code"], f["version"], f["status"], f["source"], ref, rid, ext, sha,
              call.user["id"], call.user["full_name"])
    call.tx.event("EXPORT", "form_issues", rid, f"{number} {ref}", {"format": ext, "form": f"{f['code']} v{f['version']}",
                                                                   "status": f["status"], "sha256": sha})
    name = f"{number}-{f['code']}-{ref}".replace(" ", "_").replace("/", "-")
    disp = "inline" if ext == "pdf" else "attachment"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'{disp}; filename="{name}.{ext}"', "X-Copy-Number": number})


@endpoint(auth="tenant_user")
def print_form(call: Call):
    f = _form(call, call.path["form_id"])
    if f["status"] not in ("approved", "draft"):
        raise ApiError(409, "retired", "This form version is no longer in use.")
    return _issue(call, f)


@endpoint(auth="tenant_user")
def print_default(call: Call):
    """Print with the company's default approved form for the source (used by the Print buttons)."""
    source = call.query.get("source", "")
    if source not in forms.SOURCES:
        raise bad("source", "Unknown record type.")
    fid = call.tx.val("SELECT id FROM form_templates WHERE source = %s AND status = 'approved' ORDER BY is_default DESC, code LIMIT 1", source)
    if not fid:
        raise ApiError(409, "no_form", "No approved form prints this record yet. Add one under Forms and records.")
    return _issue(call, _form(call, str(fid)))


@endpoint(auth="tenant_user")
def starter(call: Call):
    source = call.query.get("source", "")
    if source not in forms.SOURCES:
        raise bad("source", "Unknown record type.")
    return Response(forms.starter_docx(source), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="commach-{source}-template.docx"'})


@endpoint(auth="tenant_user")
def list_issues(call: Call):
    w, p = [], []
    if call.query.get("record_id"):
        w.append("record_id = %s::uuid")
        p.append(call.query["record_id"])
    if call.query.get("template_id"):
        w.append("template_id = %s::uuid")
        p.append(call.query["template_id"])
    sql = "SELECT * FROM form_issues" + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY issued_at DESC LIMIT 200"
    return {"items": call.tx.q(sql, *p)}
