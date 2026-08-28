"""Engine process configuration, loaded from the environment (and `.env`).

Mirrors apps/api/app/core/config.py's shape — discrete Postgres fields under
APP_-prefixed env vars — so both apps read the same .env values without
maintaining two different DSN formats. Path to .env is resolved absolutely
(not relative to cwd) so `python -m app.worker` behaves the same regardless
of which directory it's launched from.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = next(
    (
        path
        for path in (_ENGINE_ROOT / ".env", _ENGINE_ROOT / "engine" / ".env")
        if path.is_file()
    ),
    _ENGINE_ROOT / ".env",
)


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

    # These four (plus embedding_model_endpoint below) live in
    # apps/engine/engine/.env WITHOUT the APP_ prefix the class-level
    # env_prefix applies to everything else — validation_alias opts each one
    # out individually rather than fighting pydantic-settings' prefix.
    azure_openai_api_key: str = Field(validation_alias="AZURE_OPENAI_API_KEY")
    azure_openai_endpoint: str = Field(validation_alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_deployment: str = Field(
        default="gpt-4o-mini", validation_alias="AZURE_OPENAI_DEPLOYMENT_NAME"
    )
    azure_openai_api_version: str = Field(
        default="2024-02-15-preview", validation_alias="AZURE_OPENAI_API_VERSION"
    )

    embedding_model_endpoint: str = Field(
        default="https://ai.questkart.cloud/embeddings",
        validation_alias="EMBEDDING_MODEL_ENDPOINT",
    )
    embedding_batch_size: int = 50
    max_batches_per_run: int = 1000
    approval_timeout_days: int = 7
    reaction_concurrency: int = 8

    @property
    def asyncpg_dsn(self) -> str:
        """Plain postgresql:// DSN — asyncpg.create_pool doesn't want the
        `+asyncpg` driver suffix that SQLAlchemy's URL form uses."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()