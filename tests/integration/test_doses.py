"""Eventos de toma (lote idempotente), historial y adherencia."""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import assert_problem, bearer, register
from tests.integration.test_medications import (
    _grant_health,
    _medication,
    _patient,
    _plan,
    _schedule,
    _today,
    _watch,
)


@pytest.fixture
async def client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    async for c in make_client(app_factory(pairing_poll_interval_seconds=0)):
        yield c


def _event(dose: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "scheduleId": dose["scheduleId"],
        "scheduledAt": dose["scheduledAt"],
        "status": "TAKEN",
        "actedAt": dose["scheduledAt"],
        **overrides,
    }


async def _send(client: AsyncClient, watch: dict[str, str], *events: dict[str, Any]) -> Any:
    return await client.post(
        "/devices/me/dose-events", headers=watch, json={"events": list(events)}
    )


async def _setup(client: AsyncClient) -> tuple[Any, str, dict[str, str], dict[str, Any], list[Any]]:
    """Paciente con un horario diario 08:00/20:00, su reloj y las dosis del plan."""
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)
    schedule = await _schedule(client, owner, patient_id, medication["id"])
    watch = await _watch(client, owner, patient_id)
    doses = (await _plan(client, watch)).json()["doses"]
    return owner, patient_id, watch, schedule, doses


# ─── Idempotencia ─────────────────────────────────────────────────────────────


async def test_batch_is_idempotent_by_event_id(client: AsyncClient) -> None:
    owner, patient_id, watch, _, doses = await _setup(client)
    event = _event(doses[0])

    first = await _send(client, watch, event)
    again = await _send(client, watch, event)
    other_id_same_dose = await _send(client, watch, _event(doses[0], status="SKIPPED"))

    assert first.status_code == 200
    assert first.json()["results"] == [
        {"eventId": event["eventId"], "outcome": "created", "code": None}
    ]
    assert again.json()["results"][0]["outcome"] == "duplicate"
    assert other_id_same_dose.json()["results"][0] == {
        "eventId": other_id_same_dose.json()["results"][0]["eventId"],
        "outcome": "rejected",
        "code": "dose_already_recorded",
    }
    history = (
        await client.get(f"/patients/{patient_id}/dose-history", headers=bearer(owner))
    ).json()
    statuses = [i["status"] for i in history["items"]]
    assert statuses == ["TAKEN"]  # un solo evento: el segundo no se guardó


async def test_same_event_twice_in_one_batch_is_stored_once(client: AsyncClient) -> None:
    _, _, watch, _, doses = await _setup(client)
    event = _event(doses[1])

    response = await _send(client, watch, event, event)

    assert [r["outcome"] for r in response.json()["results"]] == ["created", "duplicate"]


async def test_replay_is_a_duplicate_even_after_the_schedule_changed(client: AsyncClient) -> None:
    owner, patient_id, watch, schedule, doses = await _setup(client)
    event = _event(doses[0])
    await _send(client, watch, event)
    await client.delete(
        f"/patients/{patient_id}/medications/{schedule['medicationId']}/schedules/{schedule['id']}",
        headers=bearer(owner),
    )

    replay = await _send(client, watch, event)

    assert replay.json()["results"][0]["outcome"] == "duplicate"


async def test_each_event_in_a_batch_is_resolved_on_its_own(client: AsyncClient) -> None:
    _, _, watch, _, doses = await _setup(client)
    other_owner, other_patient = await _patient(client)
    other_med = await _medication(client, other_owner, other_patient)
    other_schedule = await _schedule(client, other_owner, other_patient, other_med["id"])
    good = _event(doses[0])
    off_time = _event(
        doses[1], scheduledAt=doses[1]["scheduledAt"].replace(":00-05:00", ":01-05:00")
    )
    unknown = _event(doses[2], scheduleId=str(uuid.uuid4()))
    foreign = _event(doses[3], scheduleId=other_schedule["id"])

    response = await _send(client, watch, good, off_time, unknown, foreign)

    assert response.status_code == 200
    assert [(r["outcome"], r["code"]) for r in response.json()["results"]] == [
        ("created", None),
        ("rejected", "invalid_scheduled_at"),
        ("rejected", "schedule_not_found"),
        ("rejected", "schedule_not_found"),
    ]


async def test_event_instants_are_compared_across_utc_offsets(client: AsyncClient) -> None:
    _, _, watch, _, doses = await _setup(client)
    local = doses[0]["scheduledAt"]  # ...-05:00
    utc = f"{local[:10]}T{int(local[11:13]) + 5:02d}:00:00Z"

    response = await _send(client, watch, _event(doses[0], scheduledAt=utc))

    assert response.json()["results"][0]["outcome"] == "created"


# ─── Autorización y validación (reloj) ────────────────────────────────────────


async def test_dose_events_require_a_watch_token(client: AsyncClient) -> None:
    owner, _, _, _, doses = await _setup(client)

    assert_problem(
        await client.post("/devices/me/dose-events", json={"events": [_event(doses[0])]}),
        401,
        "missing_token",
    )
    assert_problem(await _send(client, bearer(owner), _event(doses[0])), 403, "wrong_token_type")


async def test_dose_events_require_health_consent(client: AsyncClient) -> None:
    owner, _, watch, _, doses = await _setup(client)
    await client.put(
        "/users/me/consents/health_data",
        headers=bearer(owner),
        json={"granted": False, "version": "v1"},
    )

    assert_problem(await _send(client, watch, _event(doses[0])), 403, "consent_required")
    await _grant_health(client, owner)
    assert (await _send(client, watch, _event(doses[0]))).status_code == 200


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: {"events": []},
        lambda e: {"events": [e] * 101},
        lambda e: {"events": [{**e, "status": "MISSED"}]},
        lambda e: {"events": [{**e, "scheduledAt": "2026-10-05T08:00:00"}]},  # sin zona horaria
        lambda e: {"events": [{**e, "eventId": "no-es-uuid"}]},
        lambda e: {"events": [{k: v for k, v in e.items() if k != "actedAt"}]},
    ],
)
async def test_dose_events_validate_the_body(client: AsyncClient, mutate: Any) -> None:
    _, _, watch, _, doses = await _setup(client)

    response = await client.post(
        "/devices/me/dose-events", headers=watch, json=mutate(_event(doses[0]))
    )

    assert_problem(response, 422, "validation_error")


# ─── Autorización (web) ───────────────────────────────────────────────────────

_WEB_ROUTES = ["/patients/{p}/dose-history", "/patients/{p}/adherence"]


@pytest.mark.parametrize("path", _WEB_ROUTES)
async def test_history_and_adherence_authorization(client: AsyncClient, path: str) -> None:
    owner, patient_id, watch, _, _ = await _setup(client)
    url = path.format(p=patient_id)
    stranger = await register(client, role="caregiver")

    assert (await client.get(url, headers=bearer(owner))).status_code == 200
    assert_problem(await client.get(url), 401, "missing_token")
    assert_problem(await client.get(url, headers=bearer(stranger)), 404, "patient_not_found")
    assert_problem(await client.get(url, headers=watch), 403, "wrong_token_type")


@pytest.mark.parametrize("path", _WEB_ROUTES)
async def test_history_and_adherence_require_health_consent(client: AsyncClient, path: str) -> None:
    owner, patient_id = await _patient(client, consent=False)

    response = await client.get(path.format(p=patient_id), headers=bearer(owner))

    assert_problem(response, 403, "consent_required")


async def test_linked_caregiver_reads_history(client: AsyncClient) -> None:
    owner, patient_id, _, _, _ = await _setup(client)
    caregiver = await register(client, role="caregiver")
    code = (
        await client.post(f"/patients/{patient_id}/caregiver-invitations", headers=bearer(owner))
    ).json()["code"]
    await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(caregiver))

    response = await client.get(f"/patients/{patient_id}/adherence", headers=bearer(caregiver))

    assert response.status_code == 200


# ─── Historial y adherencia ───────────────────────────────────────────────────


async def _past_setup(
    client: AsyncClient, db_engine: AsyncEngine, *, backdate: bool = True
) -> tuple[Any, str, dict[str, str], list[str]]:
    """Horario diario a las 12:00 desde hace 5 días; devuelve los días D-3, D-2 y D-1."""
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)
    today = _today()
    schedule = await _schedule(
        client,
        owner,
        patient_id,
        medication["id"],
        times=["12:00"],
        startDate=str(today - timedelta(days=5)),
    )
    if backdate:  # el horario "existía" hace 30 días: sus dosis pasadas cuentan como omitidas
        async with db_engine.begin() as conn:
            await conn.execute(
                text("UPDATE schedules SET effective_from = now() - interval '30 days'")
            )
    watch = await _watch(client, owner, patient_id)
    days = [str(today - timedelta(days=n)) for n in (3, 2, 1)]
    taken = {"scheduleId": schedule["id"], "scheduledAt": f"{days[0]}T12:00:00-05:00"}
    skipped = {"scheduleId": schedule["id"], "scheduledAt": f"{days[1]}T12:00:00-05:00"}
    response = await _send(client, watch, _event(taken), _event(skipped, status="SKIPPED"))
    assert [r["outcome"] for r in response.json()["results"]] == ["created", "created"]
    return owner, patient_id, watch, days


async def test_history_mixes_events_and_missed_doses(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner, patient_id, _, days = await _past_setup(client, db_engine)

    response = await client.get(
        f"/patients/{patient_id}/dose-history?from={days[0]}&to={days[2]}", headers=bearer(owner)
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(i["scheduledAt"][:10], i["status"]) for i in items] == [
        (days[2], "MISSED"),
        (days[1], "SKIPPED"),
        (days[0], "TAKEN"),
    ]
    assert items[0]["actedAt"] is None
    assert items[2]["actedAt"] is not None
    assert items[0]["medicationName"] == "Metformina"


async def test_adherence_percentage(client: AsyncClient, db_engine: AsyncEngine) -> None:
    owner, patient_id, _, days = await _past_setup(client, db_engine)

    response = await client.get(
        f"/patients/{patient_id}/adherence?from={days[0]}&to={days[2]}", headers=bearer(owner)
    )

    assert response.json() == {
        "from": days[0],
        "to": days[2],
        "taken": 1,
        "skipped": 1,
        "missed": 1,
        "percentage": 33.3,
    }


async def test_doses_before_the_schedule_was_created_are_not_missed(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner, patient_id, _, days = await _past_setup(client, db_engine, backdate=False)

    response = await client.get(
        f"/patients/{patient_id}/adherence?from={days[0]}&to={days[2]}", headers=bearer(owner)
    )

    body = response.json()
    assert (body["taken"], body["skipped"], body["missed"], body["percentage"]) == (1, 1, 0, 50.0)


async def test_adherence_without_expired_doses_is_null(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)

    response = await client.get(f"/patients/{patient_id}/adherence", headers=bearer(owner))

    body = response.json()
    assert (body["taken"], body["skipped"], body["missed"], body["percentage"]) == (0, 0, 0, None)
    assert body["to"] == str(_today())


async def test_history_is_paginated_by_cursor(client: AsyncClient, db_engine: AsyncEngine) -> None:
    owner, patient_id, _, days = await _past_setup(client, db_engine)
    url = f"/patients/{patient_id}/dose-history?from={days[0]}&to={days[2]}"

    first = (await client.get(url + "&limit=2", headers=bearer(owner))).json()
    second = (
        await client.get(url + f"&limit=2&cursor={first['nextCursor']}", headers=bearer(owner))
    ).json()

    assert [i["scheduledAt"][:10] for i in first["items"]] == [days[2], days[1]]
    assert [i["scheduledAt"][:10] for i in second["items"]] == [days[0]]
    assert second["nextCursor"] is None


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-10-05&to=2026-10-04",
        "from=2026-01-01&to=2026-10-04",  # más de 90 días
        "from=ayer",
        "cursor=no-es-cursor",
    ],
)
@pytest.mark.parametrize("path", _WEB_ROUTES)
async def test_range_and_cursor_validation(client: AsyncClient, path: str, query: str) -> None:
    owner, patient_id = await _patient(client)

    response = await client.get(f"{path.format(p=patient_id)}?{query}", headers=bearer(owner))

    if "cursor" in query and "adherence" in path:
        assert response.status_code == 200  # adherence no pagina
    else:
        assert_problem(response, 422, "validation_error")
