"""Escenario común de las pruebas de alertas y reportes: paciente con cuenta, cuidador y horario."""

import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.helpers import bearer, register, unique_email

Session = dict[str, Any]

_chat_ids = itertools.count(9000, 2)  # el chat de Telegram es único por usuario

# Miércoles 2026-10-07 09:30 en Bogotá (UTC-5): la dosis de las 08:00 lleva 90 minutos de retraso.
NOW = datetime(2026, 10, 7, 14, 30, tzinfo=UTC)
# Lunes 2026-10-12 08:00 en Bogotá: llega el reporte de la semana 05-11 de octubre.
MONDAY_8AM = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)


@dataclass
class Scenario:
    owner: Session
    caregiver: Session
    patient_id: str
    caregiver_email: str
    owner_email: str
    medication_id: str
    schedule_id: str
    caregiver_chat: int
    owner_chat: int


async def consent(client: AsyncClient, user: Session, purpose: str, granted: bool = True) -> None:
    response = await client.put(
        f"/users/me/consents/{purpose}",
        headers=bearer(user),
        json={"granted": granted, "version": "v1"},
    )
    assert response.status_code == 200, response.text


async def make_scenario(
    client: AsyncClient,
    db: AsyncEngine,
    *,
    verified: bool = True,
    telegram: bool = True,
    notifications_consent: bool = True,
    time: str = "08:00",
) -> Scenario:
    owner_email, caregiver_email = unique_email("paciente"), unique_email("cuidador")
    caregiver_chat = next(_chat_ids)
    owner_chat = caregiver_chat + 1
    owner = await register(client, role="patient", email=owner_email, display_name="Ana")
    patient_id = owner["user"]["patientId"]
    await consent(client, owner, "health_data")
    await consent(client, owner, "notifications")

    caregiver = await register(client, role="caregiver", email=caregiver_email, display_name="Luis")
    if notifications_consent:
        await consent(client, caregiver, "notifications")
    code = (
        await client.post(f"/patients/{patient_id}/caregiver-invitations", headers=bearer(owner))
    ).json()["code"]
    accepted = await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(caregiver))
    assert accepted.status_code == 201, accepted.text

    medication = (
        await client.post(
            f"/patients/{patient_id}/medications",
            headers=bearer(owner),
            json={"name": "Metformina", "dosage": "1 tableta"},
        )
    ).json()
    schedule = (
        await client.post(
            f"/patients/{patient_id}/medications/{medication['id']}/schedules",
            headers=bearer(owner),
            json={
                "times": [time],
                "daysOfWeek": [1, 2, 3, 4, 5, 6, 7],
                "startDate": "2026-10-01",
            },
        )
    ).json()
    async with db.begin() as conn:
        # El horario "existía" desde antes: sus dosis pasadas cuentan como omitidas.
        await conn.execute(text("UPDATE schedules SET effective_from = '2026-09-01T00:00:00Z'"))
        if verified:
            await conn.execute(
                text("UPDATE users SET email_verified_at = now() WHERE email = :e"),
                {"e": caregiver_email},
            )
            await conn.execute(
                text("UPDATE users SET email_verified_at = now() WHERE email = :e"),
                {"e": owner_email},
            )
        if telegram:
            for user, chat in ((caregiver, caregiver_chat), (owner, owner_chat)):
                await conn.execute(
                    text(
                        "INSERT INTO telegram_links (id, user_id, chat_id, username) "
                        "VALUES (gen_random_uuid(), :u, :c, 'tg')"
                    ),
                    {"u": user["user"]["id"], "c": chat},
                )
    return Scenario(
        owner=owner,
        caregiver=caregiver,
        patient_id=patient_id,
        caregiver_email=caregiver_email,
        owner_email=owner_email,
        medication_id=medication["id"],
        schedule_id=schedule["id"],
        caregiver_chat=caregiver_chat,
        owner_chat=owner_chat,
    )


async def record_event(
    db: AsyncEngine, scenario: Scenario, scheduled_at: str, status: str = "TAKEN"
) -> None:
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO dose_events (id, event_id, patient_id, schedule_id, medication_id, "
                "scheduled_at, status, acted_at) VALUES (gen_random_uuid(), :e, :p, :s, :m, "
                "CAST(:at AS timestamptz), :st, CAST(:at AS timestamptz))"
            ),
            {
                "e": uuid.uuid4(),
                "p": scenario.patient_id,
                "s": scenario.schedule_id,
                "m": scenario.medication_id,
                "at": scheduled_at,
                "st": status,
            },
        )


async def outbox(db: AsyncEngine, kind: str) -> list[dict[str, Any]]:
    async with db.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT channel, payload, dedupe_key FROM notifications_outbox "
                "WHERE kind = :k ORDER BY created_at, dedupe_key"
            ),
            {"k": kind},
        )
        return [dict(row._mapping) for row in rows]
