"""Canales, preferencias y webhook de Telegram (sin hablar nunca con Telegram)."""

import itertools
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import RateLimit
from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import assert_problem, bearer, register, unique_email

SECRET = "secreto-de-pruebas-0123456789"
HEADER = "X-Telegram-Bot-Api-Secret-Token"
_update_ids = itertools.count(1000)

Session = dict[str, Any]


@pytest.fixture
async def client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    app = app_factory(telegram_bot_username="TestBot", telegram_webhook_secret=SecretStr(SECRET))
    async for c in make_client(app):
        yield c


def _update(
    text_: str, chat_id: int = 111, chat_type: str = "private", **extra: Any
) -> dict[str, Any]:
    return {
        "update_id": next(_update_ids),
        "message": {
            "message_id": 1,
            "from": {"id": chat_id, "username": "ana_tg"},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text_,
            **extra,
        },
    }


async def _webhook(client: AsyncClient, update: Any, secret: str | None = SECRET) -> Response:
    headers = {HEADER: secret} if secret is not None else {}
    return await client.post("/telegram/webhook", json=update, headers=headers)


async def _link_token(client: AsyncClient, session: Session) -> str:
    response = await client.post(
        "/users/me/notification-channels/telegram/link", headers=bearer(session)
    )
    assert response.status_code == 201, response.text
    url: str = response.json()["url"]
    assert url.startswith("https://t.me/TestBot?start=")
    return url.split("start=", 1)[1]


async def _linked(client: AsyncClient, session: Session, chat_id: int = 111) -> None:
    token = await _link_token(client, session)
    assert (await _webhook(client, _update(f"/start {token}", chat_id))).status_code == 200


async def _telegram_channel(client: AsyncClient, session: Session) -> dict[str, Any]:
    items = (await client.get("/users/me/notification-channels", headers=bearer(session))).json()[
        "items"
    ]
    return next(i for i in items if i["channel"] == "telegram")


async def _replies(db: AsyncEngine) -> list[tuple[str, int]]:
    async with db.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT kind, (payload->>'chat_id')::bigint FROM notifications_outbox "
                "WHERE channel = 'telegram' ORDER BY created_at"
            )
        )
        return [(row[0], row[1]) for row in rows]


# ─── Webhook: secret_token ────────────────────────────────────────────────────


@pytest.mark.parametrize("secret", [None, "", "otro-secreto-0123456789", SECRET + "x", SECRET[:-1]])
async def test_webhook_rejects_a_missing_or_wrong_secret(
    client: AsyncClient, db_engine: AsyncEngine, secret: str | None
) -> None:
    user = await register(client)
    token = await _link_token(client, user)

    response = await _webhook(client, _update(f"/start {token}"), secret)

    assert_problem(response, 401, "invalid_webhook_secret")
    assert (await _telegram_channel(client, user))["linked"] is False
    assert await _replies(db_engine) == []


async def test_webhook_fails_closed_when_no_secret_is_configured(app_factory: AppFactory) -> None:
    app = app_factory(telegram_bot_username="TestBot", telegram_webhook_secret=None)
    async for client in make_client(app):
        assert_problem(await _webhook(client, _update("hola")), 401, "invalid_webhook_secret")
        assert_problem(await _webhook(client, _update("hola"), ""), 401, "invalid_webhook_secret")


async def test_webhook_validates_the_body_shape(client: AsyncClient) -> None:
    assert_problem(await _webhook(client, [1, 2]), 422, "validation_error")


@pytest.mark.parametrize(
    "update",
    [
        {},
        {"update_id": 1},
        {"update_id": 1, "message": "texto"},
        {"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}}},  # sin texto
        {"update_id": "x", "message": {"chat": {"id": 1, "type": "private"}, "text": "/stop"}},
        {"update_id": 1, "edited_message": {"text": "/start x"}},
    ],
)
async def test_webhook_ignores_what_it_does_not_understand(
    client: AsyncClient, db_engine: AsyncEngine, update: dict[str, Any]
) -> None:
    assert (await _webhook(client, update)).status_code == 200
    assert await _replies(db_engine) == []


# ─── Vinculación ──────────────────────────────────────────────────────────────


async def test_link_flow_links_the_chat_and_confirms_by_outbox(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    user = await register(client, role="caregiver")
    assert (await _telegram_channel(client, user))["linked"] is False
    token = await _link_token(client, user)

    response = await _webhook(client, _update(f"/start {token}", chat_id=777))

    assert response.status_code == 200
    channel = await _telegram_channel(client, user)
    assert (channel["linked"], channel["verified"], channel["label"]) == (True, True, "@ana_tg")
    assert channel["linkedAt"] is not None
    assert await _replies(db_engine) == [("telegram_linked", 777)]


async def test_link_token_is_single_use_and_expires(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    first, second = await register(client), await register(client)
    token = await _link_token(client, first)
    await _webhook(client, _update(f"/start {token}", chat_id=1))

    await _webhook(client, _update(f"/start {token}", chat_id=2))
    expired = await _link_token(client, second)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE account_tokens SET expires_at = now() - interval '1 second' "
                "WHERE used_at IS NULL"
            )
        )
    await _webhook(client, _update(f"/start {expired}", chat_id=3))

    assert [kind for kind, _ in await _replies(db_engine)] == [
        "telegram_linked",
        "telegram_link_failed",
        "telegram_link_failed",
    ]
    assert (await _telegram_channel(client, second))["linked"] is False


async def test_a_new_link_request_replaces_the_previous_token(client: AsyncClient) -> None:
    user = await register(client)
    old = await _link_token(client, user)
    new = await _link_token(client, user)

    await _webhook(client, _update(f"/start {old}"))
    assert (await _telegram_channel(client, user))["linked"] is False
    await _webhook(client, _update(f"/start {new}"))
    assert (await _telegram_channel(client, user))["linked"] is True


async def test_invalid_start_commands_get_the_failure_reply(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    await _webhook(client, _update("/start"))
    await _webhook(client, _update("/start token-que-no-existe-0123456789"))

    assert [kind for kind, _ in await _replies(db_engine)] == ["telegram_link_failed"] * 2


async def test_start_with_bot_suffix_and_group_chats(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    user = await register(client)
    token = await _link_token(client, user)

    await _webhook(client, _update(f"/start {token}", chat_id=5, chat_type="group"))
    assert (await _telegram_channel(client, user))["linked"] is False
    await _webhook(client, _update(f"/start@TestBot {token}", chat_id=5))
    assert (await _telegram_channel(client, user))["linked"] is True
    assert await _replies(db_engine) == [("telegram_linked", 5)]


async def test_telegram_redelivery_of_an_update_does_not_reply_twice(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    user = await register(client)
    token = await _link_token(client, user)
    update = _update(f"/start {token}")

    await _webhook(client, update)
    await _webhook(client, update)

    assert await _replies(db_engine) == [("telegram_linked", 111)]
    assert (await _telegram_channel(client, user))["linked"] is True


async def test_a_chat_belongs_to_one_user_and_a_user_to_one_chat(client: AsyncClient) -> None:
    ana, luis = await register(client), await register(client)
    await _linked(client, ana, chat_id=10)

    await _linked(client, luis, chat_id=10)  # mismo chat: pasa a Luis
    assert (await _telegram_channel(client, ana))["linked"] is False
    assert (await _telegram_channel(client, luis))["linked"] is True

    await _linked(client, luis, chat_id=20)  # otro chat: reemplaza al anterior
    assert (await _telegram_channel(client, luis))["linked"] is True


async def test_stop_unlinks_the_chat_and_unlink_is_idempotent(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    user = await register(client)
    await _linked(client, user, chat_id=30)

    await _webhook(client, _update("/stop", chat_id=30))
    assert (await _telegram_channel(client, user))["linked"] is False
    await _webhook(client, _update("/stop", chat_id=30))  # ya sin vínculo: no contesta de nuevo

    await _linked(client, user, chat_id=31)
    for _ in range(2):
        assert (
            await client.delete("/users/me/notification-channels/telegram", headers=bearer(user))
        ).status_code == 204
    assert (await _telegram_channel(client, user))["linked"] is False
    assert [kind for kind, _ in await _replies(db_engine)] == [
        "telegram_linked",
        "telegram_unlinked",
        "telegram_linked",
    ]


async def test_link_requires_a_configured_bot(app_factory: AppFactory) -> None:
    async for client in make_client(app_factory(telegram_bot_username=None)):
        user = await register(client)
        response = await client.post(
            "/users/me/notification-channels/telegram/link", headers=bearer(user)
        )
        assert_problem(response, 503, "telegram_not_configured")


async def test_link_requests_are_rate_limited(app_factory: AppFactory) -> None:
    app = app_factory(
        telegram_bot_username="TestBot",
        rate_telegram_link_user=RateLimit(limit=2, window_seconds=60),
    )
    async for client in make_client(app):
        user = await register(client)
        codes = [
            (
                await client.post(
                    "/users/me/notification-channels/telegram/link", headers=bearer(user)
                )
            ).status_code
            for _ in range(3)
        ]
        assert codes == [201, 201, 429]


# ─── Canales: correo y autenticación ──────────────────────────────────────────


async def test_channel_list_always_has_both_channels_with_a_masked_email(
    client: AsyncClient,
) -> None:
    email = unique_email("canal")
    user = await register(client, email=email)

    items = (await client.get("/users/me/notification-channels", headers=bearer(user))).json()[
        "items"
    ]

    assert [i["channel"] for i in items] == ["email", "telegram"]
    assert items[0]["linked"] is True
    assert items[0]["verified"] is False
    assert items[0]["label"] == f"{email[0]}***@example.com"
    assert items[0]["linkedAt"] is None
    assert items[1] == {
        "channel": "telegram",
        "linked": False,
        "verified": False,
        "label": None,
        "linkedAt": None,
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/users/me/notification-channels"),
        ("POST", "/users/me/notification-channels/telegram/link"),
        ("DELETE", "/users/me/notification-channels/telegram"),
        ("GET", "/users/me/notification-preferences"),
        ("PUT", "/users/me/notification-preferences"),
    ],
)
async def test_channel_routes_need_a_user_session(
    client: AsyncClient, method: str, path: str
) -> None:
    user = await register(client)
    pairing = (await client.post("/devices/pairing-codes", json={"model": "R"})).json()
    await client.post(
        f"/devices/pairing-codes/{pairing['code']}/confirm",
        headers=bearer(user),
        json={"patientId": user["user"]["patientId"]},
    )
    watch = (
        await client.post(
            "/devices/token", json={"grantType": "device_code", "deviceCode": pairing["deviceCode"]}
        )
    ).json()

    anonymous = await client.request(method, path)
    from_watch = await client.request(
        method, path, headers={"Authorization": f"Bearer {watch['accessToken']}"}
    )

    assert_problem(anonymous, 401, "missing_token")
    assert_problem(from_watch, 403, "wrong_token_type")


async def test_channels_are_per_user(client: AsyncClient) -> None:
    ana, luis = await register(client), await register(client)
    await _linked(client, ana)

    assert (await _telegram_channel(client, luis))["linked"] is False
    await client.delete("/users/me/notification-channels/telegram", headers=bearer(luis))
    assert (await _telegram_channel(client, ana))["linked"] is True


# ─── Preferencias ─────────────────────────────────────────────────────────────

ALL_ON = {
    "missedDose": {"email": True, "telegram": True},
    "weeklyReport": {"email": True, "telegram": True},
}


async def test_preferences_default_to_everything_on_and_are_replaced_whole(
    client: AsyncClient,
) -> None:
    user = await register(client)
    other = await register(client)
    url = "/users/me/notification-preferences"
    changed = {
        "missedDose": {"email": False, "telegram": True},
        "weeklyReport": {"email": True, "telegram": False},
    }

    assert (await client.get(url, headers=bearer(user))).json() == ALL_ON
    saved = await client.put(url, headers=bearer(user), json=changed)
    assert saved.json() == changed
    assert (await client.get(url, headers=bearer(user))).json() == changed
    assert (await client.put(url, headers=bearer(user), json=ALL_ON)).json() == ALL_ON
    assert (await client.get(url, headers=bearer(other))).json() == ALL_ON


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"missedDose": {"email": True, "telegram": True}},
        {"missedDose": {"email": True}, "weeklyReport": {"email": True, "telegram": True}},
        {"missedDose": {"email": "si", "telegram": True}, "weeklyReport": ALL_ON["weeklyReport"]},
    ],
)
async def test_preferences_validate_the_body(client: AsyncClient, body: dict[str, Any]) -> None:
    user = await register(client)

    response = await client.put(
        "/users/me/notification-preferences", headers=bearer(user), json=body
    )

    assert_problem(response, 422, "validation_error")


async def test_deleting_a_user_cascades_their_telegram_and_preferences(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    user = await register(client)
    await _linked(client, user)
    await client.put("/users/me/notification-preferences", headers=bearer(user), json=ALL_ON)

    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user["user"]["id"]})
        links = await conn.scalar(text("SELECT count(*) FROM telegram_links"))
        prefs = await conn.scalar(text("SELECT count(*) FROM notification_preferences"))

    assert (links, prefs) == (0, 0)
