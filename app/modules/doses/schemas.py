import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from app.core.schemas import ApiModel

Outcome = Literal["created", "duplicate", "rejected"]
DoseStatus = Literal["TAKEN", "SKIPPED", "MISSED"]


class DoseEventInput(ApiModel):
    event_id: uuid.UUID
    schedule_id: uuid.UUID
    scheduled_at: AwareDatetime
    status: Literal["TAKEN", "SKIPPED"]
    acted_at: AwareDatetime


class DoseEventBatch(ApiModel):
    events: Annotated[list[DoseEventInput], Field(min_length=1, max_length=100)]


class DoseEventResult(ApiModel):
    event_id: uuid.UUID
    outcome: Outcome
    code: str | None = None


class DoseEventBatchResult(ApiModel):
    results: list[DoseEventResult]


class DoseHistoryItem(ApiModel):
    schedule_id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    dosage: str
    scheduled_at: datetime
    status: DoseStatus
    acted_at: datetime | None


class DoseHistoryPage(ApiModel):
    items: list[DoseHistoryItem]
    next_cursor: str | None


class Adherence(ApiModel):
    # `from` es palabra reservada en Python: el alias lo deja como `from` en el JSON.
    from_: date = Field(alias="from")
    to: date
    taken: int
    skipped: int
    missed: int
    percentage: float | None
