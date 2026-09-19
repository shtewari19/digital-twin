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

#: Resolved absolutely, not relative to the working directory, so
#: `python -m app.worker` behaves the same wherever it is launched from.
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
    #: Upper bound on texts per HTTP request to the embedding service.
    #: Independent of the workflow's pair batch size.
    embedding_batch_size: int = 25
    #: The real ceiling is the request's total SIZE, not its item count: the
    #: service was measured returning 500 at ~30 KB of text and 200 at ~25 KB.
    #: Chunking on bytes keeps requests safe whatever length the reactions
    #: happen to be — count-based chunking silently breaks when a panel starts
    #: producing longer answers.
    embedding_max_request_bytes: int = 20_000
    #: LLM calls fanned out in parallel inside one generate_reaction_batch.
    #: Turn it down if Azure starts rate-limiting a large panel.
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