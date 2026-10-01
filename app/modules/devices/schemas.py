import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.core.schemas import ApiModel

USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"  # RFC 8628 §6.1: sin vocales ni ambigüedades


class PairingCodeRequest(ApiModel):
    model: str = Field(min_length=1, max_length=80)
    app_version: str | None = Field(default=None, max_length=32)


class PairingCodeOut(ApiModel):
    code: str
    device_code: str
    expires_in: int
    interval: int


class PairingConfirm(ApiModel):
    patient_id: uuid.UUID


class DeviceCodeGrant(ApiModel):
    grant_type: Literal["device_code"]
    device_code: str = Field(min_length=1, max_length=256)


class DeviceRefreshGrant(ApiModel):
    grant_type: Literal["refresh_token"]
    refresh_token: str = Field(min_length=1, max_length=512)


DeviceTokenRequest = Annotated[
    DeviceCodeGrant | DeviceRefreshGrant, Field(discriminator="grant_type")
]


class DeviceOut(ApiModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    model: str
    paired_at: datetime
    last_seen_at: datetime | None
    has_push_token: bool


class DevicePage(ApiModel):
    items: list[DeviceOut]
    next_cursor: str | None


class PushTokenUpdate(ApiModel):
    token: str = Field(min_length=1, max_length=4096)
