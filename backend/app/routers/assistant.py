"""Phase 6: "Ask ComMach" — answers questions from the register and the records, with each user's own access.

The model never sees the database. It asks for look-ups from a fixed, read-only list; ComMach runs them inside the
user's own transaction (row-level security plus the user's sites) and returns the results; the model then answers
from those results only. Every question, the look-ups made and the answer are kept in `assistant_questions`.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from types import SimpleNamespace

from pydantic import BaseModel, Field

from .. import hours
from ..ai_tasks import provider_for, record_usage
from ..api import ApiError, Call, endpoint
from ..engine import ai
from ..perms import visible_site_ids
from . import analysis
from .work import today

MAX_ROUNDS = 3
MAX_RESULT_CHARS = 14000

TOOLS = {
    "search_assets": "Find assets. args: q (text in code, name, serial no., model, manufacturer; optional), status (in_service, "
                     "restricted, standby, out_of_service, quarantined, out_for_calibration, draft, retired; optional), "
                     "category (instrument, equipment, utility, facility, safety…; optional), limit (≤ 50).",
    "asset": "One asset by code: identity, location, status, schedules with next due dates, the last calibrations and PMs, "
             "open tasks, run-hours. args: code.",
    "tasks": "Calibration or PM tasks. args: kind (calibration | pm), state (open | overdue | due_soon | at_vendor | in_progress "
             "| completed | oot | to_review), asset_code (optional), from / to (dates, filter the due date or, for completed, "
             "the date done; optional), limit (≤ 100).",
    "kpis": "Results for a period: completed, on-time %, out-of-tolerance (or PM findings) %, first-time pass %, overdue now, "
            "by month, by asset class, vendor performance. args: kind (calibration | pm), months (1–36, default 12).",
    "drift": "Calibration drift for one asset: as-found history per point, slope per year, projected date the drift "
             "crosses a tolerance limit, % of tolerance used. args: code.",
    "intervals": "Recommended calibration interval changes (extend stable instruments, shorten after OOT or drift). no args.",
    "deviations": "Deviations raised for late work. args: status (open | closed; optional).",
    "impacts": "Out-of-tolerance impact assessments. args: status (draft | approved; optional).",
    "plans": "Annual calibration and PM plans and their approval. args: year (optional).",
    "pm_gaps": "Open PM tasks on production machines with the free slot between Complanning campaigns. no args.",
}

SYSTEM = """You are Ask ComMach, the assistant in ComMach, a GMP calibration and preventive maintenance system.
Answer the user's question using ONLY the look-up results ComMach gives you. Never invent assets, numbers, dates or
results. If the results do not contain the answer, say so plainly and suggest where in ComMach to look.
The user sees only the sites they have access to; the look-ups already respect that.

Reply with ONE JSON object and nothing else:
- To look things up: {{"lookups": [{{"tool": "<name>", "args": {{...}}}}, ...]}}  (at most 4 per turn)
- To answer: {{"answer": "<markdown>", "refs": [{{"type": "asset" | "task", "code": "<asset code or task number>"}}]}}
Keep answers short and factual: a sentence or two, then a short list or table when it helps. Give dates as
DD Mon YYYY. Mention record numbers (e.g. CAL-2026-0012) and asset codes exactly as written in the results.
Answer in {language}. Today is {today}. The user is {name}.

Look-ups you can use:
{tools}"""


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=6000)


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1500)
    history: list[Turn] = Field(default_factory=list, max_length=12)
    language: str = Field(default="en", pattern="^(en|ar)$")


# ------------------------------------------------------------------ look-ups (read-only, the user's own access)
def _sub(call: Call, **q) -> Call:
    params = {k: str(v) for k, v in q.items() if v not in (None, "")}
    req = SimpleNamespace(query_params=params, headers=call.request.headers, client=call.request.client)
    return dataclasses.replace(call, request=req)


def _site_filter(call: Call, alias: str) -> tuple[str, list]:
    sites = visible_site_ids(call)
    return (f" AND {alias}.site_id = ANY(%s::uuid[])", [sites]) if sites is not None else ("", [])


def _limit(v, default: int, top: int) -> int:
    try:
        return max(1, min(top, int(v)))
    except (TypeError, ValueError):
        return default


def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def t_search_assets(call: Call, a: dict):
    w, p = _site_filter(call, "a")
    where = ["a.status NOT IN ('disposed')"]
    if a.get("q"):
        term = "%" + str(a["q"]).strip()[:60].replace("%", r"\%") + "%"
        where.append("(a.code ILIKE %s OR a.name_en ILIKE %s OR a.name_ar ILIKE %s OR a.serial_no ILIKE %s OR a.model ILIKE %s"
                     " OR a.manufacturer ILIKE %s OR c.name_en ILIKE %s)")
        p += [term] * 7
    if a.get("status"):
        where.append("a.status = %s")
        p.append(str(a["status"]))
    if a.get("category"):
        where.append("c.category = %s")
        p.append(str(a["category"]))
    return call.tx.q(
        "SELECT a.code, a.name_en, a.status, c.name_en AS class, c.category, s.code AS site, l.code AS location,"
        " a.gmp_criticality, a.manufacturer, a.model, a.serial_no,"
        " (SELECT min(next_due) FROM schedules x WHERE x.asset_id = a.id AND x.kind = 'calibration' AND x.active) AS next_calibration,"
        " (SELECT min(next_due) FROM schedules x WHERE x.asset_id = a.id AND x.kind = 'pm' AND x.active) AS next_pm"
        " FROM assets a JOIN asset_classes c ON c.id = a.class_id JOIN sites s ON s.id = a.site_id"
        " LEFT JOIN locations l ON l.id = a.location_id WHERE " + " AND ".join(where) + w + " ORDER BY a.code LIMIT %s",
        *p, _limit(a.get("limit"), 25, 50))


def _asset_row(call: Call, code: str):
    w, p = _site_filter(call, "a")
    return call.tx.one("SELECT a.*, c.name_en AS class, c.category, s.code AS site, l.code AS location, l.name_en AS location_name"
                       " FROM assets a JOIN asset_classes c ON c.id = a.class_id JOIN sites s ON s.id = a.site_id"
                       " LEFT JOIN locations l ON l.id = a.location_id WHERE (a.code = %s OR a.code ILIKE %s)" + w + " LIMIT 1",
                       code, code, *p)


def t_asset(call: Call, a: dict):
    x = _asset_row(call, str(a.get("code") or ""))
    if not x:
        return {"error": "No asset with this code at your sites."}
    keep = ("code", "name_en", "name_ar", "status", "class", "category", "site", "location", "location_name", "gmp_criticality",
            "manufacturer", "model", "serial_no", "range_min", "range_max", "unit", "resolution", "accuracy", "external_ref",
            "meter_unit")
    out = {k: x.get(k) for k in keep}
    out["schedules"] = call.tx.q("SELECT kind, interval_value, interval_unit, basis, performer, last_done, next_due, hours_interval"
                                 " FROM schedules WHERE asset_id = %s AND active ORDER BY kind", x["id"])
    out["recent"] = call.tx.q("SELECT number, kind, performed_date, verdict, completed_by_name FROM tasks WHERE asset_id = %s"
                              " AND status = 'completed' ORDER BY performed_date DESC LIMIT 8", x["id"])
    out["open"] = call.tx.q("SELECT number, kind, status, due_date, window_end, (window_end < %s) AS overdue FROM tasks"
                            " WHERE asset_id = %s AND status NOT IN ('completed', 'cancelled') ORDER BY due_date", today(call), x["id"])
    tot = hours.run_totals(call.tx, [x["id"]]).get(str(x["id"]))
    if tot:
        out["run_hours"] = {"total": tot["total"], "read_at": tot["read_at"], "per_day": tot["rate_per_day"]}
    return out


def t_tasks(call: Call, a: dict):
    kind = a.get("kind") if a.get("kind") in ("calibration", "pm") else "calibration"
    state = a.get("state") if a.get("state") in ("open", "overdue", "due_soon", "at_vendor", "in_progress", "completed",
                                                 "oot", "to_review") else "open"
    w, p = _site_filter(call, "t")
    where = ["t.kind = %s"]
    p = [kind] + p
    t0 = today(call)
    states = {"open": "t.status NOT IN ('completed', 'cancelled')",
              "overdue": "t.status NOT IN ('completed', 'cancelled') AND t.window_end < %s",
              "due_soon": "t.status NOT IN ('completed', 'cancelled') AND t.window_end >= %s AND t.due_date <= %s",
              "at_vendor": "t.status = 'at_vendor'", "in_progress": "t.status = 'in_progress'",
              "completed": "t.status = 'completed'", "oot": "t.status = 'completed' AND t.verdict IN ('adjusted', 'fail')",
              "to_review": "t.status = 'completed' AND t.reviewed_at IS NULL AND t.source = 'normal'"}
    where.append(states[state])
    if state == "overdue":
        p.append(t0)
    if state == "due_soon":
        p += [t0, t0 + dt.timedelta(days=30)]
    col = "t.performed_date" if state in ("completed", "oot", "to_review") else "t.due_date"
    if _date(a.get("from")):
        where.append(f"{col} >= %s")
        p.append(_date(a["from"]))
    if _date(a.get("to")):
        where.append(f"{col} <= %s")
        p.append(_date(a["to"]))
    if a.get("asset_code"):
        where.append("a.code ILIKE %s")
        p.append(str(a["asset_code"]))
    rows = call.tx.q(
        "SELECT t.number, t.status, t.due_date, t.window_end, t.performed_date, t.verdict, t.performer, v.name AS vendor,"
        " a.code AS asset_code, a.name_en AS asset_name, s.code AS site, t.due_by_hours, coalesce(t.assigned_user_id IS NOT NULL, false) AS accepted"
        " FROM tasks t JOIN assets a ON a.id = t.asset_id JOIN sites s ON s.id = t.site_id LEFT JOIN vendors v ON v.id = t.vendor_id"
        " WHERE " + " AND ".join(where) + w + f" ORDER BY {col} {'DESC' if col == 't.performed_date' else 'ASC'} LIMIT %s",
        *p, _limit(a.get("limit"), 40, 100))
    total = call.tx.val("SELECT count(*) FROM tasks t JOIN assets a ON a.id = t.asset_id WHERE " + " AND ".join(where) + w, *p)
    return {"kind": kind, "state": state, "total": total, "items": rows}


def t_kpis(call: Call, a: dict):
    out = analysis.summary.__wrapped__(_sub(call, kind=a.get("kind") or "calibration", months=_limit(a.get("months"), 12, 36)))
    return out


def t_drift(call: Call, a: dict):
    x = _asset_row(call, str(a.get("code") or ""))
    if not x:
        return {"error": "No asset with this code at your sites."}
    series = analysis.drift_series(call, str(x["id"]), today(call))
    return {"asset": x["code"], "series": [{k: s[k] for k in ("parameter", "nominal", "unit", "low_limit", "high_limit",
                                                               "slope_per_year", "projected_crossing", "margin_used_pct")}
                                           | {"as_found": [(p["performed_date"], p["as_found"], p["as_left"]) for p in s["points"]]}
                                           for s in series]}


def t_intervals(call: Call, a: dict):
    out = analysis.intervals.__wrapped__(_sub(call))
    return {"unchanged": out["unchanged"], "items": [{k: i.get(k) for k in ("asset_code", "asset_name_en", "interval_value",
                                                                              "interval_unit", "suggested_value", "suggested_unit",
                                                                              "direction", "reason", "history", "crossing")}
                                                     for i in out["items"][:40]]}


def t_deviations(call: Call, a: dict):
    w, p = _site_filter(call, "d")
    st = " AND d.status = %s" if a.get("status") in ("open", "closed") else ""
    return call.tx.q("SELECT d.number, d.status, d.title, d.created_at AS opened_at, d.closed_at, d.root_cause, a.code AS asset_code,"
                     " t.number AS task FROM deviations d LEFT JOIN tasks t ON t.id = d.task_id LEFT JOIN assets a ON a.id = d.asset_id"
                     " WHERE true" + st + w + " ORDER BY d.created_at DESC LIMIT 40", *([a["status"]] if st else []), *p)


def t_impacts(call: Call, a: dict):
    w, p = _site_filter(call, "t")
    st = " AND i.status = %s" if a.get("status") else ""
    return call.tx.q("SELECT i.number, i.status, i.risk, i.created_at, i.approved_at, t.number AS task, a.code AS asset_code"
                     " FROM impact_assessments i JOIN tasks t ON t.id = i.task_id JOIN assets a ON a.id = t.asset_id"
                     " WHERE true" + st + w + " ORDER BY i.created_at DESC LIMIT 40", *([a["status"]] if st else []), *p)


def t_plans(call: Call, a: dict):
    w, p = _site_filter(call, "p")
    yr = " AND p.year = %s" if str(a.get("year") or "").isdigit() else ""
    return call.tx.q("SELECT s.code AS site, p.kind, p.year, p.status, p.reviewed_by_name, p.reviewed_at, p.approved_by_name,"
                     " p.approved_at FROM plans p JOIN sites s ON s.id = p.site_id WHERE true" + yr + w
                     + " ORDER BY p.year DESC, s.code, p.kind LIMIT 40", *([int(a["year"])] if yr else []), *p)


def t_pm_gaps(call: Call, a: dict):
    return analysis.pm_gaps.__wrapped__(_sub(call))


RUN = {"search_assets": t_search_assets, "asset": t_asset, "tasks": t_tasks, "kpis": t_kpis, "drift": t_drift,
       "intervals": t_intervals, "deviations": t_deviations, "impacts": t_impacts, "plans": t_plans, "pm_gaps": t_pm_gaps}


def run_lookup(call: Call, tool: str, args: dict) -> object:
    fn = RUN.get(tool)
    if not fn:
        return {"error": f"Unknown look-up {tool!r}."}
    try:
        call.tx.x("SAVEPOINT lookup")
        out = fn(call, args if isinstance(args, dict) else {})
        call.tx.x("RELEASE SAVEPOINT lookup")
        return out
    except ApiError as e:
        call.tx.x("ROLLBACK TO SAVEPOINT lookup")
        return {"error": e.message}
    except Exception as e:  # a bad argument from the model must not end the conversation
        call.tx.x("ROLLBACK TO SAVEPOINT lookup")
        return {"error": f"The look-up failed: {type(e).__name__}"}


def _json(v) -> str:
    return json.dumps(v, default=str, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------ the conversation
def _links(call: Call, refs, answer: str) -> list[dict]:
    codes = {"asset": set(), "task": set()}
    for r in refs if isinstance(refs, list) else []:
        if isinstance(r, dict) and r.get("type") in codes and r.get("code"):
            codes[r["type"]].add(str(r["code"])[:60])
    codes["task"] |= set(re.findall(r"\b(?:CAL|PM)-\d{4}-\d{3,6}\b", answer or ""))
    out = []
    if codes["task"]:
        w, p = _site_filter(call, "t")
        for t in call.tx.q("SELECT id, number, kind FROM tasks t WHERE number = ANY(%s)" + w + " ORDER BY number LIMIT 20",
                           sorted(codes["task"]), *p):
            out.append({"type": "task", "label": t["number"],
                        "href": f"/{'calibration' if t['kind'] == 'calibration' else 'maintenance'}/tasks/{t['id']}"})
    if codes["asset"]:
        w, p = _site_filter(call, "a")
        for a in call.tx.q("SELECT id, code, name_en FROM assets a WHERE code = ANY(%s)" + w + " ORDER BY code LIMIT 20",
                           sorted(codes["asset"]), *p):
            out.append({"type": "asset", "label": a["code"], "name": a["name_en"], "href": f"/assets/{a['id']}"})
    return out


@endpoint(auth="tenant_user", body=AskIn)
def ask(call: Call):
    b: AskIn = call.body
    provider = provider_for(call, "ai_assistant")
    system = SYSTEM.format(language="Arabic" if b.language == "ar" else "English", today=today(call).isoformat(),
                           name=call.user["full_name"], tools="\n".join(f"- {k}: {v}" for k, v in TOOLS.items()))
    messages = [{"role": "system", "content": system}]
    for h in b.history[-8:]:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": b.question})
    lookups: list[dict] = []
    answer, refs, model, err = None, [], None, None
    try:
        for rnd in range(MAX_ROUNDS + 1):
            out = ai.chat(provider, provider.text_model, messages, max_tokens=1500)
            record_usage(call, out.get("usage"))
            model = out.get("model")
            data = out["data"] if isinstance(out.get("data"), dict) else {}
            if data.get("answer") or rnd == MAX_ROUNDS or not data.get("lookups"):
                answer = str(data.get("answer") or "").strip() or None
                refs = data.get("refs") or []
                break
            asked = [x for x in data["lookups"] if isinstance(x, dict)][:4]
            results = []
            for x in asked:
                tool, args = str(x.get("tool") or ""), x.get("args") or {}
                res = run_lookup(call, tool, args)
                lookups.append({"tool": tool, "args": args})
                results.append({"tool": tool, "args": args, "result": res})
            text = _json(results)
            if len(text) > MAX_RESULT_CHARS:
                text = text[:MAX_RESULT_CHARS] + "…(cut: ask for fewer rows if you need more)"
            messages.append({"role": "assistant", "content": _json({"lookups": asked})})
            final = " This was your last look-up: answer now." if rnd == MAX_ROUNDS - 1 else ""
            messages.append({"role": "user", "content": f"LOOKUP RESULTS: {text}\nAnswer the question from these results, "
                                                          f"or ask for more look-ups.{final}"})
    except ai.AIUnavailable as e:
        err = str(e)
    if not answer and not err:
        err = "The assistant gave no answer. Try asking in another way."
    call.tx.x("INSERT INTO assistant_questions (tenant_id, user_id, user_name, question, answer, lookups, model, error)"
              " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)", call.tenant_id, call.user["id"], call.user["full_name"],
              b.question, answer, _json(lookups), model, err)
    if err:
        raise ApiError(503, "ai_unavailable", err if "configured" in err or "model" in err.lower() else
                       f"The assistant could not answer: {err}")
    return {"answer": answer, "links": _links(call, refs, answer), "lookups": lookups, "model": model}


@endpoint(auth="tenant_user")
def history(call: Call):
    rows = call.tx.q("SELECT id, question, answer, lookups, error, asked_at FROM assistant_questions WHERE user_id = %s"
                     " ORDER BY asked_at DESC LIMIT 30", call.user["id"])
    return {"items": rows}
