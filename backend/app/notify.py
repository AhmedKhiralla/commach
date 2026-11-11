"""To-Do, notifications, e-mail and escalation (Phase 4).

The scheduler runs inside the web process (every SCHEDULER_SECONDS) under a database advisory lock, so only one
instance works at a time. For each company it:
  1. escalates open work (level 1: not accepted / due; level 2: tolerance window ended -> department head and QA;
     level 3: N days later by criticality -> site head, with a deviation record),
  2. queues one daily digest per person at the company's digest hour (only when there is something to do),
  3. sends the e-mail outbox through Gmail (SMTP over SSL), as in the Comp11 naming worker.
Every notification is also shown in the app; e-mail is an extra channel, never the only one.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from . import db, work
from .config import settings

log = logging.getLogger("commach.notify")
LOCK_ID = 727_041
STATE: dict = {"last_run": None, "last_error": None}   # shown by the system check
SUPERVISORS = {"calibration": ("qa_calibration_officer", "engineering_supervisor"), "pm": ("engineering_supervisor",)}
REVIEWERS = {"calibration": ("qa_approver", "qa_calibration_officer"), "pm": ("engineering_supervisor", "engineering_manager", "qa_approver")}
APPROVE_SUPERVISOR = {"calibration": ("engineering_supervisor", "engineering_manager", "qa_calibration_officer"),
                      "pm": ("engineering_supervisor", "engineering_manager")}


def base_url() -> str:
    return settings.public_url or "https://commach.comp11labs.com"


def work_path(kind: str) -> str:
    return "/calibration" if kind == "calibration" else "/maintenance"


# ------------------------------------------------------------------ people
def delegators(tx: db.Tx, user_id: str, day: dt.date) -> list[str]:
    """People who delegated their work to this user on that day."""
    return [str(r["from_user"]) for r in tx.q(
        "SELECT from_user FROM delegations WHERE to_user = %s::uuid AND active AND %s BETWEEN starts_on AND ends_on", user_id, day)]


def with_delegates(tx: db.Tx, ids: set[str], day: dt.date) -> set[str]:
    if not ids:
        return ids
    extra = {str(r["to_user"]) for r in tx.q(
        "SELECT d.to_user FROM delegations d JOIN users u ON u.id = d.to_user WHERE d.from_user = ANY(%s::uuid[]) AND d.active"
        " AND %s BETWEEN d.starts_on AND d.ends_on AND u.status = 'active'", sorted(ids), day)}
    return ids | extra


def role_holders(tx: db.Tx, site_id, roles) -> set[str]:
    if not roles:
        return set()
    return {str(r["user_id"]) for r in tx.q(
        "SELECT DISTINCT r.user_id FROM user_site_roles r JOIN users u ON u.id = r.user_id"
        " WHERE r.site_id = %s AND r.role = ANY(%s::text[]) AND u.status = 'active'", site_id, list(roles))}


def task_people(tx: db.Tx, t: dict) -> set[str]:
    ids = {str(x) for x in (t.get("assignee_user_ids") or [])}
    for k in ("assigned_user_id", "responsible_user_id"):
        if t.get(k):
            ids.add(str(t[k]))
    if not t.get("assigned_user_id"):
        ids |= role_holders(tx, t["site_id"], t.get("assignee_roles") or [])
    return ids


def site_head(tx: db.Tx, site_id) -> set[str]:
    head = tx.val("SELECT s.head_user_id FROM sites s JOIN users u ON u.id = s.head_user_id WHERE s.id = %s AND u.status = 'active'", site_id)
    if head:
        return {str(head)}
    mgr = role_holders(tx, site_id, ["engineering_manager"])
    return mgr or {str(r["id"]) for r in tx.q("SELECT id FROM users WHERE is_tenant_admin AND status = 'active'")}


def dept_head(tx: db.Tx, asset_id) -> set[str]:
    h = tx.val("SELECT d.head_user_id FROM assets a JOIN departments d ON d.id = a.department_id JOIN users u ON u.id = d.head_user_id"
               " WHERE a.id = %s AND u.status = 'active'", asset_id)
    return {str(h)} if h else set()


# ------------------------------------------------------------------ notifications and e-mail
def prefs(tx: db.Tx, user_id: str) -> dict:
    p = tx.val("SELECT notify FROM users WHERE id = %s::uuid", user_id) or {}
    return {"digest": p.get("digest", True), "escalations": p.get("escalations", True)}


def notify(tx: db.Tx, tenant_id: str, user_ids, kind: str, title: str, body: str, link: str, task_id=None, level=None,
           email: str | None = None, batch: dict | None = None) -> int:
    """In-app notification for each person; with email='escalation'/'reschedule' also an immediate e-mail
    (collected in `batch` per person when given, so one scheduler run sends one e-mail per person)."""
    n = 0
    for uid in sorted({str(u) for u in user_ids if u}):
        tx.x("INSERT INTO notifications (tenant_id, user_id, kind, level, title, body, link, task_id)"
             " VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s)", tenant_id, uid, kind, level, title, body, link, task_id)
        n += 1
        if email and batch is not None:
            batch.setdefault(uid, []).append((level or 1, title, body, link))
            continue
        if email and prefs(tx, uid)["escalations"]:
            u = tx.one("SELECT email, full_name FROM users WHERE id = %s::uuid AND status = 'active'", uid)
            if u:
                company = tx.val("SELECT name FROM tenants WHERE id = %s", tenant_id) or ""
                queue(tx, tenant_id, uid, u["email"], email, f"[ComMach] {title}",
                      *render(company, u["full_name"], title, [(None, [(body, link)])], level=level))
    return n


def queue(tx: db.Tx, tenant_id, user_id, to_email: str, kind: str, subject: str, html_body: str, text_body: str) -> None:
    tx.x("INSERT INTO email_outbox (tenant_id, user_id, to_email, kind, subject, html, text_body) VALUES (%s, %s, %s, %s, %s, %s, %s)",
         tenant_id, user_id, to_email, kind, subject[:250], html_body, text_body)


def render(company: str, name: str, title: str, sections: list[tuple[str | None, list[tuple[str, str]]]],
           level: int | None = None, footer: str | None = None) -> tuple[str, str]:
    """A plain, mobile-friendly e-mail in the Comp11 style (no images: nothing to block)."""
    colour = {1: "#9a5b00", 2: "#b42318", 3: "#7a0f0f"}.get(level or 0, "#67138c")
    parts = [f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:620px;margin:0 auto;color:#1d1628">',
             f'<div style="border-bottom:3px solid {colour};padding:14px 0 10px"><strong style="color:#67138c;font-size:18px">ComMach</strong>'
             f'<span style="color:#857e95"> · {html.escape(company)}</span></div>',
             f'<h2 style="font-size:18px;margin:18px 0 6px;color:{colour}">{html.escape(title)}</h2>',
             f'<p style="margin:0 0 14px">Hello {html.escape(name)},</p>']
    text = [f"ComMach · {company}", "", title, "", f"Hello {name},", ""]
    for heading, items in sections:
        if heading:
            parts.append(f'<h3 style="font-size:14px;margin:16px 0 6px;color:#5c546c">{html.escape(heading)}</h3>')
            text.append(heading)
        parts.append('<ul style="padding-left:18px;margin:0">')
        for line, link in items:
            url = base_url() + link if link and link.startswith("/") else link
            parts.append(f'<li style="margin:4px 0">{html.escape(line)}'
                         + (f' – <a href="{html.escape(url)}" style="color:#67138c">open</a>' if url else "") + "</li>")
            text.append(f"- {line}" + (f"  {url}" if url else ""))
        parts.append("</ul>")
        text.append("")
    foot = footer or "You receive this because work in ComMach is assigned to you or waits for your approval. Change your e-mail settings under My tasks."
    parts.append(f'<p style="margin:22px 0 0;font-size:12px;color:#857e95">{html.escape(foot)}</p></div>')
    text.append(foot)
    return "".join(parts), "\n".join(text)


def send_outbox(tx: db.Tx, limit: int = 40) -> dict:
    rows = tx.q("SELECT * FROM email_outbox WHERE status = 'queued' ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED", limit)
    out = {"sent": 0, "failed": 0, "not_configured": 0}
    if not rows:
        return out
    if not settings.gmail_app_password:
        for r in rows:
            tx.x("UPDATE email_outbox SET status = 'not_configured', last_error = 'GMAIL_APP_PASSWORD is not set' WHERE id = %s", r["id"])
        out["not_configured"] = len(rows)
        return out
    try:
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=ctx) if settings.smtp_ssl \
            else smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        server.login(settings.mail_from, settings.gmail_app_password)
    except Exception as e:   # the whole batch waits for the next run
        log.warning("E-mail server not reachable: %s", e)
        for r in rows:
            tx.x("UPDATE email_outbox SET attempts = attempts + 1, last_error = %s,"
                 " status = CASE WHEN attempts + 1 >= 5 THEN 'failed' ELSE 'queued' END WHERE id = %s", str(e)[:500], r["id"])
        out["failed"] = len(rows)
        return out
    with server:
        for r in rows:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = r["subject"]
            msg["From"] = formataddr((settings.mail_from_name, settings.mail_from))
            msg["To"] = r["to_email"]
            msg.attach(MIMEText(r["text_body"], "plain", "utf-8"))
            msg.attach(MIMEText(r["html"], "html", "utf-8"))
            to = [r["to_email"]] + ([settings.mail_bcc] if settings.mail_bcc else [])
            try:
                server.sendmail(settings.mail_from, to, msg.as_string())
                tx.x("UPDATE email_outbox SET status = 'sent', sent_at = now(), attempts = attempts + 1 WHERE id = %s", r["id"])
                tx.event("EMAIL_SENT", "email_outbox", str(r["id"]), r["subject"], {"to": r["to_email"], "kind": r["kind"]},
                         tenant_id=str(r["tenant_id"]))
                out["sent"] += 1
            except Exception as e:
                tx.x("UPDATE email_outbox SET attempts = attempts + 1, last_error = %s,"
                     " status = CASE WHEN attempts + 1 >= 5 THEN 'failed' ELSE 'queued' END WHERE id = %s", str(e)[:500], r["id"])
                tx.event("EMAIL_FAILED", "email_outbox", str(r["id"]), r["subject"], {"to": r["to_email"], "error": str(e)[:200]},
                         tenant_id=str(r["tenant_id"]))
                out["failed"] += 1
    return out


# ------------------------------------------------------------------ escalation
def _number(tx: db.Tx, tenant_id: str, prefix: str, width: int) -> str:
    return tx.val("SELECT cm_next_number(%s::uuid, %s, %s)", tenant_id, prefix, width)


def escalate(tx: db.Tx, tenant_id: str, today: dt.date, st: dict, features: dict) -> dict:
    esc = st["escalation"]
    kinds = esc.get("kinds") or []
    counts = {"level1": 0, "level2": 0, "level3": 0, "deviations": 0}
    if not kinds:
        return counts
    tasks = tx.q(
        "SELECT t.*, a.code AS asset_code, a.name_en AS asset_name, a.gmp_criticality, s.code AS site_code"
        " FROM tasks t JOIN assets a ON a.id = t.asset_id JOIN sites s ON s.id = t.site_id"
        " WHERE t.status IN ('open', 'in_progress', 'at_vendor') AND t.kind = ANY(%s::text[])"
        " AND t.due_date <= %s", kinds, today + dt.timedelta(days=int(esc.get("accept_before_due_days") or 0)))
    accept_on = features.get("task_acceptance", True)
    batch: dict[str, list] = {}
    for t in tasks:
        label = f"{t['number']} · {t['asset_code']} {t['asset_name']}"
        link = f"{work_path(t['kind'])}/tasks/{t['id']}"
        what = "calibration" if t["kind"] == "calibration" else "maintenance"
        people = task_people(tx, t)
        sup = role_holders(tx, t["site_id"], SUPERVISORS[t["kind"]])
        events = []
        if accept_on and t["performer"] == "internal" and t["accepted_at"] is None and today < t["due_date"]:
            events.append(("not_accepted", 1, people | sup, f"Not accepted: {label}",
                           f"The {what} {t['number']} is due {t['due_date']:%d %b %Y} and nobody has accepted it yet."))
        if t["due_date"] <= today <= t["window_end"]:
            events.append(("due", 1, people | sup, f"Due today: {label}" if t["due_date"] == today else f"Due: {label}",
                           f"The {what} {t['number']} was due {t['due_date']:%d %b %Y}; it must be done by {t['window_end']:%d %b %Y}."))
        if today > t["window_end"]:
            events.append(("window_end", 2, people | sup | dept_head(tx, t["asset_id"]) | role_holders(tx, t["site_id"], ["qa_approver"]),
                           f"Overdue: {label}",
                           f"The tolerance window of {t['number']} ended {t['window_end']:%d %b %Y}. The asset must not be used for GMP work "
                           "until the work is done." if t["kind"] == "calibration" else
                           f"The tolerance window of {t['number']} ended {t['window_end']:%d %b %Y}."))
            days = (esc.get("deviation_after_days") or {}).get(t["gmp_criticality"])
            if days is not None and today >= t["window_end"] + dt.timedelta(days=int(days) + (0 if days else 1)):
                events.append(("deviation", 3, people | sup | dept_head(tx, t["asset_id"]) | role_holders(tx, t["site_id"], ["qa_approver"])
                               | site_head(tx, t["site_id"]), f"Deviation: {label}", ""))
        for trigger, level, recipients, title, body in events:
            recipients = with_delegates(tx, recipients, today)
            eid = tx.val("INSERT INTO escalations (tenant_id, task_id, level, trigger, due_date, recipients)"
                         " VALUES (%s, %s, %s, %s, %s, %s::uuid[]) ON CONFLICT (task_id, trigger, due_date) DO NOTHING RETURNING id",
                         tenant_id, t["id"], level, trigger, t["due_date"], sorted(recipients))
            if not eid:
                continue
            if trigger == "deviation":
                num = _number(tx, tenant_id, "DEV", 3)
                late = (today - t["window_end"]).days
                did = tx.val(
                    "INSERT INTO deviations (tenant_id, site_id, asset_id, task_id, number, title, description) VALUES"
                    " (%s, %s, %s, %s, %s, %s, %s) RETURNING id", tenant_id, t["site_id"], t["asset_id"], t["id"], num,
                    f"{'Calibration' if t['kind'] == 'calibration' else 'Preventive maintenance'} {t['number']} of {t['asset_code']} overdue",
                    f"{t['number']} ({t['asset_code']} {t['asset_name']}, GMP criticality {t['gmp_criticality']}) was due {t['due_date']}"
                    f" and its tolerance window ended {t['window_end']}; it is {late} days overdue. Raised automatically by the escalation rules.")
                tx.x("UPDATE escalations SET deviation_id = %s WHERE id = %s", did, eid)
                body = f"Deviation {num} was opened: {t['number']} is {late} days past its tolerance window."
                title = f"Deviation {num}: {label}"
                counts["deviations"] += 1
            counts[f"level{level}"] += 1
            notify(tx, tenant_id, recipients, "escalation", title, body, link, task_id=t["id"], level=level, email="escalation", batch=batch)
            tx.event("ESCALATED", "tasks", str(t["id"]), t["number"], {"level": level, "trigger": trigger, "recipients": len(recipients)},
                     tenant_id=tenant_id)
    if batch:
        company = tx.val("SELECT name FROM tenants WHERE id = %s", tenant_id) or ""
        for uid, items in batch.items():
            if not prefs(tx, uid)["escalations"]:
                continue
            u = tx.one("SELECT email, full_name FROM users WHERE id = %s::uuid AND status = 'active'", uid)
            if not u:
                continue
            items.sort(key=lambda x: -x[0])
            top = items[0][0]
            title = items[0][1] if len(items) == 1 else f"{len(items)} escalations need your attention"
            sections = []
            for lv, head in ((3, "Level 3 – deviations"), (2, "Level 2 – overdue"), (1, "Level 1 – not accepted or due")):
                rows = [(f"{tt}. {bb}", ll) for (l, tt, bb, ll) in items if l == lv]
                if rows:
                    sections.append((head, rows))
            queue(tx, tenant_id, uid, u["email"], "escalation", f"[ComMach] {title}", *render(company, u["full_name"], title, sections, level=top))
    return counts


# ------------------------------------------------------------------ To-Do
def user_roles(tx: db.Tx, user_id: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in tx.q("SELECT site_id, role FROM user_site_roles WHERE user_id = %s::uuid", user_id):
        out.setdefault(str(r["site_id"]), set()).add(r["role"])
    return out


TODO_TASK_SQL = """
SELECT t.id, t.number, t.kind, t.site_id, t.asset_id, t.due_date, t.window_start, t.window_end, t.status, t.accepted_at,
       t.performer, t.assigned_user_id, t.assignee_user_ids, t.assignee_roles, t.responsible_user_id, t.completed_by,
       t.completed_by_name, t.completed_at, t.verdict,
       a.code AS asset_code, a.name_en AS asset_name_en, a.name_ar AS asset_name_ar, a.gmp_criticality, s.code AS site_code,
       tp.title AS template_title, v.name AS vendor_name,
       (SELECT p.status FROM plans p WHERE p.site_id = t.site_id AND p.kind = t.kind AND p.year = extract(year FROM t.due_date)::int) AS plan_status,
       (SELECT r.status FROM reschedules r WHERE r.task_id = t.id AND r.status IN ('pending', 'supervisor_approved') LIMIT 1) AS reschedule_status
  FROM tasks t JOIN assets a ON a.id = t.asset_id JOIN sites s ON s.id = t.site_id
  LEFT JOIN templates tp ON tp.id = t.template_id LEFT JOIN vendors v ON v.id = t.vendor_id
"""


def build_todo(tx: db.Tx, user_id: str, is_admin: bool, today: dt.date, horizon: int = 30) -> dict:
    roles = user_roles(tx, user_id)
    me = {user_id, *delegators(tx, user_id, today)}
    mine_roles = sorted({r for rs in roles.values() for r in rs})
    rows = tx.q(TODO_TASK_SQL + " WHERE t.status IN ('open', 'in_progress', 'at_vendor') AND t.due_date <= %s"
                " AND (t.assigned_user_id = ANY(%s::uuid[]) OR t.responsible_user_id = ANY(%s::uuid[])"
                "      OR t.assignee_user_ids && %s::uuid[]"
                "      OR (t.assigned_user_id IS NULL AND EXISTS (SELECT 1 FROM user_site_roles r WHERE r.user_id = ANY(%s::uuid[])"
                "          AND r.site_id = t.site_id AND r.role = ANY(t.assignee_roles))))"
                " ORDER BY t.window_end, t.due_date LIMIT 400",
                today + dt.timedelta(days=horizon), sorted(me), sorted(me), sorted(me), sorted(me))
    for r in rows:
        r["overdue"] = r["window_end"] < today
        r["delegated"] = not ({str(r["assigned_user_id"] or ""), str(r["responsible_user_id"] or "")} | {str(x) for x in r["assignee_user_ids"] or []}) & {user_id} \
            and not (r["assigned_user_id"] is None and roles.get(str(r["site_id"]), set()) & set(r["assignee_roles"] or []))
    review = []
    for kind, revs in REVIEWERS.items():
        sites = [sid for sid, rs in roles.items() if rs & set(revs)]
        if sites:
            review += tx.q(TODO_TASK_SQL + " WHERE t.kind = %s AND t.status = 'completed' AND t.reviewed_at IS NULL AND t.source = 'normal'"
                           " AND t.site_id = ANY(%s::uuid[]) AND t.completed_by IS DISTINCT FROM %s::uuid ORDER BY t.completed_at LIMIT 100",
                           kind, sites, user_id)
    approvals = []
    for r in tx.q("SELECT r.*, t.number, t.kind, t.site_id, a.code AS asset_code FROM reschedules r JOIN tasks t ON t.id = r.task_id"
                  " JOIN assets a ON a.id = t.asset_id WHERE r.status IN ('pending', 'supervisor_approved') ORDER BY r.created_at"):
        rs = roles.get(str(r["site_id"]), set())
        if str(r["requested_by"]) == user_id:
            continue
        if (r["status"] == "pending" and rs & set(APPROVE_SUPERVISOR[r["kind"]])) or (r["status"] == "supervisor_approved" and "qa_approver" in rs):
            approvals.append({"type": "reschedule", "id": r["id"], "task_id": r["task_id"], "number": r["number"], "kind": r["kind"],
                              "asset_code": r["asset_code"], "old_due": r["old_due"], "new_due": r["new_due"], "reason": r["reason"],
                              "requested_by_name": r["requested_by_name"], "status": r["status"], "within_tolerance": r["within_tolerance"]})
    for p in tx.q("SELECT p.*, s.code AS site_code FROM plans p JOIN sites s ON s.id = p.site_id WHERE p.status = 'reviewed'"):
        if "qa_approver" in roles.get(str(p["site_id"]), set()):
            approvals.append({"type": "plan", "id": p["id"], "kind": p["kind"], "year": p["year"], "site_id": p["site_id"],
                              "site_code": p["site_code"], "status": p["status"]})
    for p in tx.q("SELECT t.id, t.kind, t.code, t.version, t.title FROM templates t WHERE t.status = 'draft'"
                  " AND EXISTS (SELECT 1 FROM template_items i WHERE i.template_id = t.id)"):
        need = {"calibration": {"qa_approver", "qa_calibration_officer"}, "pm": {"engineering_manager", "qa_approver"}}[p["kind"]]
        if any(rs & need for rs in roles.values()):
            approvals.append({"type": "template", "id": p["id"], "kind": p["kind"], "code": p["code"], "version": p["version"], "title": p["title"]})
    for i in tx.q("SELECT i.id, i.number, i.site_id, a.code AS asset_code FROM impact_assessments i JOIN assets a ON a.id = i.asset_id"
                  " WHERE i.status = 'draft'"):
        if "qa_approver" in roles.get(str(i["site_id"]), set()):
            approvals.append({"type": "impact", "id": i["id"], "number": i["number"], "asset_code": i["asset_code"]})
    deviations = [d for d in tx.q("SELECT d.id, d.number, d.title, d.site_id, d.created_at, a.code AS asset_code FROM deviations d"
                                  " JOIN assets a ON a.id = d.asset_id WHERE d.status = 'open' ORDER BY d.created_at")
                  if is_admin or roles.get(str(d["site_id"]), set()) & {"qa_approver", "engineering_manager"}]
    overdue = [r for r in rows if r["overdue"]]
    soon = [r for r in rows if not r["overdue"] and r["due_date"] <= today + dt.timedelta(days=7)]
    later = [r for r in rows if not r["overdue"] and r["due_date"] > today + dt.timedelta(days=7)]
    accept = [r for r in rows if r["accepted_at"] is None and r["performer"] == "internal"]
    return {"today": today, "overdue": overdue, "soon": soon, "later": later, "to_accept": accept, "to_review": review,
            "approvals": approvals, "deviations": deviations, "roles": {k: sorted(v) for k, v in roles.items()},
            "delegated_from": [str(x) for x in me - {user_id}]}


def digest(tx: db.Tx, tenant_id: str, today: dt.date, hour_now: int, st: dict) -> int:
    if hour_now < int(st["escalation"].get("digest_hour", 7)):
        return 0
    company = tx.val("SELECT name FROM tenants WHERE id = %s", tenant_id) or ""
    n = 0
    for u in tx.q("SELECT id, email, full_name, is_tenant_admin, notify FROM users WHERE status = 'active'"
                  " AND NOT EXISTS (SELECT 1 FROM digest_log d WHERE d.user_id = users.id AND d.day = %s)", today):
        if not (u["notify"] or {}).get("digest", True):
            continue
        td = build_todo(tx, str(u["id"]), u["is_tenant_admin"], today, horizon=7)
        sections = []

        def line(r):
            return (f"{r['number']} · {r['asset_code']} {r['asset_name_en']}" + (f" ({r['template_title']})" if r.get("template_title") else "")
                    + f" · due {r['due_date']:%d %b}", f"{work_path(r['kind'])}/tasks/{r['id']}")
        if td["overdue"]:
            sections.append((f"Overdue ({len(td['overdue'])})", [line(r) for r in td["overdue"][:25]]))
        if td["soon"]:
            sections.append((f"Due in the next 7 days ({len(td['soon'])})", [line(r) for r in td["soon"][:25]]))
        if td["to_review"]:
            sections.append((f"To review and sign ({len(td['to_review'])})", [line(r) for r in td["to_review"][:25]]))
        if td["approvals"]:
            items = []
            for a in td["approvals"][:25]:
                if a["type"] == "reschedule":
                    items.append((f"Reschedule {a['number']} ({a['asset_code']}) to {a['new_due']:%d %b %Y}, asked by {a['requested_by_name']}", "/todo"))
                elif a["type"] == "plan":
                    items.append((f"Annual {'calibration' if a['kind'] == 'calibration' else 'maintenance'} plan {a['year']} of {a['site_code']}",
                                  f"/plans/{a['kind']}?year={a['year']}&site_id={a['site_id']}"))
                elif a["type"] == "template":
                    items.append((f"{'Calibration template' if a['kind'] == 'calibration' else 'PM checklist'} {a['code']} v{a['version']}",
                                  f"{work_path(a['kind'])}/templates"))
                else:
                    items.append((f"Impact assessment {a['number']} ({a['asset_code']})", f"/calibration/impacts/{a['id']}"))
            sections.append((f"Waiting for your approval ({len(td['approvals'])})", items))
        if td["deviations"]:
            sections.append((f"Open deviations ({len(td['deviations'])})", [(f"{d['number']} {d['title']}", f"/deviations/{d['id']}") for d in td["deviations"][:15]]))
        count = sum(len(i) for _, i in sections)
        tx.x("INSERT INTO digest_log (tenant_id, user_id, day, items) VALUES (%s, %s, %s, %s)", tenant_id, u["id"], today, count)
        if not count:
            continue
        title = f"Your ComMach work for {today:%A %d %B}"
        queue(tx, tenant_id, u["id"], u["email"], "digest", f"[ComMach] {title}", *render(company, u["full_name"], title, sections))
        n += 1
    return n


# ------------------------------------------------------------------ the scheduler
def run_tenant(tenant_id: str, now: dt.datetime | None = None) -> dict:
    """Escalations and digests for one company (called by the scheduler and by "Run now")."""
    ctx = db.Context(tenant_id=tenant_id, user_name="ComMach scheduler")
    with db.transaction(ctx) as tx:
        from .defaults import effective_features
        tz = tx.val("SELECT timezone FROM tenants WHERE id = %s", tenant_id) or "UTC"
        local = tx.val("SELECT (%s::timestamptz AT TIME ZONE %s)", now or dt.datetime.now(dt.timezone.utc), tz)
        st = work.effective_settings(tx.val("SELECT work_settings FROM tenants WHERE id = %s", tenant_id))
        features = effective_features(tx.val("SELECT features FROM tenants WHERE id = %s", tenant_id))
        from . import hours
        moved = hours.check(tx, local.date(), st)
        out = escalate(tx, tenant_id, local.date(), st, features)
        out["hours_due"] = moved
        out["digests"] = digest(tx, tenant_id, local.date(), local.hour, st)
    with db.transaction(ctx) as tx:
        out.update(send_outbox(tx))
    return out


def run_all() -> dict:
    totals: dict[str, int] = {}
    with db.transaction(db.SYSTEM) as lock:
        if not lock.val("SELECT pg_try_advisory_xact_lock(%s)", LOCK_ID):
            return {"skipped": 1}
        tenants = [str(r["id"]) for r in lock.q("SELECT id FROM tenants WHERE status = 'active'")]
        for tid in tenants:
            try:
                for k, v in run_tenant(tid).items():
                    totals[k] = totals.get(k, 0) + v
            except Exception:
                log.exception("Scheduler failed for company %s", tid)
    return totals


async def scheduler_loop() -> None:
    if settings.scheduler_seconds <= 0:
        return
    await asyncio.sleep(min(60, settings.scheduler_seconds))
    while True:
        try:
            out = await asyncio.to_thread(run_all)
            STATE["last_run"], STATE["last_error"] = dt.datetime.now(dt.timezone.utc), None
            if any(v for k, v in out.items() if k != "skipped"):
                log.info("Scheduler: %s", out)
        except Exception as e:
            STATE["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            log.exception("Scheduler run failed")
        await asyncio.sleep(settings.scheduler_seconds)
