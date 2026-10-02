from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from app.core.schemas import ApiModel

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


class WatchChatReply(ApiModel):
    reply: str
    remaining_messages: int
