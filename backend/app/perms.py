"""Who may do what in ComMach.

Company administrators manage users, sites and settings. Everyone else works through roles given per site:

  qa_approver             QA: approves assets into service, releases quarantine, approves plans (Phase 3)
  engineering_manager     Engineering: owns the register, approves plans and retirement
  engineering_supervisor  Engineering: maintains the register and schedules, supervises technicians
  qa_calibration_officer  QA: owns calibration (instruments, vendors, certificates)
  maintenance_technician  executes preventive maintenance; can report an asset out of service
  requester               user departments: can raise requests and read their site
  auditor                 viewer and auditor: reads everything at the site and the audit trail

Electronic signatures are never implied by the administrator flag: an administrator who must sign gives
themselves the matching role, so the audit trail shows the signing authority.
"""
from __future__ import annotations

from .api import ApiError, Call

ROLES = ("qa_approver", "engineering_manager", "engineering_supervisor", "qa_calibration_officer",
         "maintenance_technician", "requester", "auditor")

# Roles that may create and change register data (assets, locations, classes, vendors).
EDITORS = {"engineering_manager", "engineering_supervisor", "qa_calibration_officer"}

# Who may sign each asset status change. Keys are the new status; "release" is quarantined -> in service.
STATUS_SIGNERS: dict[str, set[str]] = {
    "in_service": {"qa_approver", "engineering_manager"},
    "release": {"qa_approver"},
    "restricted": {"qa_approver", "engineering_manager", "qa_calibration_officer"},
    "standby": EDITORS | {"qa_approver"},
    "out_of_service": EDITORS | {"qa_approver", "maintenance_technician"},
    "quarantined": {"qa_approver", "qa_calibration_officer", "engineering_manager"},
    "out_for_calibration": {"qa_calibration_officer", "engineering_supervisor", "engineering_manager"},
    "retired": {"engineering_manager", "qa_approver"},
    "disposed": {"engineering_manager", "qa_approver"},
    "transferred": {"engineering_manager", "qa_approver"},
}


def _roles(call: Call) -> list[dict]:
    cached = getattr(call, "_roles", None)
    if cached is None:
        cached = call.tx.q("SELECT site_id::text AS site_id, role FROM user_site_roles WHERE user_id = %s",
                           call.user["id"])
        setattr(call, "_roles", cached)
    return cached


def has_any(call: Call, *roles: str) -> bool:
    return any(r["role"] in roles for r in _roles(call))


def site_roles(call: Call, site_id: str | None) -> set[str]:
    return {r["role"] for r in _roles(call) if r["site_id"] == str(site_id)}


def is_admin(call: Call) -> bool:
    return bool(call.user["is_tenant_admin"])


def can_edit_master(call: Call) -> bool:
    """Company-wide lists: asset classes, departments, vendors."""
    return is_admin(call) or has_any(call, *EDITORS)


def require_master_edit(call: Call) -> None:
    if not can_edit_master(call):
        raise ApiError(403, "forbidden", "Engineering or QA calibration access is required to change this list.")


def require_site(call: Call, site_id: str, edit: bool = False) -> dict:
    """Return the site after checking the caller may read (or edit) its register."""
    site = call.tx.one("SELECT id, code, name, country, timezone, active, group_id FROM sites WHERE id = %s::uuid",
                       str(site_id))
    if not site:
        raise ApiError(404, "not_found", "Site was not found.")
    if is_admin(call):
        return site
    roles = site_roles(call, site_id)
    if not roles:
        raise ApiError(403, "forbidden", "You do not have access to this site.")
    if edit and not roles & EDITORS:
        raise ApiError(403, "forbidden", "Engineering or QA calibration access at this site is required to change this.")
    return site


def can_edit_site(call: Call, site_id: str) -> bool:
    return is_admin(call) or bool(site_roles(call, site_id) & EDITORS)


def can_sign_status(call: Call, site_id: str, key: str) -> bool:
    return bool(site_roles(call, site_id) & STATUS_SIGNERS.get(key, set()))


def visible_site_ids(call: Call) -> list[str] | None:
    """None = all sites (administrators)."""
    if is_admin(call):
        return None
    return sorted({r["site_id"] for r in _roles(call)})


def can_read_audit(call: Call) -> bool:
    return bool(call.user["is_platform_admin"] or call.user["is_tenant_admin"]) or has_any(call, "auditor", "qa_approver")
