"""Small helpers shared by the register routers."""
from __future__ import annotations

import re

from .api import ApiError, Call
from .defaults import effective_features

CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$")


def code(v: str, upper: bool = False, max_len: int = 40) -> str:
    v = (v or "").strip()
    if upper:
        v = v.upper()
    if not v or len(v) > max_len or not CODE_RE.match(v):
        raise ValueError(f"Use up to {max_len} letters, digits, dot, dash, slash or underscore, starting with a letter or digit.")
    return v


def clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def features(call: Call) -> dict[str, bool]:
    cached = getattr(call, "_features", None)
    if cached is None:
        cached = effective_features(call.tx.val("SELECT features FROM tenants WHERE id = %s", call.tenant_id))
        setattr(call, "_features", cached)
    return cached


def reason_for_change(call: Call, reason: str | None, required: bool = True) -> None:
    """21 CFR Part 11: a change to a GMP record carries the reason, stored with it in the audit trail."""
    text = (reason or "").strip()
    if required and features(call).get("reason_for_change", True) and len(text) < 3:
        raise ApiError(422, "reason_required", "Give the reason for this change.", fields={"reason": "required"})
    if text:
        call.tx.reason(text)


def patch_sql(call: Call, table: str, record_id: str, values: dict) -> int:
    """UPDATE only the given columns. Column names come from code, never from the request."""
    if not values:
        return 0
    cols = ", ".join(f"{c} = %s" for c in values)
    return call.tx.x(f"UPDATE {table} SET {cols} WHERE id = %s::uuid", *values.values(), record_id)


def bad(field: str, message: str) -> ApiError:
    return ApiError(422, "invalid", "Some fields need attention.", fields={field: message})
