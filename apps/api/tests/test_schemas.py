"""Unit tests for Pydantic schemas (app/schemas/).

Validates field defaults, enum values, alias handling, from_attributes
round-trips, and basic validation constraints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.common import Page, Priority, Problem, Timestamps
from app.schemas.core import (
    AvatarScope,
    AvatarSource,
    Domain,
    DomainType,
    IngestStatus,
    StudyStatus,
)
from app.schemas.platform import (
    JobKind,
    JobStatus,
    ModelType,
    Role,
    User,
)
from app.schemas.runs import (
    ExportFormat,
    ModelConfig,
    RecommendationTier,
    RunCreate,
    RunEventType,
    RunResults,
    RunStatus,
)

# ---------------------------------------------------------------------------
# common.py
# ---------------------------------------------------------------------------


class TestProblem:
    def test_defaults(self):
        p = Problem()
        assert p.type == "about:blank"
        assert p.title is None
        assert p.status is None
        assert p.detail is None
        assert p.instance is None
        assert p.errors is None

    def test_with_values(self):
        p = Problem(type="https://example.com/not-found", status=404, detail="No study")
        assert p.status == 404
        assert p.detail == "No study"


class TestPage:
    def test_generic_structure(self):
        page = Page[Domain](data=[], next_cursor=None, has_more=False)
        assert page.data == []
        assert page.has_more is False
        assert page.next_cursor is None

    def test_has_data_and_meta(self):
        page = Page[str](data=["a", "b"], next_cursor="abc", has_more=True)
        assert len(page.data) == 2
        assert page.next_cursor == "abc"
        assert page.has_more is True


class TestTimestamps:
    def test_from_attributes(self):
        now = datetime.now(tz=UTC)
        row = SimpleNamespace(created_at=now, updated_at=now)
        ts = Timestamps.model_validate(row)
        assert ts.created_at == now
        assert ts.updated_at == now


class TestPriority:
    def test_enum_values(self):
        assert Priority.HIGH == "high"
        assert Priority.MEDIUM == "medium"
        assert Priority.LOW == "low"


# ---------------------------------------------------------------------------
# core.py — enums
# ---------------------------------------------------------------------------


class TestDomainType:
    def test_values(self):
        assert DomainType.PREDEFINED == "predefined"
        assert DomainType.CUSTOM == "custom"


class TestStudyStatus:
    def test_values(self):
        assert StudyStatus.DRAFT == "draft"
        assert StudyStatus.READY == "ready"
        assert StudyStatus.ARCHIVED == "archived"


class TestIngestStatus:
    def test_values(self):
        assert IngestStatus.PENDING == "pending"
        assert IngestStatus.PROCESSING == "processing"
        assert IngestStatus.READY == "ready"
        assert IngestStatus.FAILED == "failed"


class TestAvatarScope:
    def test_values(self):
        assert AvatarScope.LIBRARY == "library"
        assert AvatarScope.STUDY == "study"


class TestAvatarSource:
    def test_values(self):
        assert AvatarSource.PREBUILT == "prebuilt"
        assert AvatarSource.CUSTOM == "custom"
        assert AvatarSource.LLM_ASSISTED == "llm_assisted"


# ---------------------------------------------------------------------------
# core.py — schema round-trips
# ---------------------------------------------------------------------------


class TestDomainSchema:
    def test_from_attributes(self):
        now = datetime.now(tz=UTC)
        row = SimpleNamespace(
            id=uuid4(),
            name="Pharma",
            type="custom",
            description=None,
            compliance_profile="standard",
            created_at=now,
            updated_at=now,
        )
        d = Domain.model_validate(row)
        assert d.name == "Pharma"
        assert d.type == DomainType.CUSTOM

    def test_domain_list_is_page(self):
        from app.schemas.core import DomainList

        assert issubclass(DomainList, Page)


# ---------------------------------------------------------------------------
# platform.py — enums and schemas
# ---------------------------------------------------------------------------


class TestRole:
    def test_values(self):
        assert Role.OPERATOR == "operator"
        assert Role.ADMIN == "admin"


class TestJobKind:
    def test_values(self):
        assert JobKind.EXPORT == "export"
        assert JobKind.REINDEX == "reindex"


class TestJobStatus:
    def test_values(self):
        assert JobStatus.QUEUED == "queued"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.SUCCEEDED == "succeeded"
        assert JobStatus.FAILED == "failed"


class TestModelType:
    def test_values(self):
        assert ModelType.CHAT == "chat"
        assert ModelType.EMBEDDING == "embedding"


class TestUserSchema:
    def test_rejects_invalid_email(self):
        with pytest.raises(ValidationError):
            User(id=uuid4(), name="Test", email="not-an-email", role=Role.OPERATOR)

    def test_accepts_valid_email(self):
        u = User(id=uuid4(), name="Test", email="test@example.com", role=Role.ADMIN)
        assert u.email == "test@example.com"
        assert u.role == Role.ADMIN


# ---------------------------------------------------------------------------
# runs.py — enums and schemas
# ---------------------------------------------------------------------------


class TestRunStatusSchema:
    def test_all_values(self):
        expected = {
            "draft",
            "configured",
            "estimated",
            "approved",
            "queued",
            "running",
            "awaiting_review",
            "finalized",
            "failed",
            "cancelled",
            "expired",
        }
        actual = {s.value for s in RunStatus}
        assert actual == expected


class TestRunCreateAlias:
    def test_model_config_alias(self):
        rc = RunCreate(model_config={"reaction_model": "anthropic/claude-sonnet"})
        assert rc.model_settings.reaction_model == "anthropic/claude-sonnet"

    def test_default_values(self):
        rc = RunCreate()
        assert rc.model_settings is None
        assert rc.repetitions == 1


class TestRunStatusParity:
    """Ensure the schema RunStatus and DB RunStatus have identical values."""

    def test_schema_matches_db(self):
        from app.db.models.run import RunStatus as DBRunStatus

        schema_values = {s.value for s in RunStatus}
        db_values = {s.value for s in DBRunStatus}
        assert schema_values == db_values


class TestExportFormat:
    def test_values(self):
        assert ExportFormat.MARKDOWN == "markdown"
        assert ExportFormat.PDF == "pdf"
        assert ExportFormat.DOCX == "docx"
        assert ExportFormat.PPTX == "pptx"


class TestRunEventType:
    def test_values(self):
        assert RunEventType.PROGRESS == "progress"
        assert RunEventType.STATE_CHANGE == "state_change"
        assert RunEventType.AWAITING_REVIEW == "awaiting_review"
        assert RunEventType.COMPLETED == "completed"
        assert RunEventType.FAILED == "failed"


class TestRecommendationTier:
    def test_values(self):
        assert RecommendationTier.RECOMMENDED == "recommended"
        assert RecommendationTier.RUNNER_UP == "runner_up"
        assert RecommendationTier.DROP == "drop"


class TestModelConfig:
    def test_all_none_by_default(self):
        mc = ModelConfig()
        assert mc.reaction_model is None
        assert mc.embedding_model is None
        assert mc.report_model is None
        assert mc.provider_key_id is None


class TestRunResults:
    def test_minimal(self):
        rr = RunResults(run_id=uuid4(), report="All good")
        assert rr.ranking == []
        assert rr.baseline_lift_pct is None
