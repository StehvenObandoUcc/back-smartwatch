import uuid
from datetime import date, datetime
from typing import Literal

from app.core.schemas import ApiModel


class MedicationAdherenceOut(ApiModel):
    medication_id: uuid.UUID
    medication_name: str
    dosage: str
    scheduled: int
    taken: int
    skipped: int
    missed: int
    percentage: float | None


class DayAdherenceOut(ApiModel):
    date: date
    taken: int
    skipped: int
    missed: int


class ReportSummaryOut(ApiModel):
    taken: int
    skipped: int
    missed: int
    percentage: float | None
    by_medication: list[MedicationAdherenceOut]
    by_day: list[DayAdherenceOut]


class ReportOut(ApiModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    period_start: date
    period_end: date
    trigger: Literal["weekly", "manual"]
    status: Literal["pending", "ready", "failed"]
    created_at: datetime
    generated_at: datetime | None
    summary: ReportSummaryOut | None


class ReportCreate(ApiModel):
    period_end: date | None = None


class ReportPage(ApiModel):
    items: list[ReportOut]
    next_cursor: str | None
