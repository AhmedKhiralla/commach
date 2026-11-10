"""Phase 4 endpoints: My tasks (To-Do), notifications, e-mail settings, the calendar feed, rescheduling with approval,
delegation, handover of a leaving user's work, and deviations raised by escalation."""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from pydantic import BaseModel, Field
from starlette.responses import Response

from .. import db, esign, notify, work
from ..api import ApiError, Call, endpoint, not_found
from ..common import bad, clean, patch_sql
from ..config import settings
from ..perms import can_edit_site, is_admin, require_site, site_roles
from .work import _schedule_dict, can_perform, settings_of, today


def _me(call: Call) -> str:
    return str(call.user["id"])


# ================================================================== My tasks and notifications
@endpoint(auth="tenant_user")
def get_todo(call: Call):
    t0 = today(call)
    out = notify.build_todo(call.tx, _me(call), is_admin(call), t0)
    out["counts"] = {k: len(out[k]) for k in ("overdue", "soon", "later", "to_accept", "to_review", "approvals", "deviations")}
    out["unread"] = call.tx.val("SELECT count(*) FROM notifications WHERE user_id = %s::uuid AND read_at IS NULL", _me(call))
    out["delegated_from_names"] = [r["full_name"] for r in call.tx.q("SELECT full_name FROM users WHERE id = ANY(%s::uuid[])",
                                                                    out["delegated_from"])]
    return out


@endpoint(auth="tenant_user")
def list_notifications(call: Call):
    items = call.tx.q("SELECT id, kind, level, title, body, link, task_id, created_at, read_at FROM notifications"
                      " WHERE user_id = %s::uuid ORDER BY created_at DESC LIMIT 60", _me(call))
    unread = call.tx.val("SELECT count(*) FROM notifications WHERE user_id = %s::uuid AND read_at IS NULL", _me(call))
    return {"items": items, "unread": unread}


class ReadIn(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=200)


@endpoint(auth="tenant_user", body=ReadIn)
def read_notifications(call: Call):
    if call.body.ids:
        call.tx.x("UPDATE notifications SET read_at = now() WHERE user_id = %s::uuid AND id = ANY(%s::uuid[]) AND read_at IS NULL",
                  _me(call), call.body.ids)
    else:
        call.tx.x("UPDATE notifications SET read_at = now() WHERE user_id = %s::uuid AND read_at IS NULL", _me(call))
    return {"ok": True}


def _notify_view(call: Call) -> dict:
    row = call.tx.one("SELECT notify, calendar_token_hash FROM users WHERE id = %s::uuid", _me(call))
    p = row["notify"] or {}
    return {"digest": p.get("digest", True), "escalations": p.get("escalations", True), "calendar": bool(row["calendar_token_hash"]),
            "email_configured": bool(settings.gmail_app_password), "mail_from": settings.mail_from,
            "digest_hour": settings_of(call)["escalation"].get("digest_hour", 7), "email": call.user["email"]}


@endpoint(auth="tenant_user")
def get_notify(call: Call):
    return _notify_view(call)


class NotifyIn(BaseModel):
    digest: bool | None = None
    escalations: bool | None = None


@endpoint(auth="tenant_user", body=NotifyIn)
def update_notify(call: Call):
    cur = call.tx.val("SELECT notify FROM users WHERE id = %s::uuid", _me(call)) or {}
    for k in ("digest", "escalations"):
        v = getattr(call.body, k)
        if v is not None:
            cur[k] = v
    import json
    call.tx.x("UPDATE users SET notify = %s::jsonb WHERE id = %s::uuid", json.dumps(cur), _me(call))
    return _notify_view(call)


@endpoint(auth="tenant_user")
def test_email(call: Call):
    company = call.tx.val("SELECT name FROM tenants WHERE id = %s", call.tenant_id) or ""
    html_body, text = notify.render(company, call.user["full_name"], "Test e-mail from ComMach",
                                    [(None, [("If you can read this, ComMach can reach your mailbox.", "/todo")])])
    notify.queue(call.tx, call.tenant_id, _me(call), call.user["email"], "test", "[ComMach] Test e-mail", html_body, text)
    out = notify.send_outbox(call.tx, limit=5)
    return {**out, "configured": bool(settings.gmail_app_password)}


@endpoint(auth="tenant_admin")
def run_now(call: Call):
    """Run escalations, digests and the e-mail outbox for this company now (the scheduler does it every few minutes)."""
    return notify.run_tenant(call.tenant_id)


@endpoint(auth="tenant_admin")
def email_log(call: Call):
    return {"items": call.tx.q("SELECT id, to_email, kind, subject, status, attempts, last_error, created_at, sent_at"
                               " FROM email_outbox ORDER BY created_at DESC LIMIT 100"),
            "configured": bool(settings.gmail_app_password), "mail_from": settings.mail_from}


# ================================================================== calendar feed (ICS)
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _base(call: Call) -> str:
    if settings.public_url:
        return settings.public_url
    h = call.request.headers
    return f"{h.get('x-forwarded-proto') or call.request.url.scheme}://{h.get('x-forwarded-host') or h.get('host')}"


@endpoint(auth="tenant_user")
def new_calendar_link(call: Call):
    """A new private feed address (the old one stops working). Shown once: only its hash is stored."""
    token = secrets.token_urlsafe(32)
    call.tx.x("UPDATE users SET calendar_token_hash = %s WHERE id = %s::uuid", _hash(token), _me(call))
    call.tx.event("CALENDAR_LINK", "users", _me(call), call.user["email"], {"action": "created"})
    url = f"{_base(call)}/api/calendar/{token}.ics"
    return {"url": url, "webcal": url.replace("https://", "webcal://").replace("http://", "webcal://")}


@endpoint(auth="tenant_user")
def revoke_calendar_link(call: Call):
    call.tx.x("UPDATE users SET calendar_token_hash = NULL WHERE id = %s::uuid", _me(call))
    call.tx.event("CALENDAR_LINK", "users", _me(call), call.user["email"], {"action": "revoked"})
    return {"ok": True}


def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, b = [], line.encode()
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode())
        b = b[cut:]
    out.append(b.decode())
    return "\r\n ".join(out)


@endpoint(auth=None)
def calendar_feed(call: Call):
    token = call.path["token"]
    row = call.tx.one("SELECT * FROM cm_calendar_user(%s)", _hash(token))
    if not row:
        return Response("Not found", status_code=404, media_type="text/plain")
    call.tx.apply(db.Context(tenant_id=str(row["tenant_id"]), user_id=str(row["user_id"]), user_name=row["full_name"]))
    t0 = call.tx.val("SELECT (now() AT TIME ZONE coalesce((SELECT timezone FROM tenants WHERE id = %s), 'UTC'))::date", row["tenant_id"])
    admin = bool(call.tx.val("SELECT is_tenant_admin FROM users WHERE id = %s", row["user_id"]))
    td = notify.build_todo(call.tx, str(row["user_id"]), admin, t0, horizon=400)
    base = _base(call)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Comp11 Labs//ComMach//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_ics_text('ComMach – ' + row['full_name'])}", "X-PUBLISHED-TTL:PT1H", "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]
    for r in td["overdue"] + td["soon"] + td["later"]:
        what = "Calibration" if r["kind"] == "calibration" else "PM"
        summary = f"{'⚠ ' if r['overdue'] else ''}{what} {r['asset_code']} – {r['asset_name_en']}"
        link = f"{base}{notify.work_path(r['kind'])}/tasks/{r['id']}"
        desc = (f"{r['number']}{' · ' + r['template_title'] if r.get('template_title') else ''}\nDue {r['due_date']:%d %b %Y}, must be done by "
                f"{r['window_end']:%d %b %Y}.\n{link}")
        lines += ["BEGIN:VEVENT", f"UID:{r['id']}@commach", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{r['due_date']:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{(r['due_date'] + dt.timedelta(days=1)):%Y%m%d}", f"SUMMARY:{_ics_text(summary)}",
                  f"DESCRIPTION:{_ics_text(desc)}", f"URL:{link}", "TRANSP:TRANSPARENT",
                  f"CATEGORIES:{'Calibration' if r['kind'] == 'calibration' else 'Maintenance'}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    body = "\r\n".join(_fold(x) for x in lines) + "\r\n"
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="commach.ics"'})


# ================================================================== rescheduling with approval
class RescheduleIn(BaseModel):
    new_due: dt.date
    reason: str = Field(min_length=3, max_length=500)


class SignIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=500)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _task(call: Call, tid: str) -> dict:
    t = call.tx.one("SELECT t.*, a.code AS asset_code FROM tasks t JOIN assets a ON a.id = t.asset_id WHERE t.id = %s::uuid", tid)
    if not t:
        raise not_found("Task")
    require_site(call, str(t["site_id"]))
    return t


def reschedule_rows(call: Call, tid: str) -> list[dict]:
    return call.tx.q("SELECT id, old_due, new_due, within_tolerance, reason, status, requested_by, requested_by_name, supervisor_name,"
                     " supervisor_at, qa_name, qa_at, decision_note, created_at FROM reschedules WHERE task_id = %s::uuid"
                     " ORDER BY created_at DESC", tid)


def can_request_reschedule(call: Call, t: dict) -> bool:
    from .work import _is_assignee
    return t["status"] not in ("completed", "cancelled") and (can_perform(call, t) or _is_assignee(call, t)
                                                             or can_edit_site(call, str(t["site_id"])))


@endpoint(auth="tenant_user", body=RescheduleIn)
def request_reschedule(call: Call):
    t = _task(call, call.path["task_id"])
    if not can_request_reschedule(call, t):
        raise ApiError(403, "forbidden", "This task is not yours to reschedule.")
    b: RescheduleIn = call.body
    t0 = today(call)
    if b.new_due < t0:
        raise bad("new_due", "Choose a date from today on.")
    if b.new_due == t["due_date"]:
        raise bad("new_due", "Choose a different date.")
    if call.tx.val("SELECT 1 FROM reschedules WHERE task_id = %s AND status IN ('pending', 'supervisor_approved')", t["id"]):
        raise ApiError(409, "pending", "A reschedule request for this task is already waiting for approval.")
    within = (t["window_start"] is None or b.new_due >= t["window_start"]) and b.new_due <= t["window_end"]
    r = call.tx.one("INSERT INTO reschedules (tenant_id, task_id, old_due, new_due, within_tolerance, reason, requested_by, requested_by_name)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *", call.tenant_id, t["id"], t["due_date"], b.new_due, within,
                    b.reason.strip(), call.user["id"], call.user["full_name"])
    approvers = notify.role_holders(call.tx, t["site_id"], notify.APPROVE_SUPERVISOR[t["kind"]]) - {_me(call)}
    notify.notify(call.tx, call.tenant_id, notify.with_delegates(call.tx, approvers, t0), "reschedule",
                  f"Reschedule to approve: {t['number']} · {t['asset_code']}",
                  f"{call.user['full_name']} asks to move {t['number']} from {t['due_date']:%d %b %Y} to {b.new_due:%d %b %Y}"
                  f"{'' if within else ' (beyond the tolerance window: QA approves too)'}: {b.reason.strip()}",
                  "/todo", task_id=t["id"], email="reschedule")
    return {"reschedule": r, "reschedules": reschedule_rows(call, str(t["id"]))}


def _rs(call: Call) -> tuple[dict, dict]:
    r = call.tx.one("SELECT * FROM reschedules WHERE id = %s::uuid", call.path["reschedule_id"])
    if not r:
        raise not_found("Reschedule request")
    return r, _task(call, str(r["task_id"]))


def _apply(call: Call, r: dict, t: dict) -> None:
    s = call.tx.one("SELECT * FROM schedules WHERE id = %s", t["schedule_id"]) if t["schedule_id"] else None
    if s:
        start, end = work.window(r["new_due"], _schedule_dict(s), settings_of(call))
    else:
        delta = r["new_due"] - t["due_date"]
        start, end = (t["window_start"] + delta if t["window_start"] else None), t["window_end"] + delta
    call.tx.reason(f"Rescheduled from {r['old_due']} to {r['new_due']}: {r['reason']}")
    call.tx.x("UPDATE tasks SET due_date = %s, window_start = %s, window_end = %s WHERE id = %s", r["new_due"], start, max(end, r["new_due"]), t["id"])


@endpoint(auth="tenant_user", body=SignIn)
def approve_reschedule(call: Call):
    r, t = _rs(call)
    if r["status"] not in ("pending", "supervisor_approved"):
        raise ApiError(409, "decided", "This request is already decided.")
    if str(r["requested_by"]) == _me(call):
        raise ApiError(403, "four_eyes", "You cannot approve your own reschedule request.")
    roles = site_roles(call, str(t["site_id"]))
    b: SignIn = call.body
    label = f"{t['number']} → {r['new_due']}"
    rhash = esign.record_hash({"task": t["number"], "old_due": r["old_due"], "new_due": r["new_due"], "reason": r["reason"]})
    if r["status"] == "pending":
        if not roles & set(notify.APPROVE_SUPERVISOR[t["kind"]]):
            raise ApiError(403, "forbidden", "A supervisor of the site approves the new date first.")
        signer = esign.verify_signer(call, b.email, b.password, entity="reschedule", record_id=str(r["id"]), label=label,
                                     meaning="reschedule_approved")
        sig = esign.record(call, signer=signer, entity="reschedule", record_id=str(r["id"]), label=label,
                           meaning="reschedule_approved", rhash=rhash, reason=b.reason)
        final = r["within_tolerance"]
        call.tx.x("UPDATE reschedules SET status = %s, supervisor_signature_id = %s, supervisor_name = %s, supervisor_at = now()"
                  " WHERE id = %s", "approved" if final else "supervisor_approved", sig["id"], call.user["full_name"], r["id"])
        if not final:
            qa = notify.role_holders(call.tx, t["site_id"], ["qa_approver"]) - {str(r["requested_by"]), _me(call)}
            notify.notify(call.tx, call.tenant_id, notify.with_delegates(call.tx, qa, today(call)), "reschedule",
                          f"Reschedule beyond tolerance to approve: {t['number']} · {t['asset_code']}",
                          f"{r['requested_by_name']} asks to move {t['number']} to {r['new_due']:%d %b %Y}, outside its tolerance window."
                          f" {call.user['full_name']} (supervisor) approved. Reason: {r['reason']}", "/todo", task_id=t["id"], email="reschedule")
    else:
        if "qa_approver" not in roles:
            raise ApiError(403, "forbidden", "A QA approver of the site approves a new date beyond the tolerance window.")
        if r["supervisor_signature_id"] and str(call.tx.val("SELECT user_id FROM signatures WHERE id = %s", r["supervisor_signature_id"])) == _me(call):
            raise ApiError(403, "four_eyes", "The supervisor who approved cannot also give the QA approval.")
        signer = esign.verify_signer(call, b.email, b.password, entity="reschedule", record_id=str(r["id"]), label=label,
                                     meaning="reschedule_qa_approved")
        sig = esign.record(call, signer=signer, entity="reschedule", record_id=str(r["id"]), label=label,
                           meaning="reschedule_qa_approved", rhash=rhash, reason=b.reason)
        call.tx.x("UPDATE reschedules SET status = 'approved', qa_signature_id = %s, qa_name = %s, qa_at = now() WHERE id = %s",
                  sig["id"], call.user["full_name"], r["id"])
        final = True
    if final:
        _apply(call, r, t)
        notify.notify(call.tx, call.tenant_id, {str(r["requested_by"])}, "reschedule", f"Reschedule approved: {t['number']}",
                      f"{t['number']} is now due {r['new_due']:%d %b %Y}.", f"{notify.work_path(t['kind'])}/tasks/{t['id']}", task_id=t["id"])
    return {"reschedules": reschedule_rows(call, str(t["id"]))}


@endpoint(auth="tenant_user", body=RejectIn)
def reject_reschedule(call: Call):
    r, t = _rs(call)
    if r["status"] not in ("pending", "supervisor_approved"):
        raise ApiError(409, "decided", "This request is already decided.")
    roles = site_roles(call, str(t["site_id"]))
    own = str(r["requested_by"]) == _me(call)
    allowed = own or roles & set(notify.APPROVE_SUPERVISOR[t["kind"]]) or (r["status"] == "supervisor_approved" and "qa_approver" in roles)
    if not allowed:
        raise ApiError(403, "forbidden", "Only an approver (or the requester, to withdraw it) can close this request.")
    call.tx.x("UPDATE reschedules SET status = %s, decision_note = %s WHERE id = %s", "withdrawn" if own else "rejected",
              call.body.reason.strip(), r["id"])
    if not own:
        notify.notify(call.tx, call.tenant_id, {str(r["requested_by"])}, "reschedule", f"Reschedule rejected: {t['number']}",
                      f"{call.user['full_name']}: {call.body.reason.strip()}", f"{notify.work_path(t['kind'])}/tasks/{t['id']}", task_id=t["id"])
    return {"reschedules": reschedule_rows(call, str(t["id"]))}


# ================================================================== delegation
class DelegationIn(BaseModel):
    from_user_id: str | None = None
    to_user_id: str
    starts_on: dt.date
    ends_on: dt.date
    reason: str = Field(min_length=3, max_length=500)


DELEGATION_SQL = """
SELECT d.id, d.from_user, d.to_user, d.starts_on, d.ends_on, d.reason, d.active, d.created_at,
       f.full_name AS from_name, t.full_name AS to_name
  FROM delegations d JOIN users f ON f.id = d.from_user JOIN users t ON t.id = d.to_user
"""


@endpoint(auth="tenant_user")
def list_delegations(call: Call):
    t0 = today(call)
    w = "" if is_admin(call) else " WHERE d.from_user = %s::uuid OR d.to_user = %s::uuid"
    rows = call.tx.q(DELEGATION_SQL + w + " ORDER BY d.active DESC, d.starts_on DESC LIMIT 200",
                     *([] if is_admin(call) else [_me(call), _me(call)]))
    for r in rows:
        r["current"] = r["active"] and r["starts_on"] <= t0 <= r["ends_on"]
    return {"items": rows}


@endpoint(auth="tenant_user", body=DelegationIn)
def create_delegation(call: Call):
    b: DelegationIn = call.body
    frm = b.from_user_id or _me(call)
    if frm != _me(call) and not is_admin(call):
        raise ApiError(403, "forbidden", "You can delegate only your own work.")
    if frm == b.to_user_id:
        raise bad("to_user_id", "Choose another person.")
    if not call.tx.val("SELECT 1 FROM users WHERE id = %s::uuid AND status = 'active' AND tenant_id IS NOT NULL", b.to_user_id):
        raise bad("to_user_id", "Choose an active person.")
    if b.ends_on < b.starts_on:
        raise bad("ends_on", "The end is before the start.")
    if b.ends_on < today(call):
        raise bad("ends_on", "The delegation would already have ended.")
    if (b.ends_on - b.starts_on).days > 366:
        raise bad("ends_on", "Delegate for a year at most.")
    d = call.tx.one("INSERT INTO delegations (tenant_id, from_user, to_user, starts_on, ends_on, reason) VALUES"
                    " (%s, %s::uuid, %s::uuid, %s, %s, %s) RETURNING id", call.tenant_id, frm, b.to_user_id, b.starts_on, b.ends_on,
                    b.reason.strip())
    name = call.tx.val("SELECT full_name FROM users WHERE id = %s::uuid", frm)
    notify.notify(call.tx, call.tenant_id, {b.to_user_id}, "delegation", f"{name} delegated their work to you",
                  f"From {b.starts_on:%d %b %Y} to {b.ends_on:%d %b %Y}: {b.reason.strip()}. Their tasks appear in My tasks. "
                  "Signing still needs your own role.", "/todo")
    return call.tx.one(DELEGATION_SQL + " WHERE d.id = %s", d["id"])


@endpoint(auth="tenant_user")
def end_delegation(call: Call):
    d = call.tx.one("SELECT * FROM delegations WHERE id = %s::uuid", call.path["delegation_id"])
    if not d:
        raise not_found("Delegation")
    if _me(call) not in (str(d["from_user"]), str(d["to_user"])) and not is_admin(call):
        raise ApiError(403, "forbidden", "This delegation is not yours.")
    call.tx.x("UPDATE delegations SET active = false WHERE id = %s", d["id"])
    return {"ok": True}


# ================================================================== handover
def _handover_items(call: Call, uid: str) -> dict:
    return {
        "schedules_responsible": call.tx.val("SELECT count(*) FROM schedules WHERE active AND responsible_user_id = %s::uuid", uid),
        "schedules_assigned": call.tx.val("SELECT count(*) FROM schedules WHERE active AND %s::uuid = ANY(assignee_user_ids)", uid),
        "tasks_assigned": call.tx.val("SELECT count(*) FROM tasks WHERE status IN ('open', 'in_progress', 'at_vendor')"
                                      " AND (assigned_user_id = %s::uuid OR %s::uuid = ANY(assignee_user_ids))", uid, uid),
        "tasks_responsible": call.tx.val("SELECT count(*) FROM tasks WHERE status IN ('open', 'in_progress', 'at_vendor')"
                                         " AND responsible_user_id = %s::uuid", uid),
        "department_head": [r["code"] for r in call.tx.q("SELECT code FROM departments WHERE head_user_id = %s::uuid", uid)],
        "site_head": [r["code"] for r in call.tx.q("SELECT code FROM sites WHERE head_user_id = %s::uuid", uid)],
        "delegations": call.tx.val("SELECT count(*) FROM delegations WHERE active AND (from_user = %s::uuid OR to_user = %s::uuid)", uid, uid),
        "roles": call.tx.q("SELECT r.site_id, s.code AS site_code, r.role FROM user_site_roles r JOIN sites s ON s.id = r.site_id"
                           " WHERE r.user_id = %s::uuid ORDER BY s.code, r.role", uid),
    }


@endpoint(auth="tenant_admin")
def get_handover(call: Call):
    uid = call.path["user_id"]
    u = call.tx.one("SELECT id, full_name, email, status FROM users WHERE id = %s::uuid", uid)
    if not u:
        raise not_found("User")
    items = _handover_items(call, uid)
    items["total"] = sum(v if isinstance(v, int) else len(v) for k, v in items.items() if k != "roles")
    return {"user": u, "items": items}


class HandoverIn(BaseModel):
    to_user_id: str
    reason: str = Field(min_length=3, max_length=500)
    copy_roles: bool = False


@endpoint(auth="tenant_admin", body=HandoverIn)
def do_handover(call: Call):
    uid = call.path["user_id"]
    b: HandoverIn = call.body
    frm = call.tx.one("SELECT id, full_name FROM users WHERE id = %s::uuid", uid)
    to = call.tx.one("SELECT id, full_name FROM users WHERE id = %s::uuid AND status = 'active'", b.to_user_id)
    if not frm:
        raise not_found("User")
    if not to or b.to_user_id == uid:
        raise bad("to_user_id", "Choose another active person.")
    before = _handover_items(call, uid)
    call.tx.reason(f"Handover from {frm['full_name']} to {to['full_name']}: {b.reason.strip()}")
    call.tx.x("UPDATE schedules SET responsible_user_id = %s::uuid WHERE active AND responsible_user_id = %s::uuid", b.to_user_id, uid)
    call.tx.x("UPDATE schedules SET assignee_user_ids = array(SELECT DISTINCT unnest(array_replace(assignee_user_ids, %s::uuid, %s::uuid)))"
              " WHERE active AND %s::uuid = ANY(assignee_user_ids)", uid, b.to_user_id, uid)
    open_ = "status IN ('open', 'in_progress', 'at_vendor')"
    call.tx.x(f"UPDATE tasks SET assigned_user_id = %s::uuid, accepted_at = NULL, accepted_by_name = NULL"
              f" WHERE {open_} AND assigned_user_id = %s::uuid AND status = 'open'", b.to_user_id, uid)
    call.tx.x(f"UPDATE tasks SET assigned_user_id = %s::uuid WHERE {open_} AND assigned_user_id = %s::uuid", b.to_user_id, uid)
    call.tx.x(f"UPDATE tasks SET responsible_user_id = %s::uuid WHERE {open_} AND responsible_user_id = %s::uuid", b.to_user_id, uid)
    call.tx.x(f"UPDATE tasks SET assignee_user_ids = array(SELECT DISTINCT unnest(array_replace(assignee_user_ids, %s::uuid, %s::uuid)))"
              f" WHERE {open_} AND %s::uuid = ANY(assignee_user_ids)", uid, b.to_user_id, uid)
    call.tx.x("UPDATE departments SET head_user_id = %s::uuid WHERE head_user_id = %s::uuid", b.to_user_id, uid)
    call.tx.x("UPDATE sites SET head_user_id = %s::uuid WHERE head_user_id = %s::uuid", b.to_user_id, uid)
    call.tx.x("UPDATE delegations SET active = false WHERE active AND (from_user = %s::uuid OR to_user = %s::uuid)", uid, uid)
    if b.copy_roles:
        for r in before["roles"]:
            call.tx.x("INSERT INTO user_site_roles (tenant_id, user_id, site_id, role) VALUES (%s, %s::uuid, %s, %s) ON CONFLICT DO NOTHING",
                      call.tenant_id, b.to_user_id, r["site_id"], r["role"])
    summary = {k: v for k, v in before.items() if k != "roles"}
    call.tx.event("HANDOVER", "users", uid, frm["full_name"], {"to": to["full_name"], "reason": b.reason.strip(),
                                                                "copied_roles": b.copy_roles, **summary})
    notify.notify(call.tx, call.tenant_id, {b.to_user_id}, "handover", f"{frm['full_name']}'s work was handed over to you",
                  f"{b.reason.strip()}. Open tasks and schedules are now yours; check My tasks.", "/todo")
    return {"moved": summary, "to": to["full_name"]}


# ================================================================== deviations
DEV_SQL = """
SELECT d.*, a.code AS asset_code, a.name_en AS asset_name_en, a.name_ar AS asset_name_ar, s.code AS site_code,
       t.number AS task_number, t.kind AS task_kind, t.status AS task_status
  FROM deviations d JOIN assets a ON a.id = d.asset_id JOIN sites s ON s.id = d.site_id LEFT JOIN tasks t ON t.id = d.task_id
"""


@endpoint(auth="tenant_user")
def list_deviations(call: Call):
    from ..perms import visible_site_ids
    sites = visible_site_ids(call)
    w, p = (" WHERE d.site_id = ANY(%s::uuid[])", [sites]) if sites is not None else ("", [])
    if call.query.get("status"):
        w += (" AND" if w else " WHERE") + " d.status = %s"
        p.append(call.query["status"])
    return {"items": call.tx.q(DEV_SQL + w + " ORDER BY d.status DESC, d.created_at DESC LIMIT 300", *p)}


def _dev(call: Call, did: str) -> dict:
    d = call.tx.one(DEV_SQL + " WHERE d.id = %s::uuid", did)
    if not d:
        raise not_found("Deviation")
    require_site(call, str(d["site_id"]))
    roles = site_roles(call, str(d["site_id"]))
    d["can_edit"] = d["status"] == "open" and bool(roles & {"qa_approver", "engineering_manager", "engineering_supervisor",
                                                            "qa_calibration_officer"} or is_admin(call))
    d["can_close"] = d["status"] == "open" and "qa_approver" in roles
    d["escalations"] = call.tx.q("SELECT level, trigger, created_at, cardinality(recipients) AS recipients FROM escalations"
                                 " WHERE task_id = %s ORDER BY created_at", d["task_id"]) if d["task_id"] else []
    d["signature"] = call.tx.one("SELECT user_name, user_email, signed_at, statement, record_hash FROM signatures WHERE id = %s",
                                 d["closed_signature_id"]) if d["closed_signature_id"] else None
    return d


@endpoint(auth="tenant_user")
def get_deviation(call: Call):
    return _dev(call, call.path["deviation_id"])


class DevPatch(BaseModel):
    description: str | None = Field(default=None, max_length=8000)
    root_cause: str | None = Field(default=None, max_length=8000)
    actions: str | None = Field(default=None, max_length=8000)


@endpoint(auth="tenant_user", body=DevPatch)
def update_deviation(call: Call):
    d = _dev(call, call.path["deviation_id"])
    if not d["can_edit"]:
        raise ApiError(403, "forbidden", "This deviation cannot be changed by you.")
    patch_sql(call, "deviations", str(d["id"]), {k: clean(v) for k, v in call.body.model_dump(exclude_unset=True).items()})
    return _dev(call, str(d["id"]))


@endpoint(auth="tenant_user", body=SignIn)
def close_deviation(call: Call):
    d = _dev(call, call.path["deviation_id"])
    if not d["can_close"]:
        raise ApiError(403, "forbidden", "A QA approver of the site closes deviations.")
    if not d["root_cause"] or not d["actions"]:
        raise ApiError(422, "incomplete", "Record the root cause and the actions before closing.", extra={"problems": ["root_cause", "actions"]})
    b: SignIn = call.body
    signer = esign.verify_signer(call, b.email, b.password, entity="deviation", record_id=str(d["id"]), label=d["number"],
                                 meaning="deviation_closed")
    sig = esign.record(call, signer=signer, entity="deviation", record_id=str(d["id"]), label=d["number"], meaning="deviation_closed",
                       rhash=esign.record_hash({k: d[k] for k in ("number", "title", "description", "root_cause", "actions")}), reason=b.reason)
    call.tx.x("UPDATE deviations SET status = 'closed', closed_signature_id = %s, closed_at = now(), closed_by_name = %s WHERE id = %s",
              sig["id"], call.user["full_name"], d["id"])
    return _dev(call, str(d["id"]))
