"""Unit tests for ORM models (app/db/models/).

Verifies table names, schemas, enum completeness, and column defaults
without requiring a live database.
"""

from __future__ import annotations

from uuid import uuid4

from app.db.base import Base
from app.db.models.domain import Domain
from app.db.models.run import Run, RunStatus
from app.db.models.user import User

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class TestBase:
    def test_domain_inherits_base(self):
        assert issubclass(Domain, Base)

    def test_run_inherits_base(self):
        assert issubclass(Run, Base)

    def test_user_inherits_base(self):
        assert issubclass(User, Base)


# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------


class TestDomainModel:
    def test_tablename(self):
        assert Domain.__tablename__ == "domains"

    def test_schema(self):
        table_args_dict = Domain.__table_args__[-1]
        assert table_args_dict["schema"] == "core"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class TestRunModel:
    def test_tablename(self):
        assert Run.__tablename__ == "runs"

    def test_schema(self):
        assert Run.__table_args__["schema"] == "runs"

    def test_status_column_default(self):
        from sqlalchemy import inspect

        mapper = inspect(Run)
        status_col = mapper.columns.status
        assert status_col.default.arg == RunStatus.DRAFT.value

    def test_workflow_id_defaults_none(self):
        run = Run(study_id=uuid4())
        assert run.workflow_id is None


# ---------------------------------------------------------------------------
# RunStatus
# ---------------------------------------------------------------------------


class TestRunStatus:
    def test_all_members(self):
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

    def test_is_str_enum(self):
        assert isinstance(RunStatus.DRAFT, str)
        assert RunStatus.DRAFT == "draft"

    def test_member_count(self):
        assert len(RunStatus) == 11


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class TestUserModel:
    def test_tablename(self):
        assert User.__tablename__ == "users"

    def test_schema(self):
        assert User.__table_args__["schema"] == "core"
