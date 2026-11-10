"""Excel: the asset register template, export, and import of any register (ComMach's template or the
customer's own sheet, mapped by hand or by AI). Import is two steps: preview (nothing saved), then commit.
Imported assets start as draft; they enter service with one signed approval for the whole batch."""
from __future__ import annotations

import datetime as dt
import decimal
import io
import re
from typing import Any

from pydantic import BaseModel, Field
from starlette.responses import Response

from ..ai_tasks import accept_suggestion, run_import_mapping, slug
from ..api import ApiError, Call, endpoint
from ..perms import can_edit_site, visible_site_ids
from .assets import LIST_SQL, _filters
from .files import read_bytes

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# key: (English label, required, description for people and for the AI mapper)
COLUMNS: dict[str, tuple[str, bool, str]] = {
    "site_code": ("Site code", False, "site code; empty = the site chosen for the import"),
    "code": ("Asset code", True, "unique asset / equipment ID, e.g. BAL-001"),
    "name_en": ("Name (English)", True, "asset or equipment name"),
    "name_ar": ("Name (Arabic)", False, "asset name in Arabic"),
    "class_code": ("Class code", True, "asset class / type of equipment"),
    "parent_code": ("Parent asset code", False, "the machine or system this item is part of"),
    "building_code": ("Building", False, "building code or name"),
    "area_code": ("Area", False, "area / zone / section code or name"),
    "room_code": ("Room", False, "room code, number or name"),
    "line_code": ("Line", False, "production line"),
    "department_code": ("Department", False, "owning / user department"),
    "manufacturer": ("Manufacturer", False, "make / manufacturer / brand"),
    "model": ("Model", False, "model / type designation"),
    "serial_no": ("Serial number", False, "serial number"),
    "asset_tag": ("Asset tag", False, "fixed asset / ERP number"),
    "capacity": ("Capacity", False, "capacity or rating"),
    "range": ("Range (text)", False, "measuring range written as text, e.g. '0 - 200 °C'"),
    "range_min": ("Range from", False, "lower limit of measuring range"),
    "range_max": ("Range to", False, "upper limit of measuring range"),
    "unit": ("Unit", False, "unit of the measuring range"),
    "resolution": ("Resolution", False, "least count / readability"),
    "accuracy": ("Accuracy", False, "accuracy / tolerance / class"),
    "gmp_criticality": ("GMP criticality", False, "critical, major, minor or non-GMP"),
    "calibration_required": ("Calibration required (Y/N)", False, "whether the item is calibrated"),
    "pm_required": ("PM required (Y/N)", False, "whether the item has preventive maintenance"),
    "is_portable": ("Portable (Y/N)", False, "whether the item moves between rooms"),
    "install_date": ("Installation date", False, "date of installation / commissioning"),
    "warranty_until": ("Warranty until", False, "end of warranty"),
    "supplier_vendor_code": ("Supplier code", False, "supplier vendor code"),
    "service_vendor_code": ("Service / calibration vendor code", False, "external calibration or service vendor"),
    "external_ref": ("External reference", False, "code in another system, e.g. Complanning machine code"),
    "notes": ("Notes", False, "remarks"),
    "status": ("Status in your register", False, "in use / not in use / out of order ..."),
}

SYNONYMS = {
    "code": ["asset_code", "equipment_id", "equip_id", "equipment_no", "equipment_number", "id_no", "instrument_id",
             "instrument_no", "asset_no", "asset_id", "tag", "tag_no", "equipment_code", "id"],
    "name_en": ["name", "equipment_name", "instrument_name", "description", "equipment", "asset_name", "name_english"],
    "name_ar": ["arabic_name", "name_arabic", "الاسم", "اسم_الجهاز"],
    "class_code": ["class", "type", "category", "equipment_type", "instrument_type", "asset_class"],
    "parent_code": ["parent", "parent_equipment", "main_equipment", "part_of", "machine"],
    "building_code": ["building", "block"],
    "area_code": ["area", "zone", "section"],
    "room_code": ["room", "room_no", "room_id", "location"],
    "line_code": ["line", "production_line"],
    "department_code": ["department", "dept", "user_department", "owner"],
    "manufacturer": ["make", "brand", "maker", "mfr", "supplier_make"],
    "model": ["model_no", "type_model"],
    "serial_no": ["serial", "serial_number", "s_n", "sn", "sr_no"],
    "asset_tag": ["fixed_asset_no", "erp_no", "asset_tag_no"],
    "capacity": ["rating", "capacity_rating"],
    "range": ["measuring_range", "operating_range", "range_of_measurement"],
    "unit": ["uom", "units"],
    "resolution": ["least_count", "readability", "division"],
    "accuracy": ["tolerance", "accuracy_class", "acceptance_criteria"],
    "gmp_criticality": ["criticality", "gmp_impact", "impact"],
    "calibration_required": ["calibration", "calibrated", "cal_required"],
    "pm_required": ["pm", "preventive_maintenance", "maintenance"],
    "install_date": ["installation_date", "date_of_installation", "commissioning_date"],
    "service_vendor_code": ["calibration_agency", "vendor", "calibrated_by", "external_agency"],
    "status": ["equipment_status", "condition", "state"],
}

STATUS_WORDS = {"in_service": ["in use", "in service", "active", "working", "ok", "operational", "qualified", "use"],
                "standby": ["standby", "idle", "not in use", "spare"],
                "out_of_service": ["out of order", "out of service", "breakdown", "under repair", "faulty", "not working"],
                "retired": ["retired", "scrapped", "obsolete", "decommissioned", "disposed"]}
CRIT_WORDS = {"critical": ["critical", "high", "direct"], "major": ["major", "medium", "indirect"],
              "minor": ["minor", "low"], "non_gmp": ["non gmp", "non-gmp", "no impact", "none", "na", "n/a"]}


def _match_header(h: str) -> str | None:
    s = slug(h)
    for key, (label, _, _) in COLUMNS.items():
        if s in (key, slug(label)):
            return key
    for key, words in SYNONYMS.items():
        if s in words:
            return key
    return None


def _word_map(value: str, table: dict[str, list[str]]) -> str | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    for key, words in table.items():
        if v == key or v in words:
            return key
    for key, words in table.items():
        if any(w in v for w in words if len(w) > 3):
            return key
    return None


def _yes(v) -> bool | None:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().lower()
    if s in ("y", "yes", "true", "1", "x", "✓", "√", "نعم", "required", "applicable"):
        return True
    if s in ("n", "no", "false", "0", "-", "لا", "na", "n/a", "not required", "not applicable"):
        return False
    return None


def _num(v) -> decimal.Decimal | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return decimal.Decimal(str(v).strip().replace(",", "."))
    except decimal.InvalidOperation:
        raise ValueError("not a number")


RANGE_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(?:-|–|to|~|\.\.)\s*(-?\d+(?:[.,]\d+)?)\s*(.*)$", re.I)


def _parse_range(v) -> tuple[decimal.Decimal | None, decimal.Decimal | None, str | None]:
    if v is None or not str(v).strip():
        return None, None, None
    m = RANGE_RE.match(str(v))
    if not m:
        raise ValueError("range not understood")
    return (decimal.Decimal(m.group(1).replace(",", ".")), decimal.Decimal(m.group(2).replace(",", ".")),
            (m.group(3) or "").strip() or None)


def _date(v) -> dt.date | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%Y", "%Y"):
        try:
            d = dt.datetime.strptime(s, fmt).date()
            return d
        except ValueError:
            continue
    raise ValueError("date not understood")


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


# ------------------------------------------------------------------ reading
def _load(call: Call, file_id: str):
    from openpyxl import load_workbook
    f, data = read_bytes(call, file_id)
    if not f["filename"].lower().endswith(".xlsx"):
        raise ApiError(422, "bad_file", "Upload the register as an Excel workbook (.xlsx).")
    try:
        return load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ApiError(422, "bad_file", "This workbook could not be opened.")


def _sheet_rows(wb, sheet: str | None, header_row: int | None) -> tuple[str, int, list[str], list[list]]:
    name = sheet if sheet in wb.sheetnames else None
    if not name:
        name = next((n for n in wb.sheetnames if n.lower() not in ("instructions", "lists", "read me", "readme")),
                    wb.sheetnames[0])
    ws = wb[name]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    rows = rows[:5000]
    if header_row is None:
        header_row = 1
        for i, r in enumerate(rows[:30], start=1):
            if sum(1 for c in r if c is not None and str(c).strip()) >= 3:
                header_row = i
                break
    if header_row < 1 or header_row > len(rows):
        raise ApiError(422, "invalid", "The header row is outside the sheet.")
    raw_headers = rows[header_row - 1]
    headers = []
    for i, h in enumerate(raw_headers):
        t = _text(h) or f"Column {i + 1}"
        while t in headers:
            t += " (2)"
        headers.append(t)
    body = [r + [None] * (len(headers) - len(r)) for r in rows[header_row:]]
    body = [r[:len(headers)] for r in body if any(c is not None and str(c).strip() for c in r)]
    # ComMach's own template has a second row that explains the columns: skip it.
    labels = {slug(lbl) for lbl, _, _ in COLUMNS.values()}
    if body and sum(1 for c in body[0] if c and slug(str(c).replace("*", "")) in labels) >= 3:
        body = body[1:]
    return name, header_row, headers, body


class PreviewIn(BaseModel):
    file_id: str
    site_id: str
    sheet: str | None = Field(default=None, max_length=100)
    header_row: int | None = Field(default=None, ge=1, le=100)
    mapping: dict[str, str | None] | None = None
    classes: dict[str, str] | None = None
    status: dict[str, str] | None = None
    criticality: dict[str, str] | None = None
    use_ai: bool = False
    create_locations: bool = True


class CommitIn(PreviewIn):
    suggestion_id: str | None = None


def _context(call: Call, site_id: str) -> dict:
    tx = call.tx
    sites = {r["code"].upper(): str(r["id"]) for r in tx.q("SELECT id, code FROM sites WHERE active")}
    classes = {r["code"].upper(): r for r in tx.q("SELECT id, code, name_en, calibration_default, pm_default FROM asset_classes WHERE active")}
    depts = {r["code"].upper(): str(r["id"]) for r in tx.q("SELECT id, code FROM departments WHERE active")}
    for r in tx.q("SELECT id, name_en FROM departments WHERE active"):
        depts.setdefault(r["name_en"].upper(), str(r["id"]))
    vendors = {r["code"].upper(): str(r["id"]) for r in tx.q("SELECT id, code FROM vendors WHERE active")}
    locs: dict[tuple[str, str, str], str] = {}
    for r in tx.q("SELECT id, site_id, kind, code, name_en FROM locations"):
        locs[(str(r["site_id"]), r["kind"], r["code"].upper())] = str(r["id"])
        locs.setdefault((str(r["site_id"]), r["kind"], r["name_en"].upper()), str(r["id"]))
    lines = {}
    for r in tx.q("SELECT id, site_id, code, name_en FROM lines"):
        lines[(str(r["site_id"]), r["code"].upper())] = str(r["id"])
        lines.setdefault((str(r["site_id"]), r["name_en"].upper()), str(r["id"]))
    existing = {r["code"].lower() for r in tx.q("SELECT code FROM assets")}
    return {"sites": sites, "classes": classes, "depts": depts, "vendors": vendors, "locs": locs, "lines": lines,
            "existing": existing, "default_site": site_id}


def _analyse(call: Call, b: PreviewIn, headers: list[str], body: list[list], mapping: dict[str, str],
             class_map: dict[str, str], status_map: dict[str, str], crit_map: dict[str, str]) -> tuple[list[dict], dict]:
    ctx = _context(call, b.site_id)
    idx = {key: headers.index(h) for h, key in mapping.items() if key and h in headers}
    out, seen = [], set()
    new_locs: set[tuple] = set()
    for n, r in enumerate(body, start=1):
        def g(key):
            return r[idx[key]] if key in idx else None
        errs, warns, v = [], [], {}
        try:
            code = _text(g("code"))
            if not code:
                errs.append("code: missing")
            elif not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$", code):
                errs.append("code: use letters, digits, dot, dash, slash or underscore")
            elif code.lower() in ctx["existing"]:
                errs.append("code: already in ComMach")
            elif code.lower() in seen:
                errs.append("code: repeated in the sheet")
            else:
                seen.add(code.lower())
            v["code"] = code
            v["name_en"] = _text(g("name_en"))
            if not v["name_en"]:
                errs.append("name_en: missing")
            v["name_ar"] = _text(g("name_ar"))
            sc = (_text(g("site_code")) or "").upper()
            site_id = ctx["sites"].get(sc) if sc else ctx["default_site"]
            if not site_id:
                errs.append(f"site_code: unknown site '{sc}'")
            elif not can_edit_site(call, site_id):
                errs.append("site_code: you cannot add assets at this site")
            v["site_id"] = site_id
            raw_class = _text(g("class_code")) or ""
            cc = class_map.get(raw_class, raw_class).upper()
            cls = ctx["classes"].get(cc)
            if not cls:
                errs.append(f"class_code: '{raw_class}' is not mapped to a class" if raw_class else "class_code: missing")
            v["class_code"] = cc if cls else None
            v["class_id"] = str(cls["id"]) if cls else None
            v["parent_code"] = _text(g("parent_code"))
            # locations: building > area > room, each optional; created if missing (when allowed)
            parent_loc = None
            for kind in ("building", "area", "room"):
                val = _text(g(f"{kind}_code"))
                if not val or not site_id:
                    continue
                lid = ctx["locs"].get((site_id, kind, val.upper()))
                if not lid:
                    if b.create_locations:
                        new_locs.add((site_id, kind, val))
                        warns.append(f"{kind} '{val}' will be created")
                        lid = f"new:{kind}:{val.upper()}"
                    else:
                        errs.append(f"{kind}_code: unknown {kind} '{val}'")
                v[f"{kind}_ref"] = lid
                parent_loc = lid or parent_loc
            v["location_ref"] = parent_loc
            ln = _text(g("line_code"))
            v["line_id"] = ctx["lines"].get((site_id, ln.upper())) if ln and site_id else None
            if ln and not v["line_id"]:
                warns.append(f"line '{ln}' is unknown and was left empty")
            dp = _text(g("department_code"))
            v["department_id"] = ctx["depts"].get(dp.upper()) if dp else None
            if dp and not v["department_id"]:
                warns.append(f"department '{dp}' is unknown and was left empty")
            for key in ("supplier_vendor_code", "service_vendor_code"):
                vc = _text(g(key))
                vid = ctx["vendors"].get(vc.upper()) if vc else None
                if vc and not vid:
                    warns.append(f"vendor '{vc}' is unknown and was left empty")
                v[key.replace("_code", "_id")] = vid
            for key in ("manufacturer", "model", "serial_no", "asset_tag", "capacity", "unit", "resolution", "accuracy",
                        "external_ref", "notes"):
                v[key] = _text(g(key))
            if "range" in idx and g("range") is not None:
                try:
                    lo, hi, unit = _parse_range(g("range"))
                    v["range_min"], v["range_max"] = lo, hi
                    v["unit"] = v["unit"] or unit
                except ValueError:
                    warns.append(f"range '{g('range')}' not understood; kept in notes")
                    v["notes"] = ((v["notes"] or "") + f" Range: {g('range')}").strip()
            for key in ("range_min", "range_max"):
                if key in idx:
                    try:
                        v[key] = _num(g(key))
                    except ValueError:
                        errs.append(f"{key}: not a number")
            if v.get("range_min") is not None and v.get("range_max") is not None and v["range_min"] > v["range_max"]:
                errs.append("range: lower limit above upper limit")
            crit_raw = _text(g("gmp_criticality"))
            crit = crit_map.get(crit_raw or "", None) or _word_map(crit_raw or "", CRIT_WORDS) if crit_raw else None
            if crit_raw and not crit:
                warns.append(f"criticality '{crit_raw}' not understood; set to major")
            v["gmp_criticality"] = crit or "major"
            for key in ("calibration_required", "pm_required", "is_portable"):
                yv = _yes(g(key))
                if key in idx and g(key) not in (None, "") and yv is None:
                    warns.append(f"{key}: '{g(key)}' not understood")
                v[key] = yv
            if v["calibration_required"] is None and cls:
                v["calibration_required"] = cls["calibration_default"]
            if v["pm_required"] is None and cls:
                v["pm_required"] = cls["pm_default"]
            v["is_portable"] = bool(v["is_portable"])
            for key in ("install_date", "warranty_until"):
                try:
                    v[key] = _date(g(key))
                except ValueError:
                    warns.append(f"{key}: '{g(key)}' not understood; left empty")
                    v[key] = None
            st_raw = _text(g("status"))
            st = (status_map.get(st_raw) if st_raw else None) or (_word_map(st_raw, STATUS_WORDS) if st_raw else None)
            v["sheet_status"] = st_raw
            v["intended_status"] = st or ("in_service" if not st_raw else None)
            if st_raw and not st:
                warns.append(f"status '{st_raw}' not understood")
        except Exception as e:  # never let one bad cell stop the preview
            errs.append(f"row could not be read: {e}")
        out.append({"row": n, "values": v, "errors": errs, "warnings": warns})
    # parent codes must exist in ComMach or in the sheet
    sheet_codes = {r["values"].get("code", "").lower() for r in out if r["values"].get("code")}
    existing = call.tx.q("SELECT code, site_id FROM assets")
    ex = {r["code"].lower(): str(r["site_id"]) for r in existing}
    for r in out:
        pc = r["values"].get("parent_code")
        if pc and pc.lower() not in sheet_codes and pc.lower() not in ex:
            r["errors"].append(f"parent_code: '{pc}' not found")
    summary = {"rows": len(out), "ok": sum(1 for r in out if not r["errors"]),
               "errors": sum(1 for r in out if r["errors"]), "new_locations": sorted({f"{k}: {c}" for _, k, c in new_locs})}
    return out, summary


def _auto_mapping(headers: list[str]) -> dict[str, str | None]:
    used, out = set(), {}
    for h in headers:
        k = _match_header(h)
        if k and k not in used:
            out[h] = k
            used.add(k)
        else:
            out[h] = None
    return out


def _distinct(headers, body, mapping, key, limit=150) -> list[str]:
    for h, k in mapping.items():
        if k == key and h in headers:
            i = headers.index(h)
            vals = []
            for r in body:
                t = _text(r[i])
                if t and t not in vals:
                    vals.append(t)
            return vals[:limit]
    return []


def _prepare(call: Call, b: PreviewIn):
    wb = _load(call, b.file_id)
    sheet, header_row, headers, body = _sheet_rows(wb, b.sheet, b.header_row)
    ai_result = None
    mapping = b.mapping or _auto_mapping(headers)
    class_map = dict(b.classes or {})
    status_map = dict(b.status or {})
    crit_map = dict(b.criticality or {})
    if b.use_ai:
        types = _distinct(headers, body, mapping, "class_code") or _distinct(headers, body, mapping, "name_en")
        targets = {k: f"{label}: {desc}" for k, (label, _, desc) in COLUMNS.items()}
        sample = [[_text(c) for c in r] for r in body[:12]]
        ai_result = run_import_mapping(call, headers, sample, targets, types)
        mapping = {h: ai_result["columns"].get(h) for h in headers}
        class_map = {**ai_result["classes"], **class_map}
        status_map = {**ai_result["status"], **status_map}
        crit_map = {**ai_result["criticality"], **crit_map}
    return wb, sheet, header_row, headers, body, mapping, class_map, status_map, crit_map, ai_result


@endpoint(auth="tenant_user", body=PreviewIn)
def preview_import(call: Call):
    b: PreviewIn = call.body
    if not can_edit_site(call, b.site_id):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access at this site is required to import assets.")
    wb, sheet, header_row, headers, body, mapping, class_map, status_map, crit_map, ai_result = _prepare(call, b)
    rows, summary = _analyse(call, b, headers, body, mapping, class_map, status_map, crit_map)
    missing = [k for k, (_, req, _) in COLUMNS.items() if req and k not in mapping.values()]
    type_values = _distinct(headers, body, mapping, "class_code")
    return {
        "sheets": wb.sheetnames, "sheet": sheet, "header_row": header_row, "headers": headers, "mapping": mapping,
        "classes": class_map, "status": status_map, "criticality": crit_map,
        "class_values": type_values, "status_values": _distinct(headers, body, mapping, "status"),
        "criticality_values": _distinct(headers, body, mapping, "gmp_criticality"),
        "missing_required": missing, "rows": rows[:300], "summary": summary,
        "samples": {h: [t for t in (_text(r[i]) for r in body[:6]) if t][:3] for i, h in enumerate(headers)},
        "columns": {k: {"label": v[0], "required": v[1], "description": v[2]} for k, v in COLUMNS.items()},
        "ai": ({"suggestion_id": ai_result["suggestion"]["id"], "model": ai_result["suggestion"]["model"],
                "notes": ai_result["notes"]} if ai_result else None),
    }


@endpoint(auth="tenant_user", body=CommitIn)
def commit_import(call: Call):
    b: CommitIn = call.body
    if not can_edit_site(call, b.site_id):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access at this site is required to import assets.")
    b2 = b.model_copy(update={"use_ai": False})
    wb, sheet, header_row, headers, body, mapping, class_map, status_map, crit_map, _ = _prepare(call, b2)
    rows, summary = _analyse(call, b2, headers, body, mapping, class_map, status_map, crit_map)
    if summary["errors"]:
        raise ApiError(422, "import_errors", f"{summary['errors']} rows need attention before importing.",
                       extra={"rows": [r for r in rows if r["errors"]][:200]})
    if not rows:
        raise ApiError(422, "empty", "The sheet has no rows to import.")
    tx = call.tx
    created_locs: dict[str, str] = {}

    def loc_id(site_id: str, ref: str | None, parent: str | None) -> str | None:
        if not ref:
            return parent
        if not ref.startswith("new:"):
            return ref
        _, kind, val = ref.split(":", 2)
        key = f"{site_id}:{kind}:{val}"
        if key not in created_locs:
            code = re.sub(r"[^A-Za-z0-9._/-]+", "-", val)[:30].strip("-") or kind[:1].upper()
            if not re.match(r"^[A-Za-z0-9]", code):
                code = "L" + code
            n, base = 1, code
            while tx.val("SELECT 1 FROM locations WHERE site_id = %s::uuid AND code = %s", site_id, code):
                n += 1
                code = f"{base[:26]}-{n}"
            row = tx.one("INSERT INTO locations (tenant_id, site_id, parent_id, kind, code, name_en)"
                         " VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s) RETURNING id",
                         call.tenant_id, site_id, parent, kind, code, val.title() if val.isupper() and len(val) > 4 else val)
            created_locs[key] = str(row["id"])
        return created_locs[key]

    code_to_id: dict[str, str] = {}
    created = []
    cols = ("code", "name_en", "name_ar", "class_id", "line_id", "department_id", "manufacturer", "model", "serial_no",
            "asset_tag", "capacity", "range_min", "range_max", "unit", "resolution", "accuracy", "gmp_criticality",
            "calibration_required", "pm_required", "is_portable", "install_date", "warranty_until",
            "supplier_vendor_id", "service_vendor_id", "external_ref", "notes")
    for r in rows:
        v = r["values"]
        site_id = v["site_id"]
        parent = None
        for kind in ("building", "area", "room"):
            ref = v.get(f"{kind}_ref")
            if ref:
                parent = loc_id(site_id, ref, parent)
        vals = [v.get(c) for c in cols]
        row = tx.one(f"INSERT INTO assets (tenant_id, site_id, location_id, {', '.join(cols)})"
                     f" VALUES (%s, %s::uuid, %s::uuid, {', '.join(['%s'] * len(cols))}) RETURNING id, code",
                     call.tenant_id, site_id, parent, *vals)
        code_to_id[v["code"].lower()] = str(row["id"])
        created.append({"id": row["id"], "code": row["code"], "intended_status": v.get("intended_status"),
                        "sheet_status": v.get("sheet_status")})
    for r in rows:
        pc = r["values"].get("parent_code")
        if pc:
            pid = code_to_id.get(pc.lower()) or tx.val("SELECT id::text FROM assets WHERE lower(code) = lower(%s)", pc)
            tx.x("UPDATE assets SET parent_id = %s::uuid WHERE id = %s::uuid", pid, code_to_id[r["values"]["code"].lower()])
    tx.event("IMPORT", "assets", None, f"{len(created)} assets from {sheet}",
             {"file_id": b.file_id, "sheet": sheet, "rows": len(created), "locations_created": len(created_locs),
              "mapping": {h: k for h, k in mapping.items() if k}})
    if b.suggestion_id:
        accept_suggestion(call, b.suggestion_id, "import", b.file_id,
                          {"columns": {h: k for h, k in mapping.items() if k}, "classes": class_map,
                           "status": status_map, "criticality": crit_map})
    return {"created": created, "locations_created": len(created_locs)}


# ------------------------------------------------------------------ template and export
def _style_sheet(ws, header_row: int, widths: list[int]):
    from openpyxl.styles import Font, PatternFill
    for cell in ws[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F5F8B")
    for i, w in enumerate(widths, start=1):
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(header_row + 1, 1)


@endpoint(auth="tenant_user")
def template(call: Call):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Assets"
    keys = [k for k in COLUMNS if k not in ("range", "status")]
    ws.append(keys)
    ws.append([COLUMNS[k][0] + (" *" if COLUMNS[k][1] else "") for k in keys])
    for c in ws[2]:
        c.font = Font(italic=True, color="666666")
    _style_sheet(ws, 1, [16] * len(keys))
    info = wb.create_sheet("Instructions")
    info.append(["ComMach asset register template"])
    info["A1"].font = Font(bold=True, size=14)
    for line in ("Row 1 holds the column keys; keep it. Row 2 explains each column and can be deleted.",
                 "Columns marked * are required. Codes must be unique across the company.",
                 "Locations (building, area, room) that do not exist yet can be created during import.",
                 "Imported assets start as draft and enter service with one signed approval.",
                 "You can also import your own register as it is: ComMach (or its AI) maps your columns."):
        info.append([line])
    lists = wb.create_sheet("Lists")
    lists.append(["Class code", "Class", "Category", "", "Site code", "Site", "", "Department code", "Department",
                  "", "Vendor code", "Vendor"])
    classes = call.tx.q("SELECT code, name_en, category FROM asset_classes WHERE active ORDER BY code")
    sites = call.tx.q("SELECT code, name FROM sites WHERE active ORDER BY code")
    depts = call.tx.q("SELECT code, name_en FROM departments WHERE active ORDER BY code")
    vendors = call.tx.q("SELECT code, name FROM vendors WHERE active ORDER BY code")
    for i in range(max(len(classes), len(sites), len(depts), len(vendors))):
        row = []
        row += [classes[i]["code"], classes[i]["name_en"], classes[i]["category"]] if i < len(classes) else ["", "", ""]
        row += [""]
        row += [sites[i]["code"], sites[i]["name"]] if i < len(sites) else ["", ""]
        row += [""]
        row += [depts[i]["code"], depts[i]["name_en"]] if i < len(depts) else ["", ""]
        row += [""]
        row += [vendors[i]["code"], vendors[i]["name"]] if i < len(vendors) else ["", ""]
        lists.append(row)
    _style_sheet(lists, 1, [14, 30, 18, 3, 12, 28, 3, 16, 26, 3, 14, 30])
    out = io.BytesIO()
    wb.save(out)
    return Response(out.getvalue(), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="commach-asset-template.xlsx"'})


@endpoint(auth="tenant_user")
def export_assets(call: Call):
    from openpyxl import Workbook
    where, params = _filters(call)
    rows = call.tx.q(LIST_SQL.replace("SELECT a.id,", "SELECT a.id, a.name_ar AS name_ar2, a.asset_tag, a.capacity,"
                                      " a.range_min, a.range_max, a.unit, a.resolution, a.accuracy, a.install_date,"
                                      " a.external_ref, a.notes,") + where + " ORDER BY s.code, a.code LIMIT 20000", *params)
    wb = Workbook()
    ws = wb.active
    ws.title = "Assets"
    head = ["Site", "Code", "Name (English)", "Name (Arabic)", "Class", "Category", "Parent", "Location", "Line",
            "Department", "Manufacturer", "Model", "Serial number", "Asset tag", "Capacity", "Range from", "Range to",
            "Unit", "Resolution", "Accuracy", "GMP criticality", "Calibration", "PM", "Portable", "Status",
            "Installation date", "External reference", "Notes"]
    ws.append(head)
    for r in rows:
        ws.append([r["site_code"], r["code"], r["name_en"], r["name_ar"], r["class_code"], r["category"], r["parent_code"],
                   r["location_code"], r["line_code"], r["department_code"], r["manufacturer"], r["model"], r["serial_no"],
                   r["asset_tag"], r["capacity"], r["range_min"], r["range_max"], r["unit"], r["resolution"],
                   r["accuracy"], r["gmp_criticality"], "Y" if r["calibration_required"] else "N",
                   "Y" if r["pm_required"] else "N", "Y" if r["is_portable"] else "N", r["status"], r["install_date"],
                   r["external_ref"], r["notes"]])
    _style_sheet(ws, 1, [8, 16, 30, 26, 12, 16, 14, 14, 10, 12, 18, 16, 16, 12, 14, 10, 10, 8, 12, 12, 12, 10, 6, 8, 14, 14, 16, 30])
    ws.auto_filter.ref = ws.dimensions
    call.tx.event("EXPORT", "assets", None, "Asset register", {"rows": len(rows), "format": "xlsx"})
    out = io.BytesIO()
    wb.save(out)
    stamp = dt.date.today().isoformat()
    return Response(out.getvalue(), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="commach-assets-{stamp}.xlsx"'})
