"""Medicamentos, horarios y plan: autorización, consentimiento, generación y ETag."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import assert_problem, bearer, register

Session = dict[str, Any]
BOGOTA = ZoneInfo("America/Bogota")


@pytest.fixture
async def client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    async for c in make_client(app_factory(pairing_poll_interval_seconds=0)):
        yield c


def _today() -> Any:
    return datetime.now(BOGOTA).date()


async def _grant_health(client: AsyncClient, user: Session) -> None:
    response = await client.put(
        "/users/me/consents/health_data",
        headers=bearer(user),
        json={"granted": True, "version": "v1"},
    )
    assert response.status_code == 200, response.text


async def _patient(client: AsyncClient, consent: bool = True) -> tuple[Session, str]:
    user = await register(client, role="patient")
    if consent:
        await _grant_health(client, user)
    return user, user["user"]["patientId"]


async def _medication(
    client: AsyncClient, user: Session, patient_id: str, **extra: Any
) -> dict[str, Any]:
    response = await client.post(
        f"/patients/{patient_id}/medications",
        headers=bearer(user),
        json={"name": "Metformina", "dosage": "1 tableta", **extra},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _schedule(
    client: AsyncClient, user: Session, patient_id: str, medication_id: str, **overrides: Any
) -> dict[str, Any]:
    body = {
        "times": ["08:00", "20:00"],
        "daysOfWeek": [1, 2, 3, 4, 5, 6, 7],
        "startDate": str(_today() - timedelta(days=1)),
        **overrides,
    }
    response = await client.post(
        f"/patients/{patient_id}/medications/{medication_id}/schedules",
        headers=bearer(user),
        json=body,
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def _watch(client: AsyncClient, user: Session, patient_id: str) -> dict[str, str]:
    pairing = (await client.post("/devices/pairing-codes", json={"model": "Pixel Watch"})).json()
    confirm = await client.post(
        f"/devices/pairing-codes/{pairing['code']}/confirm",
        headers=bearer(user),
        json={"patientId": patient_id},
    )
    assert confirm.status_code == 200, confirm.text
    token = await client.post(
        "/devices/token", json={"grantType": "device_code", "deviceCode": pairing["deviceCode"]}
    )
    assert token.status_code == 200, token.text
    return {"Authorization": f"Bearer {token.json()['accessToken']}"}


async def _plan(client: AsyncClient, headers: dict[str, str]) -> Any:
    return await client.get("/devices/me/plan", headers=headers)


# ─── Autorización ─────────────────────────────────────────────────────────────

_PATIENT_ROUTES = [
    ("GET", "/patients/{p}/medications", None),
    ("POST", "/patients/{p}/medications", {"name": "X", "dosage": "1"}),
    ("GET", "/patients/{p}/medications/{m}", None),
    ("PATCH", "/patients/{p}/medications/{m}", {"name": "Y"}),
    ("DELETE", "/patients/{p}/medications/{m}", None),
    ("GET", "/patients/{p}/medications/{m}/schedules", None),
    (
        "POST",
        "/patients/{p}/medications/{m}/schedules",
        {"times": ["08:00"], "daysOfWeek": [1], "startDate": "2026-10-05"},
    ),
    ("PATCH", "/patients/{p}/medications/{m}/schedules/{s}", {"times": ["09:00"]}),
    ("DELETE", "/patients/{p}/medications/{m}/schedules/{s}", None),
    ("GET", "/patients/{p}/plan", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _PATIENT_ROUTES)
async def test_unlinked_user_gets_404_and_anonymous_gets_401(
    client: AsyncClient, method: str, path: str, body: Any
) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)
    schedule = await _schedule(client, owner, patient_id, medication["id"])
    url = path.format(p=patient_id, m=medication["id"], s=schedule["id"])
    stranger = await register(client, role="caregiver")

    anonymous = await client.request(method, url, json=body)
    denied = await client.request(method, url, headers=bearer(stranger), json=body)

    assert_problem(anonymous, 401, "missing_token")
    assert_problem(denied, 404, "patient_not_found")


@pytest.mark.parametrize(("method", "path", "body"), _PATIENT_ROUTES)
async def test_watch_token_is_rejected_on_user_routes(
    client: AsyncClient, method: str, path: str, body: Any
) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)
    schedule = await _schedule(client, owner, patient_id, medication["id"])
    watch = await _watch(client, owner, patient_id)
    url = path.format(p=patient_id, m=medication["id"], s=schedule["id"])

    response = await client.request(method, url, headers=watch, json=body)

    assert_problem(response, 403, "wrong_token_type")


async def test_user_token_is_rejected_on_watch_plan(client: AsyncClient) -> None:
    owner, _ = await _patient(client)

    assert_problem(await _plan(client, bearer(owner)), 403, "wrong_token_type")
    assert_problem(await client.get("/devices/me/plan"), 401, "missing_token")


async def test_resources_of_another_patient_are_404(client: AsyncClient) -> None:
    owner_a, patient_a = await _patient(client)
    owner_b, patient_b = await _patient(client)
    medication = await _medication(client, owner_a, patient_a)
    schedule = await _schedule(client, owner_a, patient_a, medication["id"])
    own = await _medication(client, owner_b, patient_b)

    cross = await client.get(
        f"/patients/{patient_b}/medications/{medication['id']}", headers=bearer(owner_b)
    )
    wrong_schedule = await client.patch(
        f"/patients/{patient_b}/medications/{own['id']}/schedules/{schedule['id']}",
        headers=bearer(owner_b),
        json={"times": ["09:00"]},
    )

    assert_problem(cross, 404, "medication_not_found")
    assert_problem(wrong_schedule, 404, "schedule_not_found")


@pytest.mark.parametrize("bad_id", ["no-es-uuid", "123"])
async def test_malformed_ids_are_404(client: AsyncClient, bad_id: str) -> None:
    owner, patient_id = await _patient(client)

    response = await client.get(
        f"/patients/{patient_id}/medications/{bad_id}", headers=bearer(owner)
    )

    assert_problem(response, 404, "medication_not_found")


async def test_linked_caregiver_manages_medications_of_managed_patient(client: AsyncClient) -> None:
    caregiver = await register(client, role="caregiver")
    created = await client.post(
        "/patients",
        headers=bearer(caregiver),
        json={"displayName": "Abuela", "timezone": "America/Bogota"},
    )
    patient_id = created.json()["id"]
    await client.put(
        f"/patients/{patient_id}/consents/health_data",
        headers=bearer(caregiver),
        json={"granted": True, "version": "v1"},
    )

    medication = await _medication(client, caregiver, patient_id)
    await _schedule(client, caregiver, patient_id, medication["id"])
    plan = await client.get(f"/patients/{patient_id}/plan", headers=bearer(caregiver))

    assert plan.status_code == 200
    assert len(plan.json()["doses"]) == 14


async def test_revoked_caregiver_loses_access_to_medications(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    caregiver = await register(client, role="caregiver")
    code = (
        await client.post(f"/patients/{patient_id}/caregiver-invitations", headers=bearer(owner))
    ).json()["code"]
    await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(caregiver))
    url = f"/patients/{patient_id}/medications"
    assert (await client.get(url, headers=bearer(caregiver))).status_code == 200

    await client.delete(
        f"/patients/{patient_id}/caregivers/{caregiver['user']['id']}", headers=bearer(owner)
    )

    assert_problem(await client.get(url, headers=bearer(caregiver)), 404, "patient_not_found")


# ─── Consentimiento health_data ───────────────────────────────────────────────


async def test_health_consent_is_required_for_user_and_watch(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client, consent=False)
    watch = await _watch(client, owner, patient_id)

    listed = await client.get(f"/patients/{patient_id}/medications", headers=bearer(owner))
    created = await client.post(
        f"/patients/{patient_id}/medications",
        headers=bearer(owner),
        json={"name": "X", "dosage": "1"},
    )
    agenda = await client.get(f"/patients/{patient_id}/plan", headers=bearer(owner))

    for response in (listed, created, agenda, await _plan(client, watch)):
        assert_problem(response, 403, "consent_required")


async def test_withdrawing_consent_blocks_again(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    await client.put(
        "/users/me/consents/health_data",
        headers=bearer(owner),
        json={"granted": False, "version": "v1"},
    )

    response = await client.get(f"/patients/{patient_id}/medications", headers=bearer(owner))

    assert_problem(response, 403, "consent_required")


# ─── CRUD ─────────────────────────────────────────────────────────────────────


async def test_medication_crud_and_archive(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id, instructions="Con comida")
    url = f"/patients/{patient_id}/medications/{medication['id']}"
    assert medication["color"] == "#3B82F6"
    assert medication["archived"] is False

    patched = await client.patch(
        url,
        headers=bearer(owner),
        json={"dosage": "2 tabletas", "instructions": None, "color": "#112233"},
    )
    assert patched.status_code == 200
    assert patched.json()["dosage"] == "2 tabletas"
    assert patched.json()["instructions"] is None
    assert patched.json()["color"] == "#112233"

    for _ in range(2):  # idempotente
        assert (await client.delete(url, headers=bearer(owner))).status_code == 204
    active = await client.get(f"/patients/{patient_id}/medications", headers=bearer(owner))
    everything = await client.get(
        f"/patients/{patient_id}/medications?includeArchived=true", headers=bearer(owner)
    )
    assert active.json()["items"] == []
    assert everything.json()["items"][0]["archived"] is True


@pytest.mark.parametrize(
    "body",
    [{}, {"name": None}, {"name": "  "}, {"color": "azul"}, {"name": "x" * 121}],
)
async def test_medication_patch_validation(client: AsyncClient, body: dict[str, Any]) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)

    response = await client.patch(
        f"/patients/{patient_id}/medications/{medication['id']}", headers=bearer(owner), json=body
    )

    assert_problem(response, 422, "validation_error")


async def test_medications_are_paginated_by_cursor(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    for index in range(3):
        await _medication(client, owner, patient_id, name=f"Med {index}")
    url = f"/patients/{patient_id}/medications"

    first = (await client.get(url + "?limit=2", headers=bearer(owner))).json()
    second = (
        await client.get(url + f"?limit=2&cursor={first['nextCursor']}", headers=bearer(owner))
    ).json()

    assert [m["name"] for m in first["items"]] == ["Med 0", "Med 1"]
    assert [m["name"] for m in second["items"]] == ["Med 2"]
    assert second["nextCursor"] is None


async def test_schedule_validation_rules(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)
    base = f"/patients/{patient_id}/medications/{medication['id']}/schedules"
    today = _today()

    backwards = await client.post(
        base,
        headers=bearer(owner),
        json={
            "times": ["08:00"],
            "daysOfWeek": [1],
            "startDate": str(today),
            "endDate": str(today - timedelta(days=1)),
        },
    )
    assert_problem(backwards, 422, "validation_error")

    schedule = await _schedule(client, owner, patient_id, medication["id"], startDate=str(today))
    patch_url = f"{base}/{schedule['id']}"
    before_start = await client.patch(
        patch_url, headers=bearer(owner), json={"endDate": str(today - timedelta(days=3))}
    )
    assert_problem(before_start, 422, "validation_error")

    for _ in range(9):
        await _schedule(client, owner, patient_id, medication["id"])
    eleventh = await client.post(
        base,
        headers=bearer(owner),
        json={"times": ["08:00"], "daysOfWeek": [1], "startDate": str(today)},
    )
    assert_problem(eleventh, 422, "validation_error")


# ─── Plan: generación, versión y ETag ─────────────────────────────────────────


async def test_plan_for_daily_schedule_covers_seven_days(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)
    await _schedule(client, owner, patient_id, medication["id"])
    watch = await _watch(client, owner, patient_id)

    response = await _plan(client, watch)

    assert response.status_code == 200
    plan = response.json()
    assert plan["patientTimezone"] == "America/Bogota"
    assert len(plan["doses"]) == 14
    first = plan["doses"][0]
    assert first["medicationName"] == "Metformina"
    assert first["scheduleId"]
    assert first["scheduledAt"].endswith("-05:00")
    instants = [dose["scheduledAt"] for dose in plan["doses"]]
    assert instants == sorted(instants)


async def test_plan_respects_weekdays_and_end_date(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)
    today = _today()
    await _schedule(client, owner, patient_id, medication["id"], daysOfWeek=[today.isoweekday()])
    ending = await _medication(client, owner, patient_id, name="Corto")
    await _schedule(
        client,
        owner,
        patient_id,
        ending["id"],
        startDate=str(today),
        endDate=str(today + timedelta(days=2)),
    )

    plan = (await client.get(f"/patients/{patient_id}/plan", headers=bearer(owner))).json()

    by_name: dict[str, int] = {}
    for dose in plan["doses"]:
        by_name[dose["medicationName"]] = by_name.get(dose["medicationName"], 0) + 1
    assert by_name == {"Metformina": 2, "Corto": 6}


async def test_archived_medication_leaves_the_plan(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    medication = await _medication(client, owner, patient_id)
    await _schedule(client, owner, patient_id, medication["id"])
    await client.delete(
        f"/patients/{patient_id}/medications/{medication['id']}", headers=bearer(owner)
    )

    plan = (await client.get(f"/patients/{patient_id}/plan", headers=bearer(owner))).json()

    assert plan["doses"] == []


async def test_plan_version_and_etag_follow_every_change(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    watch = await _watch(client, owner, patient_id)
    empty = await _plan(client, watch)
    assert empty.json()["version"] == 0

    medication = await _medication(client, owner, patient_id)
    schedule = await _schedule(client, owner, patient_id, medication["id"])
    current = await _plan(client, watch)
    etag = current.headers["etag"]
    assert current.json()["version"] == 2
    assert etag != empty.headers["etag"]

    not_modified = await client.get("/devices/me/plan", headers={**watch, "If-None-Match": etag})
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert not_modified.headers["etag"] == etag

    base = f"/patients/{patient_id}/medications/{medication['id']}"
    changes = [
        client.patch(base, headers=bearer(owner), json={"name": "Otra"}),
        client.patch(
            f"{base}/schedules/{schedule['id']}", headers=bearer(owner), json={"times": ["09:00"]}
        ),
        client.delete(f"{base}/schedules/{schedule['id']}", headers=bearer(owner)),
        client.delete(base, headers=bearer(owner)),
    ]
    for offset, change in enumerate(changes, start=1):
        assert (await change).status_code in (200, 204)
        after = await client.get("/devices/me/plan", headers={**watch, "If-None-Match": etag})
        assert after.status_code == 200
        assert after.json()["version"] == 2 + offset
        etag = after.headers["etag"]

    # Archivar otra vez no cambia nada: sigue el mismo validador.
    await client.delete(base, headers=bearer(owner))
    again = await client.get("/devices/me/plan", headers={**watch, "If-None-Match": etag})
    assert again.status_code == 304


async def test_unpaired_watch_cannot_read_plan(client: AsyncClient) -> None:
    owner, patient_id = await _patient(client)
    watch = await _watch(client, owner, patient_id)
    device_id = (await client.get("/devices/me", headers=watch)).json()["id"]
    await client.delete(f"/devices/{device_id}", headers=bearer(owner))

    assert_problem(await _plan(client, watch), 401, "device_unpaired")
