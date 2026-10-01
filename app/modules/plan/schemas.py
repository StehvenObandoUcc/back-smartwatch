import uuid
from datetime import datetime

from app.core.schemas import ApiModel


class PlannedDoseOut(ApiModel):
    schedule_id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    dosage: str
    instructions: str | None
    color: str
    scheduled_at: datetime


class PlanOut(ApiModel):
    version: int
    patient_timezone: str
    generated_at: datetime
    valid_from: datetime
    valid_until: datetime
    doses: list[PlannedDoseOut]
