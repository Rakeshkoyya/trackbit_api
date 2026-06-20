"""Application configuration settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "TrackBit API"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # Database. The app connects as a restricted role (no BYPASSRLS) so the RLS
    # safety net actually applies. Migrations run as the privileged owner via
    # ADMIN_DATABASE_URL (falls back to DATABASE_URL if unset).
    DATABASE_URL: str = "postgresql+psycopg2://trackbit:trackbit@localhost:5434/trackbit"
    ADMIN_DATABASE_URL: str = ""
    TEST_DATABASE_URL: str = "postgresql+psycopg2://trackbit:trackbit@localhost:5434/trackbit_test"

    @property
    def migration_database_url(self) -> str:
        return self.ADMIN_DATABASE_URL or self.DATABASE_URL

    # JWT
    JWT_SECRET_KEY: str = "dev-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Auth token lifetimes (hours)
    MAGIC_LINK_EXPIRE_HOURS: int = 72
    INVITE_LINK_EXPIRE_HOURS: int = 7 * 24
    PASSWORD_RESET_EXPIRE_HOURS: int = 24

    # Credential rules
    PASSWORD_MIN_LENGTH: int = 8
    USERNAME_MIN_LENGTH: int = 3
    USERNAME_MAX_LENGTH: int = 32

    # Security
    BCRYPT_ROUNDS: int = 12
    RATE_LIMIT_ENABLED: bool = True

    # Frontend (magic/invite links point here)
    FRONTEND_BASE_URL: str = "http://localhost:3000"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Background jobs. Enable on exactly ONE instance (never multi-worker).
    ENABLE_SCHEDULER: bool = False

    # How often last_active_at is allowed to be written per member (seconds)
    LAST_ACTIVE_THROTTLE_SECONDS: int = 300

    # Email (Resend). Empty key => dev console adapter (logs instead of sending).
    # Per-purpose senders, all on the verified notify.trackbit.in domain.
    # RESEND_FROM is the general/default identity (digests, report cards, and any
    # mail that doesn't pick a more specific sender).
    RESEND_API_KEY: str = ""
    RESEND_FROM: str = "TrackBit <hello@notify.trackbit.in>"
    # Account-access links: invites, password reset (the "login"/magic-link mails).
    RESEND_FROM_LOGIN: str = "TrackBit <login@notify.trackbit.in>"
    # Task reminder / overdue mails.
    RESEND_FROM_REMINDERS: str = "TrackBit <reminders@notify.trackbit.in>"

    # Web Push (VAPID). Self-generated keypair; public key is exposed to the client.
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_SUBJECT: str = "mailto:notify@trackbit.app"

    # Default minutes before due_at to send a reminder (per-task override later).
    DEFAULT_REMIND_BEFORE_MINUTES: int = 30

    # Billing (Razorpay). Empty keys => stub mode: the upgrade flow is wired but
    # checkout is disabled until real keys are added (plan P4-BE-01).
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_PLAN_ID: str = ""  # the Pro ₹500/month plan id from the Razorpay dashboard
    RAZORPAY_WEBHOOK_SECRET: str = ""
    PRO_GRACE_DAYS: int = 7  # keep Pro this long after a failed payment before downgrading

    @property
    def billing_configured(self) -> bool:
        return bool(self.RAZORPAY_KEY_ID and self.RAZORPAY_KEY_SECRET and self.RAZORPAY_PLAN_ID)

    # Attachments storage. Empty R2 creds => local-disk fallback (dev): files are
    # written under MEDIA_DIR and served from MEDIA_BASE_URL (plan P4-BE-02).
    MEDIA_DIR: str = "media"
    MEDIA_BASE_URL: str = "http://localhost:8000/media"
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""
    R2_PUBLIC_BASE_URL: str = ""  # public/CDN base for objects, e.g. https://cdn.example.com

    @property
    def storage_configured(self) -> bool:
        return bool(
            self.R2_ACCOUNT_ID and self.R2_ACCESS_KEY_ID
            and self.R2_SECRET_ACCESS_KEY and self.R2_BUCKET
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
