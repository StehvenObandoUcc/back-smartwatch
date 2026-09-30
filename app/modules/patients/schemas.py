import uuid
from datetime import datetime
from typing import Self

from pydantic import model_validator

from app.core.schemas import ApiModel, DisplayName, Timezone, require_partial_update

INVITATION_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class PatientOut(ApiModel):
    id: uuid.UUID
    display_name: str
    timezone: str
    managed: bool
    created_at: datetime


class PatientCreate(ApiModel):
    display_name: DisplayName
    timezone: Timezone


class PatientUpdate(ApiModel):
    display_name: DisplayName | None = None
    timezone: Timezone | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Self:
        return require_partial_update(self)


class PatientPage(ApiModel):
    items: list[PatientOut]
    next_cursor: str | None


class CareLinkOut(ApiModel):
    patient_id: uuid.UUID
    caregiver_id: uuid.UUID
    caregiver_display_name: str
    active: bool
    created_at: datetime


class CareLinkPage(ApiModel):
    items: list[CareLinkOut]
    next_cursor: str | None


class CaregiverInvitationOut(ApiModel):
    code: str
    patient_id: uuid.UUID
    expires_at: datetime
