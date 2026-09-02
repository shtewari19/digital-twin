"""Message endpoints, nested under a study.

Not paginated — the API spec's `listMessages` takes only `study_id`, no
`limit`/`cursor`, since a study's candidate-message set is small and bounded.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.db.models.message import Message as MessageRow
from app.db.models.study import Study as StudyRow
from app.schemas import Message, MessageCreate, MessageList, MessageUpdate

router = APIRouter(tags=["messages"])


async def _get_study_or_404(session: DbSession, study_id: uuid.UUID) -> StudyRow:
    row = await session.get(StudyRow, study_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")
    return row


async def _get_message_or_404(
    session: DbSession, study_id: uuid.UUID, message_id: uuid.UUID
) -> MessageRow:
    row = await session.get(MessageRow, message_id)
    if row is None or row.study_id != study_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")
    return row


@router.get("/studies/{study_id}/messages", response_model=MessageList)
async def list_messages(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> MessageList:
    """`GET /api/v1/studies/{study_id}/messages`."""
    await _get_study_or_404(session, study_id)
    rows = (
        (
            await session.execute(
                select(MessageRow)
                .where(MessageRow.study_id == study_id)
                .order_by(MessageRow.position, MessageRow.created_at)
            )
        )
        .scalars()
        .all()
    )
    return MessageList(
        data=[Message.model_validate(r) for r in rows], next_cursor=None, has_more=False
    )


@router.post(
    "/studies/{study_id}/messages", response_model=Message, status_code=status.HTTP_201_CREATED
)
async def create_message(
    study_id: uuid.UUID, body: MessageCreate, session: DbSession, _current_user: CurrentUser
) -> Message:
    """`POST /api/v1/studies/{study_id}/messages`."""
    await _get_study_or_404(session, study_id)
    row = MessageRow(study_id=study_id, text=body.text, group=body.group)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return Message.model_validate(row)


@router.patch("/studies/{study_id}/messages/{message_id}", response_model=Message)
async def update_message(
    study_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageUpdate,
    session: DbSession,
    _current_user: CurrentUser,
) -> Message:
    """`PATCH /api/v1/studies/{study_id}/messages/{message_id}`."""
    row = await _get_message_or_404(session, study_id, message_id)
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(row, field, value)
    if updates:
        row.version += 1
    await session.commit()
    await session.refresh(row)
    return Message.model_validate(row)


@router.delete(
    "/studies/{study_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_message(
    study_id: uuid.UUID,
    message_id: uuid.UUID,
    session: DbSession,
    _current_user: CurrentUser,
) -> None:
    """`DELETE /api/v1/studies/{study_id}/messages/{message_id}`."""
    row = await _get_message_or_404(session, study_id, message_id)
    await session.delete(row)
    await session.commit()
