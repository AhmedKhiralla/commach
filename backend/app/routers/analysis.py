"""Phase 6: results analysis — KPIs, drift with projected crossing, interval recommendations, run-hours meters and PM
slots between production campaigns."""
from __future__ import annotations

import datetime as dt
import decimal

from pydantic import BaseModel, Field

from .. import gaps, hours, work
from ..api import ApiError, Call, endpoint, not_found
from ..common import clean
from ..perms import is_admin, require_site, site_roles, visible_site_ids
from .work import settings_of, today

METER_ROLES = {"engineering_manager", "engineering_supervisor", "maintenance_technician", "qa_calibration_officer", "qa_approver"}


def _sites(call: Call, alias: str = "t") -> tuple[str, list]:
    where, params = [], []
    sites = visible_site_ids(call)
    if sites is not None:
        where.append(f"{alias}.site_id = ANY(%s::uuid[])")
        params.append(sites)
    if call.query.get("site_id"):
        where.append(f"{alias}.site_id = %s::uuid")
        params.append(call.query["site_id"])
    return "".join(f" AND {w}" for w in where), params


def _kind(call: Call) -> str:
    k = call.query.get("kind", "calibration")
    if k not in ("calibration", "pm"):
        raise ApiError(422, "invalid", "kind is calibration or pm.")
    return k


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else None


# ------------------------------------------------------------------ KPIs
@endpoint(auth="tenant_user")
def summary(call: Call):
    kind = _kind(call)
    t0 = today(call)
    try:
        months = min(36, max(1, int(call.query.get("months", "12"))))
    except ValueError:
        raise ApiError(422, "invalid", "months must be a number.")
    m0 = t0.year * 12 + t0.month - 1 - (months - 1)
    since = dt.date(m0 // 12, m0 % 12 + 1, 1)
    w, p = _sites(call)
    bad = "('adjusted', 'fail')" if kind == "calibration" else "('fail')"
    base = f" FROM tasks t WHERE t.kind = %s AND t.status = 'completed' AND t.performed_date >= %s{w}"
    totals = call.tx.one(
        "SELECT count(*) AS completed, count(*) FILTER (WHERE t.performed_date <= t.window_end) AS on_time,"
        f" count(*) FILTER (WHERE t.verdict IN {bad}) AS oot, count(*) FILTER (WHERE t.verdict = 'pass') AS first_pass,"
        " count(*) FILTER (WHERE t.verdict = 'fail') AS failed, coalesce(sum(t.man_hours), 0) AS man_hours,"
        " count(*) FILTER (WHERE t.performer = 'external') AS external" + base, kind, since, *p)
    now_ = call.tx.one(
        "SELECT count(*) FILTER (WHERE t.window_end < %s) AS overdue, count(*) AS open,"
        " count(*) FILTER (WHERE t.due_by_hours) AS due_by_hours"
        f" FROM tasks t WHERE t.kind = %s AND t.status NOT IN ('completed', 'cancelled') AND t.due_date <= %s{w}",
        t0, kind, t0 + dt.timedelta(days=30), *p)
    monthly = call.tx.q(
        "SELECT to_char(date_trunc('month', t.performed_date), 'YYYY-MM') AS month, count(*) AS completed,"
        f" count(*) FILTER (WHERE t.performed_date <= t.window_end) AS on_time, count(*) FILTER (WHERE t.verdict IN {bad}) AS oot"
        + base + " GROUP BY 1 ORDER BY 1", kind, since, *p)
    have = {m["month"] for m in monthly}
    d = since
    while d <= t0:
        if d.strftime("%Y-%m") not in have:
            monthly.append({"month": d.strftime("%Y-%m"), "completed": 0, "on_time": 0, "oot": 0})
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    monthly.sort(key=lambda m: m["month"])
    by_class = call.tx.q(
        "SELECT c.code, c.name_en, c.name_ar, count(*) AS completed,"
        f" count(*) FILTER (WHERE t.verdict IN {bad}) AS oot, count(*) FILTER (WHERE t.performed_date <= t.window_end) AS on_time"
        " FROM tasks t JOIN assets a ON a.id = t.asset_id JOIN asset_classes c ON c.id = a.class_id"
        f" WHERE t.kind = %s AND t.status = 'completed' AND t.performed_date >= %s{w}"
        " GROUP BY c.code, c.name_en, c.name_ar ORDER BY oot DESC, completed DESC LIMIT 12", kind, since, *p)
    vendors = call.tx.q(
        "SELECT v.id, v.code, v.name, count(*) AS completed,"
        " count(*) FILTER (WHERE t.performed_date <= t.window_end) AS on_time,"
        f" count(*) FILTER (WHERE t.verdict IN {bad}) AS oot,"
        " round(avg(t.performed_date - t.sent_at::date) FILTER (WHERE t.sent_at IS NOT NULL), 1) AS turnaround_days,"
        " round(avg(t.completed_at::date - t.performed_date), 1) AS record_days,"
        " count(*) FILTER (WHERE t.certificate_required AND t.certificate_file_id IS NULL) AS missing_certificates"
        " FROM tasks t JOIN vendors v ON v.id = t.vendor_id"
        f" WHERE t.kind = %s AND t.status = 'completed' AND t.performer = 'external' AND t.performed_date >= %s{w}"
        " GROUP BY v.id, v.code, v.name ORDER BY count(*) DESC", kind, since, *p)
    for v in vendors:
        v["on_time_pct"] = _pct(v["on_time"], v["completed"])
        v["oot_pct"] = _pct(v["oot"], v["completed"])
    for c in by_class:
        c["oot_pct"] = _pct(c["oot"], c["completed"])
    n = totals["completed"]
    return {"kind": kind, "since": since, "until": t0, "months": months, **totals, **now_,
            "on_time_pct": _pct(totals["on_time"], n), "oot_pct": _pct(totals["oot"], n),
            "first_pass_pct": _pct(totals["first_pass"], n), "monthly": monthly, "by_class": by_class, "vendors": vendors}


# ------------------------------------------------------------------ drift
def _fit(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


DRIFT_SQL = """
SELECT t.asset_id, t.id AS task_id, t.number, t.performed_date, r.parameter, r.nominal, r.unit, r.low_limit, r.high_limit,
       r.as_found, r.as_left, r.ok_found
  FROM (SELECT id, asset_id, number, performed_date,
               row_number() OVER (PARTITION BY asset_id ORDER BY performed_date DESC) AS n
          FROM tasks WHERE asset_id = ANY(%s::uuid[]) AND kind = 'calibration' AND status = 'completed') t
  JOIN task_results r ON r.task_id = t.id
 WHERE t.n <= %s AND r.item_type = 'point'
 ORDER BY t.asset_id, r.parameter, r.nominal NULLS FIRST, t.performed_date
"""


def drift_rows(call: Call, asset_ids: list[str], limit: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in call.tx.q(DRIFT_SQL, [str(a) for a in asset_ids], limit) if asset_ids else []:
        out.setdefault(str(r["asset_id"]), []).append(r)
    return out


def drift_series(call: Call, asset_id: str, t0: dt.date, limit: int = 12, rows: list[dict] | None = None) -> list[dict]:
    """Per calibration point: the as-found history, the drift line since the last adjustment and when it will cross a limit."""
    if rows is None:
        rows = drift_rows(call, [asset_id], limit).get(str(asset_id), [])
    series: dict[tuple, dict] = {}
    for r in rows:
        key = (r["parameter"], str(r["nominal"]), r["unit"])
        s = series.setdefault(key, {"parameter": r["parameter"], "nominal": r["nominal"], "unit": r["unit"], "points": []})
        s["points"].append({k: r[k] for k in ("task_id", "number", "performed_date", "as_found", "as_left", "low_limit",
                                              "high_limit", "ok_found")})
    out = []
    for s in series.values():
        pts = s["points"]
        last = pts[-1]
        low, high = last["low_limit"], last["high_limit"]
        # the drift line starts again after the last adjustment (as-left recorded)
        start = max((i for i, p in enumerate(pts) if p["as_left"] is not None), default=None)
        use = []
        if start is not None:
            use.append((pts[start]["performed_date"], pts[start]["as_left"]))
            use += [(p["performed_date"], p["as_found"]) for p in pts[start + 1:] if p["as_found"] is not None]
        else:
            use = [(p["performed_date"], p["as_found"]) for p in pts if p["as_found"] is not None]
        origin = use[0][0] if use else None
        fit = _fit([(d - origin).days for d, _ in use], [float(v) for _, v in use]) if use else None
        crossing, slope_year = None, None
        if fit:
            a, b = fit
            slope_year = round(b * 365.25, 6)
            target = high if b > 0 else low if b < 0 else None
            if target is not None and b != 0:
                day = (float(target) - a) / b
                when = origin + dt.timedelta(days=max(0, int(day)))
                if when <= t0 + dt.timedelta(days=3650):
                    crossing = max(when, origin)
        margin = None
        if last["as_found"] is not None and s["nominal"] is not None and low is not None and high is not None:
            half = (high - low) / 2
            if half > 0:
                margin = round(float(abs(last["as_found"] - (low + high) / 2) / half) * 100, 1)
        out.append({**s, "low_limit": low, "high_limit": high, "fit": {"from": origin, "a": fit[0], "b_per_day": fit[1]} if fit else None,
                    "slope_per_year": slope_year, "projected_crossing": crossing, "margin_used_pct": margin,
                    "points_in_fit": len(use) if fit else 0})
    return out


@endpoint(auth="tenant_user")
def drift(call: Call):
    aid = call.query.get("asset_id")
    a = call.tx.one("SELECT id, code, name_en, name_ar, site_id FROM assets WHERE id = %s::uuid", aid) if aid else None
    if not a:
        raise not_found("Asset")
    require_site(call, str(a["site_id"]))
    sched = call.tx.one("SELECT interval_value, interval_unit, last_done, next_due FROM schedules WHERE asset_id = %s"
                        " AND kind = 'calibration' AND active ORDER BY created_at LIMIT 1", a["id"])
    return {"asset": a, "schedule": sched, "series": drift_series(call, str(a["id"]), today(call))}


# ------------------------------------------------------------------ interval recommendations
def _months(value: int, unit: str) -> float:
    return {"day": value / 30.4375, "week": value * 7 / 30.4375, "month": value, "year": value * 12}[unit]


def _as_interval(months: float, unit: str) -> tuple[int, str]:
    if unit in ("day", "week"):
        days = max(1, round(months * 30.4375))
        return (days // 7, "week") if unit == "week" and days % 7 == 0 else (days, "day")
    m = max(1, int(round(months)))
    return (m // 12, "year") if m % 12 == 0 else (m, "month")


@endpoint(auth="tenant_user")
def intervals(call: Call):
    t0 = today(call)
    w, p = _sites(call, "s")
    scheds = call.tx.q(
        "SELECT s.id, s.asset_id, s.site_id, s.interval_value, s.interval_unit, s.last_done, s.next_due, a.code AS asset_code,"
        " a.name_en AS asset_name_en, a.name_ar AS asset_name_ar, a.gmp_criticality"
        f" FROM schedules s JOIN assets a ON a.id = s.asset_id WHERE s.kind = 'calibration' AND s.active{w} ORDER BY a.code", *p)
    hist = call.tx.q(
        "SELECT schedule_id, verdict, performed_date FROM (SELECT t.schedule_id, t.verdict, t.performed_date,"
        " row_number() OVER (PARTITION BY t.schedule_id ORDER BY t.performed_date DESC) AS n FROM tasks t"
        " WHERE t.kind = 'calibration' AND t.status = 'completed' AND t.schedule_id = ANY(%s::uuid[])) x WHERE n <= 6"
        " ORDER BY performed_date DESC", [str(s["id"]) for s in scheds])
    preload = drift_rows(call, list({str(s["asset_id"]) for s in scheds}), 6)
    by: dict[str, list] = {}
    for h in hist:
        by.setdefault(str(h["schedule_id"]), []).append(h)
    items, kept = [], 0
    for s in scheds:
        h = by.get(str(s["id"]), [])
        if not h:
            continue
        cur = _months(s["interval_value"], s["interval_unit"])
        last_done = s["last_done"] or h[0]["performed_date"]
        rec, reason, extra = None, None, {}
        series = drift_series(call, str(s["asset_id"]), t0, 6, preload.get(str(s["asset_id"]), [])) \
            if len(h) >= 3 or work.is_oot(h[0]["verdict"]) else []
        crossings = [x["projected_crossing"] for x in series if x["projected_crossing"]]
        first_cross = min(crossings) if crossings else None
        margins = [x["margin_used_pct"] for x in series if x["margin_used_pct"] is not None]
        horizon = last_done + dt.timedelta(days=round(cur * 30.4375))
        if work.is_oot(h[0]["verdict"]):
            rec, reason = max(1.0 if s["interval_unit"] in ("month", "year") else cur / 2, cur / 2), "last_oot"
        elif first_cross and first_cross < horizon:
            rec = max(1.0 if s["interval_unit"] in ("month", "year") else 0.25, (first_cross - last_done).days / 30.4375)
            rec, reason = min(rec, cur), "drift"
            extra["crossing"] = first_cross
        elif (len(h) >= 3 and all(x["verdict"] == "pass" for x in h[:3]) and (not margins or max(margins) <= 50)
              and (not first_cross or first_cross > last_done + dt.timedelta(days=round(2 * cur * 30.4375)))):
            rec, reason = min(cur * 1.5, cur * 2), "stable"
            extra["max_margin_pct"] = max(margins) if margins else None
        if rec is None:
            kept += 1
            continue
        nv, nu = _as_interval(rec, s["interval_unit"])
        if _months(nv, nu) == cur:
            kept += 1
            continue
        items.append({**s, "history": [x["verdict"] for x in h], "reason": reason, **extra,
                      "suggested_value": nv, "suggested_unit": nu, "direction": "extend" if _months(nv, nu) > cur else "shorten",
                      "next_due_if_applied": work.add_interval(last_done, nv, nu)})
    items.sort(key=lambda x: (x["direction"] != "shorten", x["asset_code"]))
    return {"items": items, "unchanged": kept}


# ------------------------------------------------------------------ PM between campaigns
@endpoint(auth="tenant_user")
def pm_gaps(call: Call):
    t0 = today(call)
    w, p = _sites(call)
    tz = call.tx.val("SELECT timezone FROM tenants WHERE id = %s", call.tenant_id) or "UTC"
    tasks = call.tx.q(
        "SELECT t.*, a.code AS asset_code, a.name_en AS asset_name_en, a.name_ar AS asset_name_ar, tp.est_minutes"
        " FROM tasks t JOIN assets a ON a.id = t.asset_id LEFT JOIN templates tp ON tp.id = t.template_id"
        f" WHERE t.kind = 'pm' AND t.status NOT IN ('completed', 'cancelled') AND t.due_date <= %s{w}"
        " AND EXISTS (SELECT 1 FROM production_windows pw WHERE pw.asset_id = t.asset_id) ORDER BY t.due_date LIMIT 200",
        t0 + dt.timedelta(days=int(call.query.get("days", "60") or 60)), *p)
    out = []
    for t in tasks:
        g = gaps.pm_slot(call.tx, t, t0, tz)
        if g is None:
            continue
        out.append({k: t[k] for k in ("id", "number", "asset_id", "asset_code", "asset_name_en", "asset_name_ar", "due_date",
                                      "window_start", "window_end", "status")} | {
            "conflict": g["conflict"], "suggestion": g["suggestion"], "campaigns": len(g["campaigns"]),
            "need_minutes": g["need_minutes"]})
    return {"items": out}


# ------------------------------------------------------------------ run-hours meters
class MeterIn(BaseModel):
    reading: decimal.Decimal = Field(ge=0, le=100_000_000)
    read_at: dt.datetime | None = None
    meter_unit: str | None = Field(default=None, pattern="^(hours|cycles)$")
    meter_reset: bool = False
    note: str | None = Field(default=None, max_length=500)


def meter_view(call: Call, a: dict) -> dict:
    t0 = today(call)
    tot = hours.run_totals(call.tx, [a["id"]]).get(str(a["id"]))
    scheds = call.tx.q("SELECT s.id, s.kind, s.title, s.interval_value, s.interval_unit, s.hours_interval, s.hours_at_last,"
                       " s.next_due, tp.title AS template_title FROM schedules s LEFT JOIN templates tp ON tp.id = s.template_id"
                       " WHERE s.asset_id = %s AND s.active AND s.hours_interval IS NOT NULL ORDER BY s.kind, s.hours_interval", a["id"])
    for s in scheds:
        s["hours"] = hours.status(s, tot, t0)
    readings = call.tx.q("SELECT id, reading, read_at, source, meter_reset, note, entered_by_name FROM meter_readings"
                         " WHERE asset_id = %s ORDER BY read_at DESC, created_at DESC LIMIT 30", a["id"])
    roles = site_roles(call, str(a["site_id"]))
    return {"meter_unit": a["meter_unit"], "total": tot["total"] if tot else None, "last": tot["last"] if tot else None,
            "read_at": tot["read_at"] if tot else None, "rate_per_day": tot["rate_per_day"] if tot else None,
            "schedules": scheds, "readings": readings, "can_enter": bool(roles & METER_ROLES) or is_admin(call)}


def _asset(call: Call) -> dict:
    a = call.tx.one("SELECT id, code, site_id, meter_unit, status FROM assets WHERE id = %s::uuid", call.path["asset_id"])
    if not a:
        raise not_found("Asset")
    require_site(call, str(a["site_id"]))
    return a


@endpoint(auth="tenant_user")
def get_meter(call: Call):
    return meter_view(call, _asset(call))


@endpoint(auth="tenant_user", body=MeterIn)
def add_meter(call: Call):
    a = _asset(call)
    if not (site_roles(call, str(a["site_id"])) & METER_ROLES or is_admin(call)):
        raise ApiError(403, "forbidden", "Engineering, maintenance or QA access at this site is required to enter readings.")
    b: MeterIn = call.body
    at = b.read_at or dt.datetime.now(dt.timezone.utc)
    if at > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise ApiError(422, "invalid", "A reading cannot be in the future.", fields={"read_at": "A reading cannot be in the future."})
    if b.meter_unit and b.meter_unit != a["meter_unit"]:
        call.tx.x("UPDATE assets SET meter_unit = %s WHERE id = %s", b.meter_unit, a["id"])
        a["meter_unit"] = b.meter_unit
    prev = call.tx.val("SELECT reading FROM meter_readings WHERE asset_id = %s AND read_at <= %s ORDER BY read_at DESC,"
                       " created_at DESC LIMIT 1", a["id"], at)
    if not b.meter_reset and prev is not None and b.reading < prev:
        raise ApiError(422, "invalid", f"The reading is lower than the previous one ({prev}). If the meter was replaced, record it as a new meter.",
                       fields={"reading": f"Lower than the previous reading {prev}"})
    if b.meter_reset and not clean(b.note):
        raise ApiError(422, "invalid", "Say why the meter starts again.", fields={"note": "Say why the meter starts again."})
    call.tx.x("INSERT INTO meter_readings (tenant_id, asset_id, reading, read_at, meter_reset, note, entered_by, entered_by_name)"
              " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", call.tenant_id, a["id"], b.reading, at, b.meter_reset, clean(b.note),
              call.user["id"], call.user["full_name"])
    moved = hours.check(call.tx, today(call), settings_of(call), str(a["id"]))
    out = meter_view(call, a)
    out["tasks_brought_forward"] = moved
    return out
