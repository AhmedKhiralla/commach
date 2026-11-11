"""ComMach web application: JSON API under /api and the web app for everything else."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from .config import settings
from .routers import forms as forms_r
from .routers import analysis, assistant, system
from .routers import (ai_routes, assets, audit_trail, auth, company, excel, files, hierarchy, integration, labels, lists, todo,
                      platform, sample, work)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("commach")

API_ROUTES = [
    Route("/api/health", lambda r: JSONResponse({"status": "ok", "storage": "persistent" if settings.storage_persistent else "ephemeral"}), methods=["GET"]),
    # Authentication and personal settings
    Route("/api/auth/login", auth.login, methods=["POST"]),
    Route("/api/auth/logout", auth.logout, methods=["POST"]),
    Route("/api/auth/me", auth.me, methods=["GET"]),
    Route("/api/auth/change-password", auth.change_password, methods=["POST"]),
    Route("/api/auth/preferences", auth.preferences, methods=["PATCH"]),
    # Platform administration
    Route("/api/platform/system-check", system.system_check, methods=["GET"]),
    Route("/api/platform/tenants", platform.list_tenants, methods=["GET"]),
    Route("/api/platform/tenants", platform.create_tenant, methods=["POST"]),
    Route("/api/platform/tenants/{tenant_id}", platform.update_tenant, methods=["PATCH"]),
    Route("/api/platform/tenants/{tenant_id}/admins", platform.list_tenant_admins, methods=["GET"]),
    Route("/api/platform/tenants/{tenant_id}/admins", platform.create_tenant_admin, methods=["POST"]),
    Route("/api/platform/users/{user_id}/reset-password", platform.reset_tenant_admin_password, methods=["POST"]),
    # Company administration
    Route("/api/sites", company.list_sites, methods=["GET"]),
    Route("/api/sites", company.create_site, methods=["POST"]),
    Route("/api/sites/{site_id}", company.update_site, methods=["PATCH"]),
    Route("/api/people", company.list_people, methods=["GET"]),
    Route("/api/users", company.list_users, methods=["GET"]),
    Route("/api/users", company.create_user, methods=["POST"]),
    Route("/api/users/{user_id}", company.update_user, methods=["PATCH"]),
    Route("/api/users/{user_id}/roles", company.set_user_roles, methods=["PUT"]),
    Route("/api/users/{user_id}/reset-password", company.reset_user_password, methods=["POST"]),
    Route("/api/users/{user_id}/unlock", company.unlock_user, methods=["POST"]),
    Route("/api/settings", company.get_settings, methods=["GET"]),
    Route("/api/settings/company", company.update_company, methods=["PATCH"]),
    Route("/api/settings/features", company.update_features, methods=["PUT"]),
    Route("/api/settings/terminology", company.update_terminology, methods=["PUT"]),
    Route("/api/settings/security", company.update_security, methods=["PUT"]),
    Route("/api/settings/ai", company.update_ai, methods=["PUT"]),
    Route("/api/settings/ai/test", company.test_ai, methods=["POST"]),
    Route("/api/sample-data", sample.load_sample, methods=["POST"]),
    # Hierarchy: groups, locations, lines
    Route("/api/tree", hierarchy.tree, methods=["GET"]),
    Route("/api/site-groups", hierarchy.list_groups, methods=["GET"]),
    Route("/api/site-groups", hierarchy.create_group, methods=["POST"]),
    Route("/api/site-groups/{group_id}", hierarchy.update_group, methods=["PATCH"]),
    Route("/api/sites/{site_id}/locations", hierarchy.list_locations, methods=["GET"]),
    Route("/api/sites/{site_id}/locations", hierarchy.create_location, methods=["POST"]),
    Route("/api/locations/{location_id}", hierarchy.update_location, methods=["PATCH"]),
    Route("/api/sites/{site_id}/lines", hierarchy.list_lines, methods=["GET"]),
    Route("/api/sites/{site_id}/lines", hierarchy.create_line, methods=["POST"]),
    Route("/api/lines/{line_id}", hierarchy.update_line, methods=["PATCH"]),
    # Company lists
    Route("/api/departments", lists.list_departments, methods=["GET"]),
    Route("/api/departments", lists.create_department, methods=["POST"]),
    Route("/api/departments/{dept_id}", lists.update_department, methods=["PATCH"]),
    Route("/api/asset-classes", lists.list_classes, methods=["GET"]),
    Route("/api/asset-classes", lists.create_class, methods=["POST"]),
    Route("/api/asset-classes/{class_id}", lists.update_class, methods=["PATCH"]),
    Route("/api/vendors", lists.list_vendors, methods=["GET"]),
    Route("/api/vendors", lists.create_vendor, methods=["POST"]),
    Route("/api/vendors/{vendor_id}", lists.update_vendor, methods=["PATCH"]),
    # Files
    Route("/api/files", files.upload_file, methods=["POST"]),
    Route("/api/files/{file_id}/content", files.file_content, methods=["GET"]),
    # Assets
    Route("/api/assets", assets.list_assets, methods=["GET"]),
    Route("/api/assets", assets.create_asset, methods=["POST"]),
    Route("/api/assets/status", assets.change_status, methods=["POST"]),
    Route("/api/assets/status-options", assets.status_options, methods=["GET"]),
    Route("/api/assets/labels.pdf", labels.labels_pdf, methods=["GET"]),
    Route("/api/assets/export.xlsx", excel.export_assets, methods=["GET"]),
    Route("/api/assets/template.xlsx", excel.template, methods=["GET"]),
    Route("/api/assets/import/preview", excel.preview_import, methods=["POST"]),
    Route("/api/assets/import/commit", excel.commit_import, methods=["POST"]),
    Route("/api/assets/by-code/{code:path}", assets.find_by_code, methods=["GET"]),
    Route("/api/assets/{asset_id}", assets.get_asset, methods=["GET"]),
    Route("/api/assets/{asset_id}", assets.update_asset, methods=["PATCH"]),
    Route("/api/assets/{asset_id}/files", assets.add_file, methods=["POST"]),
    Route("/api/asset-files/{link_id}/remove", assets.remove_file, methods=["POST"]),
    # Calibration and maintenance work
    Route("/api/settings/work", work.get_work_settings, methods=["GET"]),
    Route("/api/settings/work", work.update_work_settings, methods=["PUT"]),
    Route("/api/templates", work.list_templates, methods=["GET"]),
    Route("/api/templates", work.create_template, methods=["POST"]),
    Route("/api/templates/{template_id}", work.get_template, methods=["GET"]),
    Route("/api/templates/{template_id}", work.update_template, methods=["PATCH"]),
    Route("/api/templates/{template_id}/approve", work.approve_template, methods=["POST"]),
    Route("/api/templates/{template_id}/new-version", work.new_version, methods=["POST"]),
    Route("/api/schedules", work.create_schedules, methods=["POST"]),
    Route("/api/schedules/preview", work.preview_schedule, methods=["POST"]),
    Route("/api/schedules/{schedule_id}", work.update_schedule, methods=["PATCH"]),
    Route("/api/schedules/{schedule_id}/end", work.end_schedule, methods=["POST"]),
    Route("/api/assets/{asset_id}/work", work.asset_work, methods=["GET"]),
    Route("/api/tasks", work.list_tasks, methods=["GET"]),
    Route("/api/tasks/{task_id}", work.get_task, methods=["GET"]),
    Route("/api/tasks/{task_id}/accept", work.accept_task, methods=["POST"]),
    Route("/api/tasks/{task_id}/send", work.send_to_vendor, methods=["POST"]),
    Route("/api/tasks/{task_id}/results", work.put_results, methods=["PUT"]),
    Route("/api/tasks/{task_id}/complete", work.complete_task, methods=["POST"]),
    Route("/api/tasks/{task_id}/review", work.review_task, methods=["POST"]),
    Route("/api/tasks/{task_id}/cancel", work.cancel_task, methods=["POST"]),
    Route("/api/tasks/{task_id}/certificate", ai_routes.certificate, methods=["POST"]),
    Route("/api/forms", forms_r.list_forms, methods=["GET"]),
    Route("/api/forms", forms_r.create_form, methods=["POST"]),
    Route("/api/forms/meta", forms_r.form_meta, methods=["GET"]),
    Route("/api/forms/preview", forms_r.preview_layout, methods=["POST"]),
    Route("/api/forms/print", forms_r.print_default, methods=["GET"]),
    Route("/api/forms/starter.docx", forms_r.starter, methods=["GET"]),
    Route("/api/forms/issues", forms_r.list_issues, methods=["GET"]),
    Route("/api/forms/{form_id}", forms_r.get_form, methods=["GET"]),
    Route("/api/forms/{form_id}", forms_r.update_form, methods=["PATCH"]),
    Route("/api/forms/{form_id}/approve", forms_r.approve_form, methods=["POST"]),
    Route("/api/forms/{form_id}/new-version", forms_r.new_form_version, methods=["POST"]),
    Route("/api/forms/{form_id}/default", forms_r.make_default, methods=["POST"]),
    Route("/api/forms/{form_id}/retire", forms_r.retire_form, methods=["POST"]),
    Route("/api/forms/{form_id}/preview", forms_r.preview_form, methods=["GET"]),
    Route("/api/forms/{form_id}/print", forms_r.print_form, methods=["GET"]),
    Route("/api/todo", todo.get_todo, methods=["GET"]),
    Route("/api/notifications", todo.list_notifications, methods=["GET"]),
    Route("/api/notifications/read", todo.read_notifications, methods=["POST"]),
    Route("/api/notifications/run", todo.run_now, methods=["POST"]),
    Route("/api/notifications/emails", todo.email_log, methods=["GET"]),
    Route("/api/me/notify", todo.get_notify, methods=["GET"]),
    Route("/api/me/notify", todo.update_notify, methods=["PUT"]),
    Route("/api/me/notify/test", todo.test_email, methods=["POST"]),
    Route("/api/me/calendar", todo.new_calendar_link, methods=["POST"]),
    Route("/api/me/calendar/revoke", todo.revoke_calendar_link, methods=["POST"]),
    Route("/api/calendar/{token}.ics", todo.calendar_feed, methods=["GET"]),
    Route("/api/tasks/{task_id}/reschedule", todo.request_reschedule, methods=["POST"]),
    Route("/api/reschedules/{reschedule_id}/approve", todo.approve_reschedule, methods=["POST"]),
    Route("/api/reschedules/{reschedule_id}/reject", todo.reject_reschedule, methods=["POST"]),
    Route("/api/delegations", todo.list_delegations, methods=["GET"]),
    Route("/api/delegations", todo.create_delegation, methods=["POST"]),
    Route("/api/delegations/{delegation_id}/end", todo.end_delegation, methods=["POST"]),
    Route("/api/users/{user_id}/handover", todo.get_handover, methods=["GET"]),
    Route("/api/users/{user_id}/handover", todo.do_handover, methods=["POST"]),
    Route("/api/deviations", todo.list_deviations, methods=["GET"]),
    Route("/api/deviations/{deviation_id}", todo.get_deviation, methods=["GET"]),
    Route("/api/deviations/{deviation_id}", todo.update_deviation, methods=["PATCH"]),
    Route("/api/deviations/{deviation_id}/close", todo.close_deviation, methods=["POST"]),
    Route("/api/plans", work.get_plan, methods=["GET"]),
    Route("/api/plans/pdf", work.plan_pdf, methods=["GET"]),
    Route("/api/plans/review", work.review_plan, methods=["POST"]),
    Route("/api/plans/approve", work.approve_plan, methods=["POST"]),
    Route("/api/ai/manual", ai_routes.manual, methods=["POST"]),
    Route("/api/impacts", work.list_impacts, methods=["GET"]),
    Route("/api/impacts/{impact_id}", work.get_impact, methods=["GET"]),
    Route("/api/impacts/{impact_id}", work.update_impact, methods=["PATCH"]),
    Route("/api/impacts/{impact_id}/approve", work.approve_impact, methods=["POST"]),
    Route("/api/impacts/{impact_id}/ai-draft", ai_routes.impact_draft, methods=["POST"]),
    # AI
    Route("/api/ai/nameplate", ai_routes.nameplate, methods=["POST"]),
    Route("/api/ai/suggestions/{suggestion_id}/reject", ai_routes.reject, methods=["POST"]),
    # Connected apps (Complanning)
    Route("/api/integration/keys", integration.list_keys, methods=["GET"]),
    Route("/api/integration/keys", integration.create_key, methods=["POST"]),
    Route("/api/integration/keys/{key_id}/revoke", integration.revoke_key, methods=["POST"]),
    Route("/api/integration/v1/assets", integration.api_assets, methods=["GET"]),
    Route("/api/integration/v1/events", integration.api_events, methods=["GET"]),
    Route("/api/integration/v1/production-plan", integration.api_production_plan, methods=["POST"]),
    Route("/api/integration/v1/meter-readings", integration.api_meter_readings, methods=["POST"]),
    Route("/api/analysis/summary", analysis.summary, methods=["GET"]),
    Route("/api/analysis/drift", analysis.drift, methods=["GET"]),
    Route("/api/analysis/intervals", analysis.intervals, methods=["GET"]),
    Route("/api/analysis/pm-gaps", analysis.pm_gaps, methods=["GET"]),
    Route("/api/assets/{asset_id}/meter", analysis.get_meter, methods=["GET"]),
    Route("/api/assets/{asset_id}/meter", analysis.add_meter, methods=["POST"]),
    Route("/api/assistant/ask", assistant.ask, methods=["POST"]),
    Route("/api/assistant/history", assistant.history, methods=["GET"]),
    # Audit trail
    Route("/api/audit", audit_trail.list_audit, methods=["GET"]),
    Route("/api/audit/facets", audit_trail.audit_facets, methods=["GET"]),
    Route("/api/audit/export", audit_trail.export_audit, methods=["GET"]),
    Route("/api/audit/verify", audit_trail.verify_audit, methods=["POST"]),
]


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        h.setdefault("Content-Security-Policy",
                     "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com;"
                     " font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; frame-src 'self' blob:;"
                     " frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if settings.cookie_secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def _frontend_routes() -> list:
    dist = Path(settings.frontend_dist).resolve()
    index = dist / "index.html"
    if not index.exists():
        log.warning("Web app build not found at %s; serving the API only.", dist)
        return [Route("/{path:path}", lambda r: PlainTextResponse(
            "The web app has not been built. Run the frontend build.", status_code=503))]

    async def spa(request):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": {"code": "not_found", "message": "Unknown API endpoint."}}, status_code=404)
        candidate = (dist / request.path_params.get("path", "")).resolve()
        if candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    # No static mount on /assets: the build's /assets/app.js shares that prefix with the app's own /assets/{id}
    # pages, which must fall through to index.html when opened directly or refreshed.
    return [Route("/{path:path}", spa)]


@asynccontextmanager
async def lifespan(app):
    if settings.run_migrations:
        from .migrate import bootstrap_platform_admin, migrate
        applied = migrate()
        if applied:
            log.info("Applied migrations: %s", ", ".join(applied))
        bootstrap_platform_admin()
    log.info("File store: %s (%s)", settings.data_dir, "persistent" if settings.storage_persistent else "NOT persistent")
    if not settings.storage_persistent:
        log.warning("No Railway volume is attached: uploads are refused until one is mounted.")
    import asyncio
    from .notify import scheduler_loop
    task = asyncio.create_task(scheduler_loop())
    log.info("E-mail: %s", f"Gmail as {settings.mail_from}" if settings.gmail_app_password else "not configured (GMAIL_APP_PASSWORD)")
    yield
    task.cancel()


def create_app(run_lifespan: bool = True) -> Starlette:
    return Starlette(routes=API_ROUTES + _frontend_routes(),
                     middleware=[Middleware(SecurityHeaders)],
                     lifespan=lifespan if run_lifespan else None)


app = create_app()
