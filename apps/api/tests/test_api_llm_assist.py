"""Contract tests for the LLM-assisted drafting endpoints
(app/api/v1/llm_assist.py).

`call_llm_json` is monkeypatched at the module boundary (`_run_assist`
calls it directly) so these tests exercise prompt wiring, request
validation, response shaping, and the 502 translation — not the LiteLLM
integration itself (see test_llm_client.py / test_llm_gateway.py for that).
"""

from __future__ import annotations

import uuid

from fakes import FakeAsyncSession

from app.api.deps import get_db
from app.core.llm_gateway import LLMProviderError, LLMResponseParseError
from app.db.models.domain import Domain as DomainRow
from app.db.models.study import Study as StudyRow


def _install_db(app, session: FakeAsyncSession) -> None:
    app.dependency_overrides[get_db] = lambda: session


def _stub_assist(monkeypatch, result: dict[str, object]) -> None:
    async def _fake(prompt: str) -> dict[str, object]:
        return result

    monkeypatch.setattr("app.api.v1.llm_assist._run_assist", _fake)


# ---------------------------------------------------------------------------
# POST /llm/assist/study-name
# ---------------------------------------------------------------------------


def test_assist_study_name_requires_description(client, current_user):
    response = client.post("/api/v1/llm/assist/study-name", json={})

    assert response.status_code == 422


def test_assist_study_name_happy_path(client, current_user, monkeypatch):
    _stub_assist(
        monkeypatch,
        result={
            "suggested_name": "Q3 lead-claim test",
            "suggested_description": "Test which claim wins",
            "intent": {"audience": "PCPs", "product": None, "decision": None,
                       "success_criteria": None},
        },
    )

    response = client.post(
        "/api/v1/llm/assist/study-name",
        json={"description": "Test which claim wins for the Q3 campaign"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_name"] == "Q3 lead-claim test"
    assert body["intent"]["audience"] == "PCPs"


def test_assist_study_name_tolerates_missing_intent_in_response(client, current_user, monkeypatch):
    _stub_assist(monkeypatch, result={"suggested_name": "X", "suggested_description": "Y"})

    response = client.post(
        "/api/v1/llm/assist/study-name", json={"description": "desc"}
    )

    assert response.status_code == 200
    assert response.json()["intent"] is None


def test_assist_study_name_translates_llm_error_to_502(client, current_user, monkeypatch):
    async def _boom(prompt: str, *, model: str):
        raise LLMProviderError("provider down")

    monkeypatch.setattr("app.api.v1.llm_assist.call_llm_json", _boom)

    response = client.post("/api/v1/llm/assist/study-name", json={"description": "desc"})

    assert response.status_code == 502


def test_assist_study_name_translates_parse_error_to_502(client, current_user, monkeypatch):
    async def _boom(prompt: str, *, model: str):
        raise LLMResponseParseError("not JSON")

    monkeypatch.setattr("app.api.v1.llm_assist.call_llm_json", _boom)

    response = client.post("/api/v1/llm/assist/study-name", json={"description": "desc"})

    assert response.status_code == 502


# ---------------------------------------------------------------------------
# POST /llm/assist/persona
# ---------------------------------------------------------------------------


def test_assist_persona_rejects_unknown_domain(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.post(
        "/api/v1/llm/assist/persona",
        json={"rough_description": "Skeptical cardiologist", "domain_id": str(uuid.uuid4())},
    )

    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


def test_assist_persona_without_domain_id_skips_lookup(client, app, current_user, monkeypatch):
    session = FakeAsyncSession()
    _install_db(app, session)
    _stub_assist(monkeypatch, result={"name": "The Skeptic", "profile": "Wants trial data."})

    response = client.post(
        "/api/v1/llm/assist/persona", json={"rough_description": "Skeptical cardiologist"}
    )

    assert response.status_code == 200
    assert response.json() == {"name": "The Skeptic", "profile": "Wants trial data."}
    assert session.last_get is None


def test_assist_persona_happy_path_with_domain(client, app, current_user, monkeypatch):
    domain_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=DomainRow(id=domain_id, name="Cardiology")))
    _stub_assist(monkeypatch, result={"name": "The Skeptic", "profile": "Wants trial data."})

    response = client.post(
        "/api/v1/llm/assist/persona",
        json={"rough_description": "Skeptical cardiologist", "domain_id": str(domain_id)},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "The Skeptic"


# ---------------------------------------------------------------------------
# POST /llm/assist/messages
# ---------------------------------------------------------------------------


def test_assist_messages_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.post(
        "/api/v1/llm/assist/messages", json={"study_id": str(uuid.uuid4())}
    )

    assert response.status_code == 404


def test_assist_messages_defaults_count_to_five(client, app, current_user, monkeypatch):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id, name="Q3 test")))
    captured: dict = {}

    async def _fake(prompt: str) -> dict[str, object]:
        captured["prompt"] = prompt
        return {"messages": [{"text": f"claim {i}", "group": None} for i in range(5)]}

    monkeypatch.setattr("app.api.v1.llm_assist._run_assist", _fake)

    response = client.post("/api/v1/llm/assist/messages", json={"study_id": str(study_id)})

    assert response.status_code == 200
    assert len(response.json()["messages"]) == 5
    assert "5 distinct" in captured["prompt"]


def test_assist_messages_tolerates_non_list_response(client, app, current_user, monkeypatch):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id, name="Q3 test")))
    _stub_assist(monkeypatch, result={"messages": "not-a-list"})

    response = client.post("/api/v1/llm/assist/messages", json={"study_id": str(study_id)})

    assert response.status_code == 200
    assert response.json()["messages"] == []


# ---------------------------------------------------------------------------
# POST /llm/assist/anchors
# ---------------------------------------------------------------------------


def test_assist_anchors_requires_scale(client, current_user):
    response = client.post(
        "/api/v1/llm/assist/anchors", json={"outcome_dimension": "purchase intent"}
    )

    assert response.status_code == 422


def test_assist_anchors_happy_path(client, current_user, monkeypatch):
    _stub_assist(
        monkeypatch,
        result={"anchors": [{"scale_point": 1, "text": "no"}, {"scale_point": 5, "text": "yes"}]},
    )

    response = client.post(
        "/api/v1/llm/assist/anchors",
        json={"outcome_dimension": "purchase intent", "scale": {"min": 1, "max": 5}},
    )

    assert response.status_code == 200
    assert len(response.json()["anchors"]) == 2


def test_assist_anchors_tolerates_non_list_response(client, current_user, monkeypatch):
    _stub_assist(monkeypatch, result={"anchors": None})

    response = client.post(
        "/api/v1/llm/assist/anchors",
        json={"outcome_dimension": "purchase intent", "scale": {"min": 1, "max": 5}},
    )

    assert response.status_code == 200
    assert response.json()["anchors"] == []
