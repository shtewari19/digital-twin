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

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    postgres_user: str = "core_api"
    postgres_password: str = "dev_password"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "core_api"

    dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")

    @property
    def async_database_url(self) -> str:
        """The asyncpg DSN the app's runtime engine connects with."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
