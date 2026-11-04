import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.core.schemas import ApiModel
from app.modules.medications.schemas import Days, Dosage, Name, ScheduleCreate, Times

Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
TurnContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class ChatTurn(ApiModel):
    role: Literal["user", "assistant"]
    content: TurnContent


class ChatRequest(ApiModel):
    message: Message
    history: Annotated[list[ChatTurn], Field(max_length=10)] = Field(default_factory=list)


class WatchChatRequest(ApiModel):
    message: Message | None = None
    history: Annotated[list[ChatTurn], Field(max_length=10)] = Field(default_factory=list)
    confirm_action_id: uuid.UUID | None = None

    @field_validator("history", mode="before")
    @classmethod
    def _null_history(cls, value: Any) -> Any:
        return [] if value is None else value  # el reloj envía null sin historial

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.message is None) == (self.confirm_action_id is None):
            raise ValueError("Envía message o confirmActionId, uno de los dos")
        return self


class PendingAction(ApiModel):
    id: uuid.UUID
    type: Literal["create_medication", "add_schedule"] = "create_medication"
    summary: str


class ChatAction(ApiModel):
    type: Literal["medication_created", "schedule_added"] = "medication_created"
    medication_id: uuid.UUID
    schedule_id: uuid.UUID


class WatchChatReply(ApiModel):
    reply: str
    remaining_messages: int
    pending_action: PendingAction | None = None
    actions: list[ChatAction] = Field(default_factory=list)


class MedicationDraft(ApiModel):
    """Argumentos de la herramienta `create_medication`, validados como en la web."""

    name: Name
    dosage: Dosage
    times: Times
    days_of_week: Days = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])


class ScheduleDraft(ApiModel):
    """Argumentos de la herramienta `add_schedule`, validados como en la web."""

    medication_id: uuid.UUID
    times: Times
    days_of_week: Days = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])


class ScheduleProposal(ApiModel):
    """Lo que se guarda en Redis para `add_schedule`. Tras confirmar se guarda con
    `schedule_id`, para que reintentar devuelva el mismo horario."""

    type: Literal["add_schedule"] = "add_schedule"
    medication_id: uuid.UUID
    schedule: ScheduleCreate
    description: str  # 'a Metformina 850 mg las 14:00 todos los días'
    schedule_id: uuid.UUID | None = None
