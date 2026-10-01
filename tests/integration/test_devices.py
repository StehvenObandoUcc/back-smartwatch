import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import RateLimit
from app.modules.auth.models import RefreshToken
from app.modules.devices.models import PairingCode
from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import REFRESH_COOKIE, assert_problem, bearer, register

Session = dict[str, Any]


@pytest.fixture
async def client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    """Sin intervalo mínimo entre consultas, salvo en las pruebas de slow_down."""
    async for c in make_client(app_factory(pairing_poll_interval_seconds=0)):
        yield c


async def _pairing(client: AsyncClient) -> dict[str, Any]:
    response = await client.post("/devices/pairing-codes", json={"model": "Pixel Watch 3"})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _token(client: AsyncClient, device_code: str) -> Any:
    return await client.post(
        "/devices/token", json={"grantType": "device_code", "deviceCode": device_code}
    )


async def _paired_watch(client: AsyncClient, user: Session, patient_id: str) -> dict[str, Any]:
    pairing = await _pairing(client)
    confirm = await client.post(
        f"/devices/pairing-codes/{pairing['code']}/confirm",
        headers=bearer(user),
        json={"patientId": patient_id},
    )
    assert confirm.status_code == 200, confirm.text
    tokens = await _token(client, pairing["deviceCode"])
    assert tokens.status_code == 200, tokens.text
    return {"device": confirm.json(), **tokens.json()}


def _watch(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['accessToken']}"}


async def _refresh_watch(client: AsyncClient, refresh_token: str) -> Any:
    return await client.post(
        "/devices/token", json={"grantType": "refresh_token", "refreshToken": refresh_token}
    )


# ─── Flujo de vinculación ─────────────────────────────────────────────────────


async def test_full_pairing_flow(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    patient_id = patient["user"]["patientId"]

    pairing = await _pairing(client)
    assert re.fullmatch(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}", pairing["code"])
    assert pairing["expiresIn"] == 600
    assert_problem(await _token(client, pairing["deviceCode"]), 400, "authorization_pending")

    confirm = await client.post(
        f"/devices/pairing-codes/{pairing['code'].lower()}/confirm",
        headers=bearer(patient),
        json={"patientId": patient_id},
    )
    assert confirm.status_code == 200
    assert confirm.json()["patientId"] == patient_id
    assert confirm.json()["model"] == "Pixel Watch 3"

    tokens = await _token(client, pairing["deviceCode"])
    assert tokens.status_code == 200
    assert set(tokens.json()) == {"accessToken", "refreshToken", "tokenType", "expiresIn"}

    me = await client.get("/devices/me", headers=_watch(tokens.json()))
    assert me.status_code == 200
    assert me.json()["id"] == confirm.json()["id"]
    assert me.json()["lastSeenAt"] is not None
    # El device code es de un solo uso.
    assert_problem(await _token(client, pairing["deviceCode"]), 400, "expired_token")


async def test_polling_too_fast_gets_slow_down(app_factory: AppFactory) -> None:
    async for client in make_client(app_factory(pairing_poll_interval_seconds=5)):
        pairing = await _pairing(client)
        assert pairing["interval"] == 5

        assert_problem(await _token(client, pairing["deviceCode"]), 400, "authorization_pending")
        slow = await _token(client, pairing["deviceCode"])

        assert_problem(slow, 400, "slow_down")
        assert "10 s" in slow.json()["detail"]  # el intervalo sube 5 s


async def test_expired_pairing_code(client: AsyncClient, db_engine: AsyncEngine) -> None:
    patient = await register(client, role="patient")
    pairing = await _pairing(client)
    async with db_engine.begin() as conn:
        await conn.execute(
            update(PairingCode).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    confirm = await client.post(
        f"/devices/pairing-codes/{pairing['code']}/confirm",
        headers=bearer(patient),
        json={"patientId": patient["user"]["patientId"]},
    )

    assert_problem(confirm, 404, "pairing_code_not_found")
    assert_problem(await _token(client, pairing["deviceCode"]), 400, "expired_token")


async def test_unknown_device_code_is_expired(client: AsyncClient) -> None:
    assert_problem(await _token(client, "inventado"), 400, "expired_token")


@pytest.mark.parametrize("code", ["BCDF-GHJK", "no-es-un-codigo", "AAAA-AAAA"])
async def test_confirm_unknown_or_malformed_code_is_404(client: AsyncClient, code: str) -> None:
    patient = await register(client, role="patient")

    response = await client.post(
        f"/devices/pairing-codes/{code}/confirm",
        headers=bearer(patient),
        json={"patientId": patient["user"]["patientId"]},
    )

    assert_problem(response, 404, "pairing_code_not_found")


async def test_confirmed_code_cannot_be_confirmed_again(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    body = {"patientId": patient["user"]["patientId"]}
    pairing = await _pairing(client)
    path = f"/devices/pairing-codes/{pairing['code']}/confirm"

    assert (await client.post(path, headers=bearer(patient), json=body)).status_code == 200
    assert_problem(
        await client.post(path, headers=bearer(patient), json=body), 404, "pairing_code_not_found"
    )


async def test_confirm_for_unlinked_patient_is_404(client: AsyncClient) -> None:
    other = await register(client, role="patient")
    caregiver = await register(client, role="caregiver")
    pairing = await _pairing(client)

    response = await client.post(
        f"/devices/pairing-codes/{pairing['code']}/confirm",
        headers=bearer(caregiver),
        json={"patientId": other["user"]["patientId"]},
    )

    assert_problem(response, 404, "patient_not_found")


async def test_linked_caregiver_pairs_watch_for_managed_patient(client: AsyncClient) -> None:
    caregiver = await register(client, role="caregiver")
    created = await client.post(
        "/patients", headers=bearer(caregiver), json={"displayName": "Abuela", "timezone": "UTC"}
    )
    patient_id = created.json()["id"]

    watch = await _paired_watch(client, caregiver, patient_id)

    assert watch["device"]["patientId"] == patient_id


@pytest.mark.parametrize(
    "body", [{"grantType": "password"}, {"grantType": "device_code"}, {"deviceCode": "x"}]
)
async def test_token_request_is_validated(client: AsyncClient, body: dict[str, str]) -> None:
    assert_problem(await client.post("/devices/token", json=body), 422, "validation_error")


# ─── Un solo reloj activo por paciente ────────────────────────────────────────


async def test_new_watch_replaces_previous_one(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    patient_id = patient["user"]["patientId"]
    old = await _paired_watch(client, patient, patient_id)

    new = await _paired_watch(client, patient, patient_id)

    assert_problem(await client.get("/devices/me", headers=_watch(old)), 401, "device_unpaired")
    assert_problem(await _refresh_watch(client, old["refreshToken"]), 401, "invalid_refresh_token")
    assert (await client.get("/devices/me", headers=_watch(new))).status_code == 200
    listed = (await client.get(f"/patients/{patient_id}/devices", headers=bearer(patient))).json()
    assert [d["id"] for d in listed["items"]] == [new["device"]["id"]]


# ─── Tokens del reloj ─────────────────────────────────────────────────────────


async def test_watch_refresh_rotates_and_detects_reuse(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    watch = await _paired_watch(client, patient, patient["user"]["patientId"])

    rotated = await _refresh_watch(client, watch["refreshToken"])
    assert rotated.status_code == 200
    assert rotated.json()["refreshToken"] != watch["refreshToken"]
    assert (await client.get("/devices/me", headers=_watch(rotated.json()))).status_code == 200

    reused = await _refresh_watch(client, watch["refreshToken"])
    assert_problem(reused, 401, "refresh_token_reused")
    assert_problem(
        await _refresh_watch(client, rotated.json()["refreshToken"]), 401, "invalid_refresh_token"
    )


async def test_watch_refresh_slides_90_days_and_expires_when_idle(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    patient = await register(client, role="patient")
    watch = await _paired_watch(client, patient, patient["user"]["patientId"])
    rotated = (await _refresh_watch(client, watch["refreshToken"])).json()

    async with db_engine.connect() as conn:
        expires = (
            await conn.execute(
                select(RefreshToken.expires_at).where(
                    RefreshToken.device_id == uuid.UUID(watch["device"]["id"]),
                    RefreshToken.rotated_at.is_(None),
                )
            )
        ).scalar_one()
    assert timedelta(days=89) < expires - datetime.now(UTC) <= timedelta(days=90)

    async with db_engine.begin() as conn:
        await conn.execute(
            update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert_problem(
        await _refresh_watch(client, rotated["refreshToken"]), 401, "invalid_refresh_token"
    )


async def test_refresh_tokens_do_not_cross_between_web_and_watch(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    web_refresh = client.cookies[REFRESH_COOKIE]
    watch = await _paired_watch(client, patient, patient["user"]["patientId"])

    assert_problem(await _refresh_watch(client, web_refresh), 401, "invalid_refresh_token")
    client.cookies.set(REFRESH_COOKIE, watch["refreshToken"], path="/auth")
    assert_problem(await client.post("/auth/refresh"), 401, "invalid_refresh_token")


async def test_watch_token_only_works_on_devices_me(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    patient_id = patient["user"]["patientId"]
    watch = await _paired_watch(client, patient, patient_id)

    for method, path in [
        ("GET", "/users/me"),
        ("GET", "/patients"),
        ("GET", f"/patients/{patient_id}"),
        ("GET", f"/patients/{patient_id}/devices"),
    ]:
        response = await client.request(method, path, headers=_watch(watch))
        assert_problem(response, 403, "wrong_token_type")


async def test_user_token_is_rejected_on_devices_me(client: AsyncClient) -> None:
    patient = await register(client, role="patient")

    assert_problem(
        await client.get("/devices/me", headers=bearer(patient)), 403, "wrong_token_type"
    )
    assert_problem(
        await client.put("/devices/me/push-token", headers=bearer(patient), json={"token": "t"}),
        403,
        "wrong_token_type",
    )


async def test_watch_registers_push_token(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    watch = await _paired_watch(client, patient, patient["user"]["patientId"])
    assert watch["device"]["hasPushToken"] is False

    response = await client.put(
        "/devices/me/push-token", headers=_watch(watch), json={"token": "fcm-token-123"}
    )

    assert response.status_code == 204
    me = await client.get("/devices/me", headers=_watch(watch))
    assert me.json()["hasPushToken"] is True


# ─── Gestión desde la web ─────────────────────────────────────────────────────


async def test_unpair_revokes_watch_immediately(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    watch = await _paired_watch(client, patient, patient["user"]["patientId"])
    device_id = watch["device"]["id"]

    response = await client.delete(f"/devices/{device_id}", headers=bearer(patient))

    assert response.status_code == 204
    assert_problem(await client.get("/devices/me", headers=_watch(watch)), 401, "device_unpaired")
    assert_problem(
        await _refresh_watch(client, watch["refreshToken"]), 401, "invalid_refresh_token"
    )
    assert (
        await client.delete(f"/devices/{device_id}", headers=bearer(patient))
    ).status_code == 204


@pytest.mark.parametrize("target", ["other", str(uuid.uuid4()), "no-es-uuid"])
async def test_unpair_unlinked_or_unknown_device_is_404(client: AsyncClient, target: str) -> None:
    owner = await register(client, role="patient")
    stranger = await register(client, role="caregiver")
    watch = await _paired_watch(client, owner, owner["user"]["patientId"])
    device_id = watch["device"]["id"] if target == "other" else target

    response = await client.delete(f"/devices/{device_id}", headers=bearer(stranger))

    assert_problem(response, 404, "device_not_found")


async def test_list_devices_of_unlinked_patient_is_404(client: AsyncClient) -> None:
    owner = await register(client, role="patient")
    stranger = await register(client, role="caregiver")

    response = await client.get(
        f"/patients/{owner['user']['patientId']}/devices", headers=bearer(stranger)
    )

    assert_problem(response, 404, "patient_not_found")


# ─── Límites en los códigos ───────────────────────────────────────────────────


@pytest.fixture
async def strict_client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    app = app_factory(
        pairing_poll_interval_seconds=0,
        rate_pairing_create_ip=RateLimit(limit=2, window_seconds=3600),
        rate_pairing_confirm_user=RateLimit(limit=2, window_seconds=900),
        rate_device_token_ip=RateLimit(limit=2, window_seconds=60),
    )
    async for c in make_client(app):
        yield c


async def test_pairing_code_creation_is_rate_limited(strict_client: AsyncClient) -> None:
    for _ in range(2):
        await _pairing(strict_client)

    response = await strict_client.post("/devices/pairing-codes", json={"model": "x"})

    assert_problem(response, 429, "rate_limited")
    assert int(response.headers["retry-after"]) > 0


async def test_pairing_code_guessing_is_rate_limited(strict_client: AsyncClient) -> None:
    patient = await register(strict_client, role="patient")
    body = {"patientId": patient["user"]["patientId"]}
    for _ in range(2):
        guess = await strict_client.post(
            "/devices/pairing-codes/BCDF-GHJK/confirm", headers=bearer(patient), json=body
        )
        assert guess.status_code == 404

    response = await strict_client.post(
        "/devices/pairing-codes/BCDF-GHJK/confirm", headers=bearer(patient), json=body
    )

    assert_problem(response, 429, "rate_limited")


async def test_device_token_is_rate_limited(strict_client: AsyncClient) -> None:
    for _ in range(2):
        await _token(strict_client, "inventado")

    assert_problem(await _token(strict_client, "inventado"), 429, "rate_limited")
