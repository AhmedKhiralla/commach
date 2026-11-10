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
from .routers import (ai_routes, assets, audit_trail, auth, company, excel, files, hierarchy, integration, labels, lists,
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
    yield


def create_app(run_lifespan: bool = True) -> Starlette:
    return Starlette(routes=API_ROUTES + _frontend_routes(),
                     middleware=[Middleware(SecurityHeaders)],
                     lifespan=lifespan if run_lifespan else None)


app = create_app()
