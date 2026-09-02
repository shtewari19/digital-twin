"""LLM-assisted drafting endpoints: study naming, persona elaboration,
candidate messages, and anchor statements.

Every route calls `app.core.llm_gateway.call_llm_json`, which wraps the
LiteLLM-backed `app.llm.llm_client.call_llm` (PR #40) off the event loop
thread — that function is synchronous — and parses its response as JSON.
There's no way to request JSON-mode output through `call_llm`'s fixed
signature, so every prompt below asks for it explicitly; a model that
doesn't comply, or any provider-side failure, surfaces as a clean 502
rather than a crash or a silently wrong response.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.llm_gateway import LLMError, LLMResponseParseError, call_llm_json
from app.db.models.domain import Domain as DomainRow
from app.db.models.study import Study as StudyRow
from app.schemas import (
    AnchorAssistRequest,
    AnchorSetUpdate,
    AvatarDraft,
    Intent,
    MessageAssistRequest,
    PersonaAssistRequest,
    StudyNameAssistRequest,
    StudyNameSuggestion,
)

router = APIRouter(prefix="/llm/assist", tags=["llm"])


async def _run_assist(prompt: str) -> dict[str, object]:
    try:
        return await call_llm_json(prompt, model=settings.llm_assist_model)
    except (LLMError, LLMResponseParseError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/study-name", response_model=StudyNameSuggestion)
async def assist_study_name(
    body: StudyNameAssistRequest, _current_user: CurrentUser
) -> StudyNameSuggestion:
    """`POST /api/v1/llm/assist/study-name`."""
    prompt = (
        "A researcher is setting up a message-testing study. Given their free-text "
        "description, suggest a short study name, a one-sentence description, and "
        "the structured intent behind it.\n\n"
        f"Description: {body.description}\n\n"
        "Respond with ONLY a JSON object, no markdown, matching exactly this shape:\n"
        '{"suggested_name": "...", "suggested_description": "...", '
        '"intent": {"audience": "...", "product": "...", "decision": "...", '
        '"success_criteria": "..."}}'
    )
    result = await _run_assist(prompt)
    intent = result.get("intent")
    return StudyNameSuggestion(
        suggested_name=result.get("suggested_name"),
        suggested_description=result.get("suggested_description"),
        intent=Intent(**intent) if isinstance(intent, dict) else None,
    )


@router.post("/persona", response_model=AvatarDraft)
async def assist_persona(
    body: PersonaAssistRequest, session: DbSession, _current_user: CurrentUser
) -> AvatarDraft:
    """`POST /api/v1/llm/assist/persona`."""
    domain_context = ""
    if body.domain_id is not None:
        domain = await session.get(DomainRow, body.domain_id)
        if domain is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Domain {body.domain_id} does not exist.",
            )
        domain_context = f" for the '{domain.name}' domain"

    prompt = (
        f"Elaborate this rough persona description{domain_context} into a full "
        "persona: a short display name and a 1-2 sentence profile describing how "
        "they evaluate messages.\n\n"
        f"Rough description: {body.rough_description}\n\n"
        "Respond with ONLY a JSON object, no markdown, matching exactly this shape:\n"
        '{"name": "...", "profile": "..."}'
    )
    result = await _run_assist(prompt)
    return AvatarDraft(name=result.get("name"), profile=result.get("profile"))


@router.post("/messages")
async def assist_messages(
    body: MessageAssistRequest, session: DbSession, _current_user: CurrentUser
) -> dict[str, object]:
    """`POST /api/v1/llm/assist/messages`.

    The API spec types this response as a generic `object`, so the shape
    below is this implementation's choice, not a documented contract —
    each item matches `MessageCreate`'s fields so a caller can POST them
    straight through to `POST /studies/{study_id}/messages`.
    """
    study = await session.get(StudyRow, body.study_id)
    if study is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")

    prompt = (
        f"Suggest {body.count} distinct candidate marketing/messaging claims to "
        f"test for the study '{study.name}'"
        + (f": {study.description}" if study.description else "")
        + f".\n\nRespond with ONLY a JSON object, no markdown, with exactly {body.count} "
        'items, matching exactly this shape:\n{"messages": [{"text": "...", "group": null}]}'
    )
    result = await _run_assist(prompt)
    messages = result.get("messages")
    return {"messages": messages if isinstance(messages, list) else []}


@router.post("/anchors", response_model=AnchorSetUpdate)
async def assist_anchors(
    body: AnchorAssistRequest, _current_user: CurrentUser
) -> AnchorSetUpdate:
    """`POST /api/v1/llm/assist/anchors`."""
    prompt = (
        f"Draft one anchor statement per scale point, from {body.scale.min} to "
        f"{body.scale.max} inclusive, for the outcome dimension "
        f"'{body.outcome_dimension}'. Each anchor is a first-person statement "
        "representing how someone at that scale point would describe their "
        "attitude.\n\n"
        "Respond with ONLY a JSON object, no markdown, with exactly one entry per "
        f"integer from {body.scale.min} to {body.scale.max}, matching exactly this "
        'shape:\n{"anchors": [{"scale_point": 1, "text": "..."}]}'
    )
    result = await _run_assist(prompt)
    anchors = result.get("anchors")
    return AnchorSetUpdate(anchors=anchors if isinstance(anchors, list) else [])
