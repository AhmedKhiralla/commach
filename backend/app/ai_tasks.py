"""AI jobs in ComMach. Each answer is stored as a suggestion (provider, model, prompt version, SHA-256 of the
input and of the raw answer) and becomes data only when a person accepts it, with their corrections recorded.

  nameplate       photo of an equipment nameplate -> manufacturer, model, serial, ranges, class, name
  import_mapping  headers and sample rows of a customer's own register -> ComMach columns and value mappings
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
