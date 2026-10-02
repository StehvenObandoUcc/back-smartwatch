from datetime import datetime
from typing import Literal

from app.core.schemas import ApiModel


class NotificationChannelOut(ApiModel):
    channel: Literal["email", "telegram"]
    linked: bool
    verified: bool
    label: str | None
    linked_at: datetime | None


class NotificationChannelList(ApiModel):
    items: list[NotificationChannelOut]


class TelegramLinkOut(ApiModel):
    url: str
    expires_at: datetime


class ChannelToggles(ApiModel):
    email: bool
    telegram: bool


class NotificationPreferences(ApiModel):
    missed_dose: ChannelToggles
    weekly_report: ChannelToggles
