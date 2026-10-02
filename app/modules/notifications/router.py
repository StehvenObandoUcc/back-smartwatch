import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Response, status

from app.core.auth import CurrentUserDep
from app.core.deps import RateLimiterDep, SessionDep, SettingsDep
from app.core.errors import UnauthorizedError
from app.modules.notifications.schemas import (
    NotificationChannelList,
    NotificationPreferences,
    TelegramLinkOut,
)
from app.modules.notifications.service import NotificationService

router = APIRouter(tags=["notifications"])

WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 (nombre de cabecera)


def get_notification_service(session: SessionDep, settings: SettingsDep) -> NotificationService:
    return NotificationService(session, settings)


ServiceDep = Annotated[NotificationService, Depends(get_notification_service)]


@router.get(
    "/users/me/notification-channels",
    operation_id="listMyNotificationChannels",
    summary="Canales de notificación del usuario",
)
async def list_channels(user: CurrentUserDep, service: ServiceDep) -> NotificationChannelList:
    return await service.list_channels(user.id)


@router.post(
    "/users/me/notification-channels/telegram/link",
    operation_id="createTelegramLink",
    summary="Pedir el enlace para vincular Telegram",
    status_code=status.HTTP_201_CREATED,
)
async def create_telegram_link(
    user: CurrentUserDep,
    service: ServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> TelegramLinkOut:
    await limiter.hit("telegram-link:user", str(user.id), settings.rate_telegram_link_user)
    return await service.create_telegram_link(user.id)


@router.delete(
    "/users/me/notification-channels/telegram",
    operation_id="unlinkTelegram",
    summary="Desvincular Telegram",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_telegram(user: CurrentUserDep, service: ServiceDep) -> Response:
    await service.unlink_telegram(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/users/me/notification-preferences",
    operation_id="getMyNotificationPreferences",
    summary="Preferencias de notificación",
)
async def get_preferences(user: CurrentUserDep, service: ServiceDep) -> NotificationPreferences:
    return await service.get_preferences(user.id)


@router.put(
    "/users/me/notification-preferences",
    operation_id="setMyNotificationPreferences",
    summary="Guardar las preferencias de notificación",
)
async def set_preferences(
    data: NotificationPreferences, user: CurrentUserDep, service: ServiceDep
) -> NotificationPreferences:
    return await service.set_preferences(user.id, data)


@router.post(
    "/telegram/webhook",
    operation_id="telegramWebhook",
    summary="Webhook del bot de Telegram (no lo usan la web ni el reloj)",
)
async def telegram_webhook(
    update: Annotated[dict[str, Any], Body()],
    service: ServiceDep,
    settings: SettingsDep,
    secret_token: Annotated[str | None, Header(alias=WEBHOOK_SECRET_HEADER)] = None,
) -> Response:
    # Falla cerrado: sin secreto configurado o con uno distinto, nadie entra. Comparación en
    # tiempo constante para no revelar el secreto por tiempo de respuesta.
    expected = settings.telegram_webhook_secret
    if (
        expected is None
        or secret_token is None
        or not hmac.compare_digest(secret_token.encode(), expected.get_secret_value().encode())
    ):
        raise UnauthorizedError("invalid_webhook_secret", "Secreto del webhook incorrecto.")
    await service.handle_update(update)
    return Response(status_code=status.HTTP_200_OK)
