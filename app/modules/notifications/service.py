"""Canales (correo, Telegram) y preferencias de notificación del usuario.

Telegram se vincula con un token de un solo uso (`https://t.me/<Bot>?start=<token>`): el bot solo
puede escribir a quien lo inició. Las respuestas del bot salen por el outbox, nunca en la petición.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError, UnauthorizedError
from app.core.security import hash_token, new_opaque_token, utcnow
from app.modules.auth.models import AccountToken
from app.modules.auth.repository import AccountTokenRepository
from app.modules.notifications.repository import (
    OutboxRepository,
    PreferenceRepository,
    TelegramLinkRepository,
)
from app.modules.notifications.schemas import (
    ChannelToggles,
    NotificationChannelList,
    NotificationChannelOut,
    NotificationPreferences,
    TelegramLinkOut,
)
from app.modules.users.models import User
from app.modules.users.repository import UserRepository

LINK = "link_telegram"


class TelegramNotConfiguredError(DomainError):
    status_code = 503
    default_title = "Telegram no disponible"

    def __init__(self) -> None:
        super().__init__("telegram_not_configured", "El bot de Telegram no está configurado.")


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    update_id: int
    chat_id: int
    text: str
    username: str | None


def parse_update(update: dict[str, Any]) -> TelegramMessage | None:
    """Mensaje de texto de un chat privado; todo lo demás (grupos, fotos, ediciones) se ignora."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat, text, update_id = message.get("chat"), message.get("text"), update.get("update_id")
    if (
        not isinstance(chat, dict)
        or chat.get("type") != "private"
        or not isinstance(chat.get("id"), int)
        or not isinstance(text, str)
        or not isinstance(update_id, int)
    ):
        return None
    sender = message.get("from")
    username = sender.get("username") if isinstance(sender, dict) else None
    return TelegramMessage(
        update_id=update_id,
        chat_id=chat["id"],
        text=text,
        username=username[:64] if isinstance(username, str) else None,
    )


class NotificationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)
        self._tokens = AccountTokenRepository(session)
        self._links = TelegramLinkRepository(session)
        self._prefs = PreferenceRepository(session)
        self._outbox = OutboxRepository(session)

    async def _user(self, user_id: uuid.UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise UnauthorizedError("invalid_token", "La sesión ya no es válida.")
        return user

    # ─── Canales ──────────────────────────────────────────────────────────────

    async def list_channels(self, user_id: uuid.UUID) -> NotificationChannelList:
        user = await self._user(user_id)
        link = await self._links.get_for_user(user_id)
        verified = user.email_verified_at is not None
        return NotificationChannelList(
            items=[
                NotificationChannelOut(
                    channel="email",
                    linked=True,
                    verified=verified,
                    label=mask_email(user.email),
                    linked_at=user.email_verified_at,
                ),
                NotificationChannelOut(
                    channel="telegram",
                    linked=link is not None,
                    verified=link is not None,
                    label=(f"@{link.username}" if link.username else "Telegram") if link else None,
                    linked_at=link.created_at if link else None,
                ),
            ]
        )

    async def create_telegram_link(self, user_id: uuid.UUID) -> TelegramLinkOut:
        bot = self._settings.telegram_bot_username
        if not bot:
            raise TelegramNotConfiguredError
        await self._user(user_id)
        now = utcnow()
        await self._tokens.invalidate(user_id, LINK, now)
        raw = new_opaque_token()
        expires_at = now + timedelta(minutes=self._settings.telegram_link_ttl_minutes)
        self._tokens.add(
            AccountToken(
                user_id=user_id,
                purpose=LINK,
                token_hash=hash_token(raw),
                expires_at=expires_at,
                created_at=now,
            )
        )
        await self._session.commit()
        return TelegramLinkOut(url=f"https://t.me/{bot}?start={raw}", expires_at=expires_at)

    async def unlink_telegram(self, user_id: uuid.UUID) -> None:
        await self._links.delete_for_user(user_id)
        await self._session.commit()

    # ─── Preferencias ─────────────────────────────────────────────────────────

    async def get_preferences(self, user_id: uuid.UUID) -> NotificationPreferences:
        row = await self._prefs.get(user_id)
        if row is None:
            return NotificationPreferences(
                missed_dose=ChannelToggles(email=True, telegram=True),
                weekly_report=ChannelToggles(email=True, telegram=True),
            )
        return NotificationPreferences(
            missed_dose=ChannelToggles(
                email=row.missed_dose_email, telegram=row.missed_dose_telegram
            ),
            weekly_report=ChannelToggles(
                email=row.weekly_report_email, telegram=row.weekly_report_telegram
            ),
        )

    async def set_preferences(
        self, user_id: uuid.UUID, data: NotificationPreferences
    ) -> NotificationPreferences:
        await self._user(user_id)
        await self._prefs.upsert(
            user_id,
            missed_dose_email=data.missed_dose.email,
            missed_dose_telegram=data.missed_dose.telegram,
            weekly_report_email=data.weekly_report.email,
            weekly_report_telegram=data.weekly_report.telegram,
        )
        await self._session.commit()
        return data

    # ─── Webhook del bot ──────────────────────────────────────────────────────

    async def handle_update(self, update: dict[str, Any]) -> None:
        """Procesa una actualización de Telegram; un reintento de Telegram no duplica respuestas."""
        message = parse_update(update)
        if message is None:
            return
        command, _, argument = message.text.strip().partition(" ")
        command = command.split("@", 1)[0].lower()
        if command == "/start":
            await self._start(message, argument.strip())
        elif command == "/stop":
            await self._stop(message)
        await self._session.commit()

    async def _reply(self, message: TelegramMessage, kind: str) -> None:
        # Una sola respuesta por actualización: si Telegram la reenvía, no se contesta dos veces.
        await self._outbox.enqueue(
            channel="telegram",
            kind=kind,
            payload={"chat_id": message.chat_id},
            dedupe_key=f"telegram:{message.update_id}",
        )

    async def _start(self, message: TelegramMessage, token: str) -> None:
        now = utcnow()
        row = (
            await self._tokens.get_usable_for_update(hash_token(token), LINK, now)
            if token
            else None
        )
        if row is None:
            await self._reply(message, "telegram_link_failed")
            return
        row.used_at = now
        await self._links.replace(row.user_id, message.chat_id, message.username)
        await self._reply(message, "telegram_linked")

    async def _stop(self, message: TelegramMessage) -> None:
        if await self._links.delete_for_chat(message.chat_id):
            await self._reply(message, "telegram_unlinked")
