"""Proveedores de envío. Cada canal del outbox tiene un `Sender`; el dispatcher no sabe más."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.logging import get_logger
from app.modules.notifications.templates import render_email, render_telegram

logger = get_logger(__name__)

RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True, slots=True)
class Message:
    kind: str
    payload: dict[str, Any]
    dedupe_key: str


class DeliveryError(Exception):
    """El envío falló. `retryable` decide si se reintenta con backoff o se da por perdido.

    `reason` acaba en la base de datos y en los logs: nunca debe incluir tokens, direcciones ni
    texto de proveedores.
    """

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


class Sender(Protocol):
    async def send(self, message: Message) -> None: ...


class ConsoleEmailSender:
    """Solo desarrollo: escribe el correo (con sus enlaces) en el log del worker. Sin red."""

    async def send(self, message: Message) -> None:
        subject, body = render_email(message.kind, message.payload)
        logger.warning("email_console", to=message.payload.get("to"), subject=subject, body=body)


class ResendEmailSender:
    def __init__(self, client: httpx.AsyncClient, api_key: str, sender: str) -> None:
        self._client = client
        self._api_key = api_key
        self._from = sender

    async def send(self, message: Message) -> None:
        subject, body = render_email(message.kind, message.payload)
        try:
            response = await self._client.post(
                RESEND_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    # Si el worker cae justo después de enviar, el reintento no duplica el correo.
                    "Idempotency-Key": message.dedupe_key,
                },
                json={
                    "from": self._from,
                    "to": [message.payload["to"]],
                    "subject": subject,
                    "text": body,
                },
            )
        except httpx.HTTPError as exc:
            raise DeliveryError(f"resend: {type(exc).__name__}", retryable=True) from None
        if response.status_code >= 400:
            # 429 y 5xx son transitorios; el resto (clave mala, dirección inválida) no mejora.
            transient = response.status_code == 429 or response.status_code >= 500
            raise DeliveryError(f"resend: http {response.status_code}", retryable=transient)


class TelegramSender:
    """Envía por la Bot API. El token va en la URL: nunca se registra (ver `configure_logging`)."""

    def __init__(self, client: httpx.AsyncClient, bot_token: str) -> None:
        self._client = client
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async def send(self, message: Message) -> None:
        text = render_telegram(message.kind, message.payload)
        try:
            response = await self._client.post(
                self._url,
                json={
                    "chat_id": message.payload["chat_id"],
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            raise DeliveryError(f"telegram: {type(exc).__name__}", retryable=True) from None
        if response.status_code >= 400:
            # 403 (el usuario bloqueó el bot) y 400 (chat inexistente) no mejoran reintentando.
            transient = response.status_code == 429 or response.status_code >= 500
            raise DeliveryError(f"telegram: http {response.status_code}", retryable=transient)
