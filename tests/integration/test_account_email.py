"""Verificación de correo y recuperación de contraseña (el correo se encola en el outbox)."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import RateLimit
from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import PASSWORD, assert_problem, bearer, register, unique_email

NEW_PASSWORD = "otra-contraseña-segura-456"


async def _outbox(db: AsyncEngine, kind: str, email: str) -> list[dict[str, Any]]:
    async with db.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT payload, channel, status FROM notifications_outbox "
                "WHERE kind = :kind AND payload->>'to' = :email ORDER BY created_at"
            ),
            {"kind": kind, "email": email},
        )
        return [dict(row._mapping) for row in rows]


def _token(message: dict[str, Any]) -> str:
    link: str = message["payload"]["link"]
    assert link.startswith("http://localhost:5173/")  # WEB_ORIGIN por defecto
    return link.split("token=", 1)[1]


async def _registered(client: AsyncClient, **kwargs: Any) -> tuple[dict[str, Any], str]:
    email = unique_email("cuenta")
    return await register(client, email=email, **kwargs), email


async def _verify(client: AsyncClient, db: AsyncEngine, email: str) -> None:
    token = _token((await _outbox(db, "verify_email", email))[-1])
    assert (await client.post("/auth/verify-email", json={"token": token})).status_code == 204


# ─── Verificación de correo ───────────────────────────────────────────────────


async def test_register_queues_verification_email(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    session, email = await _registered(client)

    messages = await _outbox(db_engine, "verify_email", email)

    assert session["user"]["emailVerified"] is False
    assert [(m["channel"], m["status"]) for m in messages] == [("email", "pending")]
    assert len(_token(messages[0])) >= 16


async def test_verify_email_marks_the_user_verified_once(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    session, email = await _registered(client)
    token = _token((await _outbox(db_engine, "verify_email", email))[0])

    first = await client.post("/auth/verify-email", json={"token": token})
    me = await client.get("/users/me", headers=bearer(session))
    again = await client.post("/auth/verify-email", json={"token": token})

    assert first.status_code == 204
    assert me.json()["emailVerified"] is True
    assert_problem(again, 400, "invalid_token")


async def test_verify_email_rejects_unknown_expired_and_wrong_purpose_tokens(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    _, email = await _registered(client)
    token = _token((await _outbox(db_engine, "verify_email", email))[0])
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE account_tokens SET expires_at = now() - interval '1 second'")
        )

    assert_problem(
        await client.post("/auth/verify-email", json={"token": "x" * 40}), 400, "invalid_token"
    )
    assert_problem(
        await client.post("/auth/verify-email", json={"token": token}), 400, "invalid_token"
    )
    assert_problem(
        await client.post("/auth/verify-email", json={"token": "corto"}), 422, "validation_error"
    )


async def test_resend_invalidates_the_previous_link(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    session, email = await _registered(client)
    old = _token((await _outbox(db_engine, "verify_email", email))[0])

    resend = await client.post("/auth/resend-verification", headers=bearer(session))
    messages = await _outbox(db_engine, "verify_email", email)

    assert resend.status_code == 202
    assert len(messages) == 2
    assert_problem(
        await client.post("/auth/verify-email", json={"token": old}), 400, "invalid_token"
    )
    assert (
        await client.post("/auth/verify-email", json={"token": _token(messages[1])})
    ).status_code == 204


async def test_resend_when_already_verified_sends_nothing(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    session, email = await _registered(client)
    await _verify(client, db_engine, email)

    response = await client.post("/auth/resend-verification", headers=bearer(session))

    assert response.status_code == 204
    assert len(await _outbox(db_engine, "verify_email", email)) == 1


async def test_resend_requires_a_user_session(client: AsyncClient) -> None:
    assert_problem(await client.post("/auth/resend-verification"), 401, "missing_token")


# ─── Recuperación de contraseña ───────────────────────────────────────────────


async def test_forgot_password_answers_the_same_for_any_email(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    _, verified = await _registered(client)
    await _verify(client, db_engine, verified)
    _, unverified = await _registered(client)
    ghost = unique_email("fantasma")

    responses = [
        await client.post("/auth/forgot-password", json={"email": email})
        for email in (verified, unverified, ghost)
    ]

    assert [r.status_code for r in responses] == [202, 202, 202]
    assert len({r.content for r in responses}) == 1
    assert len(await _outbox(db_engine, "reset_password", verified)) == 1
    # Sin cuenta o con el correo sin verificar no se envía nada.
    assert await _outbox(db_engine, "reset_password", unverified) == []
    assert await _outbox(db_engine, "reset_password", ghost) == []


async def test_reset_password_changes_it_and_closes_every_session(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    _, email = await _registered(client)
    await _verify(client, db_engine, email)
    # `register` dejó la cookie de refresh en el cliente: esa sesión debe caer con el cambio.
    await client.post("/auth/forgot-password", json={"email": email})
    token = _token((await _outbox(db_engine, "reset_password", email))[0])

    reset = await client.post(
        "/auth/reset-password", json={"token": token, "newPassword": NEW_PASSWORD}
    )

    assert reset.status_code == 204
    assert_problem(await client.post("/auth/refresh"), 401, "invalid_refresh_token")
    old = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    new = await client.post("/auth/login", json={"email": email, "password": NEW_PASSWORD})
    assert_problem(old, 401, "invalid_credentials")
    assert new.status_code == 200


async def test_reset_token_is_single_use_and_a_new_request_replaces_it(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    _, email = await _registered(client)
    await _verify(client, db_engine, email)
    await client.post("/auth/forgot-password", json={"email": email})
    await client.post("/auth/forgot-password", json={"email": email})
    first, second = [_token(m) for m in await _outbox(db_engine, "reset_password", email)]

    stale = await client.post(
        "/auth/reset-password", json={"token": first, "newPassword": NEW_PASSWORD}
    )
    used = await client.post(
        "/auth/reset-password", json={"token": second, "newPassword": NEW_PASSWORD}
    )
    reused = await client.post(
        "/auth/reset-password", json={"token": second, "newPassword": PASSWORD}
    )

    assert_problem(stale, 400, "invalid_token")
    assert used.status_code == 204
    assert_problem(reused, 400, "invalid_token")


async def test_verification_token_does_not_work_for_password_reset(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    _, email = await _registered(client)
    token = _token((await _outbox(db_engine, "verify_email", email))[0])

    response = await client.post(
        "/auth/reset-password", json={"token": token, "newPassword": NEW_PASSWORD}
    )

    assert_problem(response, 400, "invalid_token")


@pytest.mark.parametrize(
    "body",
    [
        {"token": "x" * 40, "newPassword": "corta"},
        {"token": "x" * 40},
        {"token": "corto", "newPassword": NEW_PASSWORD},
    ],
)
async def test_reset_password_validates_the_body(client: AsyncClient, body: dict[str, str]) -> None:
    assert_problem(await client.post("/auth/reset-password", json=body), 422, "validation_error")


async def test_reset_keeps_watch_sessions(client: AsyncClient, db_engine: AsyncEngine) -> None:
    session, email = await _registered(client)
    await _verify(client, db_engine, email)
    pairing = (await client.post("/devices/pairing-codes", json={"model": "Reloj"})).json()
    await client.post(
        f"/devices/pairing-codes/{pairing['code']}/confirm",
        headers=bearer(session),
        json={"patientId": session["user"]["patientId"]},
    )
    watch = (
        await client.post(
            "/devices/token",
            json={"grantType": "device_code", "deviceCode": pairing["deviceCode"]},
        )
    ).json()
    await client.post("/auth/forgot-password", json={"email": email})
    token = _token((await _outbox(db_engine, "reset_password", email))[0])
    await client.post("/auth/reset-password", json={"token": token, "newPassword": NEW_PASSWORD})

    renewed = await client.post(
        "/devices/token", json={"grantType": "refresh_token", "refreshToken": watch["refreshToken"]}
    )

    assert renewed.status_code == 200


async def test_forgot_password_is_rate_limited(app_factory: AppFactory) -> None:
    app = app_factory(rate_forgot_password_ip=RateLimit(limit=2, window_seconds=60))
    async for client in make_client(app):
        statuses = [
            (await client.post("/auth/forgot-password", json={"email": unique_email()})).status_code
            for _ in range(3)
        ]
        assert statuses == [202, 202, 429]
