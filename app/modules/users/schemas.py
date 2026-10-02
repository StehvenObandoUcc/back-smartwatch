import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import model_validator

from app.core.schemas import ApiModel, DisplayName, Timezone, require_partial_update

Role = Literal["patient", "caregiver"]
Locale = Literal["es"]


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    email_verified: bool
    display_name: str
    role: Role
    timezone: str
    locale: Locale
    patient_id: uuid.UUID | None
    created_at: datetime


class UserUpdate(ApiModel):
    display_name: DisplayName | None = None
    timezone: Timezone | None = None
    locale: Locale | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Self:
        return require_partial_update(self)
