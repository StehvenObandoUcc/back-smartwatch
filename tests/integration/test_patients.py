import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import RateLimit
from app.modules.patients.models import CaregiverInvitation
from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import assert_problem, bearer, register

Session = dict[str, Any]


async def _managed_patient(client: AsyncClient, caregiver: Session, name: str = "Abuela") -> str:
    response = await client.post(
        "/patients",
        headers=bearer(caregiver),
        json={"displayName": name, "timezone": "America/Bogota"},
    )
    assert response.status_code == 201, response.text
    patient_id: str = response.json()["id"]
    return patient_id


async def _invite(client: AsyncClient, inviter: Session, patient_id: str) -> str:
    response = await client.post(
        f"/patients/{patient_id}/caregiver-invitations", headers=bearer(inviter)
    )
    assert response.status_code == 201, response.text
    code: str = response.json()["code"]
    return code


async def _link(client: AsyncClient, inviter: Session, patient_id: str, caregiver: Session) -> None:
    code = await _invite(client, inviter, patient_id)
    response = await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(caregiver))
    assert response.status_code == 201, response.text


# ─── Registro como paciente ───────────────────────────────────────────────────


async def test_patient_registration_creates_own_patient_record(client: AsyncClient) -> None:
    session = await register(client, role="patient", display_name="Ana", timezone="Europe/Madrid")
    patient_id = session["user"]["patientId"]

    assert patient_id is not None
    me = await client.get("/users/me", headers=bearer(session))
    assert me.json()["patientId"] == patient_id
    patient = (await client.get(f"/patients/{patient_id}", headers=bearer(session))).json()
    assert patient["displayName"] == "Ana"
    assert patient["timezone"] == "Europe/Madrid"
    assert patient["managed"] is False
    listed = (await client.get("/patients", headers=bearer(session))).json()
    assert [p["id"] for p in listed["items"]] == [patient_id]


async def test_patient_own_consents_are_the_patient_record_consents(client: AsyncClient) -> None:
    session = await register(client, role="patient", display_name="Ana")
    patient_id = session["user"]["patientId"]

    await client.put(
        "/users/me/consents/health_data",
        headers=bearer(session),
        json={"granted": True, "version": "v1"},
    )

    via_patient = (
        await client.get(f"/patients/{patient_id}/consents", headers=bearer(session))
    ).json()["items"]
    health = next(item for item in via_patient if item["purpose"] == "health_data")
    assert health["granted"] is True
    assert health["grantedBy"]["onBehalf"] is False


# ─── Pacientes gestionados ────────────────────────────────────────────────────


async def test_caregiver_creates_managed_patient(client: AsyncClient) -> None:
    caregiver = await register(client, role="caregiver")

    response = await client.post(
        "/patients",
        headers=bearer(caregiver),
        json={"displayName": "Abuela", "timezone": "America/Bogota"},
    )

    assert response.status_code == 201
    assert response.json()["managed"] is True
    caregivers = await client.get(
        f"/patients/{response.json()['id']}/caregivers", headers=bearer(caregiver)
    )
    assert [c["caregiverId"] for c in caregivers.json()["items"]] == [caregiver["user"]["id"]]


async def test_patient_role_cannot_create_managed_patients(client: AsyncClient) -> None:
    patient = await register(client, role="patient")

    response = await client.post(
        "/patients", headers=bearer(patient), json={"displayName": "X", "timezone": "UTC"}
    )

    assert_problem(response, 403, "caregivers_only")


@pytest.mark.parametrize("timezone", ["Marte/Olympus", "", "America/../etc"])
async def test_managed_patient_requires_iana_timezone(client: AsyncClient, timezone: str) -> None:
    caregiver = await register(client, role="caregiver")

    response = await client.post(
        "/patients", headers=bearer(caregiver), json={"displayName": "X", "timezone": timezone}
    )

    assert_problem(response, 422, "validation_error")


async def test_list_patients_is_paginated_by_cursor(client: AsyncClient) -> None:
    caregiver = await register(client, role="caregiver")
    created = [await _managed_patient(client, caregiver, f"P{i}") for i in range(3)]

    first = (await client.get("/patients?limit=2", headers=bearer(caregiver))).json()
    second = (
        await client.get(
            f"/patients?limit=2&cursor={first['nextCursor']}", headers=bearer(caregiver)
        )
    ).json()

    assert [p["id"] for p in first["items"] + second["items"]] == created
    assert second["nextCursor"] is None


@pytest.mark.parametrize("query", ["cursor=%%%", "cursor=bm9wZQ", "limit=0", "limit=101"])
async def test_list_patients_rejects_bad_pagination(client: AsyncClient, query: str) -> None:
    caregiver = await register(client, role="caregiver")

    response = await client.get(f"/patients?{query}", headers=bearer(caregiver))

    assert_problem(response, 422, "validation_error")


# ─── 404 para pacientes sin vínculo ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/patients/{id}", None),
        ("PATCH", "/patients/{id}", {"displayName": "X"}),
        ("GET", "/patients/{id}/consents", None),
        ("PUT", "/patients/{id}/consents/health_data", {"granted": True, "version": "v1"}),
        ("GET", "/patients/{id}/caregivers", None),
        ("DELETE", "/patients/{id}/caregivers/" + str(uuid.uuid4()), None),
        ("POST", "/patients/{id}/caregiver-invitations", None),
    ],
)
async def test_unlinked_caregiver_gets_404(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    owner = await register(client, role="caregiver")
    stranger = await register(client, role="caregiver")
    patient_id = await _managed_patient(client, owner)

    response = await client.request(
        method, path.format(id=patient_id), headers=bearer(stranger), json=body
    )

    assert_problem(response, 404, "patient_not_found")


async def test_unlinked_patient_is_not_listed(client: AsyncClient) -> None:
    owner = await register(client, role="caregiver")
    stranger = await register(client, role="caregiver")
    await _managed_patient(client, owner)

    listed = (await client.get("/patients", headers=bearer(stranger))).json()

    assert listed == {"items": [], "nextCursor": None}


@pytest.mark.parametrize("bad_id", ["no-es-uuid", "123"])
async def test_malformed_patient_id_is_404(client: AsyncClient, bad_id: str) -> None:
    caregiver = await register(client, role="caregiver")

    response = await client.get(f"/patients/{bad_id}", headers=bearer(caregiver))

    assert_problem(response, 404, "patient_not_found")


async def test_revoked_caregiver_loses_access(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    caregiver = await register(client, role="caregiver")
    patient_id = patient["user"]["patientId"]
    await _link(client, patient, patient_id, caregiver)
    assert (
        await client.get(f"/patients/{patient_id}", headers=bearer(caregiver))
    ).status_code == 200

    revoked = await client.delete(
        f"/patients/{patient_id}/caregivers/{caregiver['user']['id']}", headers=bearer(patient)
    )

    assert revoked.status_code == 204
    assert_problem(
        await client.get(f"/patients/{patient_id}", headers=bearer(caregiver)),
        404,
        "patient_not_found",
    )


# ─── 403 por rol sobre pacientes con cuenta ───────────────────────────────────


async def test_caregiver_cannot_edit_patient_with_account(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    caregiver = await register(client, role="caregiver")
    patient_id = patient["user"]["patientId"]
    await _link(client, patient, patient_id, caregiver)

    edit = await client.patch(
        f"/patients/{patient_id}", headers=bearer(caregiver), json={"displayName": "X"}
    )
    consent = await client.put(
        f"/patients/{patient_id}/consents/ai_chat",
        headers=bearer(caregiver),
        json={"granted": True, "version": "v1"},
    )
    invite = await client.post(
        f"/patients/{patient_id}/caregiver-invitations", headers=bearer(caregiver)
    )

    for response in (edit, consent, invite):
        assert_problem(response, 403, "patient_has_account")
    # Pero sí puede leer.
    assert (
        await client.get(f"/patients/{patient_id}/consents", headers=bearer(caregiver))
    ).status_code == 200


async def test_owner_edits_own_patient(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    patient_id = patient["user"]["patientId"]

    response = await client.patch(
        f"/patients/{patient_id}", headers=bearer(patient), json={"timezone": "Asia/Tokyo"}
    )

    assert response.status_code == 200
    assert response.json()["timezone"] == "Asia/Tokyo"


async def test_patch_patient_validates_body(client: AsyncClient) -> None:
    caregiver = await register(client, role="caregiver")
    patient_id = await _managed_patient(client, caregiver)

    for body in ({}, {"timezone": None}):
        response = await client.patch(
            f"/patients/{patient_id}", headers=bearer(caregiver), json=body
        )
        assert_problem(response, 422, "validation_error")


# ─── Consentimientos en nombre del paciente gestionado ────────────────────────


async def test_caregiver_grants_consent_on_behalf_of_managed_patient(client: AsyncClient) -> None:
    caregiver = await register(client, role="caregiver", display_name="Luis")
    patient_id = await _managed_patient(client, caregiver)

    response = await client.put(
        f"/patients/{patient_id}/consents/health_data",
        headers=bearer(caregiver),
        json={"granted": True, "version": "2026-09"},
    )

    assert response.status_code == 200
    assert response.json()["grantedBy"] == {
        "userId": caregiver["user"]["id"],
        "displayName": "Luis",
        "onBehalf": True,
    }
    listed = (
        await client.get(f"/patients/{patient_id}/consents", headers=bearer(caregiver))
    ).json()["items"]
    assert next(i for i in listed if i["purpose"] == "health_data")["granted"] is True
    # Los consentimientos propios del cuidador no cambian.
    own = (await client.get("/users/me/consents", headers=bearer(caregiver))).json()["items"]
    assert all(item["granted"] is False for item in own)


# ─── Invitaciones ─────────────────────────────────────────────────────────────


async def test_invitation_links_caregiver_once(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    caregiver = await register(client, role="caregiver", display_name="Luis")
    patient_id = patient["user"]["patientId"]
    code = await _invite(client, patient, patient_id)

    accepted = await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(caregiver))
    reused = await client.post(
        f"/caregiver-invitations/{code}/accept",
        headers=bearer(await register(client, role="caregiver")),
    )

    assert accepted.status_code == 201
    assert accepted.json()["caregiverDisplayName"] == "Luis"
    assert accepted.json()["active"] is True
    assert_problem(reused, 404, "invitation_not_found")


async def test_invitation_expires_after_72_hours(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    patient = await register(client, role="patient")
    caregiver = await register(client, role="caregiver")
    response = await client.post(
        f"/patients/{patient['user']['patientId']}/caregiver-invitations", headers=bearer(patient)
    )
    expires_at = datetime.fromisoformat(response.json()["expiresAt"])
    assert timedelta(hours=71) < expires_at - datetime.now(UTC) <= timedelta(hours=72)

    async with db_engine.begin() as conn:
        await conn.execute(
            update(CaregiverInvitation).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    expired = await client.post(
        f"/caregiver-invitations/{response.json()['code']}/accept", headers=bearer(caregiver)
    )
    assert_problem(expired, 404, "invitation_not_found")


@pytest.mark.parametrize("code", ["ABCDEFGHJK", "corto", "ABCDEFGHJ0"])
async def test_unknown_or_malformed_invitation_is_404(client: AsyncClient, code: str) -> None:
    caregiver = await register(client, role="caregiver")

    response = await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(caregiver))

    assert_problem(response, 404, "invitation_not_found")


async def test_patient_cannot_accept_invitations(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    other = await register(client, role="patient")
    code = await _invite(client, patient, patient["user"]["patientId"])

    response = await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(other))

    assert_problem(response, 403, "caregivers_only")


async def test_accepting_when_already_linked_is_conflict(client: AsyncClient) -> None:
    caregiver = await register(client, role="caregiver")
    patient_id = await _managed_patient(client, caregiver)
    code = await _invite(client, caregiver, patient_id)

    response = await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(caregiver))

    assert_problem(response, 409, "already_linked")


@pytest.fixture
async def strict_client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    app = app_factory(
        rate_invitation_patient=RateLimit(limit=2, window_seconds=86400),
        rate_invitation_accept_user=RateLimit(limit=3, window_seconds=900),
    )
    async for c in make_client(app):
        yield c


async def test_invitation_creation_is_rate_limited_per_patient(strict_client: AsyncClient) -> None:
    patient = await register(strict_client, role="patient")
    patient_id = patient["user"]["patientId"]
    for _ in range(2):
        await _invite(strict_client, patient, patient_id)

    response = await strict_client.post(
        f"/patients/{patient_id}/caregiver-invitations", headers=bearer(patient)
    )

    assert_problem(response, 429, "rate_limited")
    assert int(response.headers["retry-after"]) > 0


async def test_invitation_guessing_is_rate_limited(strict_client: AsyncClient) -> None:
    caregiver = await register(strict_client, role="caregiver")
    for _ in range(3):
        guess = await strict_client.post(
            "/caregiver-invitations/ABCDEFGHJK/accept", headers=bearer(caregiver)
        )
        assert guess.status_code == 404

    response = await strict_client.post(
        "/caregiver-invitations/ABCDEFGHJK/accept", headers=bearer(caregiver)
    )

    assert_problem(response, 429, "rate_limited")


# ─── Revocación de vínculos ───────────────────────────────────────────────────


async def test_last_caregiver_of_managed_patient_cannot_leave(client: AsyncClient) -> None:
    caregiver = await register(client, role="caregiver")
    patient_id = await _managed_patient(client, caregiver)

    response = await client.delete(
        f"/patients/{patient_id}/caregivers/{caregiver['user']['id']}", headers=bearer(caregiver)
    )

    assert_problem(response, 409, "last_caregiver")


async def test_managed_patient_caregivers_can_revoke_each_other(client: AsyncClient) -> None:
    first = await register(client, role="caregiver")
    second = await register(client, role="caregiver")
    patient_id = await _managed_patient(client, first)
    await _link(client, first, patient_id, second)

    removed = await client.delete(
        f"/patients/{patient_id}/caregivers/{first['user']['id']}", headers=bearer(second)
    )
    again = await client.delete(
        f"/patients/{patient_id}/caregivers/{first['user']['id']}", headers=bearer(second)
    )
    last = await client.delete(
        f"/patients/{patient_id}/caregivers/{second['user']['id']}", headers=bearer(second)
    )

    assert removed.status_code == 204
    assert again.status_code == 204  # idempotente
    assert_problem(last, 409, "last_caregiver")


async def test_caregiver_leaves_patient_with_account(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    caregiver = await register(client, role="caregiver")
    patient_id = patient["user"]["patientId"]
    await _link(client, patient, patient_id, caregiver)

    response = await client.delete(
        f"/patients/{patient_id}/caregivers/{caregiver['user']['id']}", headers=bearer(caregiver)
    )

    assert response.status_code == 204


async def test_caregiver_cannot_revoke_others_on_patient_with_account(
    client: AsyncClient,
) -> None:
    patient = await register(client, role="patient")
    first = await register(client, role="caregiver")
    second = await register(client, role="caregiver")
    patient_id = patient["user"]["patientId"]
    await _link(client, patient, patient_id, first)
    await _link(client, patient, patient_id, second)

    response = await client.delete(
        f"/patients/{patient_id}/caregivers/{second['user']['id']}", headers=bearer(first)
    )

    assert_problem(response, 403, "patient_has_account")


@pytest.mark.parametrize("target", [str(uuid.uuid4()), "no-es-uuid"])
async def test_revoking_unknown_caregiver_is_404(client: AsyncClient, target: str) -> None:
    patient = await register(client, role="patient")

    response = await client.delete(
        f"/patients/{patient['user']['patientId']}/caregivers/{target}", headers=bearer(patient)
    )

    assert_problem(response, 404, "care_link_not_found")


async def test_relinking_reactivates_revoked_link(client: AsyncClient) -> None:
    patient = await register(client, role="patient")
    caregiver = await register(client, role="caregiver")
    patient_id = patient["user"]["patientId"]
    await _link(client, patient, patient_id, caregiver)
    await client.delete(
        f"/patients/{patient_id}/caregivers/{caregiver['user']['id']}", headers=bearer(patient)
    )

    await _link(client, patient, patient_id, caregiver)

    assert (
        await client.get(f"/patients/{patient_id}", headers=bearer(caregiver))
    ).status_code == 200
