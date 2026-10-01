import uuid
from datetime import date, datetime
from typing import Annotated, Self

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from app.core.schemas import ApiModel

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Dosage = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Instructions = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
DEFAULT_COLOR = "#3B82F6"

MAX_SCHEDULES_PER_MEDICATION = 10


def _unique_sorted[T: (str, int)](values: list[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("No se admiten valores repetidos")
    return sorted(values)


LocalTime = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
Times = Annotated[
    list[LocalTime], Field(min_length=1, max_length=12), AfterValidator(_unique_sorted)
]
Days = Annotated[
    list[Annotated[int, Field(ge=1, le=7)]],
    Field(min_length=1, max_length=7),
    AfterValidator(_unique_sorted),
]


class MedicationOut(ApiModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    name: str
    dosage: str
    instructions: str | None
    color: str
    archived: bool
    created_at: datetime
    updated_at: datetime


class MedicationCreate(ApiModel):
    name: Name
    dosage: Dosage
    instructions: Instructions | None = None
    color: Color = DEFAULT_COLOR


class MedicationUpdate(ApiModel):
    name: Name | None = None
    dosage: Dosage | None = None
    instructions: Instructions | None = None  # único campo que admite null (borra el texto)
    color: Color | None = None

    @model_validator(mode="after")
    def _valid_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Indica al menos un campo")
        for field in ("name", "dosage", "color"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("Los campos no admiten null")
        return self


class MedicationPage(ApiModel):
    items: list[MedicationOut]
    next_cursor: str | None


class ScheduleOut(ApiModel):
    id: uuid.UUID
    medication_id: uuid.UUID
    times: list[str]
    days_of_week: list[int]
    start_date: date
    end_date: date | None


class ScheduleCreate(ApiModel):
    times: Times
    days_of_week: Days
    start_date: date
    end_date: date | None = None

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("endDate no puede ser anterior a startDate")
        return self


class ScheduleUpdate(ApiModel):
    times: Times | None = None
    days_of_week: Days | None = None
    start_date: date | None = None
    end_date: date | None = None  # null = sin fin

    @model_validator(mode="after")
    def _valid_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Indica al menos un campo")
        for field in ("times", "days_of_week", "start_date"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("Los campos no admiten null")
        return self


class ScheduleList(ApiModel):
    items: list[ScheduleOut]
