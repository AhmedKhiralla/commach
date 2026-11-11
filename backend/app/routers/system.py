"""Pilot readiness: a system check for the platform owner (Comp11 Labs), run after each deployment.

Each check returns ok | warn | fail with a plain message and, when it is not ok, what to do. `?deep=1` also signs in
to the mail server and asks the AI provider for a one-line answer (slower, and it uses one AI request).
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import smtplib
import ssl
import time
from pathlib import Path

from .. import db, storage
from ..api import Call, endpoint
from ..config import settings

MIGRATIONS = Path(__file__).resolve().parent.parent.parent / "migrations"


def _c(key: str, status: str, detail: str, action: str | None = None) -> dict:
    return {"key": key, "status": status, "detail": detail, "action": action}


def check_database(tx: db.Tx) -> list[dict]:
    out = []
    t0 = time.perf_counter()
    ver = tx.val("SHOW server_version")
    ms = (time.perf_counter() - t0) * 1000
    out.append(_c("database", "ok" if ms < 200 else "warn", f"PostgreSQL {ver}; round trip {ms:.0f} ms",
                  None if ms < 200 else "The database answers slowly: keep the app and the database in the same Railway region."))
    files = sorted(p.stem for p in MIGRATIONS.glob("*.sql"))
    done = {r["version"] for r in tx.q("SELECT version FROM schema_migrations")}
    missing = [f for f in files if f not in done]
    out.append(_c("migrations", "ok" if not missing else "fail",
                  f"{len(done)} of {len(files)} migrations applied" + (f"; missing {', '.join(missing)}" if missing else ""),
                  "Restart the service with RUN_MIGRATIONS on." if missing else None))
    role = tx.one("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'commach_app'")
    unprotected = [r["relname"] for r in tx.q(
        "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'public' AND c.relkind = 'r' AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid"
        " AND a.attname = 'tenant_id' AND NOT a.attisdropped)") if not r["relrowsecurity"]]
    bad_role = not role or role["rolsuper"] or role["rolbypassrls"]
    out.append(_c("row_security", "fail" if bad_role or unprotected else "ok",
                  "Row-level security is on for every company table and the application role cannot bypass it"
                  if not (bad_role or unprotected) else
                  f"Application role problem: {role}" if bad_role else f"Tables without row-level security: {', '.join(unprotected)}",
                  "Contact Comp11 Labs support before use." if bad_role or unprotected else None))
    drift = tx.val("SELECT extract(epoch FROM (now() - %s::timestamptz))", dt.datetime.now(dt.timezone.utc))
    out.append(_c("clock", "ok" if abs(drift) < 5 else "warn", f"Database and server clocks differ by {abs(drift):.1f} s",
                  None if abs(drift) < 5 else "Check the server clock; record times come from the database."))
    return out


def check_audit(tx: db.Tx) -> dict:
    bad, total, n = [], 0, 0
    for t in tx.q("SELECT id, code FROM tenants ORDER BY code"):
        r = tx.one("SELECT * FROM cp_audit_verify(%s::uuid)", t["id"])
        total += r["checked"]
        n += 1
        if r["first_bad_id"] is not None:
            bad.append(f"{t['code']} (entry {r['first_bad_id']})")
    platform = tx.one("SELECT * FROM cp_audit_verify(NULL)")
    total += platform["checked"]
    if platform["first_bad_id"] is not None:
        bad.append(f"platform (entry {platform['first_bad_id']})")
    return _c("audit_chain", "fail" if bad else "ok",
              f"{total:,} audit entries in {n} companies checked; hash chain " + ("broken: " + ", ".join(bad) if bad else "intact"),
              "Treat as a data-integrity incident: do not delete anything, and contact Comp11 Labs." if bad else None)


def check_storage(tx: db.Tx) -> list[dict]:
    out = []
    root = storage.root()
    ok_write = False
    try:
        os.makedirs(root, exist_ok=True)
        probe = Path(root) / f".probe-{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
        ok_write = True
    except OSError as e:
        out.append(_c("storage_write", "fail", f"Cannot write to the file store {root}: {e}", "Mount a Railway volume and set its path."))
    if ok_write:
        free = shutil.disk_usage(root).free / 2 ** 30
        out.append(_c("storage", "ok" if settings.storage_persistent and free > 1 else "warn" if settings.storage_persistent else "fail",
                      f"File store {'on a persistent volume' if settings.storage_persistent else 'NOT persistent'}; {free:.1f} GB free",
                      None if settings.storage_persistent and free > 1 else
                      "Attach a Railway volume (files are lost on redeploy without one)." if not settings.storage_persistent
                      else "Increase the volume size."))
    rows, total = [], 0
    for tid in [str(t["id"]) for t in tx.q("SELECT id FROM tenants")]:      # files are visible per company only
        with db.transaction(db.Context(tenant_id=tid, is_system=True, user_name="system check")) as ttx:
            total += ttx.val("SELECT count(*) FROM files")
            rows += ttx.q("SELECT storage_key, sha256 FROM files ORDER BY random() LIMIT 15")
    problems = []
    for r in rows:
        try:
            storage.verify(r["storage_key"], r["sha256"])
        except storage.StorageError as e:
            problems.append(str(e).split(".")[0])
    out.append(_c("files", "ok" if not problems else "fail",
                  f"{len(rows)} of {total} stored files re-checked against their SHA-256" + (f"; {len(problems)} problem(s): {problems[0]}" if problems else ""),
                  "Restore the file store from backup; files must never change outside ComMach." if problems else None))
    return out


def check_mail(deep: bool) -> dict:
    if not settings.gmail_app_password:
        return _c("email", "warn", "E-mail is not configured: reminders, escalations and summaries stay in the app only",
                  "Set GMAIL_APP_PASSWORD (a Google app password for the sending account).")
    if not deep:
        return _c("email", "ok", f"Configured to send from {settings.mail_from} through {settings.smtp_host}:{settings.smtp_port}")
    try:
        if settings.smtp_ssl:
            s = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
        with s:
            s.login(settings.mail_from, settings.gmail_app_password)
        return _c("email", "ok", f"Signed in to {settings.smtp_host} as {settings.mail_from}")
    except Exception as e:
        return _c("email", "fail", f"Mail sign-in failed: {type(e).__name__}: {str(e)[:160]}",
                  "Check GMAIL_APP_PASSWORD and that 2-step verification and app passwords are on for the account.")


def check_ai(deep: bool) -> dict:
    from ..engine import ai
    p = ai.pick(None)
    if not p:
        return _c("ai", "warn", "No AI provider is configured: nameplate, certificate, manual, impact and assistant features are off",
                  "Set QWEN_API_KEY.")
    if not deep:
        return _c("ai", "ok", f"{p.label}: text {p.text_model}, vision {p.vision_model}")
    try:
        res = ai.test(p)
        bad = [r for r in res if not r.get("ok")]
        return _c("ai", "fail" if bad else "ok", "; ".join(f"{r.get('role')}: {r.get('model')} {'ok' if r.get('ok') else r.get('error', 'failed')}" for r in res),
                  "Set QWEN_TEXT_MODEL / QWEN_VISION_MODEL to models the account can use." if bad else None)
    except Exception as e:
        return _c("ai", "fail", f"{type(e).__name__}: {str(e)[:200]}", "Check QWEN_API_KEY and QWEN_BASE_URL.")


def check_settings() -> list[dict]:
    from .. import notify
    out = [_c("cookies", "ok" if settings.cookie_secure else "fail",
              "Session cookies are HTTPS-only" if settings.cookie_secure else "COOKIE_SECURE is off",
              None if settings.cookie_secure else "Remove COOKIE_SECURE=false in production.")]
    out.append(_c("public_url", "ok" if settings.public_url else "warn",
                  f"Links in e-mails point to {settings.public_url}" if settings.public_url else "PUBLIC_URL is not set",
                  None if settings.public_url else "Set PUBLIC_URL=https://commach.comp11labs.com so e-mail links and calendar feeds work."))
    if settings.scheduler_seconds <= 0:
        out.append(_c("scheduler", "warn", "The scheduler is off: no escalations, run-hours checks or e-mail summaries",
                      "Remove SCHEDULER_SECONDS=0 (default every 5 minutes)."))
    else:
        last = notify.STATE.get("last_run")
        late = last is None or (dt.datetime.now(dt.timezone.utc) - last).total_seconds() > settings.scheduler_seconds * 3 + 120
        out.append(_c("scheduler", "warn" if late else "ok",
                      f"Every {settings.scheduler_seconds // 60 or 1} min; last run " + (last.strftime("%Y-%m-%d %H:%M UTC") if last else "not yet since start")
                      + (f"; last error: {notify.STATE['last_error']}" if notify.STATE.get("last_error") else ""),
                      "Wait a few minutes after a deploy; if it stays late, check the logs." if late else None))
    out.append(_c("bootstrap", "warn" if settings.platform_admin_password else "ok",
                  "PLATFORM_ADMIN_PASSWORD is still set on the server" if settings.platform_admin_password else "No start-up password is kept on the server",
                  "Remove PLATFORM_ADMIN_PASSWORD once the platform owner has signed in and changed it." if settings.platform_admin_password else None))
    out.append(_c("backups", "info", "Database backups are taken by Railway, not by ComMach",
                  "Confirm daily backups are on for the Postgres service (Railway → Postgres → Backups) and restore one to a test "
                  "service once before go-live; back up the file-store volume too."))
    return out


@endpoint(auth="platform")
def system_check(call: Call):
    deep = call.query.get("deep") == "1"
    started = time.perf_counter()
    with db.transaction(db.SYSTEM) as tx:
        checks = check_database(tx) + [check_audit(tx)] + check_storage(tx)
        counts = tx.one("SELECT (SELECT count(*) FROM tenants) AS companies, (SELECT count(*) FROM audit_log) AS audit_entries,"
                        " pg_size_pretty(pg_database_size(current_database())) AS database_size")
        counts.update(users=0, assets=0, tasks=0)
        for tid in [str(t["id"]) for t in tx.q("SELECT id FROM tenants")]:
            with db.transaction(db.Context(tenant_id=tid, is_system=True, user_name="system check")) as ttx:
                c = ttx.one("SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM assets) AS assets,"
                            " (SELECT count(*) FROM tasks) AS tasks")
                for k in ("users", "assets", "tasks"):
                    counts[k] += c[k]
    checks += [check_mail(deep), check_ai(deep)] + check_settings()
    worst = "fail" if any(c["status"] == "fail" for c in checks) else "warn" if any(c["status"] == "warn" for c in checks) else "ok"
    call.tx.event("INTEGRITY_CHECK", "system", None, "System check", {"status": worst, "deep": deep,
                  "failed": [c["key"] for c in checks if c["status"] == "fail"]})
    return {"status": worst, "checks": checks, "counts": counts, "deep": deep, "seconds": round(time.perf_counter() - started, 2),
            "checked_at": dt.datetime.now(dt.timezone.utc), "version": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:12] or None}

