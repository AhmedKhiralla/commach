"""AI endpoints: nameplate reading. (Import mapping runs inside the import preview.)"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..ai_tasks import reject_suggestion, run_nameplate
from ..api import ApiError, Call, endpoint
from ..perms import can_edit_master, has_any
from .files import read_bytes


class NameplateIn(BaseModel):
    file_id: str
    hint: str | None = Field(default=None, max_length=200)


@endpoint(auth="tenant_user", body=NameplateIn)
def nameplate(call: Call):
    if not (can_edit_master(call) or has_any(call, "maintenance_technician")):
        raise ApiError(403, "forbidden", "Register access is required to read nameplates.")
    f, data = read_bytes(call, call.body.file_id)
    if not (f["mime"].startswith("image/") or f["mime"] == "application/pdf"):
        raise ApiError(422, "bad_file", "Upload a photo of the nameplate.")
    return run_nameplate(call, f, data, call.body.hint)


@endpoint(auth="tenant_user")
def reject(call: Call):
    reject_suggestion(call, call.path["suggestion_id"])
    return {"ok": True}


class CertificateIn(BaseModel):
    file_id: str


@endpoint(auth="tenant_user", body=CertificateIn)
def certificate(call: Call):
    """Read a calibration certificate for a task. The result is a suggestion: nothing is written to the task."""
    from ..ai_tasks import run_certificate
    from .work import task_detail
    t = task_detail(call, call.path["task_id"])
    if not t["perms"]["ai"]:
        raise ApiError(403, "forbidden", "This task is not open for you.")
    f, data = read_bytes(call, call.body.file_id)
    if not (f["mime"].startswith("image/") or f["mime"] == "application/pdf"):
        raise ApiError(422, "bad_file", "Upload the certificate as a PDF or a photo.")
    return run_certificate(call, t, f, data)


@endpoint(auth="tenant_user")
def impact_draft(call: Call):
    from ..ai_tasks import run_impact
    from .work import impact_detail
    i = impact_detail(call, call.path["impact_id"])
    if not i["can_edit"]:
        raise ApiError(403, "forbidden", "This impact assessment cannot be changed by you.")
    return run_impact(call, i)


class ManualIn(BaseModel):
    file_id: str
    asset_id: str | None = None
    class_id: str | None = None


@endpoint(auth="tenant_user", body=ManualIn)
def manual(call: Call):
    """Read a maintenance manual and draft PM checklists by frequency. Each group is a suggestion the user turns into
    a draft checklist (POST /api/templates with ai_suggestion_id), which is then approved by signature."""
    from ..ai_tasks import run_manual
    from .work import _can_edit_templates
    if not _can_edit_templates(call):
        raise ApiError(403, "forbidden", "Engineering access is required to write maintenance checklists.")
    b: ManualIn = call.body
    f, data = read_bytes(call, b.file_id)
    if not (f["mime"] == "application/pdf" or f["mime"].startswith("image/")):
        raise ApiError(422, "bad_file", "Upload the manual as a PDF (or a photo of the maintenance table).")
    expected = ""
    if b.asset_id:
        a = call.tx.one("SELECT a.code, a.name_en, a.manufacturer, a.model, c.name_en AS class_name FROM assets a"
                        " JOIN asset_classes c ON c.id = a.class_id WHERE a.id = %s::uuid", b.asset_id)
        if a:
            expected = f"{a['code']} {a['name_en']} ({a['class_name']}), manufacturer {a['manufacturer'] or '?'}, model {a['model'] or '?'}"
    elif b.class_id:
        expected = call.tx.val("SELECT name_en FROM asset_classes WHERE id = %s::uuid", b.class_id) or ""
    return run_manual(call, f, data, expected or "not given")
