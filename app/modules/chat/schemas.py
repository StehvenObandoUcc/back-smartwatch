import uuid
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from app.core.schemas import ApiModel
from app.modules.medications.schemas import Days, Dosage, Name, Times

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

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.message is None) == (self.confirm_action_id is None):
            raise ValueError("Envía message o confirmActionId, uno de los dos")
        return self


class PendingAction(ApiModel):
    id: uuid.UUID
    type: Literal["create_medication"] = "create_medication"
    summary: str


class ChatAction(ApiModel):
    type: Literal["medication_created"] = "medication_created"
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
