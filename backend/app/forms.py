"""Forms and records (Phase 5): print ComMach records on the company's controlled formats.

Two engines share the same record data (the "context"):
  blocks  a layout of blocks (header with logo, title, format number and revision; field grids; tables; text;
          Prepared / Approved / Authorized signature boxes; footer) rendered to PDF
  docx    the company's own Word file with merge fields: {{asset.code}} anywhere (body, tables, header, footer);
          a table row holding {{rows.<field>}} (or results / standards / parts / history / se_rows) is repeated
          once per item
Every print is a controlled copy: numbered, stamped with the template version, and logged with its SHA-256.
A template that is not approved prints with a DRAFT – NOT FOR GMP USE mark.
"""
from __future__ import annotations

import copy as _copy
import datetime as dt
import io
import re

SOURCES = ("calibration_plan", "pm_plan", "calibration_record", "pm_record", "history_card", "status_label")
LISTS = ("rows", "se_rows", "results", "standards", "parts", "history")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
VERDICT = {"calibration": {"pass": "Pass", "adjusted": "Out of tolerance – adjusted", "fail": "Out of tolerance"},
           "pm": {"pass": "Done – no findings", "fail": "Done – findings", "adjusted": "Done – adjusted"}}
STATUS_TEXT = {"draft": "Not yet released", "in_service": "In service", "restricted": "Restricted use", "standby": "Standby",
               "out_of_service": "Out of service – do not use", "quarantined": "Quarantine – do not use",
               "out_for_calibration": "At calibration – do not use", "retired": "Retired", "disposed": "Disposed",
               "transferred": "Transferred"}

# The merge fields each source offers (shown in the designer and in the Word field list).
FIELDS: dict[str, dict[str, list[str]]] = {
    "_common": {"company": ["name"], "site": ["code", "name"], "printed": ["by", "at", "date"],
                "form": ["code", "version", "title", "format_no", "revision", "status"], "copy": ["number"]},
    "calibration_plan": {"plan": ["year", "kind", "status", "count", "reviewed_by", "reviewed_at", "approved_by", "approved_at"],
                         "rows": ["n", "asset_code", "asset_name", "class_name", "location", "template", "interval", "vendor",
                                  *[f"m{i}" for i in range(1, 13)], *[f"e{i}" for i in range(1, 13)]],
                         "se_rows": ["n", "asset_code", "asset_name", "class_name", "location", "activity", *[f"m{i}" for i in range(1, 13)]]},
    "calibration_record": {"task": ["number", "due_date", "window_end", "performed_date", "performed_by", "verdict", "certificate_no",
                                    "env_temperature", "env_humidity", "remarks", "vendor", "done_by", "done_at", "checked_by", "checked_at"],
                           "asset": ["code", "name", "serial_no", "manufacturer", "model", "class_name", "location", "range",
                                     "resolution", "accuracy", "criticality", "department"],
                           "template": ["code", "version", "title", "method"],
                           "results": ["n", "parameter", "nominal", "unit", "limits", "as_found", "as_left", "uncertainty", "result", "notes"],
                           "standards": ["n", "description", "cert_ref", "valid_until"]},
    "history_card": {"asset": ["code", "name", "serial_no", "manufacturer", "model", "capacity", "class_name", "location",
                               "install_date", "install_year", "department"],
                     "history": ["n", "date", "kind", "number", "details", "result", "remarks", "parts", "man_hours", "done_by", "checked_by"]},
    "status_label": {"asset": ["code", "name", "serial_no", "status", "class_name", "location", "cal_last", "cal_due",
                               "pm_last", "pm_due", "done_by", "checked_by"]},
}
FIELDS["pm_plan"] = FIELDS["calibration_plan"]
FIELDS["pm_record"] = {**FIELDS["calibration_record"],
                       "task": FIELDS["calibration_record"]["task"] + ["man_hours"],
                       "parts": ["n", "name", "part_no", "qty", "unit"]}


def fmt_date(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        try:
            v = dt.date.fromisoformat(v[:10])
        except ValueError:
            return v
    if isinstance(v, dt.datetime):
        return v.strftime("%d %b %Y %H:%M")
    return v.strftime("%d %b %Y")


def num(v) -> str:
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.6f}".rstrip("0").rstrip(".") if f != int(f) else str(int(f))


# ------------------------------------------------------------------ record data
def _common(call, form: dict | None, copy_no: str, site: dict | None) -> dict:
    company = call.tx.val("SELECT name FROM tenants WHERE id = %s", call.tenant_id) or ""
    now = dt.datetime.now(dt.timezone.utc)
    f = form or {}
    return {"company": {"name": company}, "site": {"code": (site or {}).get("code", ""), "name": (site or {}).get("name", "")},
            "printed": {"by": call.user["full_name"], "at": now.strftime("%d %b %Y %H:%M UTC"), "date": now.strftime("%d %b %Y")},
            "form": {"code": f.get("code", ""), "version": f.get("version", ""), "title": f.get("title", ""),
                     "format_no": f.get("format_no") or "", "revision": f.get("revision") or "", "status": f.get("status", "draft")},
            "copy": {"number": copy_no}}


def plan_context(call, site_id: str, kind: str, year: int) -> tuple[dict, str]:
    from .routers.work import plan_view
    v = plan_view(call, site_id, kind, year)
    site = call.tx.one("SELECT code, name FROM sites WHERE id = %s::uuid", site_id)
    classes = {str(r["id"]): r["class_name"] for r in call.tx.q(
        "SELECT a.id, c.name_en AS class_name FROM assets a JOIN asset_classes c ON c.id = a.class_id WHERE a.site_id = %s::uuid", site_id)}
    rows, se = [], []
    for n, r in enumerate(v["rows"], start=1):
        sched = {m: [] for m in range(1, 13)}
        done = {m: [] for m in range(1, 13)}
        for e in r["entries"]:
            sched[e["due_date"].month].append(str(e["due_date"].day))
            if e.get("status") == "completed" and e.get("performed_date"):
                done[e["performed_date"].month].append(str(e["performed_date"].day))
        interval = f"{r['interval_value']} {r['interval_unit']}{'s' if (r['interval_value'] or 0) > 1 else ''}" if r["interval_value"] else ""
        base = {"n": n, "asset_code": r["asset_code"], "asset_name": r["asset_name_en"], "class_name": classes.get(str(r["asset_id"]), ""),
                "location": r["location_code"] or "", "template": r["template_code"] or "", "interval": interval, "vendor": r["vendor_name"] or ""}
        row = dict(base)
        for m in range(1, 13):
            row[f"m{m}"] = ", ".join(sched[m])
            row[f"e{m}"] = ", ".join(done[m])
        rows.append(row)
        se.append({**base, "activity": "S", **{f"m{m}": row[f"m{m}"] for m in range(1, 13)}})
        se.append({**{k: "" for k in base}, "activity": "E", **{f"m{m}": row[f"e{m}"] for m in range(1, 13)}})
    p = v["plan"] or {}
    rs, aps = p.get("reviewed_signature"), p.get("approved_signature")
    ctx = {"plan": {"year": year, "kind": "Calibration" if kind == "calibration" else "Preventive maintenance",
                    "status": {"draft": "Draft", "reviewed": "Reviewed", "approved": "Approved"}[v["status"]], "count": v["count"],
                    "reviewed_by": (rs or {}).get("user_name") or p.get("reviewed_by_name") or "",
                    "reviewed_at": fmt_date((rs or {}).get("signed_at") or p.get("reviewed_at")),
                    "approved_by": (aps or {}).get("user_name") or p.get("approved_by_name") or "",
                    "approved_at": fmt_date((aps or {}).get("signed_at") or p.get("approved_at"))},
           "rows": rows, "se_rows": se, "_site": site}
    return ctx, f"{site['code']} {'calibration' if kind == 'calibration' else 'PM'} plan {year}"


def task_context(call, task_id: str) -> tuple[dict, str, str]:
    from .routers.work import task_detail
    t = task_detail(call, task_id)
    a = t["asset"]
    full = call.tx.one("SELECT a.capacity, a.unit, l.code AS location_code, l.name_en AS location_name, d.name_en AS department"
                       " FROM assets a LEFT JOIN locations l ON l.id = a.location_id LEFT JOIN departments d ON d.id = a.department_id"
                       " WHERE a.id = %s", t["asset_id"])
    site = call.tx.one("SELECT code, name FROM sites WHERE id = %s", t["site_id"])
    kind = t["kind"]
    results = []
    for n, r in enumerate(t["results"], start=1):
        if r["item_type"] == "check":
            res = {"ok": "OK", "not_ok": "Not OK", "na": "N/A"}.get(r.get("value_text") or "", "")
        elif r["item_type"] in ("point", "reading"):
            ok = r.get("ok_found")
            res = "" if ok is None else ("In" if ok else "Out")
            if r["item_type"] == "point" and r.get("as_left") is not None and r.get("ok_left") is not None:
                res += f" / {'In' if r['ok_left'] else 'Out'}"
        elif r["item_type"] == "photo":
            res = "Photo attached" if r.get("file_id") else ""
        else:
            res = r.get("value_text") or ""
        lo, hi = r.get("low_limit"), r.get("high_limit")
        results.append({"n": n, "parameter": r["parameter"], "nominal": num(r.get("nominal")), "unit": r.get("unit") or "",
                        "limits": f"{num(lo) or '–∞'} … {num(hi) or '∞'}" if (lo is not None or hi is not None) else "",
                        "as_found": num(r.get("as_found")), "as_left": num(r.get("as_left")), "uncertainty": num(r.get("uncertainty")),
                        "result": res, "notes": r.get("notes") or ""})
    sig, rev = t.get("signature") or {}, t.get("review_signature") or {}
    rng = f"{num(a.get('range_min'))} – {num(a.get('range_max'))} {a.get('unit') or ''}".strip() \
        if a.get("range_min") is not None or a.get("range_max") is not None else ""
    ctx = {"task": {"number": t["number"], "due_date": fmt_date(t["due_date"]), "window_end": fmt_date(t["window_end"]),
                    "performed_date": fmt_date(t.get("performed_date")), "performed_by": t.get("performed_by_name") or "",
                    "verdict": VERDICT[kind].get(t.get("verdict") or "", "" if t["status"] != "cancelled" else "Cancelled"),
                    "certificate_no": t.get("certificate_no") or "", "env_temperature": t.get("env_temperature") or "",
                    "env_humidity": t.get("env_humidity") or "", "remarks": t.get("remarks") or "", "vendor": t.get("vendor_name") or "",
                    "man_hours": num(t.get("man_hours")), "done_by": sig.get("user_name") or t.get("completed_by_name") or "",
                    "done_at": fmt_date(sig.get("signed_at") or t.get("completed_at")),
                    "checked_by": rev.get("user_name") or t.get("reviewed_by_name") or "",
                    "checked_at": fmt_date(rev.get("signed_at") or t.get("reviewed_at"))},
           "asset": {"code": a["code"], "name": a["name_en"], "serial_no": a.get("serial_no") or "", "manufacturer": a.get("manufacturer") or "",
                     "model": a.get("model") or "", "class_name": a["class_name_en"],
                     "location": f"{full['location_code']} {full['location_name'] or ''}".strip() if full["location_code"] else "",
                     "range": rng, "resolution": a.get("resolution") or "", "accuracy": a.get("accuracy") or "",
                     "criticality": (a.get("gmp_criticality") or "").replace("_", "-").upper(), "department": full["department"] or ""},
           "template": {"code": (t.get("template") or {}).get("code", ""), "version": (t.get("template") or {}).get("version", ""),
                        "title": (t.get("template") or {}).get("title", ""), "method": (t.get("template") or {}).get("method_ref") or ""},
           "results": results,
           "standards": [{"n": n, "description": s["description"], "cert_ref": s.get("cert_ref") or "", "valid_until": fmt_date(s.get("valid_until"))}
                         for n, s in enumerate(t["standards"], start=1)],
           "parts": [{"n": n, "name": p["name"], "part_no": p.get("part_no") or "", "qty": num(p.get("qty")), "unit": p.get("unit") or ""}
                     for n, p in enumerate(t.get("parts") or [], start=1)],
           "_site": site}
    return ctx, f"{'Calibration' if kind == 'calibration' else 'PM'} {t['number']}", kind


def asset_context(call, asset_id: str) -> tuple[dict, str]:
    from .perms import require_site
    a = call.tx.one("SELECT a.*, c.name_en AS class_name, l.code AS location_code, l.name_en AS location_name, d.name_en AS department,"
                    " s.code AS site_code, s.name AS site_name FROM assets a JOIN asset_classes c ON c.id = a.class_id"
                    " JOIN sites s ON s.id = a.site_id LEFT JOIN locations l ON l.id = a.location_id"
                    " LEFT JOIN departments d ON d.id = a.department_id WHERE a.id = %s::uuid", asset_id)
    if not a:
        from .api import not_found
        raise not_found("Asset")
    require_site(call, str(a["site_id"]))
    hist = call.tx.q(
        "SELECT t.id, t.kind, t.number, t.performed_date, t.verdict, t.remarks, t.man_hours, t.completed_by_name, t.reviewed_by_name,"
        " tp.title AS template_title, (SELECT string_agg(p.name || coalesce(' ×' || trim(to_char(p.qty, 'FM999990.##')), ''), '; ')"
        "   FROM task_parts p WHERE p.task_id = t.id) AS parts"
        " FROM tasks t LEFT JOIN templates tp ON tp.id = t.template_id WHERE t.asset_id = %s AND t.status = 'completed'"
        " ORDER BY t.performed_date, t.number", a["id"])
    last = {k: call.tx.one("SELECT performed_date, completed_by_name, reviewed_by_name FROM tasks WHERE asset_id = %s AND kind = %s"
                           " AND status = 'completed' ORDER BY performed_date DESC LIMIT 1", a["id"], k) for k in ("calibration", "pm")}
    due = {k: call.tx.val("SELECT min(due_date) FROM tasks WHERE asset_id = %s AND kind = %s AND status NOT IN ('completed', 'cancelled')",
                          a["id"], k) for k in ("calibration", "pm")}
    lc = last["calibration"] or {}
    ctx = {"asset": {"code": a["code"], "name": a["name_en"], "serial_no": a["serial_no"] or "", "manufacturer": a["manufacturer"] or "",
                     "model": a["model"] or "", "capacity": a["capacity"] or "", "class_name": a["class_name"],
                     "location": f"{a['location_code']} {a['location_name'] or ''}".strip() if a["location_code"] else "",
                     "install_date": fmt_date(a["install_date"]), "install_year": a["install_date"].year if a["install_date"] else "",
                     "department": a["department"] or "", "status": STATUS_TEXT.get(a["status"], a["status"]),
                     "cal_last": fmt_date(lc.get("performed_date")), "cal_due": fmt_date(due["calibration"]),
                     "pm_last": fmt_date((last["pm"] or {}).get("performed_date")), "pm_due": fmt_date(due["pm"]),
                     "done_by": lc.get("completed_by_name") or "", "checked_by": lc.get("reviewed_by_name") or ""},
           "history": [{"n": n, "date": fmt_date(h["performed_date"]), "kind": "Calibration" if h["kind"] == "calibration" else "PM",
                        "number": h["number"], "details": h["template_title"] or ("Calibration" if h["kind"] == "calibration" else "Maintenance"),
                        "result": VERDICT[h["kind"]].get(h["verdict"] or "", ""), "remarks": h["remarks"] or "", "parts": h["parts"] or "",
                        "man_hours": num(h["man_hours"]), "done_by": h["completed_by_name"] or "", "checked_by": h["reviewed_by_name"] or ""}
                       for n, h in enumerate(hist, start=1)],
           "_site": {"code": a["site_code"], "name": a["site_name"]}}
    return ctx, a["code"]


# ------------------------------------------------------------------ template strings
_TOKEN = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


def lookup(ctx: dict, path: str):
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    return "" if cur is None else cur


def fill(text: str, ctx: dict) -> str:
    return _TOKEN.sub(lambda m: str(lookup(ctx, m.group(1))), text or "")


# ------------------------------------------------------------------ standard layouts
def _cols(spec: list[tuple[str, str, float]]) -> list[dict]:
    return [{"header": h, "value": v, "width": w} for h, v, w in spec]


MONTH_COLS = [(MONTHS[i], f"{{{{se_rows.m{i + 1}}}}}", 1.0) for i in range(12)]
PLAN_SIGS = [{"label": "Prepared by", "value": "{{plan.reviewed_by}}", "date": "{{plan.reviewed_at}}"},
             {"label": "Approved by (Engineering)", "value": "{{plan.reviewed_by}}", "date": "{{plan.reviewed_at}}"},
             {"label": "Authorized by (Head QA)", "value": "{{plan.approved_by}}", "date": "{{plan.approved_at}}"}]


def default_layout(source: str) -> dict:
    landscape = source in ("calibration_plan", "pm_plan", "history_card")
    head = {"logo": True, "title": "", "subtitle": "", "annexure": "", "show_company": True}
    if source == "calibration_plan":
        head.update(title="Annual calibration schedule of instruments and testing equipment for the year {{plan.year}}",
                    annexure="Annexure QA027-00/A01")
        blocks = [{"type": "fields", "columns": 4, "items": [{"label": "Site", "value": "{{site.code}} {{site.name}}"},
                                                             {"label": "Year", "value": "{{plan.year}}"},
                                                             {"label": "Plan status", "value": "{{plan.status}}"},
                                                             {"label": "Due dates", "value": "{{plan.count}}"}]},
                  {"type": "table", "list": "se_rows", "font": 7, "columns": _cols(
                      [("No.", "{{se_rows.n}}", 0.6), ("Equipment name", "{{se_rows.asset_name}}", 3.2), ("Equipment No.", "{{se_rows.asset_code}}", 1.8),
                       ("Instrument / testing equipment", "{{se_rows.class_name}}", 2.4), ("Activity", "{{se_rows.activity}}", 0.9)] + MONTH_COLS)},
                  {"type": "text", "text": "S: Scheduled (day of the month)   E: Executed (day of the month)", "style": "small"},
                  {"type": "signatures", "items": PLAN_SIGS}]
    elif source == "pm_plan":
        head.update(title="Equipment preventive maintenance schedule for the year {{plan.year}}", annexure="Annexure EN002-01/A01")
        blocks = [{"type": "fields", "columns": 4, "items": [{"label": "Site", "value": "{{site.code}} {{site.name}}"},
                                                             {"label": "Year", "value": "{{plan.year}}"},
                                                             {"label": "Plan status", "value": "{{plan.status}}"},
                                                             {"label": "Due dates", "value": "{{plan.count}}"}]},
                  {"type": "table", "list": "se_rows", "font": 7, "columns": _cols(
                      [("S. No.", "{{se_rows.n}}", 0.6), ("Room ID", "{{se_rows.location}}", 1.3), ("Equip ID", "{{se_rows.asset_code}}", 1.6),
                       ("Equipment", "{{se_rows.asset_name}}", 3.0), ("", "{{se_rows.activity}}", 0.6)] + MONTH_COLS)},
                  {"type": "text", "text": "S: Scheduled (day of the month)   E: Executed (day of the month)", "style": "small"},
                  {"type": "signatures", "items": PLAN_SIGS}]
    elif source == "calibration_record":
        head.update(title="Calibration record", annexure="")
        blocks = [{"type": "fields", "columns": 3, "items": [
                      {"label": "Record No.", "value": "{{task.number}}"}, {"label": "Equipment ID", "value": "{{asset.code}}"},
                      {"label": "Equipment", "value": "{{asset.name}}"}, {"label": "Make / model", "value": "{{asset.manufacturer}} {{asset.model}}"},
                      {"label": "Serial No.", "value": "{{asset.serial_no}}"}, {"label": "Location", "value": "{{asset.location}}"},
                      {"label": "Range", "value": "{{asset.range}}"}, {"label": "Resolution", "value": "{{asset.resolution}}"},
                      {"label": "Accuracy", "value": "{{asset.accuracy}}"}, {"label": "Procedure", "value": "{{template.code}} v{{template.version}} {{template.method}}"},
                      {"label": "Due date", "value": "{{task.due_date}}"}, {"label": "Date performed", "value": "{{task.performed_date}}"},
                      {"label": "Certificate No.", "value": "{{task.certificate_no}}"}, {"label": "Vendor", "value": "{{task.vendor}}"},
                      {"label": "Environment", "value": "{{task.env_temperature}} {{task.env_humidity}}"}]},
                  {"type": "table", "list": "results", "title": "Readings", "columns": _cols(
                      [("No.", "{{results.n}}", 0.5), ("Point", "{{results.parameter}}", 2.4), ("Nominal", "{{results.nominal}} {{results.unit}}", 1.3),
                       ("Limits", "{{results.limits}}", 1.8), ("As found", "{{results.as_found}}", 1.1), ("As left", "{{results.as_left}}", 1.1),
                       ("U", "{{results.uncertainty}}", 0.9), ("Result", "{{results.result}}", 1.1)])},
                  {"type": "table", "list": "standards", "title": "Reference standards", "columns": _cols(
                      [("No.", "{{standards.n}}", 0.5), ("Standard", "{{standards.description}}", 4), ("Certificate", "{{standards.cert_ref}}", 2),
                       ("Valid until", "{{standards.valid_until}}", 1.5)])},
                  {"type": "fields", "columns": 1, "items": [{"label": "Verdict", "value": "{{task.verdict}}"}, {"label": "Remarks", "value": "{{task.remarks}}"}]},
                  {"type": "signatures", "items": [{"label": "Performed by", "value": "{{task.done_by}}", "date": "{{task.done_at}}"},
                                                   {"label": "Checked by", "value": "{{task.checked_by}}", "date": "{{task.checked_at}}"}]}]
    elif source == "pm_record":
        head.update(title="Preventive maintenance record", annexure="")
        blocks = [{"type": "fields", "columns": 3, "items": [
                      {"label": "Work order", "value": "{{task.number}}"}, {"label": "Equipment ID", "value": "{{asset.code}}"},
                      {"label": "Equipment", "value": "{{asset.name}}"}, {"label": "Make / model", "value": "{{asset.manufacturer}} {{asset.model}}"},
                      {"label": "Location", "value": "{{asset.location}}"}, {"label": "Checklist", "value": "{{template.code}} v{{template.version}} {{template.title}}"},
                      {"label": "Due date", "value": "{{task.due_date}}"}, {"label": "Date performed", "value": "{{task.performed_date}}"},
                      {"label": "Man-hours", "value": "{{task.man_hours}}"}]},
                  {"type": "table", "list": "results", "title": "Checklist", "columns": _cols(
                      [("No.", "{{results.n}}", 0.5), ("Activity", "{{results.parameter}}", 5), ("Reading", "{{results.as_found}} {{results.unit}}", 1.3),
                       ("Limits", "{{results.limits}}", 1.4), ("Result", "{{results.result}}", 1.0), ("Remarks", "{{results.notes}}", 2.4)])},
                  {"type": "table", "list": "parts", "title": "Spare parts used", "columns": _cols(
                      [("No.", "{{parts.n}}", 0.5), ("Part", "{{parts.name}}", 4), ("Part No.", "{{parts.part_no}}", 2), ("Qty", "{{parts.qty}} {{parts.unit}}", 1.2)])},
                  {"type": "fields", "columns": 1, "items": [{"label": "Result", "value": "{{task.verdict}}"}, {"label": "Remarks", "value": "{{task.remarks}}"}]},
                  {"type": "signatures", "items": [{"label": "Done by", "value": "{{task.done_by}}", "date": "{{task.done_at}}"},
                                                   {"label": "Checked by", "value": "{{task.checked_by}}", "date": "{{task.checked_at}}"}]}]
    elif source == "history_card":
        head.update(title="Equipment history card", annexure="Annexure EN003-00/A02")
        blocks = [{"type": "fields", "columns": 3, "items": [
                      {"label": "Equipment name", "value": "{{asset.name}}"}, {"label": "Equipment ID", "value": "{{asset.code}}"},
                      {"label": "Make / model", "value": "{{asset.manufacturer}} {{asset.model}}"}, {"label": "Capacity", "value": "{{asset.capacity}}"},
                      {"label": "Date of installation", "value": "{{asset.install_date}}"}, {"label": "Location", "value": "{{asset.location}}"}]},
                  {"type": "table", "list": "history", "columns": _cols(
                      [("S. No", "{{history.n}}", 0.5), ("Date", "{{history.date}}", 1.2), ("Activity", "{{history.kind}} {{history.number}}", 1.8),
                       ("Details", "{{history.details}}", 2.6), ("Result", "{{history.result}}", 1.6), ("Parts / modification", "{{history.parts}}", 2.0),
                       ("Remarks", "{{history.remarks}}", 2.0), ("Done by", "{{history.done_by}}", 1.4), ("Checked by", "{{history.checked_by}}", 1.4)])}]
    else:  # status_label
        head.update(title="{{asset.status}}", annexure="")
        blocks = [{"type": "fields", "columns": 1, "items": [
                      {"label": "Equipment", "value": "{{asset.code}} – {{asset.name}}"}, {"label": "Serial No.", "value": "{{asset.serial_no}}"},
                      {"label": "Calibrated on", "value": "{{asset.cal_last}}"}, {"label": "Calibration due", "value": "{{asset.cal_due}}"},
                      {"label": "Done by", "value": "{{asset.done_by}}"}, {"label": "Checked by", "value": "{{asset.checked_by}}"}]}]
    return {"page": {"size": "A4" if source != "status_label" else "A6", "orientation": "landscape" if landscape else "portrait",
                     "margin_mm": 10}, "header": head, "blocks": blocks, "footer": {"text": "", "page_numbers": True}}


def pdf_page_count(pdf: bytes) -> int:
    import pypdfium2 as pdfium
    return len(pdfium.PdfDocument(pdf))


def pdf_pages(pdf: bytes, max_pages: int = 4, scale: float = 1.6) -> list[str]:
    """The first pages of a PDF as PNG data URLs (for the designer's live preview)."""
    import base64

    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    out = []
    for i in range(min(len(doc), max_pages)):
        img = doc[i].render(scale=scale).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        out.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
    return out


# ------------------------------------------------------------------ block renderer (PDF)
def render_blocks(layout: dict, ctx: dict, logo: bytes | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, A5, A6, landscape, portrait
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from .routers.labels import _font
    reg, bold = _font()
    page = layout.get("page") or {}
    size = {"A4": A4, "A5": A5, "A6": A6}.get(page.get("size", "A4"), A4)
    size = landscape(size) if page.get("orientation") == "landscape" else portrait(size)
    margin = float(page.get("margin_mm") or 10) * mm
    head = layout.get("header") or {}
    foot = layout.get("footer") or {}
    draft = ctx["form"]["status"] != "approved"
    st = {"n": ParagraphStyle("n", fontName=reg, fontSize=8.5, leading=10.5),
          "s": ParagraphStyle("s", fontName=reg, fontSize=7, leading=8.5, textColor=colors.HexColor("#5c546c")),
          "h": ParagraphStyle("h", fontName=bold, fontSize=10.5, leading=13, spaceBefore=4, spaceAfter=2),
          "lbl": ParagraphStyle("lbl", fontName=reg, fontSize=7, leading=8.5, textColor=colors.HexColor("#5c546c")),
          "val": ParagraphStyle("val", fontName=bold, fontSize=8.5, leading=10.5)}

    def P(text, style="n", size_=None):
        s = st[style]
        if size_:
            s = ParagraphStyle(f"{style}{size_}", parent=s, fontSize=size_, leading=size_ * 1.22)
        safe = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
        return Paragraph(safe, s)

    width = size[0] - 2 * margin
    story = []
    # header band
    title = fill(head.get("title", ""), ctx)
    left = []
    if head.get("logo") and logo:
        try:
            from reportlab.lib.utils import ImageReader
            ir = ImageReader(io.BytesIO(logo))
            iw, ih = ir.getSize()
            h_ = 14 * mm
            left.append(Image(io.BytesIO(logo), width=h_ * iw / ih, height=h_))
        except Exception:
            pass
    if head.get("show_company", True):
        left.append(P(ctx["company"]["name"], "val"))
    right = [P(fill(head.get("annexure", ""), ctx), "s"),
             P(f"Format No.: {ctx['form']['format_no']}" if ctx["form"]["format_no"] else "", "s"),
             P(f"Revision: {ctx['form']['revision']}" if ctx["form"]["revision"] else "", "s")]
    center = [P(title, "h"), P(fill(head.get("subtitle", ""), ctx), "s")]
    ht = Table([[left or [""], center, right]], colWidths=[width * 0.22, width * 0.56, width * 0.22])
    ht.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#433b52")),
                            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbc5d6")),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "CENTER")]))
    story += [ht, Spacer(1, 4 * mm)]
    grid = colors.HexColor("#cbc5d6")
    for b in layout.get("blocks") or []:
        t = b.get("type")
        if t == "fields":
            cols = max(1, min(6, int(b.get("columns") or 2)))
            cells = [[P(fill(i.get("label", ""), ctx), "lbl"), P(fill(i.get("value", ""), ctx), "val")] for i in b.get("items") or []]
            rows = []
            for k in range(0, len(cells), cols):
                chunk = cells[k:k + cols]
                chunk += [[P(""), P("")]] * (cols - len(chunk))
                rows.append([x for pair in chunk for x in pair])
            if rows:
                cw = []
                for _ in range(cols):
                    cw += [width / cols * 0.36, width / cols * 0.64]
                tb = Table(rows, colWidths=cw)
                tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, grid), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                        ("BACKGROUND", (0, 0), (-1, -1), colors.white)]
                                       + [("BACKGROUND", (c * 2, 0), (c * 2, -1), colors.HexColor("#f2f0f6")) for c in range(cols)]))
                story += [tb, Spacer(1, 3 * mm)]
        elif t == "table":
            name = b.get("list") if b.get("list") in LISTS else "rows"
            items = ctx.get(name) or []
            cols = b.get("columns") or []
            if not cols:
                continue
            fs = float(b.get("font") or 8)
            if b.get("title"):
                story.append(P(fill(b["title"], ctx), "h"))
            total = sum(float(c.get("width") or 1) for c in cols)
            cw = [width * float(c.get("width") or 1) / total for c in cols]
            data = [[P(fill(c.get("header", ""), ctx), "val", fs) for c in cols]]
            for it in items:
                sub = {**ctx, name: it}
                data.append([P(fill(c.get("value", ""), sub), "n", fs) for c in cols])
            if len(data) == 1:
                data.append([P("—", "s")] + [P("")] * (len(cols) - 1))
            tb = Table(data, colWidths=cw, repeatRows=1)
            style = [("GRID", (0, 0), (-1, -1), 0.4, grid), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9e6f0")),
                     ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
            if name == "se_rows":
                for r in range(1, len(data), 2):
                    style.append(("LINEBELOW", (0, r), (-1, r), 0.2, colors.HexColor("#e3dfea")))
                    style.append(("LINEBELOW", (0, r + 1), (-1, r + 1), 0.7, colors.HexColor("#857e95")))
            tb.setStyle(TableStyle(style))
            story += [tb, Spacer(1, 3 * mm)]
        elif t == "text":
            story += [P(fill(b.get("text", ""), ctx), {"small": "s", "heading": "h"}.get(b.get("style"), "n")), Spacer(1, 2 * mm)]
        elif t == "signatures":
            items = b.get("items") or []
            if not items:
                continue
            cells = [[P(fill(i.get("label", ""), ctx), "lbl"), P(fill(i.get("value", ""), ctx), "val"),
                      P((fill(i.get("date", ""), ctx) or " ") + "\n(Sign & Date)", "s")] for i in items]
            tb = Table([[c for c in cells]], colWidths=[width / len(items)] * len(items), rowHeights=[20 * mm])
            tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#433b52")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story += [Spacer(1, 2 * mm), tb, Spacer(1, 2 * mm)]
        elif t == "spacer":
            story.append(Spacer(1, float(b.get("mm") or 4) * mm))

    footer_text = fill(foot.get("text", ""), ctx)
    copy_line = (f"Controlled copy {ctx['copy']['number']} · {ctx['form']['code']} v{ctx['form']['version']}"
                 f" · printed {ctx['printed']['at']} by {ctx['printed']['by']}")

    from reportlab.pdfgen import canvas as _canvas

    class Numbered(_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._pages)
            for i, state in enumerate(self._pages, start=1):
                self.__dict__.update(state)
                self._decorate(i, n)
                super().showPage()
            super().save()

        def _decorate(self, i, n):
            self.saveState()
            self.setFont(reg, 7)
            self.setFillColor(colors.HexColor("#5c546c"))
            y = margin * 0.45
            if ctx["form"]["format_no"]:
                self.drawString(margin, y, f"Format Number: {ctx['form']['format_no']}")
            if foot.get("page_numbers", True):
                self.drawRightString(size[0] - margin, y, f"Page {i} of {n}")
            self.drawCentredString(size[0] / 2, y, copy_line[:150])
            if footer_text:
                self.drawCentredString(size[0] / 2, y + 9, footer_text[:160])
            if draft:
                self.setFillColor(colors.Color(0.75, 0.1, 0.1, alpha=0.16))
                self.setFont(bold, 46)
                self.translate(size[0] / 2, size[1] / 2)
                self.rotate(30)
                self.drawCentredString(0, 0, "DRAFT – NOT FOR GMP USE")
            self.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin + 4 * mm,
                            title=title or ctx["form"]["title"], author=ctx["company"]["name"])
    doc.build(story, canvasmaker=Numbered)
    return buf.getvalue()


# ------------------------------------------------------------------ Word templates
def _paragraphs(doc):
    def walk_cells(tables):
        for t in tables:
            for row in t.rows:
                for cell in row.cells:
                    yield from cell.paragraphs
                    yield from walk_cells(cell.tables)
    yield from doc.paragraphs
    yield from walk_cells(doc.tables)
    for s in doc.sections:
        for part in (s.header, s.footer, s.first_page_header, s.first_page_footer):
            try:
                yield from part.paragraphs
                yield from walk_cells(part.tables)
            except Exception:
                continue


def _replace_paragraph(p, ctx: dict) -> None:
    text = "".join(r.text for r in p.runs)
    if "{{" not in text:
        return
    new = fill(text, ctx)
    if not p.runs:
        return
    # keep the first run's formatting for the merged text
    p.runs[0].text = new
    for r in p.runs[1:]:
        r.text = ""


def _row_list(row) -> str | None:
    text = " ".join(c.text for c in row.cells)
    for m in _TOKEN.finditer(text):
        head = m.group(1).split(".")[0]
        if head in LISTS:
            return head
    return None


def _all_tables(doc):
    def walk(tables):
        for t in tables:
            yield t
            for row in t.rows:
                for cell in row.cells:
                    yield from walk(cell.tables)
    yield from walk(doc.tables)
    for s in doc.sections:
        for part in (s.header, s.footer):
            try:
                yield from walk(part.tables)
            except Exception:
                continue


def render_docx(data: bytes, ctx: dict) -> bytes:
    import docx
    doc = docx.Document(io.BytesIO(data))
    # repeat rows first, so their placeholders get each item's values
    for table in list(_all_tables(doc)):
        for row in list(table.rows):
            name = _row_list(row)
            if not name:
                continue
            items = ctx.get(name) or []
            tr = row._tr
            parent = tr.getparent()
            anchor = tr
            for it in items:
                clone = _copy.deepcopy(tr)
                anchor.addnext(clone)
                anchor = clone
                from docx.table import _Row
                r = _Row(clone, table)
                sub = {**ctx, name: it}
                for cell in r.cells:
                    for p in cell.paragraphs:
                        _replace_paragraph(p, sub)
            parent.remove(tr)
    for p in _paragraphs(doc):
        _replace_paragraph(p, ctx)
    # controlled copy line and draft mark
    sec = doc.sections[0]
    try:
        fp = sec.footer.add_paragraph()
        run = fp.add_run(f"Controlled copy {ctx['copy']['number']} · {ctx['form']['code']} v{ctx['form']['version']} · "
                         f"printed {ctx['printed']['at']} by {ctx['printed']['by']}")
        run.font.size = docx.shared.Pt(6.5)
        if ctx["form"]["status"] != "approved":
            hp = sec.header.paragraphs[0] if sec.header.paragraphs else sec.header.add_paragraph()
            r2 = hp.insert_paragraph_before().add_run("DRAFT – NOT FOR GMP USE")
            r2.bold = True
            r2.font.color.rgb = docx.shared.RGBColor(0xB4, 0x23, 0x18)
    except Exception:
        pass
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def placeholders(data: bytes) -> list[str]:
    """The merge fields a Word template uses (checked against the source when it is uploaded)."""
    import docx
    doc = docx.Document(io.BytesIO(data))
    found = set()
    for p in _paragraphs(doc):
        found |= {m.group(1) for m in _TOKEN.finditer("".join(r.text for r in p.runs))}
    return sorted(found)


def unknown_fields(source: str, used: list[str]) -> list[str]:
    known = {**FIELDS["_common"], **FIELDS[source]}
    bad = []
    for u in used:
        head, _, rest = u.partition(".")
        if head not in known or (rest and rest not in known[head]):
            bad.append(u)
    return bad


def starter_docx(source: str) -> bytes:
    """A Word file with this source's merge fields in place: the company restyles it into its own format."""
    import docx
    from docx.enum.section import WD_ORIENT
    from docx.shared import Pt
    lay = default_layout(source)
    doc = docx.Document()
    sec = doc.sections[0]
    if lay["page"]["orientation"] == "landscape":
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = sec.page_height, sec.page_width
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(8)
    hp = sec.header.paragraphs[0]
    hp.text = (lay["header"].get("annexure") or "{{company.name}}") + "\t\tFormat No.: {{form.format_no}}  Rev. {{form.revision}}"
    doc.add_heading(lay["header"]["title"], level=1)
    for b in lay["blocks"]:
        if b["type"] == "fields":
            t = doc.add_table(rows=0, cols=2 * b.get("columns", 2))
            t.style = "Table Grid"
            items = b["items"]
            per = b.get("columns", 2)
            for k in range(0, len(items), per):
                cells = t.add_row().cells
                for j, it in enumerate(items[k:k + per]):
                    cells[2 * j].text = it["label"]
                    cells[2 * j + 1].text = it["value"]
            doc.add_paragraph()
        elif b["type"] == "table":
            if b.get("title"):
                doc.add_paragraph(b["title"]).runs[0].bold = True
            cols = b["columns"]
            t = doc.add_table(rows=2, cols=len(cols))
            t.style = "Table Grid"
            for j, c in enumerate(cols):
                t.rows[0].cells[j].text = c["header"]
                t.rows[1].cells[j].text = c["value"]
            doc.add_paragraph()
        elif b["type"] == "text":
            doc.add_paragraph(b["text"])
        elif b["type"] == "signatures":
            t = doc.add_table(rows=2, cols=len(b["items"]))
            t.style = "Table Grid"
            for j, it in enumerate(b["items"]):
                t.rows[0].cells[j].text = it["label"]
                t.rows[1].cells[j].text = f"{it['value']}\n{it.get('date', '')}\n(Sign & Date)"
    fp = sec.footer.paragraphs[0]
    fp.text = "Format Number: {{form.format_no}}"
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
