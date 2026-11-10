"""Runtime configuration, read from environment variables."""
import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/commach")
    # Railway and Heroku style URLs work directly with libpq; normalise the scheme.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_database_url)
    # Cookies are marked Secure unless explicitly disabled for local development.
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", True))
    # Absolute session lifetime; the inactivity timeout comes from tenant security policy.
    session_max_hours: int = field(default_factory=lambda: int(os.environ.get("SESSION_MAX_HOURS", "12")))
    db_pool_size: int = field(default_factory=lambda: int(os.environ.get("DB_POOL_SIZE", "10")))
    platform_admin_email: str | None = field(default_factory=lambda: os.environ.get("PLATFORM_ADMIN_EMAIL"))
    platform_admin_password: str | None = field(default_factory=lambda: os.environ.get("PLATFORM_ADMIN_PASSWORD"))
    platform_admin_name: str = field(default_factory=lambda: os.environ.get("PLATFORM_ADMIN_NAME", "Platform administrator"))
    frontend_dist: str = field(default_factory=lambda: os.environ.get(
        "FRONTEND_DIST",
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")))
    run_migrations: bool = field(default_factory=lambda: _bool("RUN_MIGRATIONS", True))
    # Controlled file store: photos, nameplates, manuals, certificates and imports. Mount a persistent volume here.
    # On Railway the attached volume's mount path wins, so files always land on the persistent disk.
    data_dir: str = field(default_factory=lambda: os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                          or os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data")))
    # False only on Railway without a volume: uploads are refused rather than lost at the next deploy.
    storage_persistent: bool = field(default_factory=lambda: not (
        os.environ.get("RAILWAY_PROJECT_ID") or os.environ.get("RAILWAY_ENVIRONMENT_NAME"))
        or bool(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")) or _bool("ALLOW_EPHEMERAL_STORAGE", False))
    max_upload_mb: int = field(default_factory=lambda: int(os.environ.get("MAX_UPLOAD_MB", "60")))
    # Public address used in QR codes on labels, e.g. https://commach.comp11labs.com (falls back to the request host).
    public_url: str | None = field(default_factory=lambda: (os.environ.get("PUBLIC_URL") or "").rstrip("/") or None)
    # E-mail through Gmail (SMTP over SSL), as in the Comp11 naming worker. Without the app password, e-mails stay
    # in the outbox marked not_configured and the app still shows every notification.
    mail_from: str = field(default_factory=lambda: os.environ.get("MAIL_FROM", "comp11labs@gmail.com"))
    mail_from_name: str = field(default_factory=lambda: os.environ.get("MAIL_FROM_NAME", "ComMach by Comp11 Labs"))
    gmail_app_password: str | None = field(default_factory=lambda: os.environ.get("GMAIL_APP_PASSWORD") or None)
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "465")))
    smtp_ssl: bool = field(default_factory=lambda: _bool("SMTP_SSL", True))
    mail_bcc: str | None = field(default_factory=lambda: os.environ.get("MAIL_BCC") or None)
    # The scheduler (escalations, digests, e-mail) runs inside the web process every N seconds; 0 switches it off.
    scheduler_seconds: int = field(default_factory=lambda: int(os.environ.get("SCHEDULER_SECONDS", "300")))


settings = Settings()
