"""Application configuration, loaded from the environment (and `.env`).

Discrete host/user/password/db fields, rather than one DSN, so
`scripts/apply_schema.py` (asyncpg's `connect(**kwargs)`) and
`async_database_url` (SQLAlchemy's `postgresql+asyncpg://` URL form) both
build off the same source of truth instead of each parsing the other's
string format.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings, populated from `APP_`-prefixed env vars."""

    # extra="ignore" so unknown APP_* keys in .env don't crash Settings().
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APP_",
        extra="ignore",
    )

    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    postgres_db: str

    temporal_host: str
    temporal_namespace: str
    task_queue: str

    temporal_cors_origins: str
    
    dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")


    # -----------------------------------------------------------------------
    # Run lifecycle
    # -----------------------------------------------------------------------
    # Gate 2. When true the workflow blocks at awaiting_review until
    # /finalize or /reject arrives; set false to auto-finalize (the old
    # behaviour) for load tests and unattended environments.
    run_review_gate_enabled: bool = True
    # How long the workflow waits at that gate before giving up and marking
    # the run `expired`. Default 24h.
    run_review_timeout_seconds: int = 86_400
    # One active (queued/running/awaiting_review) run per user at a time.
    max_active_runs_per_user: int = 1

    # Estimate model — per-reaction bounds used by POST /runs/{id}/estimate.
    # Wall-clock is per reaction *after* batch concurrency, so the min/max
    # spread mostly reflects LLM latency variance.
    estimate_seconds_per_reaction_min: float = 0.4
    estimate_seconds_per_reaction_max: float = 1.5
    estimate_credits_per_reaction_min: float = 0.8
    estimate_credits_per_reaction_max: float = 1.8

    # SSE (`GET /runs/{id}/events`): DB poll interval and a hard cap on how
    # long one stream is held open, so an abandoned browser tab can't pin a
    # connection and a DB session forever.
    sse_poll_interval_seconds: float = 2.0
    sse_max_duration_seconds: int = 3_600

    # ---------------------------------------------------------------------------
    # Microsoft Entra ID (Azure AD) — JWT validation
    # Required in .env — see apps/api/.env.example and README.md.
    # ---------------------------------------------------------------------------
    entra_tenant_id: str
    entra_client_id: str
    # Expected `aud` claim in the JWT. Defaults to the client_id.
    entra_audience: str | None = None

    @property
    def async_database_url(self) -> str:
        """The asyncpg DSN the app's runtime engine connects with."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def jwks_uri(self) -> str:
        """Entra ID JWKS endpoint for the configured tenant."""
        return (
            f"https://login.microsoftonline.com/{self.entra_tenant_id}"
            "/discovery/v2.0/keys"
        )

    @property
    def jwt_issuer(self) -> str:
        """Expected `iss` claim for single-tenant Entra ID tokens (v2)."""
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"

    @property
    def jwt_audience(self) -> str:
        """The audience this API accepts. Falls back to the client_id."""
        return self.entra_audience or self.entra_client_id


settings = Settings()
