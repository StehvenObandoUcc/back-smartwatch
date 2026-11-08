"""`DELETE /users/me`: derecho de supresión con borrado en cascada seguro."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import RateLimit
from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import (
    PASSWORD,
    REFRESH_COOKIE,
    assert_problem,
    bearer,
    register,
)

Session = dict[str, Any]


@pytest.fixture
async def client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    # Sin intervalo mínimo entre consultas del reloj (se vincula en varias pruebas).
    async for c in make_client(app_factory(pairing_poll_interval_seconds=0)):
        yield c


async def _delete(client: AsyncClient, user: Session, password: str = PASSWORD) -> Any:
    return await client.request(
        "DELETE", "/users/me", headers=bearer(user), json={"password": password}
    )


async def _scalar(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()


async def _managed_patient(client: AsyncClient, caregiver: Session) -> str:
    response = await client.post(
        "/patients",
        headers=bearer(caregiver),
        json={"displayName": "Abuela", "timezone": "America/Bogota"},
    )
    assert response.status_code == 201, response.text
    patient_id: str = response.json()["id"]
    return patient_id


async def _link(client: AsyncClient, inviter: Session, patient_id: str, other: Session) -> None:
    invitation = await client.post(
        f"/patients/{patient_id}/caregiver-invitations", headers=bearer(inviter)
    )
    code = invitation.json()["code"]
    accepted = await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(other))
    assert accepted.status_code == 201, accepted.text


async def _grant(client: AsyncClient, user: Session, patient_id: str, purpose: str) -> None:
    response = await client.put(
        f"/patients/{patient_id}/consents/{purpose}",
        headers=bearer(user),
        json={"granted": True, "version": "2026-09"},
    )
    assert response.status_code == 200, response.text


async def _pair_watch(client: AsyncClient, user: Session, patient_id: str) -> dict[str, str]:
    pairing = (await client.post("/devices/pairing-codes", json={"model": "Pixel Watch"})).json()
    confirm = await client.post(
        f"/devices/pairing-codes/{pairing['code']}/confirm",
        headers=bearer(user),
        json={"patientId": patient_id},
    )
    assert confirm.status_code == 200, confirm.text
    tokens = await client.post(
        "/devices/token", json={"grantType": "device_code", "deviceCode": pairing["deviceCode"]}
    )
    assert tokens.status_code == 200, tokens.text
    return {"Authorization": f"Bearer {tokens.json()['accessToken']}"}


async def test_patient_deletes_account_and_loses_session_and_data(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    patient = await register(client, role="patient")
    patient_id = patient["user"]["patientId"]
    user_id = patient["user"]["id"]
    cookie = client.cookies[REFRESH_COOKIE]
    await _grant(client, patient, patient_id, "health_data")
    medication = await client.post(
        f"/patients/{patient_id}/medications",
        headers=bearer(patient),
        json={"name": "Metformina", "dosage": "850 mg"},
    )
    assert medication.status_code == 201, medication.text

    response = await _delete(client, patient)

    assert response.status_code == 204
    assert response.content == b""
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert REFRESH_COOKIE in response.headers["set-cookie"]
    client.cookies.set(REFRESH_COOKIE, cookie, path="/auth")
    assert_problem(await client.post("/auth/refresh"), 401, "invalid_refresh_token")
    assert_problem(await client.get("/users/me", headers=bearer(patient)), 401, "invalid_token")
    login = await client.post(
        "/auth/login", json={"email": patient["user"]["email"], "password": PASSWORD}
    )
    assert_problem(login, 401, "invalid_credentials")
    assert await _scalar(db_engine, "SELECT count(*) FROM users WHERE id = :i", i=user_id) == 0
    assert await _scalar(db_engine, "SELECT count(*) FROM patients") == 0
    assert await _scalar(db_engine, "SELECT count(*) FROM medications") == 0
    assert await _scalar(db_engine, "SELECT count(*) FROM consent_events") == 0


async def test_patient_data_disappears_for_its_caregiver(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    caregiver = await register(client, role="caregiver")
    patient_id = patient["user"]["patientId"]
    await _link(client, patient, patient_id, caregiver)
    assert len((await client.get("/patients", headers=bearer(caregiver))).json()["items"]) == 1

    assert (await _delete(client, patient)).status_code == 204

    assert (await client.get("/patients", headers=bearer(caregiver))).json()["items"] == []
    gone = await client.get(f"/patients/{patient_id}", headers=bearer(caregiver))
    assert_problem(gone, 404, "patient_not_found")
    # El cuidador conserva su cuenta.
    assert (await client.get("/users/me", headers=bearer(caregiver))).status_code == 200


async def test_patient_with_watch_loses_watch_and_chat_usage(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    patient = await register(client, role="patient")
    other = await register(client, role="patient")
    patient_id = patient["user"]["patientId"]
    watch = await _pair_watch(client, patient, patient_id)
    device_id = (await client.get("/devices/me", headers=watch)).json()["id"]
    async with db_engine.begin() as conn:
        for principal in (patient["user"]["id"], device_id, other["user"]["id"]):
            await conn.execute(
                text("INSERT INTO chat_usage (principal_id, day, messages) VALUES (:p, now(), 3)"),
                {"p": principal},
            )

    assert (await _delete(client, patient)).status_code == 204

    assert_problem(await client.get("/devices/me", headers=watch), 401, "device_unpaired")
    assert await _scalar(db_engine, "SELECT count(*) FROM devices") == 0
    remaining = await _scalar(db_engine, "SELECT array_agg(principal_id::text) FROM chat_usage")
    assert remaining == [other["user"]["id"]]


async def test_sole_caregiver_deletes_managed_patient(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    caregiver = await register(client, role="caregiver")
    patient_id = await _managed_patient(client, caregiver)
    await _grant(client, caregiver, patient_id, "health_data")
    watch = await _pair_watch(client, caregiver, patient_id)
    device_id = (await client.get("/devices/me", headers=watch)).json()["id"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO chat_usage (principal_id, day, messages) VALUES (:p, now(), 1)"),
            {"p": device_id},
        )

    assert (await _delete(client, caregiver)).status_code == 204

    assert await _scalar(db_engine, "SELECT count(*) FROM patients") == 0
    assert await _scalar(db_engine, "SELECT count(*) FROM consent_events") == 0
    assert await _scalar(db_engine, "SELECT count(*) FROM chat_usage") == 0
    assert_problem(await client.get("/devices/me", headers=watch), 401, "device_unpaired")


async def test_managed_patient_with_other_caregiver_is_kept(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    leaving = await register(client, role="caregiver", display_name="Luis")
    staying = await register(client, role="caregiver", display_name="Marta")
    patient_id = await _managed_patient(client, leaving)
    await _link(client, leaving, patient_id, staying)
    await _grant(client, leaving, patient_id, "health_data")

    assert (await _delete(client, leaving)).status_code == 204

    patients = (await client.get("/patients", headers=bearer(staying))).json()["items"]
    assert [p["id"] for p in patients] == [patient_id]
    assert await _scalar(db_engine, "SELECT count(*) FROM care_links") == 1
    assert (
        await _scalar(
            db_engine,
            "SELECT created_by_user_id IS NULL FROM patients WHERE id = :p",
            p=patient_id,
        )
        is True
    )
    # El consentimiento sigue vigente y registrado, pero sin actor.
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM consent_events WHERE actor_user_id IS NULL AND granted",
        )
        == 1
    )
    consents = await client.get(f"/patients/{patient_id}/consents", headers=bearer(staying))
    assert consents.status_code == 200
    health = next(i for i in consents.json()["items"] if i["purpose"] == "health_data")
    assert health["granted"] is True
    assert health["grantedBy"] is None
    # Quien se queda sigue pudiendo decidir, y su decisión sí lleva autor.
    await _grant(client, staying, patient_id, "ai_chat")
    consents = await client.get(f"/patients/{patient_id}/consents", headers=bearer(staying))
    chat = next(i for i in consents.json()["items"] if i["purpose"] == "ai_chat")
    assert chat["grantedBy"]["displayName"] == "Marta"


async def test_inactive_link_does_not_keep_managed_patient_alive(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    first = await register(client, role="caregiver")
    second = await register(client, role="caregiver")
    patient_id = await _managed_patient(client, first)
    await _link(client, first, patient_id, second)
    revoke = await client.delete(
        f"/patients/{patient_id}/caregivers/{first['user']['id']}", headers=bearer(second)
    )
    assert revoke.status_code == 204

    # `second` es ahora el único cuidador activo: el paciente se borra con él.
    assert (await _delete(client, second)).status_code == 204

    assert await _scalar(db_engine, "SELECT count(*) FROM patients") == 0


async def test_wrong_password_is_forbidden_and_deletes_nothing(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    patient = await register(client, role="patient")

    response = await _delete(client, patient, password="incorrecta-123")

    assert_problem(response, 403, "invalid_password")
    assert (await client.get("/users/me", headers=bearer(patient))).status_code == 200
    assert await _scalar(db_engine, "SELECT count(*) FROM users") == 1


async def test_delete_requires_authentication(client: AsyncClient) -> None:
    response = await client.request("DELETE", "/users/me", json={"password": PASSWORD})

    assert_problem(response, 401, "missing_token")


@pytest.mark.parametrize("body", [None, {}, {"password": ""}, {"password": "x" * 129}])
async def test_delete_validates_body(client: AsyncClient, body: dict[str, Any] | None) -> None:
    patient = await register(client, role="patient")

    response = await client.request("DELETE", "/users/me", headers=bearer(patient), json=body)

    assert_problem(response, 422, "validation_error")


async def test_delete_is_rate_limited_before_checking_password(app_factory: AppFactory) -> None:
    app = app_factory(rate_delete_account_user=RateLimit(limit=1, window_seconds=60))
    async for client in make_client(app):
        patient = await register(client, role="patient")
        assert_problem(await _delete(client, patient, "mal-1234567"), 403, "invalid_password")

        # Segundo intento: 429 aunque ahora la contraseña sea la correcta.
        response = await _delete(client, patient)

        assert_problem(response, 429, "rate_limited")
        assert 0 < int(response.headers["retry-after"]) <= 60


async def test_pending_outbox_for_the_user_is_removed(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    user = await register(client, role="caregiver")
    other = await register(client, role="caregiver")
    email = user["user"]["email"]
    chat_id = 987654321
    async with db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO telegram_links (id, user_id, chat_id) VALUES (:i, :u, :c)"),
            {"i": uuid.uuid4(), "u": user["user"]["id"], "c": chat_id},
        )
        rows = [
            ("a", "email", "failed", f'{{"to": "{email.upper()}"}}'),
            ("b", "telegram", "pending", f'{{"chat_id": {chat_id}}}'),
            ("c", "email", "sent", "{}"),
            ("d", "telegram", "pending", '{"chat_id": 1}'),
        ]
        for key, channel, status, payload in rows:
            await conn.execute(
                text(
                    "INSERT INTO notifications_outbox"
                    " (id, dedupe_key, channel, kind, payload, status)"
                    " VALUES (:i, :k, :ch, 'missed_dose', CAST(:p AS jsonb), :s)"
                ),
                {"i": uuid.uuid4(), "k": key, "ch": channel, "p": payload, "s": status},
            )
    # El registro dejó además el correo de verificación pendiente de cada cuenta.
    pending_before = await _scalar(
        db_engine, "SELECT count(*) FROM notifications_outbox WHERE payload->>'to' = :e", e=email
    )
    assert pending_before == 1

    assert (await _delete(client, user)).status_code == 204

    keys = await _scalar(
        db_engine,
        "SELECT array_agg(dedupe_key ORDER BY dedupe_key) FILTER (WHERE true) "
        "FROM notifications_outbox WHERE dedupe_key IN ('a','b','c','d')",
    )
    assert keys == ["c", "d"]
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM notifications_outbox WHERE payload->>'to' = :e",
            e=email,
        )
        == 0
    )
    # El correo pendiente de otra cuenta no se toca.
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM notifications_outbox WHERE payload->>'to' = :e",
            e=other["user"]["email"],
        )
        == 1
    )
