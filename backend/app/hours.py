"""Phase 6: run-hours triggers.

A schedule may carry `hours_interval`: the work falls due after that many run-hours (or cycles) since it was last
done, or at its date, whichever comes first. Meter readings only go up; a replaced meter is recorded as a reset, so
the run-hours are the sum of the increases between readings.
"""
from __future__ import annotations

import datetime as dt
import decimal

from . import db, work


def run_totals(tx: db.Tx, asset_ids: list) -> dict[str, dict]:
    """Per asset: cumulative run-hours, last reading, when it was read and the average use per day (last 90 days)."""
    out: dict[str, dict] = {}
    if not asset_ids:
        return out
    rows = tx.q("SELECT asset_id, reading, read_at, meter_reset FROM meter_readings WHERE asset_id = ANY(%s::uuid[])"
                " ORDER BY asset_id, read_at, created_at", [str(a) for a in asset_ids])
    for r in rows:
        k = str(r["asset_id"])
        x = out.get(k)
        if x is None:
            x = out[k] = {"total": decimal.Decimal(0), "last": r["reading"], "read_at": r["read_at"], "points": []}
        elif r["meter_reset"]:
            x["last"] = r["reading"]
        else:
            x["total"] += r["reading"] - x["last"]
            x["last"] = r["reading"]
        x["read_at"] = r["read_at"]
        x["points"].append((r["read_at"], x["total"]))
    for x in out.values():
        pts = x.pop("points")
        recent = [p for p in pts if p[0] >= pts[-1][0] - dt.timedelta(days=90)]
        first = recent[0] if len(recent) > 1 else (pts[-2] if len(pts) > 1 else None)
        days = (pts[-1][0] - first[0]).total_seconds() / 86400 if first else 0
        x["rate_per_day"] = round(float(pts[-1][1] - first[1]) / days, 2) if first and days >= 1 else None
    return out


def status(s: dict, tot: dict | None, today: dt.date) -> dict | None:
    """Run-hours position of one schedule: used since last done, remaining, and the projected date."""
    if not s.get("hours_interval"):
        return None
    total = tot["total"] if tot else None
    used = (total - (s["hours_at_last"] or 0)) if total is not None and s.get("hours_at_last") is not None else None
    remaining = (s["hours_interval"] - used) if used is not None else None
    projected = None
    if remaining is not None and tot and tot.get("rate_per_day"):
        start = tot["read_at"].date() if tot.get("read_at") else today
        projected = max(today, start + dt.timedelta(days=int(float(max(remaining, 0)) / tot["rate_per_day"])))
    return {"hours_interval": s["hours_interval"], "used": used, "remaining": remaining, "projected_date": projected,
            "total": total, "rate_per_day": tot.get("rate_per_day") if tot else None,
            "read_at": tot.get("read_at") if tot else None, "due": remaining is not None and remaining <= 0}


def check(tx: db.Tx, today: dt.date, st: dict, asset_id: str | None = None) -> int:
    """Bring forward the open task of every schedule whose run-hours are used up. Returns how many tasks became due."""
    where, params = ["s.active", "s.hours_interval IS NOT NULL"], []
    if asset_id:
        where.append("s.asset_id = %s::uuid")
        params.append(asset_id)
    scheds = tx.q("SELECT s.* FROM schedules s WHERE " + " AND ".join(where), *params)
    totals = run_totals(tx, list({s["asset_id"] for s in scheds}))
    moved = 0
    for s in scheds:
        tot = totals.get(str(s["asset_id"]))
        if not tot:
            continue
        if s["hours_at_last"] is None:       # the first reading after hours were set is the starting point
            tx.x("UPDATE schedules SET hours_at_last = %s WHERE id = %s", tot["total"], s["id"])
            continue
        if tot["total"] - s["hours_at_last"] < s["hours_interval"]:
            continue
        t = tx.one("SELECT * FROM tasks WHERE schedule_id = %s AND status = 'open' AND NOT due_by_hours"
                   " ORDER BY due_date LIMIT 1", s["id"])
        if not t:
            continue
        due = work.working_day(today, st, s["kind"])
        if t["due_date"] <= due:               # already due: only record why
            tx.x("UPDATE tasks SET due_by_hours = true WHERE id = %s", t["id"])
            moved += 1
            continue
        sd = {k: s.get(k) for k in ("kind", "interval_value", "interval_unit", "basis", "tol_before_value", "tol_before_unit",
                                    "tol_after_value", "tol_after_unit")}
        start, end = work.window(due, sd, st)
        tx.x("UPDATE tasks SET due_date = %s, window_start = %s, window_end = %s, due_by_hours = true WHERE id = %s",
             due, start, end, t["id"])
        moved += 1
    return moved


def on_completed(tx: db.Tx, schedule_id) -> None:
    """The run-hours count again from the moment the work is done."""
    s = tx.one("SELECT asset_id, hours_interval FROM schedules WHERE id = %s", schedule_id)
    if not s or not s["hours_interval"]:
        return
    tot = run_totals(tx, [s["asset_id"]]).get(str(s["asset_id"]))
    tx.x("UPDATE schedules SET hours_at_last = %s WHERE id = %s", tot["total"] if tot else None, schedule_id)
