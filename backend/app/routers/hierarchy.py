"""Where assets are: groups of sites, buildings, areas, rooms and production lines.

Every level is optional: a room may sit directly under a site, an asset directly under a site.
The tree endpoint returns the whole structure with asset counts rolled up at every level.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..api import ApiError, Call, endpoint, not_found
from ..common import bad, clean, code, patch_sql, reason_for_change
from ..perms import can_edit_site, is_admin, require_site, visible_site_ids

LIVE = ("draft", "in_service", "restricted", "standby", "out_of_service", "quarantined", "out_for_calibration")
UNAVAILABLE = ("standby", "out_of_service", "quarantined", "out_for_calibration")


# ------------------------------------------------------------------ site groups
class GroupIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=160)

    @field_validator("code")
    @classmethod
    def _c(cls, v):
        v = v.strip().upper()
        if not re.match(r"^[A-Z0-9][A-Z0-9_-]{0,19}$", v):
            raise ValueError("Use up to 20 letters, digits, dash or underscore.")
        return v


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    active: bool | None = None


@endpoint(auth="tenant_user")
def list_groups(call: Call):
    return {"items": call.tx.q(
        "SELECT g.id, g.code, g.name, g.active,"
        " (SELECT count(*) FROM sites s WHERE s.group_id = g.id) AS site_count"
        " FROM site_groups g ORDER BY g.active DESC, g.code")}


@endpoint(auth="tenant_admin", body=GroupIn)
def create_group(call: Call):
    b: GroupIn = call.body
    if call.tx.val("SELECT 1 FROM site_groups WHERE code = %s", b.code):
        raise bad("code", "This code is already used.")
    return call.tx.one("INSERT INTO site_groups (tenant_id, code, name) VALUES (%s, %s, %s)"
                       " RETURNING id, code, name, active", call.tenant_id, b.code, b.name.strip())


@endpoint(auth="tenant_admin", body=GroupPatch)
def update_group(call: Call):
    gid = call.path["group_id"]
    if not call.tx.val("SELECT 1 FROM site_groups WHERE id = %s::uuid", gid):
        raise not_found("Group")
    b: GroupPatch = call.body
    vals = {k: (v.strip() if isinstance(v, str) else v) for k, v in b.model_dump(exclude_unset=True).items() if v is not None}
    patch_sql(call, "site_groups", gid, vals)
    return call.tx.one("SELECT id, code, name, active FROM site_groups WHERE id = %s::uuid", gid)


# ------------------------------------------------------------------ locations
class LocationIn(BaseModel):
    kind: Literal["building", "area", "room"]
    parent_id: str | None = None
    code: str
    name_en: str = Field(min_length=1, max_length=160)
    name_ar: str | None = Field(default=None, max_length=160)
    cleanroom_grade: Literal["A", "B", "C", "D", "CNC", "none"] | None = None
    notes: str | None = Field(default=None, max_length=1000)
    _c = field_validator("code")(lambda v: code(v, max_len=30))


class LocationPatch(BaseModel):
    parent_id: str | None = None
    name_en: str | None = Field(default=None, min_length=1, max_length=160)
    name_ar: str | None = Field(default=None, max_length=160)
    cleanroom_grade: Literal["A", "B", "C", "D", "CNC", "none"] | None = None
    notes: str | None = Field(default=None, max_length=1000)
    active: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


LOC_COLS = "id, site_id, parent_id, kind, code, name_en, name_ar, cleanroom_grade, notes, active, updated_at"


@endpoint(auth="tenant_user")
def list_locations(call: Call):
    sid = call.path["site_id"]
    require_site(call, sid)
    return {"items": call.tx.q(f"SELECT {LOC_COLS} FROM locations WHERE site_id = %s::uuid ORDER BY code", sid)}


@endpoint(auth="tenant_user", body=LocationIn)
def create_location(call: Call):
    sid = call.path["site_id"]
    require_site(call, sid, edit=True)
    b: LocationIn = call.body
    if call.tx.val("SELECT 1 FROM locations WHERE site_id = %s::uuid AND code = %s", sid, b.code):
        raise bad("code", "This code is already used at this site.")
    if b.parent_id and not call.tx.val("SELECT 1 FROM locations WHERE id = %s::uuid AND site_id = %s::uuid", b.parent_id, sid):
        raise bad("parent_id", "Choose a location at this site.")
    return call.tx.one(
        "INSERT INTO locations (tenant_id, site_id, parent_id, kind, code, name_en, name_ar, cleanroom_grade, notes)"
        f" VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s) RETURNING {LOC_COLS}",
        call.tenant_id, sid, b.parent_id, b.kind, b.code, b.name_en.strip(), clean(b.name_ar), b.cleanroom_grade, clean(b.notes))


@endpoint(auth="tenant_user", body=LocationPatch)
def update_location(call: Call):
    lid = call.path["location_id"]
    loc = call.tx.one("SELECT * FROM locations WHERE id = %s::uuid", lid)
    if not loc:
        raise not_found("Location")
    require_site(call, str(loc["site_id"]), edit=True)
    b: LocationPatch = call.body
    reason_for_change(call, b.reason)
    data = b.model_dump(exclude_unset=True)
    data.pop("reason", None)
    vals = {}
    for k, v in data.items():
        if k in ("name_ar", "notes", "parent_id"):
            vals[k] = clean(v) if isinstance(v, str) or v is None else v
        elif v is not None:
            vals[k] = v.strip() if isinstance(v, str) else v
    if "parent_id" in vals and vals["parent_id"]:
        # The new parent must not be this location or one of its descendants.
        ids = {r["id"] for r in call.tx.q(
            "WITH RECURSIVE d AS (SELECT id FROM locations WHERE id = %s::uuid"
            " UNION ALL SELECT l.id FROM locations l JOIN d ON l.parent_id = d.id) SELECT id::text AS id FROM d", lid)}
        if vals["parent_id"] in ids:
            raise bad("parent_id", "A location cannot be moved inside itself.")
    if "parent_id" in vals:
        call.tx.x("UPDATE locations SET parent_id = %s::uuid WHERE id = %s::uuid", vals.pop("parent_id"), lid)
    patch_sql(call, "locations", lid, vals)
    return call.tx.one(f"SELECT {LOC_COLS} FROM locations WHERE id = %s::uuid", lid)


# ------------------------------------------------------------------ lines
class LineIn(BaseModel):
    code: str
    name_en: str = Field(min_length=1, max_length=160)
    name_ar: str | None = Field(default=None, max_length=160)
    location_id: str | None = None
    _c = field_validator("code")(lambda v: code(v, max_len=30))


class LinePatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=160)
    name_ar: str | None = Field(default=None, max_length=160)
    location_id: str | None = None
    active: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


LINE_COLS = "id, site_id, location_id, code, name_en, name_ar, active"


@endpoint(auth="tenant_user")
def list_lines(call: Call):
    sid = call.path["site_id"]
    require_site(call, sid)
    return {"items": call.tx.q(
        f"SELECT {LINE_COLS}, (SELECT count(*) FROM assets a WHERE a.line_id = l.id) AS asset_count"
        " FROM lines l WHERE site_id = %s::uuid ORDER BY code", sid)}


@endpoint(auth="tenant_user", body=LineIn)
def create_line(call: Call):
    sid = call.path["site_id"]
    require_site(call, sid, edit=True)
    b: LineIn = call.body
    if call.tx.val("SELECT 1 FROM lines WHERE site_id = %s::uuid AND code = %s", sid, b.code):
        raise bad("code", "This code is already used at this site.")
    if b.location_id and not call.tx.val("SELECT 1 FROM locations WHERE id = %s::uuid AND site_id = %s::uuid", b.location_id, sid):
        raise bad("location_id", "Choose a location at this site.")
    return call.tx.one(f"INSERT INTO lines (tenant_id, site_id, location_id, code, name_en, name_ar)"
                       f" VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s) RETURNING {LINE_COLS}",
                       call.tenant_id, sid, b.location_id, b.code, b.name_en.strip(), clean(b.name_ar))


@endpoint(auth="tenant_user", body=LinePatch)
def update_line(call: Call):
    lid = call.path["line_id"]
    line = call.tx.one("SELECT * FROM lines WHERE id = %s::uuid", lid)
    if not line:
        raise not_found("Line")
    require_site(call, str(line["site_id"]), edit=True)
    b: LinePatch = call.body
    reason_for_change(call, b.reason)
    data = b.model_dump(exclude_unset=True)
    data.pop("reason", None)
    if "location_id" in data:
        call.tx.x("UPDATE lines SET location_id = %s::uuid WHERE id = %s::uuid", data.pop("location_id"), lid)
    patch_sql(call, "lines", lid, {k: (clean(v) if k == "name_ar" else v) for k, v in data.items()
                                   if v is not None or k == "name_ar"})
    return call.tx.one(f"SELECT {LINE_COLS} FROM lines WHERE id = %s::uuid", lid)


# ------------------------------------------------------------------ tree with counts
def _w() -> dict:
    return {"open": 0, "overdue": 0, "due_soon": 0, "next": None}


def _empty() -> dict:
    return {"total": 0, "calibration": 0, "pm": 0, "critical": 0, "unavailable": 0, "draft": 0, "categories": {},
            "work": {"calibration": _w(), "pm": _w()}}


def _add(c: dict, other: dict) -> None:
    for k in ("total", "calibration", "pm", "critical", "unavailable", "draft"):
        c[k] += other[k]
    for k, v in other["categories"].items():
        c["categories"][k] = c["categories"].get(k, 0) + v
    for kind in ("calibration", "pm"):
        a, b = c["work"][kind], other["work"][kind]
        for k in ("open", "overdue", "due_soon"):
            a[k] += b[k]
        if b["next"] and (a["next"] is None or b["next"] < a["next"]):
            a["next"] = b["next"]


def build_tree(call: Call, site_filter: list[str] | None) -> dict:
    where, params = "", []
    if site_filter is not None:
        where = " WHERE s.id = ANY(%s::uuid[])"
        params = [site_filter]
    sites = call.tx.q("SELECT s.id, s.code, s.name, s.group_id, s.active FROM sites s" + where + " ORDER BY s.code", *params)
    site_ids = [str(s["id"]) for s in sites]
    if not site_ids:
        return {"groups": [], "sites": []}
    locs = call.tx.q(f"SELECT {LOC_COLS} FROM locations WHERE site_id = ANY(%s::uuid[]) ORDER BY code", site_ids)
    lines = call.tx.q(f"SELECT {LINE_COLS} FROM lines WHERE site_id = ANY(%s::uuid[]) ORDER BY code", site_ids)
    agg = call.tx.q(
        "SELECT a.site_id, a.location_id, c.category, a.status, a.gmp_criticality,"
        "       a.calibration_required, a.pm_required, count(*) AS n"
        "  FROM assets a JOIN asset_classes c ON c.id = a.class_id"
        " WHERE a.site_id = ANY(%s::uuid[]) AND a.status = ANY(%s::text[])"
        " GROUP BY 1, 2, 3, 4, 5, 6, 7", site_ids, list(LIVE))
    direct: dict[str, dict] = {}
    for r in agg:
        key = str(r["location_id"]) if r["location_id"] else f"site:{r['site_id']}"
        c = direct.setdefault(key, _empty())
        n = r["n"]
        c["total"] += n
        c["calibration"] += n if r["calibration_required"] else 0
        c["pm"] += n if r["pm_required"] else 0
        c["critical"] += n if r["gmp_criticality"] == "critical" else 0
        c["unavailable"] += n if r["status"] in UNAVAILABLE else 0
        c["draft"] += n if r["status"] == "draft" else 0
        c["categories"][r["category"]] = c["categories"].get(r["category"], 0) + n

    from .work import settings_of, today
    t0 = today(call)
    import datetime as _dt
    soon = t0 + _dt.timedelta(days=settings_of(call)["due_soon_days"])
    wrows = call.tx.q(
        "SELECT a.site_id, a.location_id, t.kind, count(*) AS n,"
        " count(*) FILTER (WHERE t.window_end < %s) AS overdue,"
        " count(*) FILTER (WHERE t.window_end >= %s AND t.due_date <= %s) AS due_soon, min(t.due_date) AS next"
        "  FROM tasks t JOIN assets a ON a.id = t.asset_id"
        " WHERE t.site_id = ANY(%s::uuid[]) AND t.status NOT IN ('completed', 'cancelled')"
        "   AND a.status = ANY(%s::text[]) GROUP BY 1, 2, 3", t0, t0, soon, site_ids, list(LIVE))
    for r in wrows:
        key = str(r["location_id"]) if r["location_id"] else f"site:{r['site_id']}"
        w = direct.setdefault(key, _empty())["work"][r["kind"]]
        w["open"] += r["n"]
        w["overdue"] += r["overdue"]
        w["due_soon"] += r["due_soon"]
        w["next"] = r["next"] if w["next"] is None or r["next"] < w["next"] else w["next"]

    nodes = {str(l["id"]): {**l, "children": [], "counts": _empty()} for l in locs}
    roots: dict[str, list] = {sid: [] for sid in site_ids}
    for n in nodes.values():
        parent = str(n["parent_id"]) if n["parent_id"] else None
        if parent and parent in nodes:
            nodes[parent]["children"].append(n)
        else:
            roots[str(n["site_id"])].append(n)

    def roll(n: dict) -> dict:
        c = _empty()
        _add(c, direct.get(str(n["id"]), _empty()))
        n["own"] = direct.get(str(n["id"]), _empty())["total"]
        for ch in n["children"]:
            _add(c, roll(ch))
        n["counts"] = c
        return c

    out_sites = []
    for s in sites:
        sid = str(s["id"])
        c = _empty()
        unplaced = direct.get(f"site:{sid}", _empty())
        _add(c, unplaced)
        for n in roots[sid]:
            _add(c, roll(n))
        out_sites.append({**s, "counts": c, "unplaced": unplaced["total"], "nodes": roots[sid],
                          "lines": [l for l in lines if str(l["site_id"]) == sid],
                          "can_edit": can_edit_site(call, sid)})
    group_ids = {str(s["group_id"]) for s in sites if s["group_id"]}
    groups = call.tx.q("SELECT id, code, name, active FROM site_groups ORDER BY code") if is_admin(call) else \
        call.tx.q("SELECT id, code, name, active FROM site_groups WHERE id = ANY(%s::uuid[]) ORDER BY code", list(group_ids))
    for g in groups:
        c = _empty()
        for s in out_sites:
            if str(s["group_id"]) == str(g["id"]):
                _add(c, s["counts"])
        g["counts"] = c
    return {"groups": groups, "sites": out_sites}


@endpoint(auth="tenant_user")
def tree(call: Call):
    return build_tree(call, visible_site_ids(call))
