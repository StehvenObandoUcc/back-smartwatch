import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.security import AccessClaims, TokenType
from tests.integration.helpers import assert_problem, bearer, register


def _device_token(app: FastAPI) -> dict[str, str]:
    token = app.state.access_tokens.issue(
        AccessClaims(subject=uuid.uuid4(), token_type=TokenType.DEVICE, patient_id=uuid.uuid4()),
        datetime.now(UTC),
    )
    return {"Authorization": f"Bearer {token}"}


# ─── Autenticación y tipo de token ────────────────────────────────────────────


async def test_me_requires_token(client: AsyncClient) -> None:
    response = await client.get("/users/me")

    assert_problem(response, 401, "missing_token")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_rejects_invalid_token(client: AsyncClient) -> None:
    response = await client.get("/users/me", headers={"Authorization": "Bearer no.es.jwt"})

    assert_problem(response, 401, "invalid_token")


async def test_me_rejects_expired_token(integration_app: FastAPI, client: AsyncClient) -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC)
    token = integration_app.state.access_tokens.issue(
        AccessClaims(subject=uuid.uuid4(), token_type=TokenType.USER, role="patient"), old
    )

    response = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

    assert_problem(response, 401, "invalid_token")


async def test_device_token_is_rejected_outside_devices_me(
    integration_app: FastAPI, client: AsyncClient
) -> None:
    for method, path in [("GET", "/users/me"), ("GET", "/users/me/consents")]:
        response = await client.request(method, path, headers=_device_token(integration_app))
        assert_problem(response, 403, "wrong_token_type")


# ─── Perfil ───────────────────────────────────────────────────────────────────


async def test_get_and_update_me(client: AsyncClient) -> None:
    session = await register(client, display_name="Ana")

    response = await client.patch(
        "/users/me",
        headers=bearer(session),
        json={"displayName": "Ana María", "timezone": "Europe/Madrid"},
    )

    assert response.status_code == 200
    assert response.json()["displayName"] == "Ana María"
    assert response.json()["timezone"] == "Europe/Madrid"
    me = await client.get("/users/me", headers=bearer(session))
    assert me.json()["displayName"] == "Ana María"


@pytest.mark.parametrize(
    "payload", [{}, {"displayName": None}, {"timezone": "No/Existe"}, {"locale": "en"}]
)
async def test_update_me_validates(client: AsyncClient, payload: dict[str, object]) -> None:
    session = await register(client)

    response = await client.patch("/users/me", headers=bearer(session), json=payload)

    assert_problem(response, 422, "validation_error")


# ─── Consentimientos propios ──────────────────────────────────────────────────


async def test_consents_default_to_not_granted(client: AsyncClient) -> None:
    session = await register(client, role="caregiver")

    response = await client.get("/users/me/consents", headers=bearer(session))

    assert response.status_code == 200
    assert response.json()["items"] == [
        {"purpose": p, "granted": False, "version": None, "updatedAt": None, "grantedBy": None}
        for p in ("health_data", "ai_chat", "notifications")
    ]


async def test_set_consent_records_self_as_actor_and_keeps_latest(client: AsyncClient) -> None:
    session = await register(client, role="caregiver", display_name="Luis")
    headers = bearer(session)

    await client.put(
        "/users/me/consents/ai_chat", headers=headers, json={"granted": True, "version": "v1"}
    )
    response = await client.put(
        "/users/me/consents/ai_chat", headers=headers, json={"granted": False, "version": "v2"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["granted"] is False
    assert body["version"] == "v2"
    assert body["grantedBy"] == {
        "userId": session["user"]["id"],
        "displayName": "Luis",
        "onBehalf": False,
    }
    listed = (await client.get("/users/me/consents", headers=headers)).json()["items"]
    ai_chat = next(item for item in listed if item["purpose"] == "ai_chat")
    assert ai_chat["granted"] is False
    assert ai_chat["version"] == "v2"


async def test_set_consent_rejects_unknown_purpose(client: AsyncClient) -> None:
    session = await register(client)

    response = await client.put(
        "/users/me/consents/marketing",
        headers=bearer(session),
        json={"granted": True, "version": "v1"},
    )

    assert_problem(response, 422, "validation_error")
