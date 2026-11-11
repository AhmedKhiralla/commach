"""Connected apps: a read-only API for other Comp11 Labs products (first of all Complanning).

A company administrator issues a key (shown once). The other app then calls, with
"Authorization: Bearer <key>":
  GET /api/integration/v1/assets                 the register (code, name, site, status, availability, external_ref)
  GET /api/integration/v1/events?after=<id>      changes since the last event it has seen
Phase 6:
  events also carry calibration.planned / maintenance.planned (and .completed / .cancelled), asset.out_of_service and
  asset.back_in_service, which Complanning turns into machine downtime
  POST /api/integration/v1/production-plan       the released production plan per machine (for PM between campaigns)
  POST /api/integration/v1/meter-readings        run-hours or cycle counters per machine (for run-hours triggers)
"""
from __future__ import annotations

import hashlib
import json
import secrets

import datetime as dt
import decimal

from pydantic import BaseModel, Field

from .. import db
from ..api import ApiError, Call, endpoint, not_found


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@endpoint(auth="tenant_admin")
def list_keys(call: Call):
    return {"items": call.tx.q("SELECT id, name, prefix, last_used_at, revoked_at, created_at FROM integration_keys"
                               " ORDER BY revoked_at NULLS FIRST, created_at DESC")}


@endpoint(auth="tenant_admin", body=KeyIn)
def create_key(call: Call):
    key = "cmk_" + secrets.token_urlsafe(32)
    row = call.tx.one("INSERT INTO integration_keys (tenant_id, name, key_hash, prefix) VALUES (%s, %s, %s, %s)"
                      " RETURNING id, name, prefix, created_at", call.tenant_id, call.body.name.strip(),
                      hashlib.sha256(key.encode()).hexdigest(), key[:10])
    return {"key": key, **row}


@endpoint(auth="tenant_admin")
def revoke_key(call: Call):
    n = call.tx.x("UPDATE integration_keys SET revoked_at = now() WHERE id = %s::uuid AND revoked_at IS NULL",
                  call.path["key_id"])
    if not n:
        raise not_found("Key")
    return {"ok": True}


def _key_context(call: Call) -> str:
    auth = call.request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise ApiError(401, "no_key", "Send the integration key as 'Authorization: Bearer <key>'.")
    h = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
    call.tx.apply(db.Context(user_name="integration"))
    tid = call.tx.val("SELECT cm_integration_tenant(%s)", h)
    if not tid:
        raise ApiError(401, "bad_key", "This integration key is not valid or has been revoked.")
    call.tx.apply(db.Context(tenant_id=str(tid), user_name="integration"))
    call.tx.x("UPDATE integration_keys SET last_used_at = now() WHERE key_hash = %s", h)
    return str(tid)


@endpoint(auth=None)
def api_assets(call: Call):
    _key_context(call)
    rows = call.tx.q(
        "SELECT a.id, a.code, a.name_en, a.name_ar, s.code AS site_code, c.code AS class_code, c.category,"
        " a.status, a.status IN ('in_service', 'restricted') AS available, a.external_ref, a.parent_id, a.updated_at"
        " FROM assets a JOIN sites s ON s.id = a.site_id JOIN asset_classes c ON c.id = a.class_id ORDER BY a.code")
    return {"items": rows}


@endpoint(auth=None)
def api_events(call: Call):
    _key_context(call)
    try:
        after = int(call.query.get("after", "0"))
        limit = min(1000, max(1, int(call.query.get("limit", "500"))))
    except ValueError:
        raise ApiError(422, "invalid", "after and limit must be numbers.")
    rows = call.tx.q("SELECT id, event, entity, record_id, payload, occurred_at FROM integration_events"
                     " WHERE id > %s ORDER BY id LIMIT %s", after, limit)
    return {"items": rows, "last_id": rows[-1]["id"] if rows else after}


# ------------------------------------------------------------------ Phase 6: data from Complanning
class CampaignIn(BaseModel):
    start: dt.datetime
    end: dt.datetime
    label: str | None = Field(default=None, max_length=200)
    ref: str | None = Field(default=None, max_length=100)


class MachinePlanIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)      # ComMach asset code or its external reference
    campaigns: list[CampaignIn] = Field(default_factory=list, max_length=2000)


class PlanIn(BaseModel):
    start: dt.datetime                                  # the plan replaces everything for these machines in [start, end)
    end: dt.datetime
    machines: list[MachinePlanIn] = Field(max_length=2000)


def _machines(call: Call, codes: list[str]) -> dict[str, str]:
    rows = call.tx.q("SELECT id, code, external_ref FROM assets WHERE code = ANY(%s) OR external_ref = ANY(%s)", codes, codes)
    out: dict[str, str] = {}
    for r in rows:
        for k in (r["external_ref"], r["code"]):    # the asset code wins over another asset's external reference
            if k:
                out[k] = str(r["id"])
    return out


@endpoint(auth=None, body=PlanIn)
def api_production_plan(call: Call):
    tid = _key_context(call)
    b: PlanIn = call.body
    if b.end <= b.start or (b.end - b.start).days > 800:
        raise ApiError(422, "invalid", "end must be after start, and the plan at most about two years long.")
    found = _machines(call, [m.code for m in b.machines])
    unknown, n = [], 0
    for m in b.machines:
        aid = found.get(m.code)
        if not aid:
            unknown.append(m.code)
            continue
        call.tx.x("DELETE FROM production_windows WHERE asset_id = %s::uuid AND source = 'complanning'"
                  " AND starts_at < %s AND ends_at > %s", aid, b.end, b.start)
        for c in m.campaigns:
            if c.end <= c.start:
                continue
            call.tx.x("INSERT INTO production_windows (tenant_id, asset_id, starts_at, ends_at, label, ref)"
                      " VALUES (%s, %s, %s, %s, %s, %s)", tid, aid, max(c.start, b.start), min(c.end, b.end), c.label, c.ref)
            n += 1
    call.tx.x("SELECT cp_audit_event('IMPORT', 'production_windows', NULL, 'Complanning production plan', %s::jsonb, %s, NULL, 'integration')",
              json.dumps({"machines": len(b.machines) - len(unknown), "campaigns": n, "unknown": unknown[:50],
                          "start": b.start.isoformat(), "end": b.end.isoformat()}), tid)
    return {"machines": len(b.machines) - len(unknown), "campaigns": n, "unknown": unknown}


class ReadingIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    reading: decimal.Decimal = Field(ge=0, le=100_000_000)
    read_at: dt.datetime
    unit: str | None = Field(default=None, pattern="^(hours|cycles)$")


class ReadingsIn(BaseModel):
    readings: list[ReadingIn] = Field(max_length=5000)


@endpoint(auth=None, body=ReadingsIn)
def api_meter_readings(call: Call):
    from .. import hours, work
    tid = _key_context(call)
    b: ReadingsIn = call.body
    found = _machines(call, [r.code for r in b.readings])
    stored, skipped = 0, []
    touched: set[str] = set()
    for r in sorted(b.readings, key=lambda x: x.read_at):
        aid = found.get(r.code)
        if not aid:
            skipped.append({"code": r.code, "reason": "unknown"})
            continue
        last = call.tx.one("SELECT reading, read_at FROM meter_readings WHERE asset_id = %s::uuid ORDER BY read_at DESC,"
                           " created_at DESC LIMIT 1", aid)
        if last and (r.read_at <= last["read_at"] or r.reading < last["reading"]):
            skipped.append({"code": r.code, "reason": "not_newer" if r.read_at <= last["read_at"] else "lower"})
            continue
        if r.unit:
            call.tx.x("UPDATE assets SET meter_unit = %s WHERE id = %s::uuid AND meter_unit IS DISTINCT FROM %s", r.unit, aid, r.unit)
        call.tx.x("INSERT INTO meter_readings (tenant_id, asset_id, reading, read_at, source, entered_by_name)"
                  " VALUES (%s, %s, %s, %s, 'complanning', 'Complanning')", tid, aid, r.reading, r.read_at)
        stored += 1
        touched.add(aid)
    moved = 0
    if touched:
        st = work.effective_settings(call.tx.val("SELECT work_settings FROM tenants WHERE id = %s", tid))
        tz = call.tx.val("SELECT timezone FROM tenants WHERE id = %s", tid) or "UTC"
        day = call.tx.val("SELECT (now() AT TIME ZONE %s)::date", tz)
        for aid in touched:
            moved += hours.check(call.tx, day, st, aid)
    return {"stored": stored, "skipped": skipped, "tasks_brought_forward": moved}
