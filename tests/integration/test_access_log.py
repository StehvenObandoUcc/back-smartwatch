"""Registro de accesos a datos de salud (`health_data_access_log`, ADR 0012)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import PASSWORD, assert_problem, bearer, register
from tests.integration.notify_helpers import consent
from tests.integration.test_chat import ScriptedProvider
from tests.integration.test_medications import _medication, _watch

Session = dict[str, Any]

_COLUMNS = "actor_type, actor_id::text, patient_id::text, action, resource, method, status"


@pytest.fixture
async def client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    app = app_factory(pairing_poll_interval_seconds=0)
    app.state.chat_provider = ScriptedProvider()
    async for c in make_client(app):
        yield c


async def _rows(engine: AsyncEngine, where: str = "true") -> list[tuple[Any, ...]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(f"SELECT {_COLUMNS} FROM health_data_access_log WHERE {where} ORDER BY created_at")  # noqa: S608
        )
        return [tuple(row) for row in result]


async def _patient(client: AsyncClient) -> tuple[Session, str]:
    owner = await register(client, role="patient")
    await consent(client, owner, "health_data")
    return owner, owner["user"]["patientId"]


async def _link_caregiver(client: AsyncClient, owner: Session, patient_id: str) -> Session:
    caregiver = await register(client, role="caregiver")
    invitation = await client.post(
        f"/patients/{patient_id}/caregiver-invitations", headers=bearer(owner)
    )
    accepted = await client.post(
        f"/caregiver-invitations/{invitation.json()['code']}/accept", headers=bearer(caregiver)
    )
    assert accepted.status_code == 201, accepted.text
    return caregiver


async def test_caregiver_read_and_write_are_recorded(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner, patient_id = await _patient(client)
    caregiver = await _link_caregiver(client, owner, patient_id)
    caregiver_id = caregiver["user"]["id"]

    listed = await client.get(f"/patients/{patient_id}/medications", headers=bearer(caregiver))
    assert listed.status_code == 200
    created = await _medication(client, caregiver, patient_id)

    rows = await _rows(db_engine, f"actor_id = '{caregiver_id}' AND resource = 'medications'")
    assert rows == [
        ("user", caregiver_id, patient_id, "read", "medications", "GET", 200),
        ("user", caregiver_id, patient_id, "write", "medications", "POST", 201),
    ]
    assert created["id"]


async def test_request_id_is_recorded(client: AsyncClient, db_engine: AsyncEngine) -> None:
    owner, patient_id = await _patient(client)

    response = await client.get(
        f"/patients/{patient_id}/medications",
        headers={**bearer(owner), "X-Request-ID": "peticion-123"},
    )

    assert response.headers["x-request-id"] == "peticion-123"
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT request_id FROM health_data_access_log WHERE resource = 'medications'")
        )
        assert list(result.scalars()) == ["peticion-123"]


async def test_schedules_and_patient_resources(client: AsyncClient, db_engine: AsyncEngine) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)

    await client.get(f"/patients/{patient_id}", headers=bearer(owner))
    await client.get(
        f"/patients/{patient_id}/medications/{medication['id']}/schedules", headers=bearer(owner)
    )
    await client.get(f"/patients/{patient_id}/adherence", headers=bearer(owner))

    resources = [row[4] for row in await _rows(db_engine, "action = 'read'")]
    assert resources == ["patient", "schedules", "adherence"]


async def test_inaccessible_patient_is_not_recorded(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    _, patient_id = await _patient(client)
    stranger = await register(client, role="caregiver")
    before = len(await _rows(db_engine))

    response = await client.get(f"/patients/{patient_id}/medications", headers=bearer(stranger))

    assert_problem(response, 404, "patient_not_found")
    assert len(await _rows(db_engine)) == before


async def test_watch_access_is_recorded_as_device(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner, patient_id = await _patient(client)
    watch = await _watch(client, owner, patient_id)
    device_id = (await client.get("/devices/me", headers=watch)).json()["id"]

    plan = await client.get("/devices/me/plan", headers=watch)

    assert plan.status_code == 200
    # `/devices/me` no es un recurso de salud: solo queda el acceso al plan.
    assert await _rows(db_engine, "actor_type = 'device'") == [
        ("device", device_id, patient_id, "read", "plan", "GET", 200)
    ]


async def test_web_chat_stream_is_recorded(client: AsyncClient, db_engine: AsyncEngine) -> None:
    owner, patient_id = await _patient(client)
    await consent(client, owner, "ai_chat")

    response = await client.post(
        f"/patients/{patient_id}/chat/messages", headers=bearer(owner), json={"message": "Hola"}
    )

    assert response.status_code == 200
    assert "event: done" in response.text
    rows = await _rows(db_engine, "resource = 'chat'")
    assert [(r[0], r[3], r[5], r[6]) for r in rows] == [("user", "write", "POST", 200)]


async def test_non_health_routes_are_not_recorded(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner = await register(client, role="patient")

    assert (await client.get("/users/me", headers=bearer(owner))).status_code == 200
    assert (await client.get("/patients", headers=bearer(owner))).status_code == 200
    assert (await client.get("/health")).status_code == 200

    assert await _rows(db_engine) == []


async def test_disabled_audit_records_nothing(
    app_factory: AppFactory, db_engine: AsyncEngine
) -> None:
    async for client in make_client(app_factory(audit_enabled=False)):
        owner, patient_id = await _patient(client)
        response = await client.get(f"/patients/{patient_id}/medications", headers=bearer(owner))
        assert response.status_code == 200

    assert await _rows(db_engine) == []


async def test_audit_failure_does_not_break_the_response(
    app_factory: AppFactory, db_engine: AsyncEngine
) -> None:
    async for client in make_client(app_factory()):
        owner, patient_id = await _patient(client)
        async with db_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE health_data_access_log RENAME TO audit_tmp"))
        try:
            response = await client.get(
                f"/patients/{patient_id}/medications", headers=bearer(owner)
            )
        finally:
            async with db_engine.begin() as conn:
                await conn.execute(text("ALTER TABLE audit_tmp RENAME TO health_data_access_log"))

        assert response.status_code == 200


async def test_trail_survives_account_deletion(client: AsyncClient, db_engine: AsyncEngine) -> None:
    owner, patient_id = await _patient(client)
    await client.get(f"/patients/{patient_id}/medications", headers=bearer(owner))
    owner_id = owner["user"]["id"]
    before = await _rows(db_engine, f"actor_id = '{owner_id}'")
    assert before

    deleted = await client.request(
        "DELETE", "/users/me", headers=bearer(owner), json={"password": PASSWORD}
    )

    assert deleted.status_code == 204
    assert await _rows(db_engine, f"actor_id = '{owner_id}'") == before
