"""Company-wide lists: departments, asset classes and vendors (calibration labs, service providers)."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..api import Call, endpoint, not_found
from ..common import bad, clean, code, patch_sql, reason_for_change
from ..perms import require_master_edit

CATEGORIES = ("equipment", "instrument", "gauge", "sensor", "tool", "reference_standard", "utility", "facility", "safety")
Category = Literal["equipment", "instrument", "gauge", "sensor", "tool", "reference_standard", "utility", "facility", "safety"]


def _patch(call: Call, table: str, rid: str, body: BaseModel, nullable: tuple[str, ...] = ()) -> None:
    data = body.model_dump(exclude_unset=True)
    reason_for_change(call, data.pop("reason", None))
    vals = {}
    for k, v in data.items():
        if k in nullable:
            vals[k] = clean(v) if isinstance(v, str) else v
        elif v is not None:
            vals[k] = v.strip() if isinstance(v, str) else v
    patch_sql(call, table, rid, vals)


# ------------------------------------------------------------------ departments
class DeptIn(BaseModel):
    code: str
    name_en: str = Field(min_length=1, max_length=120)
    name_ar: str | None = Field(default=None, max_length=120)
    _c = field_validator("code")(lambda v: code(v, upper=True, max_len=20))


class DeptPatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=120)
    name_ar: str | None = Field(default=None, max_length=120)
    active: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


DEPT_SQL = ("SELECT d.id, d.code, d.name_en, d.name_ar, d.active,"
            " (SELECT count(*) FROM assets a WHERE a.department_id = d.id) AS asset_count FROM departments d")


@endpoint(auth="tenant_user")
def list_departments(call: Call):
    return {"items": call.tx.q(DEPT_SQL + " ORDER BY d.active DESC, d.code")}


@endpoint(auth="tenant_user", body=DeptIn)
def create_department(call: Call):
    require_master_edit(call)
    b: DeptIn = call.body
    if call.tx.val("SELECT 1 FROM departments WHERE code = %s", b.code):
        raise bad("code", "This code is already used.")
    row = call.tx.one("INSERT INTO departments (tenant_id, code, name_en, name_ar) VALUES (%s, %s, %s, %s) RETURNING id",
                      call.tenant_id, b.code, b.name_en.strip(), clean(b.name_ar))
    return call.tx.one(DEPT_SQL + " WHERE d.id = %s", row["id"])


@endpoint(auth="tenant_user", body=DeptPatch)
def update_department(call: Call):
    require_master_edit(call)
    did = call.path["dept_id"]
    if not call.tx.val("SELECT 1 FROM departments WHERE id = %s::uuid", did):
        raise not_found("Department")
    _patch(call, "departments", did, call.body, nullable=("name_ar",))
    return call.tx.one(DEPT_SQL + " WHERE d.id = %s::uuid", did)


# ------------------------------------------------------------------ asset classes
class ClassIn(BaseModel):
    code: str
    name_en: str = Field(min_length=1, max_length=120)
    name_ar: str | None = Field(default=None, max_length=120)
    category: Category
    calibration_default: bool = False
    pm_default: bool = False
    description: str | None = Field(default=None, max_length=1000)
    _c = field_validator("code")(lambda v: code(v, upper=True, max_len=20))


class ClassPatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=120)
    name_ar: str | None = Field(default=None, max_length=120)
    category: Category | None = None
    calibration_default: bool | None = None
    pm_default: bool | None = None
    description: str | None = Field(default=None, max_length=1000)
    active: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


CLASS_SQL = ("SELECT c.id, c.code, c.name_en, c.name_ar, c.category, c.calibration_default, c.pm_default,"
             " c.description, c.active, (SELECT count(*) FROM assets a WHERE a.class_id = c.id) AS asset_count"
             " FROM asset_classes c")


@endpoint(auth="tenant_user")
def list_classes(call: Call):
    return {"items": call.tx.q(CLASS_SQL + " ORDER BY c.active DESC, c.category, c.code"), "categories": CATEGORIES}


@endpoint(auth="tenant_user", body=ClassIn)
def create_class(call: Call):
    require_master_edit(call)
    b: ClassIn = call.body
    if call.tx.val("SELECT 1 FROM asset_classes WHERE code = %s", b.code):
        raise bad("code", "This code is already used.")
    row = call.tx.one(
        "INSERT INTO asset_classes (tenant_id, code, name_en, name_ar, category, calibration_default, pm_default, description)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        call.tenant_id, b.code, b.name_en.strip(), clean(b.name_ar), b.category, b.calibration_default, b.pm_default,
        clean(b.description))
    return call.tx.one(CLASS_SQL + " WHERE c.id = %s", row["id"])


@endpoint(auth="tenant_user", body=ClassPatch)
def update_class(call: Call):
    require_master_edit(call)
    cid = call.path["class_id"]
    if not call.tx.val("SELECT 1 FROM asset_classes WHERE id = %s::uuid", cid):
        raise not_found("Asset class")
    _patch(call, "asset_classes", cid, call.body, nullable=("name_ar", "description"))
    return call.tx.one(CLASS_SQL + " WHERE c.id = %s::uuid", cid)


# ------------------------------------------------------------------ vendors
VendorKind = Literal["calibration_lab", "service_provider", "manufacturer", "supplier"]


class ContactIn(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    position: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=254)
    is_primary: bool = False
    active: bool = True


class VendorIn(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=160)
    kind: VendorKind = "calibration_lab"
    accreditation_body: str | None = Field(default=None, max_length=120)
    accreditation_no: str | None = Field(default=None, max_length=80)
    accreditation_expiry: dt.date | None = None
    scope: str | None = Field(default=None, max_length=2000)
    address: str | None = Field(default=None, max_length=400)
    phone: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=254)
    website: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    contacts: list[ContactIn] = Field(default_factory=list, max_length=30)
    _c = field_validator("code")(lambda v: code(v, upper=True, max_len=20))


class VendorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    kind: VendorKind | None = None
    accreditation_body: str | None = Field(default=None, max_length=120)
    accreditation_no: str | None = Field(default=None, max_length=80)
    accreditation_expiry: dt.date | None = None
    scope: str | None = Field(default=None, max_length=2000)
    address: str | None = Field(default=None, max_length=400)
    phone: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=254)
    website: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    active: bool | None = None
    contacts: list[ContactIn] | None = Field(default=None, max_length=30)
    reason: str | None = Field(default=None, max_length=500)


VENDOR_SQL = """
SELECT v.id, v.code, v.name, v.kind, v.accreditation_body, v.accreditation_no, v.accreditation_expiry, v.scope,
       v.address, v.phone, v.email, v.website, v.notes, v.active,
       (v.accreditation_expiry IS NOT NULL AND v.accreditation_expiry < current_date) AS accreditation_expired,
       coalesce((SELECT json_agg(json_build_object('id', c.id, 'name', c.name, 'position', c.position, 'phone', c.phone,
                                                   'email', c.email, 'is_primary', c.is_primary, 'active', c.active)
                                 ORDER BY c.is_primary DESC, c.name)
                   FROM vendor_contacts c WHERE c.vendor_id = v.id), '[]'::json) AS contacts,
       (SELECT count(*) FROM assets a WHERE a.service_vendor_id = v.id OR a.supplier_vendor_id = v.id) AS asset_count
  FROM vendors v
"""


def _contacts(call: Call, vid: str, contacts: list[ContactIn]) -> None:
    if sum(1 for c in contacts if c.is_primary and c.active) > 1:
        raise bad("contacts", "Mark only one primary contact.")
    current = {str(r["id"]) for r in call.tx.q("SELECT id FROM vendor_contacts WHERE vendor_id = %s::uuid", vid)}
    seen = set()
    for c in contacts:
        vals = (c.name.strip(), clean(c.position), clean(c.phone), clean((c.email or "").lower()), c.is_primary, c.active)
        if c.id and c.id in current:
            seen.add(c.id)
            call.tx.x("UPDATE vendor_contacts SET name = %s, position = %s, phone = %s, email = %s, is_primary = %s,"
                      " active = %s WHERE id = %s::uuid", *vals, c.id)
        else:
            call.tx.x("INSERT INTO vendor_contacts (tenant_id, vendor_id, name, position, phone, email, is_primary, active)"
                      " VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s)", call.tenant_id, vid, *vals)
    # Contacts are never deleted (they may be named on past records); missing ones become inactive.
    for cid in current - seen:
        call.tx.x("UPDATE vendor_contacts SET active = false, is_primary = false WHERE id = %s::uuid", cid)


@endpoint(auth="tenant_user")
def list_vendors(call: Call):
    return {"items": call.tx.q(VENDOR_SQL + " ORDER BY v.active DESC, v.name")}


@endpoint(auth="tenant_user", body=VendorIn)
def create_vendor(call: Call):
    require_master_edit(call)
    b: VendorIn = call.body
    if call.tx.val("SELECT 1 FROM vendors WHERE code = %s", b.code):
        raise bad("code", "This code is already used.")
    row = call.tx.one(
        "INSERT INTO vendors (tenant_id, code, name, kind, accreditation_body, accreditation_no, accreditation_expiry,"
        " scope, address, phone, email, website, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id", call.tenant_id, b.code, b.name.strip(), b.kind, clean(b.accreditation_body),
        clean(b.accreditation_no), b.accreditation_expiry, clean(b.scope), clean(b.address), clean(b.phone),
        clean((b.email or "").lower()), clean(b.website), clean(b.notes))
    _contacts(call, str(row["id"]), b.contacts)
    return call.tx.one(VENDOR_SQL + " WHERE v.id = %s", row["id"])


@endpoint(auth="tenant_user", body=VendorPatch)
def update_vendor(call: Call):
    require_master_edit(call)
    vid = call.path["vendor_id"]
    if not call.tx.val("SELECT 1 FROM vendors WHERE id = %s::uuid", vid):
        raise not_found("Vendor")
    b: VendorPatch = call.body
    contacts = b.contacts
    b.contacts = None  # handled below; _patch skips None values outside the nullable list
    _patch(call, "vendors", vid, b,
           nullable=("accreditation_body", "accreditation_no", "accreditation_expiry", "scope", "address", "phone",
                     "email", "website", "notes"))
    if contacts is not None:
        _contacts(call, vid, contacts)
    return call.tx.one(VENDOR_SQL + " WHERE v.id = %s::uuid", vid)
