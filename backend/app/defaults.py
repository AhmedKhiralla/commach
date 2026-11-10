"""Default feature toggles, AI settings and terminology for new companies."""
from __future__ import annotations

# Feature toggles, grouped for the settings screen.
FEATURES: dict[str, dict] = {
    # Modules: a calibration team can work without preventive maintenance and the reverse.
    "calibration":         {"group": "modules", "default": True},
    "preventive_maintenance": {"group": "modules", "default": True},
    # Workflow controls (switchable per company)
    "plan_approval":       {"group": "workflow", "default": True},    # supervisor/QA approve the plan before tasks are released
    "task_acceptance":     {"group": "workflow", "default": True},    # the assignee accepts a task before starting it
    "completion_review":   {"group": "workflow", "default": False},   # a reviewer approves each completed task
    "reason_for_change":   {"group": "workflow", "default": True},    # a reason is required for every change to the register
    # AI (needs a provider key on the server). Every AI result is a draft a person accepts or corrects.
    "ai_nameplate":        {"group": "ai", "default": True},          # photo of a nameplate -> asset fields
    "ai_import":           {"group": "ai", "default": True},          # map messy Excel registers to ComMach columns
    "ai_certificate":      {"group": "ai", "default": True},          # calibration certificate -> readings and verdict
    "ai_manual":           {"group": "ai", "default": True},          # manual PDF -> PM checklist, intervals, spare parts
    "ai_impact":           {"group": "ai", "default": True},          # out-of-tolerance impact assessment draft
    "ai_assistant":        {"group": "ai", "default": True},          # "Ask ComMach" chat
}


def effective_features(stored: dict | None) -> dict[str, bool]:
    base = {k: v["default"] for k, v in FEATURES.items()}
    for k, v in (stored or {}).items():
        if k in base and isinstance(v, bool):
            base[k] = v
    return base


# AI settings per company (tenants.engine).
AI_DEFAULTS: dict = {
    "provider": None,             # AI provider name; None = first configured (Qwen by default)
    "daily_limit": 100,           # AI requests per user per day
}


def effective_ai(stored: dict | None) -> dict:
    out = dict(AI_DEFAULTS)
    for k, v in (stored or {}).items():
        if k in out:
            out[k] = v
    return out


# Renamable terms. Each company can change the words used across the app.
TERMS: dict[str, dict[str, str]] = {
    "asset":      {"en": "Asset", "ar": "أصل"},
    "group":      {"en": "Group", "ar": "مجموعة"},
    "building":   {"en": "Building", "ar": "مبنى"},
    "area":       {"en": "Area", "ar": "منطقة"},
    "room":       {"en": "Room", "ar": "غرفة"},
    "line":       {"en": "Line", "ar": "خط إنتاج"},
    "department": {"en": "Department", "ar": "إدارة"},
}


def effective_terms(stored: dict | None) -> dict[str, dict[str, str]]:
    out = {k: dict(v) for k, v in TERMS.items()}
    for key, langs in (stored or {}).items():
        if key in out and isinstance(langs, dict):
            for lang in ("en", "ar"):
                val = langs.get(lang)
                if isinstance(val, str) and val.strip():
                    out[key][lang] = val.strip()[:60]
    return out
