"""Electronic signatures (21 CFR Part 11 §11.50, §11.70, §11.100, §11.200, §11.300).

* Each signing re-enters both components: the user's email (identification) and password.
* The signature stores the signer's printed name, the date and time (UTC, server clock), the meaning,
  the statement shown, the reason, and the SHA-256 of exactly what was signed, so it is linked to its
  record and cannot be moved to another.
* A wrong email or password is recorded in the audit trail and counts toward the account lockout,
  like a failed sign-in; reaching the limit locks the account and ends its sessions.
* One signing act may cover several records (for example approving 40 imported assets into service):
  the password is checked once and one signature row, with its own record hash, is written per record.
"""
from __future__ import annotations

import hashlib
import json

from .api import ApiError, Call
from .security import verify_password

# Meaning -> statement shown in the signing dialog and stored with the signature.
STATEMENTS = {
    "in_service": "I approve this asset into service: its registration is complete and correct and it may be used.",
    "release": "I release this asset from quarantine: the reason for quarantine has been assessed and closed.",
    "restricted": "I restrict the use of this asset to the limits recorded in the note.",
    "standby": "I place this asset on standby: it is not to be used until it is returned to service.",
    "out_of_service": "I take this asset out of service: it must not be used until it is repaired and returned to service.",
    "quarantined": "I quarantine this asset: it must not be used until QA releases it.",
    "out_for_calibration": "I confirm this asset has left for calibration and must not be used until it returns.",
    "retired": "I retire this asset: it is permanently withdrawn from GMP use.",
    "disposed": "I confirm this asset has been disposed of.",
    "transferred": "I confirm this asset has been transferred out of this site.",
    # calibration and maintenance work
    "template_approved": "I approve this template: its points, tolerances and method are correct for use.",
    "sent_to_vendor": "I confirm this asset has left for external calibration and must not be used until it returns.",
    "calibration_completed": "I confirm this calibration was performed as recorded and the results are true and complete.",
    "calibration_reviewed": "I have reviewed this calibration record and agree with its results and verdict.",
    "task_cancelled": "I cancel this task for the reason given.",
    "impact_approved": "I approve this impact assessment and its conclusion.",
    "pm_completed": "Done by: I performed this maintenance as recorded; the checklist, readings and parts used are true and complete.",
    "pm_checked": "Checked by: I have checked this maintenance record and the condition of the equipment.",
    "plan_reviewed": "I have reviewed this annual plan: the assets, intervals and due dates are complete and correct.",
    "plan_approved": "I approve this annual plan and release its work.",
    "reschedule_approved": "As supervisor I approve moving this task to the new due date for the reason given.",
    "reschedule_qa_approved": "As QA I approve moving this task beyond its tolerance window for the reason given.",
    "deviation_closed": "I close this deviation: the root cause is identified and the actions are complete.",
}


def statement(meaning: str) -> str:
    return STATEMENTS[meaning]


def record_hash(data: dict) -> str:
    """SHA-256 of a canonical JSON form of what is being signed."""
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_signer(call: Call, email: str, password: str, *, entity: str, record_id: str | None, label: str,
                  meaning: str) -> dict:
    """Check both signature components; on failure audit it, count it toward lockout and raise."""
    tx, u = call.tx, call.user
    row = tx.one("SELECT email, password_hash, failed_attempts FROM users WHERE id = %s", u["id"])
    ok = email.strip().lower() == row["email"].lower() and verify_password(password, row["password_hash"])
    if ok:
        tx.x("UPDATE users SET failed_attempts = 0 WHERE id = %s", u["id"])
        return row
    policy = call.policy
    attempts = row["failed_attempts"] + 1
    locked = attempts >= policy["max_failed_attempts"]
    tx.event("SIGNATURE_FAILED", entity, record_id, label, {"meaning": meaning, "attempt": attempts, "locked": locked})
    if locked:
        tx.x("UPDATE users SET failed_attempts = 0, locked_until = now() + make_interval(mins => %s) WHERE id = %s",
             policy["lockout_minutes"], u["id"])
        tx.x("UPDATE sessions SET revoked_at = now(), revoke_reason = 'signature_lockout'"
             " WHERE user_id = %s AND revoked_at IS NULL", u["id"])
        tx.event("LOCKED", "users", str(u["id"]), u["email"], {"reason": "failed signatures"})
    else:
        tx.x("UPDATE users SET failed_attempts = %s WHERE id = %s", attempts, u["id"])
    tx.commit()  # keep the audit entry even though nothing is signed
    if locked:
        raise ApiError(401, "locked", "Too many failed signatures. Your account is locked; ask your administrator.")
    raise ApiError(403, "bad_signature", "The email or password is not correct. Nothing was signed.")


def record(call: Call, *, signer: dict, entity: str, record_id: str, label: str, meaning: str, rhash: str,
           reason: str | None) -> dict:
    u = call.user
    sig = call.tx.one(
        "INSERT INTO signatures (tenant_id, entity, record_id, record_label, meaning, statement, reason, record_hash,"
        " user_id, user_name, user_email) VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id, meaning, statement, reason, user_name, user_email, signed_at, record_hash",
        call.tenant_id, entity, record_id, label, meaning, statement(meaning), (reason or "").strip() or None,
        rhash, u["id"], u["full_name"], signer["email"])
    call.tx.event("SIGNED", entity, record_id, label, {"meaning": meaning, "record_hash": rhash, "signature_id": sig["id"]})
    return sig


def list_for(call: Call, entity: str, record_id: str) -> list[dict]:
    return call.tx.q("SELECT id, meaning, statement, reason, user_id, user_name, user_email, signed_at, record_hash"
                     " FROM signatures WHERE entity = %s AND record_id = %s::uuid ORDER BY id", entity, record_id)
