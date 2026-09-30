import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.core.schemas import ApiModel

Purpose = Literal["health_data", "ai_chat", "notifications"]


class ConsentActorOut(ApiModel):
    user_id: uuid.UUID
    display_name: str
    on_behalf: bool


class ConsentOut(ApiModel):
    purpose: Purpose
    granted: bool
    version: str | None
    updated_at: datetime | None
    granted_by: ConsentActorOut | None


class ConsentListOut(ApiModel):
    items: list[ConsentOut]


class ConsentUpdate(ApiModel):
    granted: bool
    version: str = Field(min_length=1, max_length=32)
