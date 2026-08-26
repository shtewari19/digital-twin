"""Engine process configuration, loaded from the environment (and `.env`).

Mirrors apps/api/app/core/config.py's shape — discrete Postgres fields under
APP_-prefixed env vars — so both apps read the same .env values without
maintaining two different DSN formats. Path to .env is resolved absolutely
(not relative to cwd) so `python -m app.worker` behaves the same regardless
of which directory it's launched from.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env" 


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_PATH, env_prefix="APP_", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    postgres_db: str

    temporal_host: str
    temporal_namespace: str
    task_queue: str

    embedding_model_endpoint: str = "https://ai.questkart.cloud/embeddings"
    embedding_batch_size: int = 50
    max_batches_per_run: int = 1000
    approval_timeout_days: int = 7

    @property
    def asyncpg_dsn(self) -> str:
        """Plain postgresql:// DSN — asyncpg.create_pool doesn't want the
        `+asyncpg` driver suffix that SQLAlchemy's URL form uses."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()