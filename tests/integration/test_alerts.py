"""Alerta de dosis omitida: a quién avisa, por qué canal y sin repetirse."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.db import create_session_factory
from app.modules.notifications.alerts import MissedDoseAlerts
from tests.integration.helpers import bearer, register, unique_email
from tests.integration.notify_helpers import (
    NOW,
    consent,
    make_scenario,
    outbox,
    record_event,
)


def _alerts(db: AsyncEngine, settings: Settings) -> MissedDoseAlerts:
    return MissedDoseAlerts(create_session_factory(db), settings)


async def _channels(db: AsyncEngine) -> list[str]:
    return sorted(m["channel"] for m in await outbox(db, "missed_dose"))


async def test_missed_dose_alerts_the_caregiver_by_email_and_telegram(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)

    queued = await _alerts(db_engine, integration_settings).run(NOW)

    assert queued == 2
    messages = {m["channel"]: m["payload"] for m in await outbox(db_engine, "missed_dose")}
    assert messages["email"]["to"] == scenario.caregiver_email
    assert messages["email"]["local_time"] == "08:00"
    assert messages["telegram"]["chat_id"] == scenario.caregiver_chat
    for payload in messages.values():
        assert payload["patient_name"] == "Ana"
        assert payload["link"] == f"http://localhost:5173/patients/{scenario.patient_id}"
        # Resumen mínimo: ni el medicamento ni la dosis salen del sistema.
        assert "Metformina" not in str(payload)
        assert "tableta" not in str(payload)


async def test_running_again_never_duplicates_alerts(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine)
    alerts = _alerts(db_engine, integration_settings)

    first = await alerts.run(NOW)
    again = await alerts.run(NOW + timedelta(minutes=1))

    assert (first, again) == (2, 0)
    assert len(await outbox(db_engine, "missed_dose")) == 2


async def test_the_patient_with_an_account_is_not_alerted_about_themselves(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)

    await _alerts(db_engine, integration_settings).run(NOW)

    recipients = {m["payload"].get("to") for m in await outbox(db_engine, "missed_dose")}
    assert scenario.owner_email not in recipients
    chats = {m["payload"].get("chat_id") for m in await outbox(db_engine, "missed_dose")}
    assert scenario.owner_chat not in chats


async def test_no_alert_while_the_dose_is_inside_the_sixty_minute_window(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine)

    queued = await _alerts(db_engine, integration_settings).run(NOW - timedelta(minutes=40))

    assert queued == 0


async def test_no_alert_for_doses_older_than_the_lookback(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine)

    queued = await _alerts(db_engine, integration_settings).run(NOW + timedelta(hours=6))

    assert queued == 0


async def test_no_alert_when_the_dose_was_recorded(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    await record_event(db_engine, scenario, "2026-10-07T08:00:00-05:00", "SKIPPED")

    queued = await _alerts(db_engine, integration_settings).run(NOW)

    assert queued == 0


async def test_no_alert_for_archived_medications_or_schedules_created_later(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    alerts = _alerts(db_engine, integration_settings)
    async with db_engine.begin() as conn:
        await conn.execute(text("UPDATE schedules SET effective_from = '2026-10-07T14:00:00Z'"))
    assert await alerts.run(NOW) == 0  # el horario es posterior a la dosis

    async with db_engine.begin() as conn:
        await conn.execute(text("UPDATE schedules SET effective_from = '2026-09-01T00:00:00Z'"))
        await conn.execute(text("UPDATE medications SET archived_at = '2026-10-06T00:00:00Z'"))
    assert await alerts.run(NOW) == 0  # el medicamento se archivó antes de la dosis
    assert scenario.schedule_id


async def test_preferences_pick_the_channels(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    off = {
        "missedDose": {"email": False, "telegram": True},
        "weeklyReport": {"email": True, "telegram": True},
    }
    await client.put(
        "/users/me/notification-preferences", headers=bearer(scenario.caregiver), json=off
    )

    await _alerts(db_engine, integration_settings).run(NOW)

    assert await _channels(db_engine) == ["telegram"]


async def test_all_channels_off_means_no_alerts(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    off = {
        "missedDose": {"email": False, "telegram": False},
        "weeklyReport": {"email": True, "telegram": True},
    }
    await client.put(
        "/users/me/notification-preferences", headers=bearer(scenario.caregiver), json=off
    )

    assert await _alerts(db_engine, integration_settings).run(NOW) == 0


async def test_an_unverified_email_or_unlinked_telegram_gets_nothing(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine, verified=False)
    await _alerts(db_engine, integration_settings).run(NOW)
    assert await _channels(db_engine) == ["telegram"]


async def test_no_telegram_link_means_email_only(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine, telegram=False)
    await _alerts(db_engine, integration_settings).run(NOW)
    assert await _channels(db_engine) == ["email"]


async def test_no_notifications_consent_means_no_alerts(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine, notifications_consent=False)

    assert await _alerts(db_engine, integration_settings).run(NOW) == 0


async def test_withdrawing_the_consent_stops_the_alerts(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    await client.put(
        "/users/me/consents/notifications",
        headers=bearer(scenario.caregiver),
        json={"granted": False, "version": "v2"},
    )

    assert await _alerts(db_engine, integration_settings).run(NOW) == 0


async def test_a_revoked_caregiver_is_not_alerted(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    revoked = await client.delete(
        f"/patients/{scenario.patient_id}/caregivers/{scenario.caregiver['user']['id']}",
        headers=bearer(scenario.owner),
    )
    assert revoked.status_code == 204

    assert await _alerts(db_engine, integration_settings).run(NOW) == 0


async def test_each_caregiver_gets_their_own_alert(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine, telegram=False)
    second_email = unique_email("segundo")
    second = await register(client, role="caregiver", email=second_email)
    await consent(client, second, "notifications")
    code = (
        await client.post(
            f"/patients/{scenario.patient_id}/caregiver-invitations", headers=bearer(scenario.owner)
        )
    ).json()["code"]
    await client.post(f"/caregiver-invitations/{code}/accept", headers=bearer(second))
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET email_verified_at = now() WHERE email = :e"), {"e": second_email}
        )

    await _alerts(db_engine, integration_settings).run(NOW)

    recipients = sorted(m["payload"]["to"] for m in await outbox(db_engine, "missed_dose"))
    assert recipients == sorted([scenario.caregiver_email, second_email])
