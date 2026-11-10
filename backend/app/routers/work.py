"""Calibration work (Phase 2; the same endpoints serve preventive maintenance in Phase 3).

Templates   what is measured: points with tolerances; approved by e-signature, changed only by a new version
Schedules   when and by whom; one open task per active schedule, recreated after every completion
Tasks       accept -> (send to vendor) -> results -> signed completion -> optional review
            A failed calibration quarantines the asset with the same signature; any out-of-tolerance result
            opens an impact assessment that QA approves.
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .. import esign, work
from ..api import ApiError, Call, endpoint, not_found
from ..common import bad, clean, code, features, patch_sql, reason_for_change
from ..perms import _roles as perm_roles
from ..perms import EDITORS, can_edit_site, has_any, is_admin, require_master_edit, require_site, site_roles, visible_site_ids
from .assets import TRANSITIONS, apply_status

Kind = Literal["calibration", "pm"]
PERFORMERS = {"calibration": {"qa_calibration_officer", "engineering_supervisor", "engineering_manager"},
              "pm": {"engineering_supervisor", "engineering_manager"}}
# a maintenance technician performs work only when it is assigned to them (by name or by role)
REVIEWERS = {"calibration": {"qa_approver", "qa_calibration_officer"},
             "pm": {"engineering_supervisor", "engineering_manager", "qa_approver"}}
TEMPLATE_APPROVERS = {"calibration": {"qa_approver", "qa_calibration_officer"}, "pm": {"engineering_manager", "qa_approver"}}
PLAN_REVIEWERS = {"engineering_supervisor", "engineering_manager"}
PLAN_APPROVERS = {"qa_approver"}
MEANING = {"calibration": ("calibration_completed", "calibration_reviewed"), "pm": ("pm_completed", "pm_checked")}


def settings_of(call: Call) -> dict:
    cached = getattr(call, "_work_settings", None)
    if cached is None:
        cached = work.effective_settings(call.tx.val("SELECT work_settings FROM tenants WHERE id = %s", call.tenant_id))
        setattr(call, "_work_settings", cached)
    return cached


def today(call: Call) -> dt.date:
    return call.tx.val("SELECT (now() AT TIME ZONE coalesce((SELECT timezone FROM tenants WHERE id = %s), 'UTC'))::date",
                       call.tenant_id)


# ================================================================== settings
class Band(BaseModel):
    up_to_days: int | None = Field(default=None, ge=1, le=36600)
    before_value: float | None = Field(default=None, ge=0, le=1000)
    before_unit: Literal["any", "day", "week", "month", "percent"]
    after_value: float = Field(ge=0, le=1000)
    after_unit: Literal["day", "week", "month", "percent"]


class Closure(BaseModel):
    from_: dt.date = Field(alias="from")
    to: dt.date | None = None
    name: str = Field(min_length=1, max_length=120)


class CalendarIn(BaseModel):
    weekend: list[int] = Field(default_factory=lambda: [4], max_length=6)
    closures: list[Closure] = Field(default_factory=list, max_length=200)
    shift: Literal["earlier", "later"] = "earlier"
    kinds: list[Kind] = Field(default_factory=lambda: ["pm"])


class WorkSettingsIn(BaseModel):
    calendar: CalendarIn | None = None
    calibration: list[Band] = Field(min_length=1, max_length=12)
    pm: list[Band] | None = Field(default=None, max_length=12)
    due_soon_days: int = Field(default=30, ge=1, le=180)
    quarantine_on_fail: bool = True
    uncertainty_ratio_min: float = Field(default=4, ge=1, le=10)


@endpoint(auth="tenant_user")
def get_work_settings(call: Call):
    return {"settings": settings_of(call), "defaults": work.WORK_DEFAULTS}


@endpoint(auth="tenant_admin", body=WorkSettingsIn)
def update_work_settings(call: Call):
    b: WorkSettingsIn = call.body
    tol = {}
    for kind, bands in (("calibration", b.calibration), ("pm", b.pm)):
        if bands is None:
            continue
        rows = [x.model_dump() for x in bands]
        if sum(1 for r in rows if r["up_to_days"] is None) != 1:
            raise bad(kind, "Keep exactly one band for the longest intervals (no upper limit).")
        if len({r["up_to_days"] for r in rows}) != len(rows):
            raise bad(kind, "Two bands have the same upper limit.")
        for r in rows:
            if r["before_unit"] != "any" and r["before_value"] is None:
                raise bad(kind, "Give the early limit or choose 'any time'.")
            if "percent" in (r["before_unit"], r["after_unit"]) and max(r["before_value"] or 0, r["after_value"]) > 50:
                raise bad(kind, "Use 50 % of the interval or less.")
        tol[kind] = sorted(rows, key=lambda r: (r["up_to_days"] is None, r["up_to_days"] or 0))
    data = {"tolerance": {**settings_of(call)["tolerance"], **tol}, "due_soon_days": b.due_soon_days,
            "quarantine_on_fail": b.quarantine_on_fail, "uncertainty_ratio_min": b.uncertainty_ratio_min,
            "calendar": settings_of(call)["calendar"]}
    if b.calendar is not None:
        if any(d < 0 or d > 6 for d in b.calendar.weekend):
            raise bad("calendar", "Weekend days are 0 (Monday) to 6 (Sunday).")
        for c in b.calendar.closures:
            if c.to and c.to < c.from_:
                raise bad("calendar", f"The closure {c.name} ends before it starts.")
        data["calendar"] = {"weekend": sorted(set(b.calendar.weekend)), "shift": b.calendar.shift, "kinds": b.calendar.kinds,
                            "closures": sorted(({"from": c.from_.isoformat(), "to": (c.to or c.from_).isoformat(), "name": c.name.strip()}
                                                for c in b.calendar.closures), key=lambda c: c["from"])}
    call.tx.x("UPDATE tenants SET work_settings = %s::jsonb WHERE id = %s", json.dumps(data), call.tenant_id)
    setattr(call, "_work_settings", None)
    return {"settings": settings_of(call), "defaults": work.WORK_DEFAULTS}


# ================================================================== templates
class ItemIn(BaseModel):
    item_type: Literal["point", "check", "reading", "text", "photo"] = "point"
    parameter: str = Field(min_length=1, max_length=200)
    nominal: decimal.Decimal | None = None
    unit: str | None = Field(default=None, max_length=30)
    tol_mode: Literal["abs", "pct", "limits"] | None = None
    tol_minus: decimal.Decimal | None = Field(default=None, ge=0)
    tol_plus: decimal.Decimal | None = Field(default=None, ge=0)
    low_limit: decimal.Decimal | None = None
    high_limit: decimal.Decimal | None = None
    required: bool = True
    notes: str | None = Field(default=None, max_length=500)
    page_ref: str | None = Field(default=None, max_length=40)


class SparePart(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    part_no: str | None = Field(default=None, max_length=80)
    qty: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=20)
    page_ref: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=300)


class TemplateExtra(BaseModel):
    spare_parts: list[SparePart] | None = Field(default=None, max_length=100)
    suggested_interval_value: int | None = Field(default=None, ge=1, le=3650)
    suggested_interval_unit: Literal["day", "week", "month", "year"] | None = None
    est_minutes: int | None = Field(default=None, ge=1, le=100000)


class TemplateIn(TemplateExtra):
    kind: Kind = "calibration"
    code: str
    title: str = Field(min_length=1, max_length=200)
    title_ar: str | None = Field(default=None, max_length=200)
    class_id: str | None = None
    method_ref: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    standards_note: str | None = Field(default=None, max_length=1000)
    environment_note: str | None = Field(default=None, max_length=500)
    items: list[ItemIn] = Field(default_factory=list, max_length=200)
    source_file_id: str | None = None
    ai_suggestion_id: str | None = None
    _c = field_validator("code")(lambda v: code(v, upper=True))


class TemplatePatch(TemplateExtra):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    title_ar: str | None = Field(default=None, max_length=200)
    class_id: str | None = None
    method_ref: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    standards_note: str | None = Field(default=None, max_length=1000)
    environment_note: str | None = Field(default=None, max_length=500)
    items: list[ItemIn] | None = Field(default=None, max_length=200)


class SignIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=500)


TEMPLATE_SQL = """
SELECT t.id, t.kind, t.code, t.version, t.title, t.title_ar, t.class_id, t.method_ref, t.description, t.standards_note,
       t.environment_note, t.status, t.based_on, t.approved_at, t.approved_by_name, t.source, t.updated_at,
       t.spare_parts, t.suggested_interval_value, t.suggested_interval_unit, t.est_minutes, t.source_file_id,
       t.ai_suggestion_id, f.filename AS source_filename,
       c.code AS class_code, c.name_en AS class_name_en, c.name_ar AS class_name_ar,
       (SELECT count(*) FROM template_items i WHERE i.template_id = t.id) AS item_count,
       (SELECT count(*) FROM schedules s WHERE s.template_id = t.id AND s.active) AS schedule_count
  FROM templates t LEFT JOIN asset_classes c ON c.id = t.class_id LEFT JOIN files f ON f.id = t.source_file_id
"""


def _check_items(kind: str, items: list[ItemIn]) -> None:
    for i, it in enumerate(items):
        if it.item_type == "point":
            if it.nominal is None:
                raise bad(f"items.{i}.nominal", "A calibration point needs a nominal value.")
            if it.tol_mode is None:
                raise bad(f"items.{i}.tol_mode", "Choose how the tolerance is given.")
            if it.tol_mode in ("abs", "pct") and it.tol_minus is None and it.tol_plus is None:
                raise bad(f"items.{i}.tol_plus", "Give the tolerance.")
            if it.tol_mode == "limits" and it.low_limit is None and it.high_limit is None:
                raise bad(f"items.{i}.low_limit", "Give the lower or upper limit.")
        if kind == "calibration" and it.item_type != "point":
            raise bad(f"items.{i}.item_type", "Calibration templates hold calibration points.")
        if kind == "pm" and it.item_type == "point":
            raise bad(f"items.{i}.item_type", "Use a reading with limits in a maintenance checklist.")
        if it.low_limit is not None and it.high_limit is not None and it.low_limit > it.high_limit:
            raise bad(f"items.{i}.high_limit", "The upper limit must not be below the lower limit.")


def _write_items(call: Call, tid: str, items: list[ItemIn]) -> None:
    call.tx.x("DELETE FROM template_items WHERE template_id = %s::uuid", tid)
    for n, it in enumerate(items, start=1):
        tol_minus, tol_plus = it.tol_minus, it.tol_plus
        if it.item_type != "point" and it.item_type != "reading":
            it.tol_mode, it.nominal, tol_minus, tol_plus, it.low_limit, it.high_limit = None, None, None, None, None, None
        elif it.item_type == "reading":
            it.tol_mode = "limits" if (it.low_limit is not None or it.high_limit is not None) else None
            tol_minus = tol_plus = None
        if it.tol_mode in ("abs", "pct"):
            tol_minus = tol_minus if tol_minus is not None else tol_plus
            tol_plus = tol_plus if tol_plus is not None else tol_minus
        call.tx.x("INSERT INTO template_items (tenant_id, template_id, seq, item_type, parameter, nominal, unit, tol_mode,"
                  " tol_minus, tol_plus, low_limit, high_limit, required, notes, page_ref)"
                  " VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                  call.tenant_id, tid, n, it.item_type, it.parameter.strip(), it.nominal, clean(it.unit), it.tol_mode,
                  tol_minus, tol_plus, it.low_limit, it.high_limit, it.required, clean(it.notes), clean(it.page_ref))


def _extra_sql(call: Call, tid: str, data: dict) -> None:
    if "spare_parts" in data:
        call.tx.x("UPDATE templates SET spare_parts = %s::jsonb WHERE id = %s::uuid",
                  json.dumps([p for p in (data.pop("spare_parts") or [])]), tid)


def template_detail(call: Call, tid: str) -> dict:
    t = call.tx.one(TEMPLATE_SQL + " WHERE t.id = %s::uuid", tid)
    if not t:
        raise not_found("Template")
    t["items"] = call.tx.q("SELECT id, seq, item_type, parameter, nominal, unit, tol_mode, tol_minus, tol_plus, low_limit,"
                           " high_limit, required, notes, page_ref FROM template_items WHERE template_id = %s::uuid ORDER BY seq", tid)
    for it in t["items"]:
        lo, hi = work.limits(it)
        it["limit_low"], it["limit_high"] = lo, hi
    t["signatures"] = esign.list_for(call, "template", tid)
    t["versions"] = call.tx.q("SELECT id, version, status, approved_at FROM templates WHERE code = %s AND kind = %s"
                              " ORDER BY version DESC", t["code"], t["kind"])
    t["can_edit"] = t["status"] == "draft" and _can_edit_templates(call)
    t["can_approve"] = t["status"] == "draft" and has_any(call, *TEMPLATE_APPROVERS[t["kind"]]) and len(t["items"]) > 0
    return t


def _can_edit_templates(call: Call) -> bool:
    return is_admin(call) or has_any(call, *EDITORS)


@endpoint(auth="tenant_user")
def list_templates(call: Call):
    kind = call.query.get("kind", "calibration")
    where = " WHERE t.kind = %s"
    params: list = [kind]
    if call.query.get("status"):
        where += " AND t.status = %s"
        params.append(call.query["status"])
    elif call.query.get("all") != "1":
        where += " AND t.status IN ('draft', 'approved')"
    return {"items": call.tx.q(TEMPLATE_SQL + where + " ORDER BY t.code, t.version DESC", *params),
            "can_edit": _can_edit_templates(call)}


@endpoint(auth="tenant_user")
def get_template(call: Call):
    return template_detail(call, call.path["template_id"])


@endpoint(auth="tenant_user", body=TemplateIn)
def create_template(call: Call):
    if not _can_edit_templates(call):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access is required to write templates.")
    b: TemplateIn = call.body
    if call.tx.val("SELECT 1 FROM templates WHERE code = %s", b.code):
        raise bad("code", "This code is already used.")
    if b.class_id and not call.tx.val("SELECT 1 FROM asset_classes WHERE id = %s::uuid", b.class_id):
        raise bad("class_id", "Choose a class.")
    _check_items(b.kind, b.items)
    if b.source_file_id and not call.tx.val("SELECT 1 FROM files WHERE id = %s::uuid", b.source_file_id):
        raise bad("source_file_id", "Upload the manual again.")
    tid = str(call.tx.val(
        "INSERT INTO templates (tenant_id, kind, code, title, title_ar, class_id, method_ref, description, standards_note,"
        " environment_note, spare_parts, suggested_interval_value, suggested_interval_unit, est_minutes, source_file_id)"
        " VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::uuid) RETURNING id",
        call.tenant_id, b.kind, b.code, b.title.strip(), clean(b.title_ar), b.class_id, clean(b.method_ref),
        clean(b.description), clean(b.standards_note), clean(b.environment_note),
        json.dumps([p.model_dump() for p in b.spare_parts or []]), b.suggested_interval_value, b.suggested_interval_unit,
        b.est_minutes, b.source_file_id))
    _write_items(call, tid, b.items)
    if b.ai_suggestion_id:
        from ..ai_tasks import accept_suggestion
        t = template_detail(call, tid)
        accept_suggestion(call, b.ai_suggestion_id, "template", tid, manual_final(t))
        call.tx.x("UPDATE templates SET ai_suggestion_id = %s::uuid WHERE id = %s::uuid", b.ai_suggestion_id, tid)
    return template_detail(call, tid)


def manual_final(t: dict) -> dict:
    """The values compared with the AI draft of a manual group (see ai_tasks.run_manual)."""
    return {"title": t["title"], "interval": f"{t['suggested_interval_value']} {t['suggested_interval_unit']}",
            "items": [f"{i['item_type']}: {i['parameter']}" for i in t["items"]],
            "spare_parts": [p.get("name") for p in t["spare_parts"] or []]}


@endpoint(auth="tenant_user", body=TemplatePatch)
def update_template(call: Call):
    if not _can_edit_templates(call):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access is required to write templates.")
    tid = call.path["template_id"]
    t = call.tx.one("SELECT * FROM templates WHERE id = %s::uuid", tid)
    if not t:
        raise not_found("Template")
    if t["status"] != "draft":
        raise ApiError(409, "approved", "An approved template cannot be changed. Make a new version.")
    b: TemplatePatch = call.body
    data = b.model_dump(exclude_unset=True)
    items = data.pop("items", None)
    _extra_sql(call, tid, data)
    if "class_id" in data:
        call.tx.x("UPDATE templates SET class_id = %s::uuid WHERE id = %s::uuid", data.pop("class_id"), tid)
    patch_sql(call, "templates", tid, {k: ((clean(v) if k != "title" else v.strip()) if isinstance(v, str) else v)
                                        for k, v in data.items() if k != "title" or v})
    if items is not None:
        its = [ItemIn(**i) for i in items]
        _check_items(t["kind"], its)
        _write_items(call, tid, its)
    return template_detail(call, tid)


@endpoint(auth="tenant_user", body=SignIn)
def approve_template(call: Call):
    tid = call.path["template_id"]
    t = template_detail(call, tid)
    if t["status"] != "draft":
        raise ApiError(409, "approved", "This version is already approved.")
    if not has_any(call, *TEMPLATE_APPROVERS[t["kind"]]):
        raise ApiError(403, "forbidden", "Only a QA approver or the QA calibration officer can approve calibration templates."
                       if t["kind"] == "calibration" else "Only the engineering manager or a QA approver can approve maintenance checklists.")
    if not t["items"]:
        raise ApiError(422, "empty", "Add the points before approving the template.")
    b: SignIn = call.body
    label = f"{t['code']} v{t['version']}"
    signer = esign.verify_signer(call, b.email, b.password, entity="template", record_id=tid, label=label,
                                 meaning="template_approved")
    rhash = esign.record_hash({"template": {k: t[k] for k in ("id", "code", "version", "title", "method_ref")},
                               "items": [{k: it[k] for k in ("seq", "item_type", "parameter", "nominal", "unit", "tol_mode",
                                                              "tol_minus", "tol_plus", "low_limit", "high_limit", "required")}
                                         for it in t["items"]],
                               "spare_parts": t["spare_parts"],
                               "interval": [t["suggested_interval_value"], t["suggested_interval_unit"]]})
    sig = esign.record(call, signer=signer, entity="template", record_id=tid, label=label, meaning="template_approved",
                       rhash=rhash, reason=b.reason)
    old = call.tx.q("SELECT id FROM templates WHERE code = %s AND kind = %s AND status = 'approved'", t["code"], t["kind"])
    for o in old:
        call.tx.x("UPDATE templates SET status = 'superseded' WHERE id = %s", o["id"])
        call.tx.x("UPDATE schedules SET template_id = %s::uuid WHERE template_id = %s AND active", tid, o["id"])
    call.tx.x("UPDATE templates SET status = 'approved', approved_signature_id = %s, approved_at = now(),"
              " approved_by_name = %s WHERE id = %s::uuid", sig["id"], call.user["full_name"], tid)
    return template_detail(call, tid)


@endpoint(auth="tenant_user")
def new_version(call: Call):
    if not _can_edit_templates(call):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access is required to write templates.")
    t = call.tx.one("SELECT * FROM templates WHERE id = %s::uuid", call.path["template_id"])
    if not t:
        raise not_found("Template")
    if call.tx.val("SELECT 1 FROM templates WHERE code = %s AND kind = %s AND status = 'draft'", t["code"], t["kind"]):
        raise ApiError(409, "draft_exists", "A draft version of this template already exists.")
    v = call.tx.val("SELECT max(version) FROM templates WHERE code = %s AND kind = %s", t["code"], t["kind"]) + 1
    nid = str(call.tx.val(
        "INSERT INTO templates (tenant_id, kind, code, version, title, title_ar, class_id, method_ref, description,"
        " standards_note, environment_note, based_on, spare_parts, suggested_interval_value, suggested_interval_unit,"
        " est_minutes, source_file_id) SELECT tenant_id, kind, code, %s, title, title_ar, class_id,"
        " method_ref, description, standards_note, environment_note, id, spare_parts, suggested_interval_value,"
        " suggested_interval_unit, est_minutes, source_file_id FROM templates WHERE id = %s RETURNING id", v, t["id"]))
    call.tx.x("INSERT INTO template_items (tenant_id, template_id, seq, item_type, parameter, nominal, unit, tol_mode,"
              " tol_minus, tol_plus, low_limit, high_limit, required, notes, page_ref) SELECT tenant_id, %s::uuid, seq, item_type,"
              " parameter, nominal, unit, tol_mode, tol_minus, tol_plus, low_limit, high_limit, required, notes, page_ref"
              " FROM template_items WHERE template_id = %s", nid, t["id"])
    return template_detail(call, nid)


# ================================================================== schedules
class ScheduleFields(BaseModel):
    template_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    interval_value: int | None = Field(default=None, ge=1, le=3650)
    interval_unit: Literal["day", "week", "month", "year"] | None = None
    basis: Literal["floating", "fixed"] | None = None
    tol_before_value: decimal.Decimal | None = Field(default=None, ge=0)
    tol_before_unit: Literal["any", "day", "week", "month", "percent"] | None = None
    tol_after_value: decimal.Decimal | None = Field(default=None, ge=0)
    tol_after_unit: Literal["day", "week", "month", "percent"] | None = None
    performer: Literal["internal", "external"] | None = None
    vendor_id: str | None = None
    responsible_user_id: str | None = None
    assignee_user_ids: list[str] | None = Field(default=None, max_length=50)
    assignee_roles: list[Literal["qa_calibration_officer", "engineering_supervisor", "engineering_manager",
                                 "maintenance_technician"]] | None = None
    certificate_required: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ScheduleIn(ScheduleFields):
    asset_ids: list[str] = Field(min_length=1, max_length=500)
    kind: Kind = "calibration"
    interval_value: int = Field(ge=1, le=3650)
    interval_unit: Literal["day", "week", "month", "year"]
    first_due: dt.date | None = None     # due date of the first task
    last_done: dt.date | None = None     # or: date of the last calibration (first due = last + interval)
    stagger_days: int = Field(default=0, ge=0, le=60)   # spread first due dates of many assets


class SchedulePatch(ScheduleFields):
    next_due: dt.date | None = None
    reason: str | None = Field(default=None, max_length=500)


class PreviewIn(BaseModel):
    kind: Kind = "calibration"
    interval_value: int = Field(ge=1, le=3650)
    interval_unit: Literal["day", "week", "month", "year"]
    basis: Literal["floating", "fixed"] = "floating"
    tol_before_value: decimal.Decimal | None = None
    tol_before_unit: Literal["any", "day", "week", "month", "percent"] | None = None
    tol_after_value: decimal.Decimal | None = None
    tol_after_unit: Literal["day", "week", "month", "percent"] | None = None
    first_due: dt.date | None = None
    last_done: dt.date | None = None


SCHEDULE_SQL = """
SELECT s.*, t.code AS template_code, t.version AS template_version, t.title AS template_title, t.status AS template_status,
       v.code AS vendor_code, v.name AS vendor_name, u.full_name AS responsible_name,
       (SELECT coalesce(json_agg(x.full_name ORDER BY x.full_name), '[]'::json) FROM users x WHERE x.id = ANY(s.assignee_user_ids)) AS assignee_names
  FROM schedules s LEFT JOIN templates t ON t.id = s.template_id LEFT JOIN vendors v ON v.id = s.vendor_id
  LEFT JOIN users u ON u.id = s.responsible_user_id
"""


def _schedule_dict(s: dict) -> dict:
    return {k: s.get(k) for k in ("kind", "interval_value", "interval_unit", "basis", "tol_before_value", "tol_before_unit",
                                  "tol_after_value", "tol_after_unit")}


def _validate_schedule(call: Call, data: dict, kind: str) -> None:
    if data.get("template_id"):
        t = call.tx.one("SELECT kind, status FROM templates WHERE id = %s::uuid", data["template_id"])
        if not t or t["kind"] != kind or t["status"] not in ("approved", "draft"):
            raise bad("template_id", "Choose a template of this kind.")
    if data.get("performer") == "external" and not data.get("vendor_id"):
        raise bad("vendor_id", "Choose the vendor that performs the work.")
    if data.get("vendor_id") and not call.tx.val("SELECT 1 FROM vendors WHERE id = %s::uuid AND active", data["vendor_id"]):
        raise bad("vendor_id", "Choose an active vendor.")
    for uid in [data.get("responsible_user_id")] + list(data.get("assignee_user_ids") or []):
        if uid and not call.tx.val("SELECT 1 FROM users WHERE id = %s::uuid AND status = 'active'", uid):
            raise bad("assignee_user_ids", "Choose active users.")
    if data.get("tol_before_unit") not in (None, "any") and data.get("tol_before_value") is None:
        raise bad("tol_before_value", "Give the early limit.")
    if data.get("tol_after_unit") and data.get("tol_after_value") is None:
        raise bad("tol_after_value", "Give the late limit.")


def create_task(call: Call, s: dict, due: dt.date) -> dict:
    """The open task for a schedule's next due date (moved off weekends and closures by the working calendar)."""
    due = work.working_day(due, settings_of(call), s["kind"])
    start, end = work.window(due, _schedule_dict(s), settings_of(call))
    prefix = "CAL" if s["kind"] == "calibration" else "PM"
    number = call.tx.val("SELECT cm_next_number(%s::uuid, %s, 4)", call.tenant_id, prefix)
    return call.tx.one(
        "INSERT INTO tasks (tenant_id, site_id, asset_id, schedule_id, kind, number, template_id, due_date, window_start,"
        " window_end, performer, vendor_id, responsible_user_id, assignee_user_ids, assignee_roles, certificate_required)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid[], %s::text[], %s) RETURNING id, number, due_date",
        call.tenant_id, s["site_id"], s["asset_id"], s["id"], s["kind"], number, s["template_id"], due, start, end,
        s["performer"], s["vendor_id"], s["responsible_user_id"], [str(x) for x in s["assignee_user_ids"] or []],
        list(s["assignee_roles"] or []), s["certificate_required"])


@endpoint(auth="tenant_user", body=PreviewIn)
def preview_schedule(call: Call):
    b: PreviewIn = call.body
    first = b.first_due or (work.add_interval(b.last_done, b.interval_value, b.interval_unit) if b.last_done else today(call))
    s = b.model_dump()
    rule = work.rule_for(s, settings_of(call))
    return {"dates": work.preview(s, settings_of(call), first, 12), "rule": rule}


@endpoint(auth="tenant_user", body=ScheduleIn)
def create_schedules(call: Call):
    b: ScheduleIn = call.body
    data = b.model_dump()
    data["performer"] = data["performer"] or "internal"
    data["basis"] = data["basis"] or ("floating" if b.kind == "calibration" else "fixed")
    _validate_schedule(call, data, b.kind)
    assets = call.tx.q("SELECT id, site_id, code, status, calibration_required, pm_required FROM assets"
                       " WHERE id = ANY(%s::uuid[]) ORDER BY code", b.asset_ids)
    if len(assets) != len(set(b.asset_ids)):
        raise not_found("Asset")
    t0 = today(call)
    created, skipped = [], []
    for i, a in enumerate(assets):
        if not can_edit_site(call, str(a["site_id"])):
            raise ApiError(403, "forbidden", f"You cannot plan work at the site of {a['code']}.")
        if a["status"] in ("retired", "disposed", "transferred"):
            skipped.append({"code": a["code"], "reason": "retired"})
            continue
        if call.tx.val("SELECT 1 FROM schedules WHERE asset_id = %s AND kind = %s AND active"
                       " AND (kind = 'calibration' OR template_id IS NOT DISTINCT FROM %s::uuid)", a["id"], b.kind, b.template_id):
            skipped.append({"code": a["code"], "reason": "exists"})
            continue
        first = b.first_due or (work.add_interval(b.last_done, b.interval_value, b.interval_unit) if b.last_done else t0)
        first = first + dt.timedelta(days=i * b.stagger_days) if b.stagger_days else first
        s = call.tx.one(
            "INSERT INTO schedules (tenant_id, site_id, asset_id, kind, template_id, title, interval_value, interval_unit,"
            " basis, tol_before_value, tol_before_unit, tol_after_value, tol_after_unit, performer, vendor_id,"
            " responsible_user_id, assignee_user_ids, assignee_roles, certificate_required, last_done, next_due, notes)"
            " VALUES (%s, %s, %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s::uuid, %s::uuid[], %s::text[],"
            " %s, %s, %s, %s) RETURNING *",
            call.tenant_id, a["site_id"], a["id"], b.kind, b.template_id, clean(b.title), b.interval_value, b.interval_unit,
            data["basis"], b.tol_before_value, b.tol_before_unit, b.tol_after_value, b.tol_after_unit, data["performer"],
            b.vendor_id, b.responsible_user_id, b.assignee_user_ids or [], b.assignee_roles or [],
            bool(b.certificate_required if b.certificate_required is not None else data["performer"] == "external"),
            b.last_done, first, clean(b.notes))
        if b.kind == "calibration" and not a["calibration_required"]:
            call.tx.x("UPDATE assets SET calibration_required = true WHERE id = %s", a["id"])
        if b.kind == "pm" and not a["pm_required"]:
            call.tx.x("UPDATE assets SET pm_required = true WHERE id = %s", a["id"])
        task = create_task(call, s, first)
        created.append({"asset_id": a["id"], "code": a["code"], "schedule_id": s["id"], "task": task})
    return {"created": created, "skipped": skipped}


@endpoint(auth="tenant_user", body=SchedulePatch)
def update_schedule(call: Call):
    sid = call.path["schedule_id"]
    s = call.tx.one("SELECT * FROM schedules WHERE id = %s::uuid", sid)
    if not s:
        raise not_found("Schedule")
    if not can_edit_site(call, str(s["site_id"])):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access at this site is required.")
    if not s["active"]:
        raise ApiError(409, "inactive", "This schedule has ended.")
    b: SchedulePatch = call.body
    data = b.model_dump(exclude_unset=True)
    reason_for_change(call, data.pop("reason", None))
    merged = {**s, **data}
    _validate_schedule(call, merged, s["kind"])
    nd = data.pop("next_due", None)
    for col in ("template_id", "vendor_id", "responsible_user_id"):
        if col in data:
            call.tx.x(f"UPDATE schedules SET {col} = %s::uuid WHERE id = %s::uuid", data.pop(col), sid)
    if "assignee_user_ids" in data:
        call.tx.x("UPDATE schedules SET assignee_user_ids = %s::uuid[] WHERE id = %s::uuid", data.pop("assignee_user_ids") or [], sid)
    if "assignee_roles" in data:
        call.tx.x("UPDATE schedules SET assignee_roles = %s::text[] WHERE id = %s::uuid", data.pop("assignee_roles") or [], sid)
    patch_sql(call, "schedules", sid, {k: (clean(v) if k in ("title", "notes") else v) for k, v in data.items()
                                       if v is not None or k in ("title", "notes", "tol_before_value", "tol_before_unit",
                                                                 "tol_after_value", "tol_after_unit")})
    s = call.tx.one("SELECT * FROM schedules WHERE id = %s::uuid", sid)
    # carry the change into the open task that has not started yet
    t = call.tx.one("SELECT * FROM tasks WHERE schedule_id = %s AND status = 'open' ORDER BY due_date LIMIT 1", sid)
    if t:
        due = work.working_day(nd or s["next_due"] or t["due_date"], settings_of(call), s["kind"])
        start, end = work.window(due, _schedule_dict(s), settings_of(call))
        call.tx.x("UPDATE tasks SET due_date = %s, window_start = %s, window_end = %s, template_id = %s, performer = %s,"
                  " vendor_id = %s, responsible_user_id = %s, assignee_user_ids = %s::uuid[], assignee_roles = %s::text[],"
                  " certificate_required = %s WHERE id = %s", due, start, end, s["template_id"], s["performer"], s["vendor_id"],
                  s["responsible_user_id"], [str(x) for x in s["assignee_user_ids"]], list(s["assignee_roles"]),
                  s["certificate_required"], t["id"])
        if nd:
            call.tx.x("UPDATE schedules SET next_due = %s WHERE id = %s::uuid", nd, sid)
    return call.tx.one(SCHEDULE_SQL + " WHERE s.id = %s::uuid", sid)


class EndIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@endpoint(auth="tenant_user", body=EndIn)
def end_schedule(call: Call):
    sid = call.path["schedule_id"]
    s = call.tx.one("SELECT * FROM schedules WHERE id = %s::uuid", sid)
    if not s:
        raise not_found("Schedule")
    if not can_edit_site(call, str(s["site_id"])):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access at this site is required.")
    call.tx.reason(call.body.reason)
    call.tx.x("UPDATE tasks SET status = 'cancelled', cancel_reason = %s WHERE schedule_id = %s::uuid AND status = 'open'",
              call.body.reason.strip(), sid)
    call.tx.x("UPDATE schedules SET active = false WHERE id = %s::uuid", sid)
    return {"ok": True}


@endpoint(auth="tenant_user")
def asset_work(call: Call):
    aid = call.path["asset_id"]
    a = call.tx.one("SELECT id, site_id FROM assets WHERE id = %s::uuid", aid)
    if not a:
        raise not_found("Asset")
    require_site(call, str(a["site_id"]))
    schedules = call.tx.q(SCHEDULE_SQL + " WHERE s.asset_id = %s::uuid ORDER BY s.active DESC, s.kind, s.created_at", aid)
    for s in schedules:
        s["rule"] = work.rule_for(s, settings_of(call))
    tasks = call.tx.q(TASK_LIST_SQL + " WHERE t.asset_id = %s::uuid ORDER BY t.due_date DESC LIMIT 60", today(call), aid)
    impacts = call.tx.q("SELECT id, number, status, period_from, period_to, risk FROM impact_assessments"
                        " WHERE asset_id = %s::uuid ORDER BY created_at DESC", aid)
    return {"schedules": schedules, "tasks": tasks, "impacts": impacts, "can_plan": can_edit_site(call, str(a["site_id"]))}


# ================================================================== tasks
TASK_LIST_SQL = """
SELECT t.id, t.number, t.kind, t.site_id, t.asset_id, t.schedule_id, t.due_date, t.window_start, t.window_end, t.status,
       t.performer, t.vendor_id, t.assigned_user_id, t.accepted_at, t.performed_date, t.verdict, t.completed_at,
       t.completed_by_name, t.reviewed_at, t.source, t.certificate_required,
       (t.status NOT IN ('completed', 'cancelled') AND t.window_end < %s) AS overdue,
       a.code AS asset_code, a.name_en AS asset_name_en, a.name_ar AS asset_name_ar, a.status AS asset_status,
       a.gmp_criticality, s.code AS site_code, l.code AS location_code, c.category,
       v.name AS vendor_name, au.full_name AS assigned_name, ru.full_name AS responsible_name,
       tp.code AS template_code, tp.title AS template_title,
       (SELECT p.status FROM plans p WHERE p.site_id = t.site_id AND p.kind = t.kind
         AND p.year = extract(year FROM t.due_date)::int) AS plan_status
  FROM tasks t JOIN assets a ON a.id = t.asset_id JOIN sites s ON s.id = t.site_id
  LEFT JOIN templates tp ON tp.id = t.template_id
  JOIN asset_classes c ON c.id = a.class_id LEFT JOIN locations l ON l.id = a.location_id
  LEFT JOIN vendors v ON v.id = t.vendor_id LEFT JOIN users au ON au.id = t.assigned_user_id
  LEFT JOIN users ru ON ru.id = t.responsible_user_id
"""


def _task_filters(call: Call, t0: dt.date) -> tuple[str, list]:
    q = call.query
    where, params = ["t.kind = %s"], [q.get("kind", "calibration")]
    sites = visible_site_ids(call)
    if sites is not None:
        where.append("t.site_id = ANY(%s::uuid[])")
        params.append(sites)
    if q.get("site_id"):
        where.append("t.site_id = %s::uuid")
        params.append(q["site_id"])
    if q.get("asset_id"):
        where.append("t.asset_id = %s::uuid")
        params.append(q["asset_id"])
    soon = t0 + dt.timedelta(days=settings_of(call)["due_soon_days"])
    state = q.get("state", "open")
    if state == "open":
        where.append("t.status NOT IN ('completed', 'cancelled')")
    elif state == "overdue":
        where.append("t.status NOT IN ('completed', 'cancelled') AND t.window_end < %s")
        params.append(t0)
    elif state == "due_soon":
        where.append("t.status NOT IN ('completed', 'cancelled') AND t.window_end >= %s AND t.due_date <= %s")
        params += [t0, soon]
    elif state == "at_vendor":
        where.append("t.status = 'at_vendor'")
    elif state == "in_progress":
        where.append("t.status = 'in_progress'")
    elif state == "completed":
        where.append("t.status = 'completed'")
    elif state == "oot":
        where.append("t.status = 'completed' AND t.verdict IN ('adjusted', 'fail')")
    elif state == "to_review":
        where.append("t.status = 'completed' AND t.reviewed_at IS NULL AND t.source = 'normal'")
    elif state == "mine":
        where.append("t.status NOT IN ('completed', 'cancelled') AND (t.assigned_user_id = %s OR %s = ANY(t.assignee_user_ids)"
                     " OR t.responsible_user_id = %s OR t.assignee_roles && %s::text[])")
        uid = str(call.user["id"])
        params += [uid, uid, uid, sorted({r["role"] for r in perm_roles(call)})]
    if q.get("q"):
        term = "%" + q["q"].strip().replace("%", r"\%")[:80] + "%"
        where.append("(t.number ILIKE %s OR a.code ILIKE %s OR a.name_en ILIKE %s)")
        params += [term, term, term]
    return " WHERE " + " AND ".join(where), params


@endpoint(auth="tenant_user")
def list_tasks(call: Call):
    t0 = today(call)
    where, params = _task_filters(call, t0)
    order = "t.completed_at DESC" if call.query.get("state") in ("completed", "oot", "to_review") else "t.window_end, t.due_date"
    try:
        page = max(1, int(call.query.get("page", "1")))
    except ValueError:
        page = 1
    total = call.tx.val("SELECT count(*) FROM tasks t JOIN assets a ON a.id = t.asset_id" + where, *params)
    rows = call.tx.q(TASK_LIST_SQL + where + f" ORDER BY {order} LIMIT 100 OFFSET %s", t0, *params, (page - 1) * 100)
    kind = call.query.get("kind", "calibration")
    sites = visible_site_ids(call)
    sw, sp = ("AND t.site_id = ANY(%s::uuid[])", [sites]) if sites is not None else ("", [])
    if call.query.get("site_id"):
        sw += " AND t.site_id = %s::uuid"
        sp.append(call.query["site_id"])
    soon = t0 + dt.timedelta(days=settings_of(call)["due_soon_days"])
    counts = call.tx.one(
        "SELECT count(*) FILTER (WHERE t.status NOT IN ('completed', 'cancelled')) AS open,"
        " count(*) FILTER (WHERE t.status NOT IN ('completed', 'cancelled') AND t.window_end < %s) AS overdue,"
        " count(*) FILTER (WHERE t.status NOT IN ('completed', 'cancelled') AND t.window_end >= %s AND t.due_date <= %s) AS due_soon,"
        " count(*) FILTER (WHERE t.status = 'at_vendor') AS at_vendor,"
        " count(*) FILTER (WHERE t.status = 'in_progress') AS in_progress,"
        " count(*) FILTER (WHERE t.status = 'completed' AND t.completed_at > now() - interval '30 days') AS completed_30,"
        " count(*) FILTER (WHERE t.status = 'completed' AND t.verdict IN ('adjusted', 'fail') AND t.completed_at > now() - interval '365 days') AS oot_12m,"
        " count(*) FILTER (WHERE t.status = 'completed' AND t.reviewed_at IS NULL AND t.source = 'normal') AS to_review"
        f" FROM tasks t WHERE t.kind = %s {sw}", t0, t0, soon, kind, *sp)
    impacts_open = call.tx.val("SELECT count(*) FROM impact_assessments WHERE status = 'draft'"
                               + (" AND site_id = ANY(%s::uuid[])" if sites is not None else ""), *([sites] if sites is not None else []))
    return {"items": rows, "total": total, "page": page, "counts": {**counts, "impacts_open": impacts_open},
            "today": t0, "due_soon_days": settings_of(call)["due_soon_days"],
            "plan_gating": features(call).get("plan_approval", True)}


def _task(call: Call, tid: str) -> dict:
    t = call.tx.one("SELECT * FROM tasks WHERE id = %s::uuid", tid)
    if not t:
        raise not_found("Task")
    require_site(call, str(t["site_id"]))
    return t


def _is_assignee(call: Call, t: dict) -> bool:
    uid = str(call.user["id"])
    roles = site_roles(call, str(t["site_id"]))
    return (str(t["assigned_user_id"] or "") == uid or uid in [str(x) for x in t["assignee_user_ids"] or []]
            or str(t["responsible_user_id"] or "") == uid or bool(roles & set(t["assignee_roles"] or [])))


def can_perform(call: Call, t: dict) -> bool:
    roles = site_roles(call, str(t["site_id"]))
    return bool(roles & PERFORMERS[t["kind"]]) or ("maintenance_technician" in roles and _is_assignee(call, t))


def plan_status(call: Call, t: dict) -> str | None:
    return call.tx.val("SELECT status FROM plans WHERE site_id = %s AND kind = %s AND year = %s",
                       t["site_id"], t["kind"], t["due_date"].year)


def released(call: Call, t: dict) -> bool:
    """With plan approval switched on, work is released when QA has approved the site's plan for the year it is due."""
    return not features(call).get("plan_approval", True) or plan_status(call, t) == "approved"


def _require_released(call: Call, t: dict) -> None:
    if not released(call, t):
        raise ApiError(409, "not_released", f"The {t['due_date'].year} {'calibration' if t['kind'] == 'calibration' else 'maintenance'}"
                       " plan of this site is not approved yet, so this task is not released.")


def needs_acceptance(call: Call, t: dict) -> bool:
    return features(call).get("task_acceptance", True) and t["accepted_at"] is None and t["performer"] == "internal"


def task_detail(call: Call, tid: str) -> dict:
    t0 = today(call)
    t = call.tx.one(TASK_LIST_SQL + " WHERE t.id = %s::uuid", t0, tid)
    if not t:
        raise not_found("Task")
    require_site(call, str(t["site_id"]))
    full = call.tx.one("SELECT * FROM tasks WHERE id = %s::uuid", tid)
    t.update({k: full[k] for k in full if k not in t})
    t["asset"] = call.tx.one(
        "SELECT a.id, a.code, a.name_en, a.name_ar, a.status, a.serial_no, a.manufacturer, a.model, a.range_min, a.range_max,"
        " a.unit, a.resolution, a.accuracy, a.gmp_criticality, a.site_id, a.location_id, a.line_id, c.name_en AS class_name_en,"
        " c.name_ar AS class_name_ar, c.category FROM assets a JOIN asset_classes c ON c.id = a.class_id WHERE a.id = %s",
        t["asset_id"])
    t["schedule"] = call.tx.one(SCHEDULE_SQL + " WHERE s.id = %s", t["schedule_id"]) if t["schedule_id"] else None
    t["template"] = template_detail(call, str(t["template_id"])) if t["template_id"] else None
    results = call.tx.q("SELECT * FROM task_results WHERE task_id = %s::uuid ORDER BY seq", tid)
    if not results and t["template"] and t["status"] not in ("completed", "cancelled"):
        results = [{"id": None, "item_id": it["id"], "seq": it["seq"], "item_type": it["item_type"], "parameter": it["parameter"],
                    "nominal": it["nominal"], "unit": it["unit"], "low_limit": it["limit_low"], "high_limit": it["limit_high"],
                    "as_found": None, "as_left": None, "uncertainty": None, "value_text": None, "ok_found": None,
                    "ok_left": None, "notes": None, "file_id": None, "required": it["required"], "page_ref": it["page_ref"],
                    "item_notes": it["notes"]} for it in t["template"]["items"]]
    elif results and t["template"]:
        info = {str(it["id"]): it for it in t["template"]["items"]}
        for r in results:
            it = info.get(str(r["item_id"] or ""))
            r["page_ref"], r["item_notes"] = (it["page_ref"], it["notes"]) if it else (None, None)
    t["results"] = results
    t["verdict_preview"] = (work.verdict(results) if t["kind"] == "calibration" else work.pm_verdict(results)) if results else None
    t["parts"] = call.tx.q("SELECT id, name, part_no, qty, unit, notes FROM task_parts WHERE task_id = %s::uuid ORDER BY created_at", tid)
    t["standards"] = call.tx.q("SELECT * FROM task_standards WHERE task_id = %s::uuid ORDER BY created_at", tid)
    t["standard_options"] = standard_options(call, str(t["site_id"]), t0)
    t["certificate"] = call.tx.one("SELECT id, filename, mime, sha256, page_count, created_at FROM files WHERE id = %s",
                                   t["certificate_file_id"]) if t["certificate_file_id"] else None
    t["previous"] = call.tx.q("SELECT id, number, performed_date, verdict, completed_by_name FROM tasks WHERE asset_id = %s"
                              " AND kind = %s AND status = 'completed' AND id <> %s ORDER BY performed_date DESC LIMIT 6",
                              t["asset_id"], t["kind"], t["id"])
    t["signature"] = call.tx.one("SELECT id, meaning, statement, user_name, user_email, signed_at, record_hash FROM signatures"
                                 " WHERE id = %s", t["signature_id"]) if t["signature_id"] else None
    t["review_signature"] = call.tx.one("SELECT id, meaning, statement, user_name, user_email, signed_at, record_hash FROM signatures"
                                        " WHERE id = %s", t["review_signature_id"]) if t["review_signature_id"] else None
    t["impact"] = call.tx.one("SELECT id, number, status FROM impact_assessments WHERE task_id = %s", tid)
    t["ai"] = call.tx.one("SELECT id, model, status, changed_fields, reviewed_by_name FROM ai_suggestions WHERE id = %s",
                          t["ai_suggestion_id"]) if t["ai_suggestion_id"] else None
    open_ = t["status"] not in ("completed", "cancelled")
    perform = can_perform(call, full)
    roles = site_roles(call, str(t["site_id"]))
    t["released"] = released(call, full) or t["status"] in ("in_progress", "at_vendor")
    rel = t["released"]
    t["perms"] = {
        "accept": open_ and rel and needs_acceptance(call, full) and (perform or _is_assignee(call, full)),
        "edit": open_ and rel and perform and not needs_acceptance(call, full),
        "complete": open_ and rel and perform and not needs_acceptance(call, full),
        "send": open_ and rel and t["performer"] == "external" and t["status"] != "at_vendor" and perform
                and t["kind"] == "calibration" and "out_for_calibration" in TRANSITIONS.get(t["asset"]["status"], ()),
        "review": (t["status"] == "completed" and t["reviewed_at"] is None and t["source"] == "normal"
                   and bool(roles & REVIEWERS[t["kind"]]) and str(full["completed_by"] or "") != str(call.user["id"])),
        "cancel": open_ and can_edit_site(call, str(t["site_id"])),
        "ai": open_ and rel and perform and t["kind"] == "calibration",
    }
    t["needs_acceptance"] = open_ and needs_acceptance(call, full)
    return t


def standard_options(call: Call, site_id: str, t0: dt.date) -> list[dict]:
    """Reference standards of the company with their own calibration status on the day."""
    rows = call.tx.q(
        "SELECT a.id, a.code, a.name_en, a.name_ar, a.status, a.site_id,"
        " (SELECT min(x.window_end) FROM tasks x WHERE x.asset_id = a.id AND x.kind = 'calibration'"
        "   AND x.status NOT IN ('completed', 'cancelled')) AS valid_until,"
        " (SELECT x.certificate_no FROM tasks x WHERE x.asset_id = a.id AND x.kind = 'calibration' AND x.status = 'completed'"
        "   ORDER BY x.performed_date DESC LIMIT 1) AS last_certificate"
        " FROM assets a JOIN asset_classes c ON c.id = a.class_id"
        " WHERE c.category = 'reference_standard' AND a.status NOT IN ('retired', 'disposed', 'transferred') ORDER BY a.code")
    for r in rows:
        r["usable"] = r["status"] in ("in_service", "restricted") and (r["valid_until"] is None or r["valid_until"] >= t0)
    return rows


@endpoint(auth="tenant_user")
def get_task(call: Call):
    return task_detail(call, call.path["task_id"])


@endpoint(auth="tenant_user")
def accept_task(call: Call):
    t = _task(call, call.path["task_id"])
    if t["status"] in ("completed", "cancelled"):
        raise ApiError(409, "closed", "This task is closed.")
    if t["accepted_at"]:
        raise ApiError(409, "accepted", f"This task was already accepted by {t['accepted_by_name']}.")
    if not (can_perform(call, t) or _is_assignee(call, t)):
        raise ApiError(403, "forbidden", "This task is not assigned to you.")
    _require_released(call, t)
    call.tx.x("UPDATE tasks SET accepted_at = now(), accepted_by_name = %s, assigned_user_id = %s WHERE id = %s",
              call.user["full_name"], call.user["id"], t["id"])
    return task_detail(call, str(t["id"]))


class SendIn(SignIn):
    note: str | None = Field(default=None, max_length=500)


@endpoint(auth="tenant_user", body=SendIn)
def send_to_vendor(call: Call):
    t = _task(call, call.path["task_id"])
    if t["status"] in ("completed", "cancelled", "at_vendor") or t["performer"] != "external":
        raise ApiError(409, "not_external", "Only an open external calibration can be sent to the vendor.")
    if not can_perform(call, t):
        raise ApiError(403, "forbidden", "The QA calibration officer or an engineering supervisor sends assets to the vendor.")
    _require_released(call, t)
    a = call.tx.one("SELECT * FROM assets WHERE id = %s", t["asset_id"])
    if "out_for_calibration" not in TRANSITIONS[a["status"]]:
        raise ApiError(422, "status_not_allowed", "This asset cannot be sent out from its current status.")
    b: SendIn = call.body
    vendor = call.tx.val("SELECT name FROM vendors WHERE id = %s", t["vendor_id"]) or ""
    signer = esign.verify_signer(call, b.email, b.password, entity="task", record_id=str(t["id"]), label=t["number"],
                                 meaning="sent_to_vendor")
    reason = (b.reason or "").strip() or f"Sent to {vendor} for calibration ({t['number']})"
    rhash = esign.record_hash({"task": t["number"], "asset": a["code"], "vendor": vendor, "reason": reason})
    sig = esign.record(call, signer=signer, entity="task", record_id=str(t["id"]), label=t["number"],
                       meaning="sent_to_vendor", rhash=rhash, reason=reason)
    call.tx.reason(reason)
    apply_status(call, a, "out_for_calibration", reason, b.note or f"At {vendor} ({t['number']})", sig["id"])
    call.tx.x("UPDATE tasks SET status = 'at_vendor', sent_at = now() WHERE id = %s", t["id"])
    return task_detail(call, str(t["id"]))


class ResultIn(BaseModel):
    item_id: str | None = None
    item_type: Literal["point", "check", "reading", "text", "photo"] = "point"
    parameter: str = Field(min_length=1, max_length=200)
    nominal: decimal.Decimal | None = None
    unit: str | None = Field(default=None, max_length=30)
    low_limit: decimal.Decimal | None = None
    high_limit: decimal.Decimal | None = None
    as_found: decimal.Decimal | None = None
    as_left: decimal.Decimal | None = None
    uncertainty: decimal.Decimal | None = Field(default=None, ge=0)
    value_text: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=500)
    file_id: str | None = None
    required: bool = True


class PartIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    part_no: str | None = Field(default=None, max_length=80)
    qty: decimal.Decimal = Field(default=decimal.Decimal(1), gt=0)
    unit: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=300)


class StandardIn(BaseModel):
    standard_asset_id: str | None = None
    description: str | None = Field(default=None, max_length=300)
    cert_ref: str | None = Field(default=None, max_length=120)
    valid_until: dt.date | None = None


class ResultsIn(BaseModel):
    performed_date: dt.date | None = None
    performed_by_name: str | None = Field(default=None, max_length=160)
    certificate_no: str | None = Field(default=None, max_length=120)
    certificate_file_id: str | None = None
    env_temperature: str | None = Field(default=None, max_length=40)
    env_humidity: str | None = Field(default=None, max_length=40)
    remarks: str | None = Field(default=None, max_length=4000)
    results: list[ResultIn] = Field(default_factory=list, max_length=300)
    standards: list[StandardIn] = Field(default_factory=list, max_length=30)
    parts: list[PartIn] = Field(default_factory=list, max_length=100)
    man_hours: decimal.Decimal | None = Field(default=None, ge=0, le=10000)


def _require_editable(call: Call, t: dict) -> None:
    if t["status"] in ("completed", "cancelled"):
        raise ApiError(409, "closed", "This task is closed.")
    if not can_perform(call, t):
        raise ApiError(403, "forbidden", "This task is not assigned to you.")
    _require_released(call, t)
    if needs_acceptance(call, t):
        raise ApiError(409, "not_accepted", "Accept the task before recording results.")


def save_results(call: Call, t: dict, b: ResultsIn) -> None:
    tid = str(t["id"])
    if b.certificate_file_id and not call.tx.val("SELECT 1 FROM files WHERE id = %s::uuid", b.certificate_file_id):
        raise bad("certificate_file_id", "Upload the certificate again.")
    if b.performed_date and b.performed_date > today(call):
        raise bad("performed_date", "The date cannot be in the future.")
    items = {str(i["id"]): i for i in call.tx.q("SELECT * FROM template_items WHERE template_id = %s", t["template_id"])} \
        if t["template_id"] else {}
    call.tx.x("UPDATE tasks SET performed_date = %s, performed_by_name = %s, certificate_no = %s, certificate_file_id = %s::uuid,"
              " env_temperature = %s, env_humidity = %s, remarks = %s, man_hours = %s,"
              " status = CASE WHEN status = 'open' THEN 'in_progress' ELSE status END WHERE id = %s::uuid",
              b.performed_date, clean(b.performed_by_name), clean(b.certificate_no), b.certificate_file_id,
              clean(b.env_temperature), clean(b.env_humidity), clean(b.remarks), b.man_hours, tid)
    call.tx.x("DELETE FROM task_results WHERE task_id = %s::uuid", tid)
    for n, r in enumerate(b.results, start=1):
        item = items.get(r.item_id or "")
        lo, hi = work.limits(item) if item else (r.low_limit, r.high_limit)
        nominal = item["nominal"] if item else r.nominal
        itype = item["item_type"] if item else r.item_type
        value = clean(r.value_text)
        if itype == "check" and value not in (None, "ok", "not_ok", "na"):
            raise bad(f"results.{n - 1}.value_text", "Answer OK, not OK or not applicable.")
        if r.file_id and not call.tx.val("SELECT 1 FROM files WHERE id = %s::uuid", r.file_id):
            raise bad(f"results.{n - 1}.file_id", "Upload the photo again.")
        ok = (value == "ok" if value in ("ok", "not_ok") else None) if itype == "check" else work.within(r.as_found, lo, hi)
        call.tx.x("INSERT INTO task_results (tenant_id, task_id, item_id, seq, item_type, parameter, nominal, unit, low_limit,"
                  " high_limit, as_found, as_left, uncertainty, value_text, ok_found, ok_left, notes, file_id, required)"
                  " VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s)",
                  call.tenant_id, tid, item["id"] if item else None, n, itype,
                  item["parameter"] if item else r.parameter.strip(), nominal, (item or {}).get("unit") or clean(r.unit),
                  lo, hi, r.as_found, r.as_left, r.uncertainty, value, ok,
                  work.within(r.as_left, lo, hi) if r.as_left is not None else None,
                  clean(r.notes), r.file_id, item["required"] if item else r.required)
    call.tx.x("DELETE FROM task_parts WHERE task_id = %s::uuid", tid)
    for p in b.parts:
        call.tx.x("INSERT INTO task_parts (tenant_id, task_id, name, part_no, qty, unit, notes) VALUES (%s, %s::uuid, %s, %s, %s, %s, %s)",
                  call.tenant_id, tid, p.name.strip(), clean(p.part_no), p.qty, clean(p.unit), clean(p.notes))
    call.tx.x("DELETE FROM task_standards WHERE task_id = %s::uuid", tid)
    for s in b.standards:
        desc, valid = clean(s.description), s.valid_until
        if s.standard_asset_id:
            a = call.tx.one("SELECT code, name_en FROM assets WHERE id = %s::uuid", s.standard_asset_id)
            if not a:
                raise bad("standards", "Choose a reference standard from the register.")
            desc = desc or f"{a['code']} · {a['name_en']}"
            valid = valid or call.tx.val("SELECT min(window_end) FROM tasks WHERE asset_id = %s::uuid AND kind = 'calibration'"
                                         " AND status NOT IN ('completed', 'cancelled')", s.standard_asset_id)
        if not desc:
            continue
        call.tx.x("INSERT INTO task_standards (tenant_id, task_id, standard_asset_id, description, cert_ref, valid_until)"
                  " VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s)", call.tenant_id, tid, s.standard_asset_id, desc,
                  clean(s.cert_ref), valid)


@endpoint(auth="tenant_user", body=ResultsIn)
def put_results(call: Call):
    t = _task(call, call.path["task_id"])
    _require_editable(call, t)
    save_results(call, t, call.body)
    return task_detail(call, str(t["id"]))


class CompleteIn(SignIn):
    ai_suggestion_id: str | None = None


@endpoint(auth="tenant_user", body=CompleteIn)
def complete_task(call: Call):
    t = _task(call, call.path["task_id"])
    _require_editable(call, t)
    b: CompleteIn = call.body
    tid = str(t["id"])
    results = call.tx.q("SELECT * FROM task_results WHERE task_id = %s::uuid ORDER BY seq", tid)
    problems = []
    if not t["performed_date"]:
        problems.append("performed_date")
    points = [r for r in results if r["item_type"] == "point"]
    cal = t["kind"] == "calibration"
    if cal and not points:
        problems.append("results")
    if any(r["as_found"] is None for r in points):
        problems.append("as_found")
    if not cal:
        if not results:
            problems.append("checklist")
        elif work.pm_verdict(results) is None:
            problems.append("checklist_open")
    if t["certificate_required"] and not t["certificate_file_id"]:
        problems.append("certificate")
    if cal and t["performer"] == "external" and not t["certificate_no"]:
        problems.append("certificate_no")
    standards = call.tx.q("SELECT ts.*, a.status AS std_status, a.code AS std_code FROM task_standards ts"
                          " LEFT JOIN assets a ON a.id = ts.standard_asset_id WHERE ts.task_id = %s::uuid", tid)
    if t["performer"] == "internal" and t["kind"] == "calibration" and not standards:
        problems.append("standards")
    for s in standards:
        if s["standard_asset_id"] and (s["std_status"] not in ("in_service", "restricted")
                                        or (s["valid_until"] and t["performed_date"] and s["valid_until"] < t["performed_date"])):
            problems.append(f"standard_invalid:{s['std_code']}")
    if problems:
        raise ApiError(422, "incomplete", "The record is not complete yet.", extra={"problems": problems})
    v = (work.verdict(results) if points else "pass") if cal else work.pm_verdict(results)
    a = call.tx.one("SELECT * FROM assets WHERE id = %s", t["asset_id"])
    meaning = MEANING[t["kind"]][0]
    parts = call.tx.q("SELECT name, part_no, qty, unit FROM task_parts WHERE task_id = %s::uuid ORDER BY created_at", tid)
    signer = esign.verify_signer(call, b.email, b.password, entity="task", record_id=tid, label=t["number"], meaning=meaning)
    rhash = esign.record_hash({
        "task": {k: t[k] for k in ("id", "number", "asset_id", "due_date", "performed_date", "performed_by_name",
                                   "certificate_no", "certificate_file_id", "env_temperature", "env_humidity", "man_hours")},
        "results": [{k: r[k] for k in ("seq", "parameter", "nominal", "unit", "low_limit", "high_limit", "as_found", "as_left",
                                       "uncertainty", "value_text", "file_id", "ok_found", "ok_left")} for r in results],
        "parts": parts,
        "standards": [{k: s[k] for k in ("description", "cert_ref", "valid_until")} for s in standards],
        "verdict": v})
    sig = esign.record(call, signer=signer, entity="task", record_id=tid, label=t["number"], meaning=meaning,
                       rhash=rhash, reason=b.reason)
    if b.ai_suggestion_id:
        from ..ai_tasks import accept_suggestion
        accept_suggestion(call, b.ai_suggestion_id, "task", tid, certificate_final(t, results))
        call.tx.x("UPDATE tasks SET ai_suggestion_id = %s::uuid WHERE id = %s::uuid", b.ai_suggestion_id, tid)
    call.tx.x("UPDATE tasks SET status = 'completed', verdict = %s, completed_at = now(), completed_by = %s,"
              " completed_by_name = %s, signature_id = %s WHERE id = %s::uuid", v, call.user["id"], call.user["full_name"],
              sig["id"], tid)
    # asset status follows the result, under the same signature
    st = settings_of(call)
    if cal and v == "fail" and st["quarantine_on_fail"] and "quarantined" in TRANSITIONS[a["status"]]:
        apply_status(call, a, "quarantined", f"Calibration {t['number']} out of tolerance", f"Out of tolerance ({t['number']})", sig["id"])
    elif a["status"] == "out_for_calibration":
        apply_status(call, a, "in_service", f"Returned from calibration {t['number']}", None, sig["id"])
    # next occurrence
    nxt = None
    if t["schedule_id"]:
        s = call.tx.one("SELECT * FROM schedules WHERE id = %s", t["schedule_id"])
        if s and s["active"]:
            due = work.next_due(s, t["performed_date"], s["next_due"] or t["due_date"])
            call.tx.x("UPDATE schedules SET last_done = %s, next_due = %s WHERE id = %s", t["performed_date"], due, s["id"])
            nxt = create_task(call, s, due)
    impact = None
    if cal and work.is_oot(v):
        impact = open_impact(call, t, a)
    out = task_detail(call, tid)
    out["next_task"] = nxt
    out["impact_created"] = impact
    return out


def certificate_final(t: dict, results: list[dict]) -> dict:
    final = {"certificate_no": t["certificate_no"], "performed_date": str(t["performed_date"]) if t["performed_date"] else None,
             "env_temperature": t["env_temperature"], "env_humidity": t["env_humidity"]}
    for r in results:
        for k in ("as_found", "as_left", "uncertainty"):
            final[f"p{r['seq']}.{k}"] = float(r[k]) if r[k] is not None else None
    return final


def open_impact(call: Call, t: dict, a: dict) -> dict:
    last_pass = call.tx.val("SELECT max(performed_date) FROM tasks WHERE asset_id = %s AND kind = %s AND status = 'completed'"
                            " AND verdict = 'pass' AND performed_date < %s", t["asset_id"], t["kind"], t["performed_date"])
    number = call.tx.val("SELECT cm_next_number(%s::uuid, 'IA', 3)", call.tenant_id)
    return call.tx.one("INSERT INTO impact_assessments (tenant_id, site_id, asset_id, task_id, number, period_from, period_to)"
                       " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, number",
                       call.tenant_id, t["site_id"], a["id"], t["id"], number, last_pass, t["performed_date"])


@endpoint(auth="tenant_user", body=SignIn)
def review_task(call: Call):
    t = _task(call, call.path["task_id"])
    if t["status"] != "completed" or t["reviewed_at"] or t["source"] != "normal":
        raise ApiError(409, "not_reviewable", "This task cannot be reviewed.")
    if not site_roles(call, str(t["site_id"])) & REVIEWERS[t["kind"]]:
        raise ApiError(403, "forbidden", "Only a QA approver or the QA calibration officer reviews calibrations." if t["kind"] == "calibration"
                       else "An engineering supervisor, the engineering manager or a QA approver checks maintenance.")
    if str(t["completed_by"]) == str(call.user["id"]):
        raise ApiError(403, "four_eyes", "The person who completed the calibration cannot review it.")
    b: SignIn = call.body
    signer = esign.verify_signer(call, b.email, b.password, entity="task", record_id=str(t["id"]), label=t["number"],
                                 meaning=MEANING[t["kind"]][1])
    completion = call.tx.val("SELECT record_hash FROM signatures WHERE id = %s", t["signature_id"])
    sig = esign.record(call, signer=signer, entity="task", record_id=str(t["id"]), label=t["number"],
                       meaning=MEANING[t["kind"]][1], rhash=esign.record_hash({"completion": completion, "task": t["number"]}),
                       reason=b.reason)
    call.tx.x("UPDATE tasks SET reviewed_at = now(), reviewed_by_name = %s, review_signature_id = %s WHERE id = %s",
              call.user["full_name"], sig["id"], t["id"])
    return task_detail(call, str(t["id"]))


class CancelIn(SignIn):
    reason: str = Field(min_length=3, max_length=500)


@endpoint(auth="tenant_user", body=CancelIn)
def cancel_task(call: Call):
    t = _task(call, call.path["task_id"])
    if t["status"] in ("completed", "cancelled"):
        raise ApiError(409, "closed", "This task is closed.")
    if not can_edit_site(call, str(t["site_id"])):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access at this site is required.")
    if t["status"] == "at_vendor":
        raise ApiError(409, "at_vendor", "The asset is at the vendor: record the results when it returns.")
    b: CancelIn = call.body
    signer = esign.verify_signer(call, b.email, b.password, entity="task", record_id=str(t["id"]), label=t["number"],
                                 meaning="task_cancelled")
    sig = esign.record(call, signer=signer, entity="task", record_id=str(t["id"]), label=t["number"], meaning="task_cancelled",
                       rhash=esign.record_hash({"task": t["number"], "due": t["due_date"], "reason": b.reason}), reason=b.reason)
    call.tx.reason(b.reason)
    call.tx.x("UPDATE tasks SET status = 'cancelled', cancel_reason = %s WHERE id = %s", b.reason.strip(), t["id"])
    nxt = None
    if t["schedule_id"]:
        s = call.tx.one("SELECT * FROM schedules WHERE id = %s", t["schedule_id"])
        if s and s["active"]:
            due = work.add_interval(s["next_due"] or t["due_date"], s["interval_value"], s["interval_unit"])
            call.tx.x("UPDATE schedules SET next_due = %s WHERE id = %s", due, s["id"])
            nxt = create_task(call, s, due)
    out = task_detail(call, str(t["id"]))
    out["next_task"] = nxt
    out["cancel_signature_id"] = sig["id"]
    return out


# ================================================================== impact assessments
IMPACT_SQL = """
SELECT i.*, a.code AS asset_code, a.name_en AS asset_name_en, a.name_ar AS asset_name_ar, t.number AS task_number,
       t.verdict, t.performed_date, s.code AS site_code
  FROM impact_assessments i JOIN assets a ON a.id = i.asset_id JOIN tasks t ON t.id = i.task_id JOIN sites s ON s.id = i.site_id
"""


@endpoint(auth="tenant_user")
def list_impacts(call: Call):
    where, params = [], []
    sites = visible_site_ids(call)
    if sites is not None:
        where.append("i.site_id = ANY(%s::uuid[])")
        params.append(sites)
    if call.query.get("status"):
        where.append("i.status = %s")
        params.append(call.query["status"])
    w = (" WHERE " + " AND ".join(where)) if where else ""
    return {"items": call.tx.q(IMPACT_SQL + w + " ORDER BY i.status, i.created_at DESC LIMIT 300", *params)}


def impact_detail(call: Call, iid: str) -> dict:
    i = call.tx.one(IMPACT_SQL + " WHERE i.id = %s::uuid", iid)
    if not i:
        raise not_found("Impact assessment")
    require_site(call, str(i["site_id"]))
    i["results"] = call.tx.q("SELECT seq, parameter, nominal, unit, low_limit, high_limit, as_found, as_left, ok_found, ok_left"
                             " FROM task_results WHERE task_id = %s ORDER BY seq", i["task_id"])
    a = call.tx.one("SELECT a.*, l.code AS location_code, l.name_en AS location_name_en, ln.code AS line_code,"
                    " ln.name_en AS line_name_en, p.code AS parent_code, p.name_en AS parent_name_en"
                    " FROM assets a LEFT JOIN locations l ON l.id = a.location_id LEFT JOIN lines ln ON ln.id = a.line_id"
                    " LEFT JOIN assets p ON p.id = a.parent_id WHERE a.id = %s", i["asset_id"])
    i["asset"] = {k: a[k] for k in ("id", "code", "name_en", "name_ar", "status", "gmp_criticality", "location_code",
                                    "location_name_en", "line_code", "line_name_en", "parent_code", "parent_name_en")}
    i["signature"] = call.tx.one("SELECT meaning, statement, user_name, user_email, signed_at, record_hash FROM signatures"
                                 " WHERE id = %s", i["approved_signature_id"]) if i["approved_signature_id"] else None
    i["ai"] = call.tx.one("SELECT id, model, status, changed_fields, reviewed_by_name FROM ai_suggestions WHERE id = %s",
                          i["ai_suggestion_id"]) if i["ai_suggestion_id"] else None
    roles = site_roles(call, str(i["site_id"]))
    i["can_edit"] = i["status"] == "draft" and bool(roles & (REVIEWERS["calibration"] | EDITORS)) or (i["status"] == "draft" and is_admin(call))
    i["can_approve"] = i["status"] == "draft" and "qa_approver" in roles
    return i


@endpoint(auth="tenant_user")
def get_impact(call: Call):
    return impact_detail(call, call.path["impact_id"])


class ImpactPatch(BaseModel):
    period_from: dt.date | None = None
    summary: str | None = Field(default=None, max_length=8000)
    scope: str | None = Field(default=None, max_length=8000)
    products: str | None = Field(default=None, max_length=8000)
    risk: Literal["low", "medium", "high"] | None = None
    actions: str | None = Field(default=None, max_length=8000)
    conclusion: str | None = Field(default=None, max_length=8000)
    ai_suggestion_id: str | None = None


IMPACT_FIELDS = ("summary", "scope", "products", "risk", "actions", "conclusion")


@endpoint(auth="tenant_user", body=ImpactPatch)
def update_impact(call: Call):
    i = impact_detail(call, call.path["impact_id"])
    if not i["can_edit"]:
        raise ApiError(403, "forbidden", "This impact assessment cannot be changed by you.")
    b: ImpactPatch = call.body
    data = b.model_dump(exclude_unset=True)
    sid = data.pop("ai_suggestion_id", None)
    patch_sql(call, "impact_assessments", str(i["id"]), {k: (clean(v) if isinstance(v, str) else v) for k, v in data.items()})
    if sid:
        from ..ai_tasks import accept_suggestion
        cur = call.tx.one("SELECT * FROM impact_assessments WHERE id = %s", i["id"])
        accept_suggestion(call, sid, "impact", str(i["id"]), {k: cur[k] for k in IMPACT_FIELDS})
        call.tx.x("UPDATE impact_assessments SET ai_suggestion_id = %s::uuid WHERE id = %s", sid, i["id"])
    return impact_detail(call, str(i["id"]))


@endpoint(auth="tenant_user", body=SignIn)
def approve_impact(call: Call):
    i = impact_detail(call, call.path["impact_id"])
    if not i["can_approve"]:
        raise ApiError(403, "forbidden", "Only a QA approver at the site approves impact assessments.")
    missing = [k for k in ("summary", "scope", "risk", "conclusion") if not i[k]]
    if missing:
        raise ApiError(422, "incomplete", "Complete the assessment before approving it.", extra={"problems": missing})
    b: SignIn = call.body
    signer = esign.verify_signer(call, b.email, b.password, entity="impact", record_id=str(i["id"]), label=i["number"],
                                 meaning="impact_approved")
    rhash = esign.record_hash({k: i[k] for k in ("id", "number", "period_from", "period_to", *IMPACT_FIELDS)})
    sig = esign.record(call, signer=signer, entity="impact", record_id=str(i["id"]), label=i["number"],
                       meaning="impact_approved", rhash=rhash, reason=b.reason)
    call.tx.x("UPDATE impact_assessments SET status = 'approved', approved_signature_id = %s, approved_at = now(),"
              " approved_by_name = %s WHERE id = %s", sig["id"], call.user["full_name"], i["id"])
    return impact_detail(call, str(i["id"]))


# ================================================================== annual plans
class PlanSignIn(SignIn):
    site_id: str
    kind: Kind
    year: int = Field(ge=2000, le=2100)


def plan_content(call: Call, site_id: str, kind: str, year: int) -> dict:
    """The year's work of a site: done and open tasks, plus the due dates the active schedules will create."""
    st = settings_of(call)
    t0 = today(call)
    tasks = call.tx.q(
        "SELECT t.id, t.number, t.schedule_id, t.asset_id, t.due_date, t.window_end, t.status, t.verdict, t.performed_date,"
        " t.created_at, (t.status NOT IN ('completed', 'cancelled') AND t.window_end < %s) AS overdue"
        " FROM tasks t WHERE t.site_id = %s::uuid AND t.kind = %s AND t.status <> 'cancelled'"
        " AND extract(year FROM t.due_date)::int = %s ORDER BY t.due_date", t0, site_id, kind, year)
    schedules = call.tx.q(SCHEDULE_SQL + " WHERE s.site_id = %s::uuid AND s.kind = %s"
                          " AND (s.active OR s.id IN (SELECT schedule_id FROM tasks WHERE site_id = %s::uuid AND kind = %s"
                          " AND extract(year FROM due_date)::int = %s))", site_id, kind, site_id, kind, year)
    assets = {str(a["id"]): a for a in call.tx.q(
        "SELECT a.id, a.code, a.name_en, a.name_ar, l.code AS location_code FROM assets a LEFT JOIN locations l ON l.id = a.location_id"
        " WHERE a.id IN (SELECT asset_id FROM schedules WHERE site_id = %s::uuid AND kind = %s"
        " UNION SELECT asset_id FROM tasks WHERE site_id = %s::uuid AND kind = %s)", site_id, kind, site_id, kind)}
    rows: dict[str, dict] = {}
    for s in schedules:
        a = assets.get(str(s["asset_id"]))
        if not a:
            continue
        rows[str(s["id"])] = {"schedule_id": s["id"], "asset_id": a["id"], "asset_code": a["code"], "asset_name_en": a["name_en"],
                              "asset_name_ar": a["name_ar"], "location_code": a["location_code"], "active": s["active"],
                              "interval_value": s["interval_value"], "interval_unit": s["interval_unit"],
                              "template_code": s["template_code"], "template_title": s["template_title"],
                              "performer": s["performer"], "vendor_name": s["vendor_name"], "entries": []}
        if s["active"] and s["next_due"]:
            d, i = s["next_due"], 0
            while d.year <= year and i < 800:
                i += 1
                d = work.add_interval(s["next_due"], i * s["interval_value"], s["interval_unit"]) \
                    if s["interval_unit"] in ("month", "year") else d + dt.timedelta(days=work.interval_days(s["interval_value"], s["interval_unit"]))
                due = work.working_day(d, st, kind)
                if due.year == year:
                    rows[str(s["id"])]["entries"].append({"due_date": due, "status": "planned"})
    for t in tasks:
        key = str(t["schedule_id"] or f"x-{t['asset_id']}")
        if key not in rows:
            a = assets.get(str(t["asset_id"]), {})
            rows[key] = {"schedule_id": t["schedule_id"], "asset_id": t["asset_id"], "asset_code": a.get("code"),
                         "asset_name_en": a.get("name_en"), "asset_name_ar": a.get("name_ar"), "location_code": a.get("location_code"),
                         "active": False, "interval_value": None, "interval_unit": None, "template_code": None,
                         "template_title": None, "performer": None, "vendor_name": None, "entries": []}
        rows[key]["entries"].append({"task_id": t["id"], "number": t["number"], "due_date": t["due_date"], "status": t["status"],
                                     "verdict": t["verdict"], "performed_date": t["performed_date"], "overdue": t["overdue"],
                                     "created_at": t["created_at"]})
    out = [r for r in rows.values() if r["entries"]]
    for r in out:
        r["entries"].sort(key=lambda e: e["due_date"])
    out.sort(key=lambda r: (r["asset_code"] or "", r["template_code"] or ""))
    digest = esign.record_hash({"site": site_id, "kind": kind, "year": year,
                                "rows": [[r["asset_code"], str(r["schedule_id"]), [str(e["due_date"]) for e in r["entries"]]] for r in out]})
    return {"rows": out, "hash": digest, "count": sum(len(r["entries"]) for r in out)}


def _plan(call: Call, site_id: str, kind: str, year: int) -> dict | None:
    return call.tx.one("SELECT * FROM plans WHERE site_id = %s::uuid AND kind = %s AND year = %s", site_id, kind, year)


def plan_view(call: Call, site_id: str, kind: str, year: int) -> dict:
    require_site(call, site_id)
    p = _plan(call, site_id, kind, year)
    c = plan_content(call, site_id, kind, year)
    roles = site_roles(call, site_id)
    status = p["status"] if p else "draft"
    if p:
        p["reviewed_signature"] = call.tx.one("SELECT user_name, user_email, signed_at, statement, record_hash FROM signatures WHERE id = %s",
                                              p["reviewed_signature_id"]) if p["reviewed_signature_id"] else None
        p["approved_signature"] = call.tx.one("SELECT user_name, user_email, signed_at, statement, record_hash FROM signatures WHERE id = %s",
                                              p["approved_signature_id"]) if p["approved_signature_id"] else None
    changed = bool(p and p["content_hash"] and p["content_hash"] != c["hash"])
    return {"plan": p, "status": status, "rows": c["rows"], "count": c["count"], "hash": c["hash"], "changed_since_signed": changed,
            "gating": features(call).get("plan_approval", True),
            "can_review": status != "approved" and bool(roles & PLAN_REVIEWERS) and c["count"] > 0,
            "can_approve": status == "reviewed" and bool(roles & PLAN_APPROVERS) and not changed,
            "site_id": site_id, "kind": kind, "year": year}


def _plan_args(call: Call) -> tuple[str, str, int]:
    q = call.query
    site_id = q.get("site_id") or ""
    if not site_id:
        raise bad("site_id", "Choose a site.")
    kind = q.get("kind", "calibration")
    if kind not in ("calibration", "pm"):
        raise bad("kind", "Choose calibration or maintenance.")
    try:
        year = int(q.get("year") or today(call).year)
    except ValueError:
        raise bad("year", "Give the year.")
    return site_id, kind, year


@endpoint(auth="tenant_user")
def get_plan(call: Call):
    return plan_view(call, *_plan_args(call))


@endpoint(auth="tenant_user", body=PlanSignIn)
def review_plan(call: Call):
    b: PlanSignIn = call.body
    v = plan_view(call, b.site_id, b.kind, b.year)
    if not v["can_review"]:
        raise ApiError(403, "forbidden", "An engineering supervisor or the engineering manager of the site reviews the plan,"
                       " once it holds work and before QA approves it.")
    site = call.tx.val("SELECT code FROM sites WHERE id = %s::uuid", b.site_id)
    label = f"{site} {b.kind} plan {b.year}"
    p = v["plan"] or call.tx.one("INSERT INTO plans (tenant_id, site_id, kind, year) VALUES (%s, %s::uuid, %s, %s) RETURNING *",
                                 call.tenant_id, b.site_id, b.kind, b.year)
    signer = esign.verify_signer(call, b.email, b.password, entity="plan", record_id=str(p["id"]), label=label, meaning="plan_reviewed")
    sig = esign.record(call, signer=signer, entity="plan", record_id=str(p["id"]), label=label, meaning="plan_reviewed",
                       rhash=v["hash"], reason=b.reason)
    call.tx.x("UPDATE plans SET status = 'reviewed', reviewed_signature_id = %s, reviewed_at = now(), reviewed_by_name = %s,"
              " content_hash = %s, task_count = %s WHERE id = %s", sig["id"], call.user["full_name"], v["hash"], v["count"], p["id"])
    return plan_view(call, b.site_id, b.kind, b.year)


@endpoint(auth="tenant_user", body=PlanSignIn)
def approve_plan(call: Call):
    b: PlanSignIn = call.body
    v = plan_view(call, b.site_id, b.kind, b.year)
    if v["changed_since_signed"]:
        raise ApiError(409, "plan_changed", "The plan changed after its review. It must be reviewed again before QA approves it.")
    if not v["can_approve"]:
        raise ApiError(403, "forbidden", "A QA approver of the site approves the plan after the supervisor's review.")
    p = v["plan"]
    if p["reviewed_signature_id"] and str(call.tx.val("SELECT user_id FROM signatures WHERE id = %s", p["reviewed_signature_id"])) \
            == str(call.user["id"]):
        raise ApiError(403, "four_eyes", "The person who reviewed the plan cannot also approve it.")
    site = call.tx.val("SELECT code FROM sites WHERE id = %s::uuid", b.site_id)
    label = f"{site} {b.kind} plan {b.year}"
    signer = esign.verify_signer(call, b.email, b.password, entity="plan", record_id=str(p["id"]), label=label, meaning="plan_approved")
    sig = esign.record(call, signer=signer, entity="plan", record_id=str(p["id"]), label=label, meaning="plan_approved",
                       rhash=v["hash"], reason=b.reason)
    call.tx.x("UPDATE plans SET status = 'approved', approved_signature_id = %s, approved_at = now(), approved_by_name = %s"
              " WHERE id = %s", sig["id"], call.user["full_name"], p["id"])
    return plan_view(call, b.site_id, b.kind, b.year)


@endpoint(auth="tenant_user")
def plan_pdf(call: Call):
    from starlette.responses import Response
    from .plan_pdf import render
    v = plan_view(call, *_plan_args(call))
    site = call.tx.one("SELECT code, name FROM sites WHERE id = %s::uuid", v["site_id"])
    company = call.tx.val("SELECT name FROM tenants WHERE id = %s", call.tenant_id) or ""
    pdf = render(v, site, company, today(call))
    call.tx.event("EXPORT", "plans", str(v["plan"]["id"]) if v["plan"] else None,
                  f"{site['code']} {v['kind']} {v['year']}", {"format": "pdf", "document": "annual plan", "status": v["status"], "hash": v["hash"]})
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="commach-{v["kind"]}-plan-{site["code"]}-{v["year"]}.pdf"'})
