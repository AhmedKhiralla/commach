"""Scheduling and results logic shared by calibration (Phase 2) and preventive maintenance (Phase 3).

Due dates
  floating  the next due date is counted from the date the work was actually done (calibration default)
  fixed     the next due date is counted from the previous due date, so the calendar never drifts (PM default)

Tolerance window
  Each due date has a window [due - before, due + after]. "before" may be "any" (early is always allowed, the usual
  rule for calibration). The company sets the rule per interval band in any unit (days, weeks, months or a percentage
  of the interval); a schedule can override it. After the window ends the task is overdue.

Results
  A calibration point has limits from its tolerance (± absolute, ± percent of nominal, or low/high limits).
  Verdict: pass (every as-found reading in tolerance), adjusted (as found out of tolerance, as left in tolerance)
  or fail (still out of tolerance). Adjusted and fail are out-of-tolerance (OOT) results and need an impact assessment.
"""
from __future__ import annotations

import calendar
import datetime as dt
import decimal
import math

UNIT_DAYS = {"day": 1, "week": 7, "month": 30.4375, "year": 365.25}

# Company defaults. up_to_days = the longest interval the band applies to (None = everything longer).
TOLERANCE_DEFAULTS: dict[str, list[dict]] = {
    "calibration": [
        {"up_to_days": 7, "before_value": None, "before_unit": "any", "after_value": 1, "after_unit": "day"},
        {"up_to_days": 31, "before_value": None, "before_unit": "any", "after_value": 3, "after_unit": "day"},
        {"up_to_days": 92, "before_value": None, "before_unit": "any", "after_value": 7, "after_unit": "day"},
        {"up_to_days": 183, "before_value": None, "before_unit": "any", "after_value": 10, "after_unit": "day"},
        {"up_to_days": 366, "before_value": None, "before_unit": "any", "after_value": 15, "after_unit": "day"},
        {"up_to_days": None, "before_value": None, "before_unit": "any", "after_value": 30, "after_unit": "day"},
    ],
    "pm": [
        {"up_to_days": 7, "before_value": 1, "before_unit": "day", "after_value": 1, "after_unit": "day"},
        {"up_to_days": 31, "before_value": 3, "before_unit": "day", "after_value": 3, "after_unit": "day"},
        {"up_to_days": 92, "before_value": 1, "before_unit": "week", "after_value": 1, "after_unit": "week"},
        {"up_to_days": 183, "before_value": 2, "before_unit": "week", "after_value": 2, "after_unit": "week"},
        {"up_to_days": 366, "before_value": 1, "before_unit": "month", "after_value": 1, "after_unit": "month"},
        {"up_to_days": None, "before_value": 10, "before_unit": "percent", "after_value": 10, "after_unit": "percent"},
    ],
}

WORK_DEFAULTS = {
    "tolerance": TOLERANCE_DEFAULTS,
    "due_soon_days": 30,           # "due soon" on the overview and work lists
    "quarantine_on_fail": True,    # a failed calibration quarantines the asset with the completion signature
    "uncertainty_ratio_min": 4,    # test uncertainty ratio expected on certificates (warning below it)
}


def effective_settings(stored: dict | None) -> dict:
    out = {**WORK_DEFAULTS, "tolerance": {k: [dict(b) for b in v] for k, v in TOLERANCE_DEFAULTS.items()}}
    for k, v in (stored or {}).items():
        if k == "tolerance" and isinstance(v, dict):
            for kind in ("calibration", "pm"):
                if isinstance(v.get(kind), list) and v[kind]:
                    out["tolerance"][kind] = v[kind]
        elif k in WORK_DEFAULTS:
            out[k] = v
    return out


def add_interval(d: dt.date, value: int, unit: str) -> dt.date:
    if unit == "day":
        return d + dt.timedelta(days=value)
    if unit == "week":
        return d + dt.timedelta(weeks=value)
    months = value * (12 if unit == "year" else 1)
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def interval_days(value: int, unit: str) -> float:
    return value * UNIT_DAYS[unit]


def _offset(d: dt.date, value, unit: str, sign: int, interval_d: float) -> dt.date:
    v = float(value or 0)
    if unit == "percent":
        days = math.floor(interval_d * v / 100.0 + 1e-9)
        return d + dt.timedelta(days=sign * days)
    if unit == "month":
        return add_interval(d, int(v), "month") if sign > 0 else _sub_months(d, int(v))
    return d + dt.timedelta(days=sign * int(round(v * UNIT_DAYS[unit])))


def _sub_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 - n, 12)
    year, month = d.year + y, m + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def band_for(settings: dict, kind: str, value: int, unit: str) -> dict:
    days = interval_days(value, unit)
    bands = sorted(settings["tolerance"][kind], key=lambda b: (b.get("up_to_days") is None, b.get("up_to_days") or 0))
    for b in bands:
        if b.get("up_to_days") is None or days <= b["up_to_days"]:
            return b
    return bands[-1]


def rule_for(schedule: dict, settings: dict) -> dict:
    """The tolerance rule a schedule uses: its own values, or the company band for its interval."""
    band = band_for(settings, schedule["kind"], schedule["interval_value"], schedule["interval_unit"])
    rule = dict(band)
    if schedule.get("tol_before_unit"):
        rule["before_unit"] = schedule["tol_before_unit"]
        rule["before_value"] = schedule.get("tol_before_value")
    if schedule.get("tol_after_unit"):
        rule["after_unit"] = schedule["tol_after_unit"]
        rule["after_value"] = schedule.get("tol_after_value")
    return rule


def window(due: dt.date, schedule: dict, settings: dict) -> tuple[dt.date | None, dt.date]:
    rule = rule_for(schedule, settings)
    idays = interval_days(schedule["interval_value"], schedule["interval_unit"])
    start = None if rule["before_unit"] == "any" else _offset(due, rule["before_value"], rule["before_unit"], -1, idays)
    end = _offset(due, rule["after_value"], rule["after_unit"], +1, idays)
    return start, max(end, due)


def next_due(schedule: dict, done: dt.date, previous_due: dt.date | None) -> dt.date:
    base = previous_due if schedule["basis"] == "fixed" and previous_due else done
    return add_interval(base, schedule["interval_value"], schedule["interval_unit"])


def preview(schedule: dict, settings: dict, first_due: dt.date, n: int = 12) -> list[dict]:
    """The next n due dates with their windows (floating schedules assume each is done on its due date)."""
    out, due = [], first_due
    for _ in range(n):
        s, e = window(due, schedule, settings)
        out.append({"due": due, "window_start": s, "window_end": e})
        due = add_interval(due, schedule["interval_value"], schedule["interval_unit"])
    return out


# ------------------------------------------------------------------ calibration limits and verdict
def D(v) -> decimal.Decimal | None:
    if v is None or v == "":
        return None
    return v if isinstance(v, decimal.Decimal) else decimal.Decimal(str(v))


def limits(item: dict) -> tuple[decimal.Decimal | None, decimal.Decimal | None]:
    mode = item.get("tol_mode")
    nominal = D(item.get("nominal"))
    if mode == "limits":
        return D(item.get("low_limit")), D(item.get("high_limit"))
    if nominal is None or mode is None:
        return D(item.get("low_limit")), D(item.get("high_limit"))
    minus, plus = D(item.get("tol_minus")) or 0, D(item.get("tol_plus")) or 0
    if mode == "pct":
        minus, plus = abs(nominal) * minus / 100, abs(nominal) * plus / 100
    return nominal - minus, nominal + plus


def within(value, low, high) -> bool | None:
    v = D(value)
    if v is None:
        return None
    return (low is None or v >= low) and (high is None or v <= high)


def verdict(results: list[dict]) -> str | None:
    """pass | adjusted | fail, from the as-found / as-left results of the calibration points."""
    points = [r for r in results if r["item_type"] == "point"]
    if not points or any(r.get("ok_found") is None for r in points):
        return None
    if all(r["ok_found"] for r in points):
        return "pass"
    left = [r.get("ok_left") if r.get("as_left") is not None else r["ok_found"] for r in points]
    return "adjusted" if all(left) else "fail"


def is_oot(v: str | None) -> bool:
    return v in ("adjusted", "fail")
