"""AI jobs in ComMach. Each answer is stored as a suggestion (provider, model, prompt version, SHA-256 of the
input and of the raw answer) and becomes data only when a person accepts it, with their corrections recorded.

  nameplate       photo of an equipment nameplate -> manufacturer, model, serial, ranges, class, name
  import_mapping  headers and sample rows of a customer's own register -> ComMach columns and value mappings
  certificate     calibration certificate -> readings mapped to the task's points, plus validation checks
  impact          out-of-tolerance result -> first draft of the impact assessment for QA
"""
from __future__ import annotations

import hashlib
import io
import json
import re

from .api import ApiError, Call
from .common import features
from .defaults import effective_ai
from .engine import ai

PROMPTS = {"nameplate": "nameplate-v1", "import_mapping": "import-mapping-v1"}


# ------------------------------------------------------------------ shared
def provider_for(call: Call, feature: str) -> ai.Provider:
    if not features(call).get(feature, True):
        raise ApiError(403, "ai_off", "This AI function is switched off in Company settings.")
    settings = effective_ai(call.tx.val("SELECT engine FROM tenants WHERE id = %s", call.tenant_id))
    p = ai.pick(settings["provider"])
    if not p:
        raise ApiError(503, "ai_unavailable", "No AI provider is configured on the server. Set QWEN_API_KEY (or AI_API_KEY).")
    limit = settings["daily_limit"]
    used = call.tx.val("SELECT requests FROM ai_usage WHERE user_id = %s AND day = current_date", call.user["id"]) or 0
    if limit and used >= limit:
        raise ApiError(429, "ai_limit", f"You have used today's {limit} AI requests. The limit resets tomorrow.")
    return p


def record_usage(call: Call, usage: dict) -> None:
    tokens = int((usage or {}).get("total_tokens") or 0)
    call.tx.x("INSERT INTO ai_usage (tenant_id, user_id, day, requests, tokens) VALUES (%s, %s, current_date, 1, %s)"
              " ON CONFLICT (user_id, day) DO UPDATE SET requests = ai_usage.requests + 1, tokens = ai_usage.tokens + %s",
              call.tenant_id, call.user["id"], tokens, tokens)


def store_suggestion(call: Call, kind: str, provider: ai.Provider, model: str, output: dict,
                     file_id: str | None = None, input_sha: str | None = None) -> dict:
    raw = json.dumps(output, sort_keys=True, ensure_ascii=False)
    row = call.tx.one(
        "INSERT INTO ai_suggestions (tenant_id, kind, file_id, provider, model, prompt_version, input_sha256, output,"
        " output_sha256, created_by, created_by_name) VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)"
        " RETURNING id, kind, model, prompt_version, output_sha256, created_at",
        call.tenant_id, kind, file_id, provider.name, model, PROMPTS[kind], input_sha, raw,
        hashlib.sha256(raw.encode("utf-8")).hexdigest(), call.user["id"], call.user["full_name"])
    call.tx.event("AI_SUGGESTED", "ai_suggestions", str(row["id"]), kind,
                  {"model": model, "prompt": PROMPTS[kind], "output_sha256": row["output_sha256"], "file_id": file_id})
    return row


def _norm(v):
    if v is None:
        return None
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        return s.lower() or None


def accept_suggestion(call: Call, suggestion_id: str, entity: str, record_id: str, final: dict) -> None:
    """Record that a person accepted the suggestion, and which proposed values they changed."""
    s = call.tx.one("SELECT * FROM ai_suggestions WHERE id = %s::uuid", suggestion_id)
    if not s:
        raise ApiError(404, "not_found", "The AI suggestion was not found.")
    if s["status"] != "proposed":
        raise ApiError(409, "already_reviewed", "This AI suggestion has already been reviewed.")
    proposed = (s["output"] or {}).get("fields") or {}
    changed = {}
    for k, v in proposed.items():
        if k in final and _norm(v) != _norm(final.get(k)):
            changed[k] = {"ai": v, "final": final.get(k)}
    final_json = json.dumps({k: final[k] for k in final}, default=str, ensure_ascii=False)
    call.tx.x("UPDATE ai_suggestions SET status = 'accepted', record_entity = %s, record_id = %s::uuid, final = %s::jsonb,"
              " changed_fields = %s::jsonb, reviewed_by = %s, reviewed_by_name = %s, reviewed_at = now() WHERE id = %s",
              entity, record_id, final_json, json.dumps(changed, default=str, ensure_ascii=False), call.user["id"],
              call.user["full_name"], s["id"])
    call.tx.event("AI_REVIEWED", "ai_suggestions", str(s["id"]), s["kind"],
                  {"decision": "accepted", "record": f"{entity}:{record_id}", "changed_fields": sorted(changed)})


def reject_suggestion(call: Call, suggestion_id: str) -> None:
    s = call.tx.one("SELECT id, kind, status FROM ai_suggestions WHERE id = %s::uuid", suggestion_id)
    if not s or s["status"] != "proposed":
        return
    call.tx.x("UPDATE ai_suggestions SET status = 'rejected', reviewed_by = %s, reviewed_by_name = %s, reviewed_at = now()"
              " WHERE id = %s", call.user["id"], call.user["full_name"], s["id"])
    call.tx.event("AI_REVIEWED", "ai_suggestions", str(s["id"]), s["kind"], {"decision": "rejected"})


def image_from_file(mime: str, data: bytes):
    from PIL import Image, ImageOps
    if mime == "application/pdf":
        import pypdfium2 as pdfium
        page = pdfium.PdfDocument(io.BytesIO(data))[0]
        img = page.render(scale=2).to_pil()
    else:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    img = img.convert("RGB")
    img.thumbnail((1800, 1800))
    return img


# ------------------------------------------------------------------ nameplate
NAMEPLATE_PROMPT = """You read equipment nameplates and labels for a GMP calibration and maintenance register.
The photo shows a nameplate, rating plate or label of: {hint}.
Read only what is printed. Never guess a value that is not visible; use null instead.
Asset classes available (code: name): {classes}

Return JSON:
{{"fields": {{
   "name_en": "short descriptive English name, e.g. 'Analytical balance' or 'Tablet press'",
   "name_ar": "the same name in Arabic",
   "manufacturer": "...", "model": "...", "serial_no": "...",
   "capacity": "capacity or rating as printed, e.g. '220 g' or '45 stations'",
   "range_min": number or null, "range_max": number or null, "unit": "unit of the measuring range, e.g. 'g', 'bar', '°C'",
   "resolution": "readability / division as printed, e.g. 'd = 0.1 mg'",
   "accuracy": "accuracy class or tolerance as printed",
   "year": "year of manufacture if printed",
   "power": "voltage / power as printed",
   "class_code": "the best matching class code from the list, or null"
 }},
 "confidence": {{"<field>": "high" | "medium" | "low"}},
 "printed_text": ["every line of text you can read, as printed"],
 "notes": "anything the reviewer should check (blur, glare, partly hidden text)"}}"""


def run_nameplate(call: Call, file_row: dict, data: bytes, hint: str | None) -> dict:
    p = provider_for(call, "ai_nameplate")
    classes = call.tx.q("SELECT code, name_en FROM asset_classes WHERE active ORDER BY code")
    prompt = NAMEPLATE_PROMPT.format(hint=(hint or "a piece of pharmaceutical plant or laboratory equipment")[:200],
                                     classes="; ".join(f"{c['code']}: {c['name_en']}" for c in classes))
    try:
        img = image_from_file(file_row["mime"], data)
    except Exception:
        raise ApiError(422, "bad_file", "This photo could not be opened.")
    msgs = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                         {"type": "image_url", "image_url": {"url": ai.img_data_url(img)}}]}]
    try:
        out = ai.chat(p, p.vision_model, msgs, max_tokens=1500, timeout=90)
    except ai.AIUnavailable as e:
        raise ApiError(502, "ai_failed", f"The AI could not read the photo: {e}")
    record_usage(call, out.get("usage"))
    data_out = out["data"] if isinstance(out["data"], dict) else {}
    fields = data_out.get("fields") if isinstance(data_out.get("fields"), dict) else {}
    clean_fields = {}
    for k in ("name_en", "name_ar", "manufacturer", "model", "serial_no", "capacity", "unit", "resolution", "accuracy",
              "year", "power", "class_code"):
        v = fields.get(k)
        if v not in (None, "", "null"):
            clean_fields[k] = str(v).strip()[:200]
    for k in ("range_min", "range_max"):
        v = fields.get(k)
        try:
            if v not in (None, "", "null"):
                clean_fields[k] = float(str(v).replace(",", "."))
        except ValueError:
            pass
    codes = {c["code"] for c in classes}
    if clean_fields.get("class_code") and clean_fields["class_code"].upper() not in codes:
        clean_fields.pop("class_code")
    elif clean_fields.get("class_code"):
        clean_fields["class_code"] = clean_fields["class_code"].upper()
    result = {"fields": clean_fields,
              "confidence": {k: v for k, v in (data_out.get("confidence") or {}).items() if isinstance(v, str)},
              "printed_text": [str(x)[:200] for x in (data_out.get("printed_text") or [])][:60],
              "notes": str(data_out.get("notes") or "")[:1000]}
    s = store_suggestion(call, "nameplate", p, out["model"], result, file_id=str(file_row["id"]), input_sha=file_row["sha256"])
    if clean_fields.get("class_code"):
        cid = call.tx.val("SELECT id FROM asset_classes WHERE code = %s", clean_fields["class_code"])
        result["class_id"] = cid
    return {"suggestion": s, **result}


# ------------------------------------------------------------------ import mapping
IMPORT_PROMPT = """You map a pharmaceutical company's own equipment register (an Excel sheet) to the columns of
ComMach, a GMP calibration and preventive maintenance system.

ComMach columns (key: meaning):
{targets}

Asset classes in ComMach (code: name, category):
{classes}

The sheet's header row and sample rows:
{sample}

Return JSON:
{{"columns": {{"<sheet header exactly as given>": "<ComMach column key, or null to ignore>"}},
  "classes": {{"<distinct value of the sheet column you mapped to class_code, or of a type/description column>": "<class code>"}},
  "status": {{"<distinct status value in the sheet>": "in_service" | "standby" | "out_of_service" | "retired" | "draft"}},
  "criticality": {{"<distinct value>": "critical" | "major" | "minor" | "non_gmp"}},
  "notes": "short notes for the reviewer: columns you were unsure about, values that need a decision"}}
Rules: map each ComMach column at most once. If the sheet has no class column, fill "classes" from the equipment
name or type (e.g. "Magnehelic" -> DPGAUGE, "Balance" -> BALANCE, "Pressure gauge" -> PGAUGE).
Never invent values that are not in the sheet."""


def run_import_mapping(call: Call, headers: list[str], rows: list[list], targets: dict[str, str],
                       class_values: list[str]) -> dict:
    p = provider_for(call, "ai_import")
    classes = call.tx.q("SELECT code, name_en, category FROM asset_classes WHERE active ORDER BY code")
    sample = json.dumps({"headers": headers, "rows": rows[:12], "distinct_type_values": class_values[:120]},
                        ensure_ascii=False, default=str)
    prompt = IMPORT_PROMPT.format(targets="\n".join(f"- {k}: {v}" for k, v in targets.items()),
                                  classes="\n".join(f"- {c['code']}: {c['name_en']} ({c['category']})" for c in classes),
                                  sample=sample[:12000])
    try:
        out = ai.chat(p, p.text_model, [{"role": "user", "content": prompt}], max_tokens=3000, timeout=120)
    except ai.AIUnavailable as e:
        raise ApiError(502, "ai_failed", f"The AI could not map the sheet: {e}")
    record_usage(call, out.get("usage"))
    d = out["data"] if isinstance(out["data"], dict) else {}
    codes = {c["code"] for c in classes}
    cols = {}
    used = set()
    for h, t in (d.get("columns") or {}).items():
        if h in headers and t in targets and t not in used:
            cols[h] = t
            used.add(t)
    result = {
        "fields": {"columns": cols},
        "columns": cols,
        "classes": {str(k): str(v).upper() for k, v in (d.get("classes") or {}).items() if str(v).upper() in codes},
        "status": {str(k): v for k, v in (d.get("status") or {}).items()
                   if v in ("in_service", "standby", "out_of_service", "retired", "draft")},
        "criticality": {str(k): v for k, v in (d.get("criticality") or {}).items()
                        if v in ("critical", "major", "minor", "non_gmp")},
        "notes": str(d.get("notes") or "")[:1500],
    }
    sha = hashlib.sha256(sample.encode("utf-8")).hexdigest()
    s = store_suggestion(call, "import_mapping", p, out["model"], result, input_sha=sha)
    return {"suggestion": s, **result}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


# ------------------------------------------------------------------ calibration certificate
PROMPTS["certificate"] = "certificate-v1"
PROMPTS["impact"] = "impact-v1"

CERT_PROMPT = """You read calibration certificates (English or Arabic) for a GMP calibration system.
Read only what is printed on the certificate pages. Never guess; use null for anything not printed.
Numbers: give plain numbers with a dot as decimal separator (convert Arabic-Indic digits and commas).
Dates: YYYY-MM-DD.

The instrument we expect: {expected}

Return JSON:
{{"certificate_no": "...", "lab_name": "...", "accreditation_body": "...", "accreditation_no": "...",
  "calibration_date": "YYYY-MM-DD", "next_due_date": "YYYY-MM-DD or null", "issue_date": "YYYY-MM-DD or null",
  "instrument": {{"description": "...", "manufacturer": "...", "model": "...", "serial_no": "...", "id": "..."}},
  "environment": {{"temperature": "e.g. 21.3 °C", "humidity": "e.g. 45 %RH"}},
  "standards": [{{"description": "...", "id": "...", "certificate_no": "...", "due_date": "YYYY-MM-DD or null"}}],
  "readings": [{{"parameter": "...", "nominal": number, "unit": "...", "as_found": number or null,
                 "as_left": number or null, "error": number or null, "uncertainty": number or null,
                 "tolerance": "as printed or null"}}],
  "conformity": "the statement of conformity as printed, or null",
  "technician": "name of the person who calibrated, or null",
  "notes": "anything the reviewer should check (unreadable cells, stamps over text, missing pages)"}}
If the certificate gives only one reading per point (no adjustment), put it in as_found and leave as_left null.
If it gives a reading "before adjustment" and "after adjustment", those are as_found and as_left."""


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ".").replace("٫", ".").strip())
    except ValueError:
        return None


def _date(v):
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _norm_id(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def certificate_images(f: dict, data: bytes) -> list:
    from PIL import Image, ImageOps
    if f["mime"] == "application/pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(io.BytesIO(data))
        out = []
        for i in range(min(len(pdf), 3)):
            img = pdf[i].render(scale=2).to_pil().convert("RGB")
            img.thumbnail((1700, 2200))
            out.append(img)
        return out
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((1800, 2400))
    return [img]


def run_certificate(call: Call, task: dict, f: dict, data: bytes) -> dict:
    """Read a certificate, map its readings to the task's points and check it. Nothing is saved to the task."""
    from . import work
    p = provider_for(call, "ai_certificate")
    a = task["asset"]
    expected = (f"{a['code']} {a['name_en']}; manufacturer {a.get('manufacturer') or '?'}; model {a.get('model') or '?'};"
                f" serial {a.get('serial_no') or '?'}; points: " +
                ", ".join(f"{r['parameter']} {r['nominal']} {r['unit'] or ''}" for r in task["results"] if r["item_type"] == "point"))
    try:
        imgs = certificate_images(f, data)
    except Exception:
        raise ApiError(422, "bad_file", "This certificate could not be opened.")
    content = [{"type": "text", "text": CERT_PROMPT.format(expected=expected[:1500])}]
    content += [{"type": "image_url", "image_url": {"url": ai.img_data_url(i)}} for i in imgs]
    try:
        out = ai.chat(p, p.vision_model, [{"role": "user", "content": content}], max_tokens=3500, timeout=150)
    except ai.AIUnavailable as e:
        raise ApiError(502, "ai_failed", f"The AI could not read the certificate: {e}")
    record_usage(call, out.get("usage"))
    x = out["data"] if isinstance(out["data"], dict) else {}
    readings = [r for r in (x.get("readings") or []) if isinstance(r, dict)]
    for r in readings:
        for k in ("nominal", "as_found", "as_left", "error", "uncertainty"):
            r[k] = _num(r.get(k))
    # map readings to the task's points by nominal value (then by parameter name)
    mapped, used = [], set()
    for row in task["results"]:
        if row["item_type"] != "point":
            continue
        nominal = float(row["nominal"]) if row["nominal"] is not None else None
        best = None
        for i, r in enumerate(readings):
            if i in used or r["nominal"] is None or nominal is None:
                continue
            if abs(r["nominal"] - nominal) <= max(1e-9, abs(nominal) * 0.005):
                if best is None or (row["parameter"].lower()[:4] in str(r.get("parameter", "")).lower()):
                    best = i
        if best is not None:
            used.add(best)
            r = readings[best]
            mapped.append({"seq": row["seq"], "as_found": r["as_found"] if r["as_found"] is not None else
                           (nominal + r["error"] if r["error"] is not None else None),
                           "as_left": r["as_left"], "uncertainty": r["uncertainty"]})
        else:
            mapped.append({"seq": row["seq"], "as_found": None, "as_left": None, "uncertainty": None, "missing": True})
    extra = [r for i, r in enumerate(readings) if i not in used]
    st = work.effective_settings(call.tx.val("SELECT work_settings FROM tenants WHERE id = %s", call.tenant_id))
    checks = certificate_checks(call, task, x, mapped, extra, st)
    fields = {"certificate_no": x.get("certificate_no"), "performed_date": x.get("calibration_date"),
              "env_temperature": (x.get("environment") or {}).get("temperature"),
              "env_humidity": (x.get("environment") or {}).get("humidity")}
    for m in mapped:
        for k in ("as_found", "as_left", "uncertainty"):
            fields[f"p{m['seq']}.{k}"] = m.get(k)
    result = {"fields": fields, "extracted": x, "mapped": mapped, "extra_readings": extra, "checks": checks}
    s = store_suggestion(call, "certificate", p, out["model"], result, file_id=str(f["id"]), input_sha=f["sha256"])
    return {"suggestion": s, **result}


def certificate_checks(call: Call, task: dict, x: dict, mapped: list, extra: list, st: dict) -> list[dict]:
    from . import work
    checks = []

    def add(key, status, **vals):
        checks.append({"key": key, "status": status, "values": vals})

    a = task["asset"]
    cert_sn, our_sn = _norm_id((x.get("instrument") or {}).get("serial_no")), _norm_id(a.get("serial_no"))
    if cert_sn and our_sn:
        add("serial", "ok" if cert_sn == our_sn else "fail", certificate=(x.get("instrument") or {}).get("serial_no"),
            register=a.get("serial_no"))
    else:
        add("serial", "warn", certificate=(x.get("instrument") or {}).get("serial_no"), register=a.get("serial_no"))
    cdate = _date(x.get("calibration_date"))
    import datetime as _dt
    today_ = _dt.date.today()
    if not cdate:
        add("date", "fail", date=None)
    elif cdate > today_:
        add("date", "fail", date=str(cdate))
    elif task["window_end"] and cdate > task["window_end"]:
        add("date", "warn", date=str(cdate), window_end=str(task["window_end"]))
    else:
        add("date", "ok", date=str(cdate))
    missing = [m["seq"] for m in mapped if m.get("missing")]
    if not mapped:
        # no template points: the certificate's own readings become the points, to be checked by the user
        add("coverage", "warn", found=0, expected=0, missing=[], extra=len(extra), no_points=True)
    else:
        add("coverage", "ok" if not missing else "fail", found=len(mapped) - len(missing), expected=len(mapped),
            missing=missing, extra=len(extra))
    # tolerance per point and uncertainty ratio
    oot, low_ratio = [], []
    rows = {r["seq"]: r for r in task["results"]}
    for m in mapped:
        r = rows[m["seq"]]
        lo = float(r["low_limit"]) if r["low_limit"] is not None else None
        hi = float(r["high_limit"]) if r["high_limit"] is not None else None
        if m["as_found"] is not None and not work.within(m["as_found"], work.D(lo), work.D(hi)):
            oot.append(m["seq"])
        if m.get("uncertainty") and lo is not None and hi is not None:
            ratio = ((hi - lo) / 2) / m["uncertainty"] if m["uncertainty"] else None
            if ratio is not None and ratio < float(st["uncertainty_ratio_min"]):
                low_ratio.append({"seq": m["seq"], "ratio": round(ratio, 2)})
    add("tolerance", "ok" if not oot else "fail", out=oot)
    add("uncertainty", "ok" if not low_ratio else "warn", low=low_ratio, minimum=st["uncertainty_ratio_min"])
    conf = str(x.get("conformity") or "").lower()
    if conf:
        lab_pass = any(w in conf for w in ("pass", "conform", "within", "complies", "مطابق", "ضمن"))
        lab_fail = any(w in conf for w in ("fail", "not conform", "out of", "does not", "غير مطابق"))
        agree = (lab_pass and not oot) or (lab_fail and oot) or not (lab_pass or lab_fail)
        add("conformity", "ok" if agree else "warn", statement=x.get("conformity"))
    if task.get("vendor_id"):
        v = call.tx.one("SELECT name, accreditation_no, accreditation_expiry FROM vendors WHERE id = %s", task["vendor_id"])
        cert_acc = _norm_id(x.get("accreditation_no"))
        if v and v["accreditation_no"] and cert_acc and _norm_id(v["accreditation_no"]) not in cert_acc and cert_acc not in _norm_id(v["accreditation_no"]):
            add("accreditation", "warn", certificate=x.get("accreditation_no"), register=v["accreditation_no"])
        elif v and v["accreditation_expiry"] and cdate and v["accreditation_expiry"] < cdate:
            add("accreditation", "fail", expired=str(v["accreditation_expiry"]))
        else:
            add("accreditation", "ok" if v and v["accreditation_no"] else "warn", register=v["accreditation_no"] if v else None)
    stds = [s for s in (x.get("standards") or []) if isinstance(s, dict)]
    bad_std = [s.get("id") or s.get("description") for s in stds if _date(s.get("due_date")) and cdate and _date(s.get("due_date")) < cdate]
    add("standards", "warn" if not stds else ("fail" if bad_std else "ok"), count=len(stds), expired=bad_std)
    return checks


# ------------------------------------------------------------------ impact assessment draft
IMPACT_PROMPT = """You are a pharmaceutical QA specialist writing the first draft of an impact assessment for an
out-of-tolerance (OOT) calibration result, for a QA approver to review and complete. Be factual and specific to the
data below. Do not invent batch numbers, product names or test results that are not given: where they are needed,
say exactly which records QA must pull (for example "batches processed on line L-TAB1 between 2026-03-04 and 2026-09-02").

Instrument and result:
{context}

Return JSON:
{{"summary": "what was found, in 2-4 sentences",
  "scope": "which rooms, lines, equipment, processes or tests relied on this instrument during the period",
  "products": "which products, batches or test results may be affected and which records to review",
  "risk": "low" | "medium" | "high",
  "risk_reason": "one or two sentences why",
  "actions": "numbered list of actions (containment, record review, recalculation, CAPA, re-qualification)",
  "conclusion": "a draft conclusion that QA must confirm after the review"}}"""


def run_impact(call: Call, i: dict) -> dict:
    p = provider_for(call, "ai_impact")
    a = i["asset"]
    pts = "; ".join(
        f"{r['parameter']} nominal {r['nominal']} {r['unit'] or ''}: limits {r['low_limit']}..{r['high_limit']}, as found "
        f"{r['as_found']}{' (OUT)' if r['ok_found'] is False else ''}, as left {r['as_left'] if r['as_left'] is not None else '-'}"
        for r in i["results"])
    children = call.tx.q("SELECT code, name_en FROM assets WHERE parent_id = %s", a["id"])
    users = call.tx.q("SELECT p.code, p.name_en FROM assets p WHERE p.id = (SELECT parent_id FROM assets WHERE id = %s)", a["id"])
    context = (f"Asset {a['code']} – {a['name_en']} (GMP criticality {a['gmp_criticality']}); location {a['location_code'] or '-'} "
               f"{a['location_name_en'] or ''}; line {a['line_code'] or '-'} {a['line_name_en'] or ''}; part of "
               f"{(users[0]['code'] + ' ' + users[0]['name_en']) if users else 'none'}; components "
               f"{', '.join(c['code'] for c in children) or 'none'}.\nCalibration {i['task_number']} on {i['performed_date']}: "
               f"verdict {i['verdict']} (adjusted = out of tolerance as found, corrected as left; fail = still out of tolerance).\n"
               f"Readings: {pts}\nPeriod at risk: from the last calibration in tolerance ({i['period_from'] or 'unknown'}) "
               f"to {i['period_to']}.")
    try:
        out = ai.chat(p, p.text_model, [{"role": "user", "content": IMPACT_PROMPT.format(context=context)}],
                      max_tokens=2500, timeout=120)
    except ai.AIUnavailable as e:
        raise ApiError(502, "ai_failed", f"The AI could not draft the assessment: {e}")
    record_usage(call, out.get("usage"))
    d = out["data"] if isinstance(out["data"], dict) else {}
    fields = {k: str(d.get(k) or "").strip()[:8000] for k in ("summary", "scope", "products", "actions", "conclusion")}
    if isinstance(d.get("actions"), list):
        fields["actions"] = "\n".join(f"{n}. {x}" for n, x in enumerate(d["actions"], 1))
    risk = d.get("risk") if d.get("risk") in ("low", "medium", "high") else None
    fields["risk"] = risk
    result = {"fields": fields, "risk_reason": str(d.get("risk_reason") or "")[:1000]}
    s = store_suggestion(call, "impact", p, out["model"], result, input_sha=hashlib.sha256(context.encode()).hexdigest())
    return {"suggestion": s, **result}
