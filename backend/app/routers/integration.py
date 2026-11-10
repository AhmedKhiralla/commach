"""Connected apps: a read-only API for other Comp11 Labs products (first of all Complanning).

A company administrator issues a key (shown once). The other app then calls, with
"Authorization: Bearer <key>":
  GET /api/integration/v1/assets                 the register (code, name, site, status, availability, external_ref)
  GET /api/integration/v1/events?after=<id>      changes since the last event it has seen
Phase 6 adds planned calibration and maintenance windows, which Complanning turns into machine downtime.
"""
from __future__ import annotations

import hashlib
import secrets

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
