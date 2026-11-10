"""The asset register: machines, instruments, gauges, sensors, tools, reference standards, utilities,
facility and safety items, with parent/child assets, files, and a lifecycle status that changes only with
an electronic signature (one signing act can cover many assets)."""
from __future__ import annotations

import datetime as dt
import decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .. import esign
from ..api import ApiError, Call, endpoint, not_found
from ..common import bad, clean, code, patch_sql, reason_for_change
from ..perms import STATUS_SIGNERS, can_edit_site, can_sign_status, require_site, site_roles, visible_site_ids

Status = Literal["draft", "in_service", "restricted", "standby", "out_of_service", "quarantined",
                 "out_for_calibration", "retired", "disposed", "transferred"]
Criticality = Literal["critical", "major", "minor", "non_gmp"]

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("in_service", "retired"),
    "in_service": ("restricted", "standby", "out_of_service", "quarantined", "out_for_calibration", "retired", "transferred"),
    "restricted": ("in_service", "standby", "out_of_service", "quarantined", "out_for_calibration", "retired", "transferred"),
    "standby": ("in_service", "out_of_service", "quarantined", "out_for_calibration", "retired", "transferred"),
    "out_of_service": ("in_service", "standby", "quarantined", "out_for_calibration", "retired", "disposed"),
    "quarantined": ("in_service", "out_of_service", "retired", "disposed"),
    "out_for_calibration": ("in_service", "quarantined", "out_of_service"),
    "retired": ("disposed", "transferred"),
    "disposed": (),
    "transferred": (),
}
# Returning from the calibration lab is signed by the calibration officer as well as QA / engineering.
STATUS_SIGNERS_EXTRA = {"returned": {"qa_calibration_officer", "qa_approver", "engineering_manager"}}


def signer_key(old: str, new: str) -> str:
    if old == "quarantined" and new == "in_service":
        return "release"
    if old == "out_for_calibration" and new == "in_service":
        return "returned"
    return new


def meaning_for(old: str, new: str) -> str:
    return "release" if (old, new) == ("quarantined", "in_service") else new


def may_sign(call: Call, site_id: str, old: str, new: str) -> bool:
    key = signer_key(old, new)
    roles = site_roles(call, site_id)
    return bool(roles & (STATUS_SIGNERS.get(key) or STATUS_SIGNERS_EXTRA.get(key) or set()))


# ------------------------------------------------------------------ bodies
class AssetFields(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    name_ar: str | None = Field(default=None, max_length=200)
    class_id: str | None = None
    parent_id: str | None = None
    location_id: str | None = None
    current_location_id: str | None = None
    line_id: str | None = None
    department_id: str | None = None
    manufacturer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_no: str | None = Field(default=None, max_length=120)
    asset_tag: str | None = Field(default=None, max_length=60)
    capacity: str | None = Field(default=None, max_length=120)
    range_min: decimal.Decimal | None = None
    range_max: decimal.Decimal | None = None
    unit: str | None = Field(default=None, max_length=30)
    resolution: str | None = Field(default=None, max_length=60)
    accuracy: str | None = Field(default=None, max_length=60)
    gmp_criticality: Criticality | None = None
    calibration_required: bool | None = None
    pm_required: bool | None = None
    is_portable: bool | None = None
    install_date: dt.date | None = None
    warranty_until: dt.date | None = None
    supplier_vendor_id: str | None = None
    service_vendor_id: str | None = None
    photo_file_id: str | None = None
    nameplate_file_id: str | None = None
    external_ref: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=4000)


class AssetIn(AssetFields):
    site_id: str
    code: str
    name_en: str = Field(min_length=1, max_length=200)
    class_id: str
    ai_suggestion_id: str | None = None
    _c = field_validator("code")(lambda v: code(v))


class AssetPatch(AssetFields):
    reason: str | None = Field(default=None, max_length=500)


class StatusIn(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=500)
    status: Status
    reason: str = Field(min_length=3, max_length=500)
    note: str | None = Field(default=None, max_length=1000)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class FileLinkIn(BaseModel):
    file_id: str
    kind: Literal["photo", "nameplate", "manual", "certificate", "drawing", "other"]
    title: str | None = Field(default=None, max_length=200)


class FileRemoveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


NULLABLE_REFS = ("parent_id", "location_id", "current_location_id", "line_id", "department_id",
                 "supplier_vendor_id", "service_vendor_id", "photo_file_id", "nameplate_file_id")
TEXT = ("name_ar", "manufacturer", "model", "serial_no", "asset_tag", "capacity", "unit", "resolution", "accuracy",
        "external_ref", "notes")


def _check_refs(call: Call, site_id: str, data: dict, asset_id: str | None = None) -> None:
    tx = call.tx
    if data.get("class_id") and not tx.val("SELECT 1 FROM asset_classes WHERE id = %s::uuid AND active", data["class_id"]):
        raise bad("class_id", "Choose an active asset class.")
    for f in ("location_id", "current_location_id"):
        if data.get(f) and not tx.val("SELECT 1 FROM locations WHERE id = %s::uuid AND site_id = %s::uuid", data[f], site_id):
            raise bad(f, "Choose a location at the asset's site.")
    if data.get("line_id") and not tx.val("SELECT 1 FROM lines WHERE id = %s::uuid AND site_id = %s::uuid", data["line_id"], site_id):
        raise bad("line_id", "Choose a line at the asset's site.")
    if data.get("department_id") and not tx.val("SELECT 1 FROM departments WHERE id = %s::uuid", data["department_id"]):
        raise bad("department_id", "Choose a department.")
    for f in ("supplier_vendor_id", "service_vendor_id"):
        if data.get(f) and not tx.val("SELECT 1 FROM vendors WHERE id = %s::uuid", data[f]):
            raise bad(f, "Choose a vendor.")
    for f in ("photo_file_id", "nameplate_file_id"):
        if data.get(f) and not tx.val("SELECT 1 FROM files WHERE id = %s::uuid AND mime LIKE 'image/%%'", data[f]):
            raise bad(f, "Choose an uploaded photo.")
    if data.get("parent_id"):
        parent = tx.one("SELECT site_id FROM assets WHERE id = %s::uuid", data["parent_id"])
        if not parent or str(parent["site_id"]) != str(site_id):
            raise bad("parent_id", "Choose a parent asset at the same site.")
        if asset_id:
            below = {r["id"] for r in tx.q(
                "WITH RECURSIVE d AS (SELECT id FROM assets WHERE id = %s::uuid"
                " UNION ALL SELECT a.id FROM assets a JOIN d ON a.parent_id = d.id) SELECT id::text AS id FROM d", asset_id)}
            if data["parent_id"] in below:
                raise bad("parent_id", "An asset cannot be placed inside itself or one of its components.")
    if data.get("range_min") is not None and data.get("range_max") is not None and data["range_min"] > data["range_max"]:
        raise bad("range_max", "The upper limit must not be below the lower limit.")


# ------------------------------------------------------------------ list
LIST_SQL = """
SELECT a.id, a.site_id, a.code, a.name_en, a.name_ar, a.status, a.gmp_criticality, a.calibration_required,
       a.pm_required, a.is_portable, a.manufacturer, a.model, a.serial_no, a.parent_id, a.location_id, a.line_id,
       a.department_id, a.photo_file_id, a.updated_at, a.status_changed_at,
       c.code AS class_code, c.name_en AS class_name_en, c.name_ar AS class_name_ar, c.category,
       l.code AS location_code, l.name_en AS location_name_en, l.name_ar AS location_name_ar, l.kind AS location_kind,
       p.code AS parent_code, s.code AS site_code, ln.code AS line_code, d.code AS department_code,
       (SELECT count(*) FROM assets ch WHERE ch.parent_id = a.id) AS child_count,
       (SELECT min(t.due_date) FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'calibration'
         AND t.status NOT IN ('completed', 'cancelled')) AS cal_due,
       (SELECT bool_or(t.window_end < current_date) FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'calibration'
         AND t.status NOT IN ('completed', 'cancelled')) AS cal_overdue,
       (SELECT min(t.due_date) FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'pm'
         AND t.status NOT IN ('completed', 'cancelled')) AS pm_due,
       (SELECT max(t.performed_date) FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'calibration'
         AND t.status = 'completed') AS cal_last
  FROM assets a
  JOIN asset_classes c ON c.id = a.class_id
  JOIN sites s ON s.id = a.site_id
  LEFT JOIN locations l ON l.id = a.location_id
  LEFT JOIN assets p ON p.id = a.parent_id
  LEFT JOIN lines ln ON ln.id = a.line_id
  LEFT JOIN departments d ON d.id = a.department_id
"""


def _filters(call: Call) -> tuple[str, list]:
    q = call.query
    where, params = [], []
    sites = visible_site_ids(call)
    if sites is not None:
        where.append("a.site_id = ANY(%s::uuid[])")
        params.append(sites)
    if q.get("site_id"):
        where.append("a.site_id = %s::uuid")
        params.append(q["site_id"])
    if q.get("location_id"):
        where.append("a.location_id IN (WITH RECURSIVE d AS (SELECT id FROM locations WHERE id = %s::uuid"
                     " UNION ALL SELECT l2.id FROM locations l2 JOIN d ON l2.parent_id = d.id) SELECT id FROM d)")
        params.append(q["location_id"])
    for key, col in (("class_id", "a.class_id"), ("department_id", "a.department_id"), ("line_id", "a.line_id"),
                     ("parent_id", "a.parent_id"), ("group_id", "s.group_id")):
        if q.get(key):
            where.append(f"{col} = %s::uuid")
            params.append(q[key])
    if q.get("category"):
        where.append("c.category = %s")
        params.append(q["category"])
    if q.get("status"):
        where.append("a.status = ANY(%s::text[])")
        params.append(q["status"].split(","))
    elif q.get("include_retired") != "1":
        where.append("a.status NOT IN ('retired', 'disposed', 'transferred')")
    if q.get("criticality"):
        where.append("a.gmp_criticality = %s")
        params.append(q["criticality"])
    if q.get("calibration") == "1":
        where.append("a.calibration_required")
    if q.get("pm") == "1":
        where.append("a.pm_required")
    if q.get("top") == "1":
        where.append("a.parent_id IS NULL")
    if q.get("unplaced") == "1":
        where.append("a.location_id IS NULL")
    if q.get("q"):
        term = "%" + q["q"].strip().replace("%", r"\%").replace("_", r"\_")[:100] + "%"
        where.append("(a.code ILIKE %s OR a.name_en ILIKE %s OR coalesce(a.name_ar, '') ILIKE %s"
                     " OR coalesce(a.serial_no, '') ILIKE %s OR coalesce(a.model, '') ILIKE %s"
                     " OR coalesce(a.manufacturer, '') ILIKE %s OR coalesce(a.asset_tag, '') ILIKE %s)")
        params += [term] * 7
    return (" WHERE " + " AND ".join(where)) if where else "", params


@endpoint(auth="tenant_user")
def list_assets(call: Call):
    where, params = _filters(call)
    try:
        page = max(1, int(call.query.get("page", "1")))
        size = min(500, max(10, int(call.query.get("page_size", "50"))))
    except ValueError:
        raise ApiError(422, "invalid", "Page must be a number.")
    sort = {"code": "a.code", "name": "a.name_en", "status": "a.status, a.code", "class": "c.code, a.code",
            "location": "l.code NULLS LAST, a.code", "updated": "a.updated_at DESC",
            "cal_due": "(SELECT min(t.due_date) FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'calibration' AND t.status NOT IN ('completed', 'cancelled')) NULLS LAST, a.code"}.get(call.query.get("sort", "code"), "a.code")
    total = call.tx.val("SELECT count(*) FROM assets a JOIN asset_classes c ON c.id = a.class_id"
                        " JOIN sites s ON s.id = a.site_id LEFT JOIN locations l ON l.id = a.location_id" + where, *params)
    rows = call.tx.q(LIST_SQL + where + f" ORDER BY {sort} LIMIT %s OFFSET %s", *params, size, (page - 1) * size)
    return {"items": rows, "total": total, "page": page, "page_size": size}


# ------------------------------------------------------------------ detail
def _location_path(call: Call, location_id) -> list[dict]:
    if not location_id:
        return []
    return call.tx.q(
        "WITH RECURSIVE up AS (SELECT id, parent_id, kind, code, name_en, name_ar, 0 AS depth FROM locations WHERE id = %s::uuid"
        " UNION ALL SELECT l.id, l.parent_id, l.kind, l.code, l.name_en, l.name_ar, up.depth + 1"
        " FROM locations l JOIN up ON l.id = up.parent_id)"
        " SELECT id, kind, code, name_en, name_ar FROM up ORDER BY depth DESC", str(location_id))


def asset_detail(call: Call, aid: str) -> dict:
    a = call.tx.one(
        "SELECT a.*, c.code AS class_code, c.name_en AS class_name_en, c.name_ar AS class_name_ar, c.category,"
        "       s.code AS site_code, s.name AS site_name, g.name AS group_name,"
        "       p.code AS parent_code, p.name_en AS parent_name_en, p.name_ar AS parent_name_ar,"
        "       ln.code AS line_code, ln.name_en AS line_name_en, ln.name_ar AS line_name_ar,"
        "       d.code AS department_code, d.name_en AS department_name_en, d.name_ar AS department_name_ar,"
        "       sv.code AS supplier_code, sv.name AS supplier_name, vv.code AS service_vendor_code, vv.name AS service_vendor_name,"
        "       cl.code AS current_location_code, cl.name_en AS current_location_name_en, cl.name_ar AS current_location_name_ar"
        "  FROM assets a JOIN asset_classes c ON c.id = a.class_id JOIN sites s ON s.id = a.site_id"
        "  LEFT JOIN site_groups g ON g.id = s.group_id"
        "  LEFT JOIN assets p ON p.id = a.parent_id LEFT JOIN lines ln ON ln.id = a.line_id"
        "  LEFT JOIN departments d ON d.id = a.department_id LEFT JOIN vendors sv ON sv.id = a.supplier_vendor_id"
        "  LEFT JOIN vendors vv ON vv.id = a.service_vendor_id LEFT JOIN locations cl ON cl.id = a.current_location_id"
        " WHERE a.id = %s::uuid", aid)
    if not a:
        raise not_found("Asset")
    sid = str(a["site_id"])
    require_site(call, sid)
    a["location_path"] = _location_path(call, a["location_id"])
    a["children"] = call.tx.q(
        "SELECT a.id, a.code, a.name_en, a.name_ar, a.status, a.calibration_required, a.pm_required,"
        " c.code AS class_code, c.name_en AS class_name_en, c.name_ar AS class_name_ar, c.category"
        " FROM assets a JOIN asset_classes c ON c.id = a.class_id WHERE a.parent_id = %s::uuid ORDER BY a.code", aid)
    a["history"] = call.tx.q(
        "SELECT h.id, h.from_status, h.to_status, h.reason, h.source, h.user_name, h.changed_at,"
        " s.meaning, s.statement, s.user_email, s.record_hash, s.signed_at"
        " FROM asset_status_history h LEFT JOIN signatures s ON s.id = h.signature_id"
        " WHERE h.asset_id = %s::uuid ORDER BY h.id DESC", aid)
    a["files"] = call.tx.q(
        "SELECT af.id, af.kind, af.title, af.created_at, f.id AS file_id, f.filename, f.mime, f.size_bytes, f.sha256,"
        " f.page_count, u.full_name AS added_by"
        " FROM asset_files af JOIN files f ON f.id = af.file_id LEFT JOIN users u ON u.id = af.created_by"
        " WHERE af.asset_id = %s::uuid AND af.removed_at IS NULL ORDER BY af.created_at DESC", aid)
    a["ai"] = call.tx.q(
        "SELECT id, kind, model, prompt_version, status, changed_fields, reviewed_by_name, reviewed_at, created_at, created_by_name"
        " FROM ai_suggestions WHERE record_entity = 'asset' AND record_id = %s::uuid ORDER BY created_at DESC", aid)
    a["can_edit"] = can_edit_site(call, sid) and a["status"] not in ("disposed", "transferred")
    a["transitions"] = [{"status": s, "can_sign": may_sign(call, sid, a["status"], s)} for s in TRANSITIONS[a["status"]]]
    return a


@endpoint(auth="tenant_user")
def get_asset(call: Call):
    return asset_detail(call, call.path["asset_id"])


@endpoint(auth="tenant_user")
def find_by_code(call: Call):
    row = call.tx.one("SELECT id, site_id FROM assets WHERE lower(code) = lower(%s)", call.path["code"])
    if not row:
        raise not_found("Asset")
    require_site(call, str(row["site_id"]))
    return {"id": row["id"]}


# ------------------------------------------------------------------ create / update
def _values(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k in TEXT:
            out[k] = clean(v)
        elif k == "name_en":
            out[k] = v.strip()
        else:
            out[k] = v
    return out


@endpoint(auth="tenant_user", body=AssetIn)
def create_asset(call: Call):
    b: AssetIn = call.body
    require_site(call, b.site_id, edit=True)
    if call.tx.val("SELECT 1 FROM assets WHERE lower(code) = lower(%s)", b.code):
        raise bad("code", "This code is already used.")
    data = b.model_dump(exclude={"site_id", "code", "ai_suggestion_id"})
    cls = call.tx.one("SELECT calibration_default, pm_default FROM asset_classes WHERE id = %s::uuid", b.class_id)
    if not cls:
        raise bad("class_id", "Choose an active asset class.")
    if data["calibration_required"] is None:
        data["calibration_required"] = cls["calibration_default"]
    if data["pm_required"] is None:
        data["pm_required"] = cls["pm_default"]
    data["is_portable"] = bool(data["is_portable"])
    data["gmp_criticality"] = data["gmp_criticality"] or "major"
    _check_refs(call, b.site_id, data)
    vals = _values(data)
    cols = ["tenant_id", "site_id", "code"] + list(vals)
    row = call.tx.one(f"INSERT INTO assets ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                      call.tenant_id, b.site_id, b.code, *vals.values())
    aid = str(row["id"])
    if b.ai_suggestion_id:
        from ..ai_tasks import accept_suggestion
        accept_suggestion(call, b.ai_suggestion_id, "asset", aid, {"code": b.code, **{k: v for k, v in vals.items()}})
    for f, kind in (("photo_file_id", "photo"), ("nameplate_file_id", "nameplate")):
        if vals.get(f):
            call.tx.x("INSERT INTO asset_files (tenant_id, asset_id, file_id, kind) VALUES (%s, %s::uuid, %s::uuid, %s)",
                      call.tenant_id, aid, vals[f], kind)
    return asset_detail(call, aid)


@endpoint(auth="tenant_user", body=AssetPatch)
def update_asset(call: Call):
    aid = call.path["asset_id"]
    a = call.tx.one("SELECT * FROM assets WHERE id = %s::uuid", aid)
    if not a:
        raise not_found("Asset")
    sid = str(a["site_id"])
    require_site(call, sid, edit=True)
    if a["status"] in ("disposed", "transferred"):
        raise ApiError(422, "closed", "This asset has left the site; its record can no longer be changed.")
    b: AssetPatch = call.body
    data = b.model_dump(exclude_unset=True)
    reason_for_change(call, data.pop("reason", None))
    for k in list(data):
        if data[k] is None and k not in NULLABLE_REFS and k not in TEXT and k not in (
                "range_min", "range_max", "install_date", "warranty_until"):
            data.pop(k)  # required columns: None means "leave as it is"
    _check_refs(call, sid, {**{k: a[k] for k in ("range_min", "range_max")}, **data}, asset_id=aid)
    vals = _values(data)
    for k in NULLABLE_REFS:
        if k in vals:
            call.tx.x(f"UPDATE assets SET {k} = %s::uuid WHERE id = %s::uuid", vals.pop(k), aid)
    patch_sql(call, "assets", aid, vals)
    for f, kind in (("photo_file_id", "photo"), ("nameplate_file_id", "nameplate")):
        fid = data.get(f)
        if fid and not call.tx.val("SELECT 1 FROM asset_files WHERE asset_id = %s::uuid AND file_id = %s::uuid", aid, fid):
            call.tx.x("INSERT INTO asset_files (tenant_id, asset_id, file_id, kind) VALUES (%s, %s::uuid, %s::uuid, %s)",
                      call.tenant_id, aid, fid, kind)
    return asset_detail(call, aid)


# ------------------------------------------------------------------ status (signed)
def signed_fields(a: dict) -> dict:
    keep = ("id", "code", "name_en", "class_id", "site_id", "location_id", "manufacturer", "model", "serial_no",
            "range_min", "range_max", "unit", "gmp_criticality", "calibration_required", "pm_required", "status")
    return {k: a[k] for k in keep}


def apply_status(call: Call, a: dict, status: str, reason: str, note: str | None, signature_id) -> None:
    """Change an asset's status under a signature that was just recorded (caller sets app.status_change)."""
    call.tx.x("SELECT set_config('app.status_change', 'signed', true)")
    call.tx.x("UPDATE assets SET status = %s, status_note = %s WHERE id = %s", status, clean(note), a["id"])
    call.tx.x("INSERT INTO asset_status_history (tenant_id, asset_id, from_status, to_status, reason, source,"
              " signature_id, user_id, user_name) VALUES (%s, %s, %s, %s, %s, 'signature', %s, %s, %s)",
              call.tenant_id, a["id"], a["status"], status, reason.strip(), signature_id, call.user["id"],
              call.user["full_name"])
    call.tx.x("SELECT set_config('app.status_change', '', true)")


@endpoint(auth="tenant_user", body=StatusIn)
def change_status(call: Call):
    b: StatusIn = call.body
    ids = list(dict.fromkeys(b.asset_ids))
    assets = call.tx.q("SELECT * FROM assets WHERE id = ANY(%s::uuid[]) ORDER BY code", ids)
    if len(assets) != len(ids):
        raise not_found("Asset")
    problems = []
    for a in assets:
        sid = str(a["site_id"])
        require_site(call, sid)
        if b.status not in TRANSITIONS[a["status"]]:
            problems.append({"code": a["code"], "problem": "transition", "from": a["status"]})
        elif not may_sign(call, sid, a["status"], b.status):
            problems.append({"code": a["code"], "problem": "role", "from": a["status"]})
    if problems:
        raise ApiError(422, "status_not_allowed", "This change cannot be signed for some of the selected assets.",
                       extra={"problems": problems})
    label = assets[0]["code"] if len(assets) == 1 else f"{len(assets)} assets"
    meanings = {meaning_for(a["status"], b.status) for a in assets}
    signer = esign.verify_signer(call, b.email, b.password, entity="asset", record_id=str(assets[0]["id"]), label=label,
                                 meaning=", ".join(sorted(meanings)))
    call.tx.reason(b.reason)
    call.tx.x("SELECT set_config('app.status_change', 'signed', true)")
    out = []
    for a in assets:
        meaning = meaning_for(a["status"], b.status)
        rhash = esign.record_hash({"asset": signed_fields(a), "to": b.status, "reason": b.reason.strip(),
                                   "note": (b.note or "").strip()})
        sig = esign.record(call, signer=signer, entity="asset", record_id=str(a["id"]), label=a["code"],
                           meaning=meaning, rhash=rhash, reason=b.reason)
        apply_status(call, a, b.status, b.reason, b.note, sig["id"])
        out.append({"id": a["id"], "code": a["code"], "from": a["status"], "to": b.status, "signature_id": sig["id"]})
    call.tx.x("SELECT set_config('app.status_change', '', true)")
    return {"changed": out}


@endpoint(auth="tenant_user")
def status_options(call: Call):
    """Statuses the caller can sign for every selected asset (?ids=a,b,c)."""
    ids = [x for x in (call.query.get("ids") or "").split(",") if x][:500]
    assets = call.tx.q("SELECT id, site_id, status FROM assets WHERE id = ANY(%s::uuid[])", ids)
    options = None
    for a in assets:
        require_site(call, str(a["site_id"]))
        ok = {s for s in TRANSITIONS[a["status"]] if may_sign(call, str(a["site_id"]), a["status"], s)}
        options = ok if options is None else options & ok
    return {"statuses": sorted(options or [])}


# ------------------------------------------------------------------ files
@endpoint(auth="tenant_user", body=FileLinkIn)
def add_file(call: Call):
    aid = call.path["asset_id"]
    a = call.tx.one("SELECT site_id FROM assets WHERE id = %s::uuid", aid)
    if not a:
        raise not_found("Asset")
    require_site(call, str(a["site_id"]), edit=True)
    b: FileLinkIn = call.body
    if not call.tx.val("SELECT 1 FROM files WHERE id = %s::uuid", b.file_id):
        raise not_found("File")
    call.tx.x("INSERT INTO asset_files (tenant_id, asset_id, file_id, kind, title) VALUES (%s, %s::uuid, %s::uuid, %s, %s)",
              call.tenant_id, aid, b.file_id, b.kind, clean(b.title))
    if b.kind in ("photo", "nameplate"):
        col = "photo_file_id" if b.kind == "photo" else "nameplate_file_id"
        if call.tx.val("SELECT mime LIKE 'image/%%' FROM files WHERE id = %s::uuid", b.file_id):
            call.tx.x(f"UPDATE assets SET {col} = %s::uuid WHERE id = %s::uuid AND {col} IS NULL", b.file_id, aid)
    return asset_detail(call, aid)


@endpoint(auth="tenant_user", body=FileRemoveIn)
def remove_file(call: Call):
    link = call.tx.one("SELECT af.*, a.site_id FROM asset_files af JOIN assets a ON a.id = af.asset_id"
                       " WHERE af.id = %s::uuid AND af.removed_at IS NULL", call.path["link_id"])
    if not link:
        raise not_found("File")
    require_site(call, str(link["site_id"]), edit=True)
    call.tx.reason(call.body.reason)
    call.tx.x("UPDATE asset_files SET removed_at = now(), removed_reason = %s WHERE id = %s",
              call.body.reason.strip(), link["id"])
    for col in ("photo_file_id", "nameplate_file_id"):
        call.tx.x(f"UPDATE assets SET {col} = NULL WHERE id = %s AND {col} = %s", link["asset_id"], link["file_id"])
    return asset_detail(call, str(link["asset_id"]))
