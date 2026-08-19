import uuid
from pydantic import BaseModel, ConfigDict

from app.db.models.run import RunStatus


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    study_id: uuid.UUID
    status: RunStatus
    workflow_id: str | None = None