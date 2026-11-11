"""Phase 6: PM in the gaps between production campaigns (the released plan comes from Complanning)."""
from __future__ import annotations

import datetime as dt

from . import db


def pm_slot(tx: db.Tx, task: dict, today: dt.date, tz: str = "UTC") -> dict | None:
    """Campaigns on the machine around an open task and the free slot nearest its due date inside its window.
    None when Complanning has sent no plan for this machine."""
    if task["status"] in ("completed", "cancelled"):
        return None
    lo = max(today, task["window_start"] or task["due_date"] - dt.timedelta(days=14))
    hi = max(lo, task["window_end"])
    rows = tx.q("SELECT starts_at, ends_at, label, ref FROM production_windows WHERE asset_id = %s"
                " AND ends_at > (%s::date)::timestamp AT TIME ZONE %s AND starts_at < ((%s::date + 1)::timestamp AT TIME ZONE %s)"
                " ORDER BY starts_at", task["asset_id"], lo, tz, hi, tz)
    if not rows and not tx.val("SELECT 1 FROM production_windows WHERE asset_id = %s LIMIT 1", task["asset_id"]):
        return None
    need = dt.timedelta(minutes=task.get("est_minutes") or 240)
    zone = tx.val("SELECT now()::timestamptz AT TIME ZONE %s - now()::timestamptz AT TIME ZONE 'UTC'", tz) or dt.timedelta(0)
    to_utc = lambda d: dt.datetime.combine(d, dt.time(), dt.timezone.utc) - zone   # local midnight in UTC
    start, end = to_utc(lo), to_utc(hi + dt.timedelta(days=1))
    free, cur = [], start
    for r in rows:
        if r["starts_at"] > cur:
            free.append((cur, min(r["starts_at"], end)))
        cur = max(cur, r["ends_at"])
    if cur < end:
        free.append((cur, end))
    free = [f for f in free if f[1] - f[0] >= need]
    due_at = to_utc(task["due_date"])
    conflict = any(r["starts_at"] < due_at + dt.timedelta(days=1) and r["ends_at"] > due_at for r in rows)
    prefer = due_at + dt.timedelta(hours=8)          # the start of the working day on the due date
    place = lambda f: max(f[0], min(prefer, f[1] - need))
    best = min(free, key=lambda f: abs((place(f) - prefer).total_seconds())) if free else None
    suggestion = None
    if best:
        s = place(best)
        suggestion = {"start": s, "end": s + need, "date": (s + zone).date(), "free_until": best[1], "hours_free": round((best[1] - best[0]).total_seconds() / 3600, 1)}
    return {"campaigns": rows, "conflict": conflict, "suggestion": suggestion, "need_minutes": int(need.total_seconds() // 60),
            "from": lo, "to": hi}
