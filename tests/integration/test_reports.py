"""Reportes: API (crear, consultar, PDF) y worker (semanales, generación, avisos)."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import RateLimit, Settings
from app.core.db import create_session_factory
from app.modules.reports import processor as processor_module
from app.modules.reports.processor import ReportProcessor
from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import assert_problem, bearer, register
from tests.integration.notify_helpers import (
    MONDAY_8AM,
    Scenario,
    consent,
    make_scenario,
    outbox,
    record_event,
)

BOGOTA = ZoneInfo("America/Bogota")


def _processor(db: AsyncEngine, settings: Settings) -> ReportProcessor:
    return ReportProcessor(create_session_factory(db), settings)


async def _reports(db: AsyncEngine) -> list[dict[str, Any]]:
    async with db.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT id, patient_id, period_start, period_end, trigger, status, summary, "
                "(pdf IS NOT NULL) AS has_pdf FROM reports ORDER BY created_at"
            )
        )
        return [dict(row._mapping) for row in rows]


async def _week_of_events(db: AsyncEngine, scenario: Scenario) -> None:
    """Semana del 5 al 11 de octubre: 4 tomadas, 1 omitida por el paciente y 2 sin registrar."""
    for day, status in [(5, "TAKEN"), (6, "TAKEN"), (7, "TAKEN"), (8, "TAKEN"), (9, "SKIPPED")]:
        await record_event(db, scenario, f"2026-10-{day:02d}T08:00:00-05:00", status)


# ─── Reportes semanales ───────────────────────────────────────────────────────


async def test_weekly_report_is_created_on_monday_at_8_local(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine)
    processor = _processor(db_engine, integration_settings)

    before = await processor.create_weekly(MONDAY_8AM - timedelta(minutes=1))
    created = await processor.create_weekly(MONDAY_8AM)
    again = await processor.create_weekly(MONDAY_8AM + timedelta(hours=1))
    midweek = await processor.create_weekly(MONDAY_8AM + timedelta(days=2))

    assert (before, created, again, midweek) == (0, 1, 0, 0)
    [report] = await _reports(db_engine)
    assert report["period_start"] == date(2026, 10, 5)
    assert report["period_end"] == date(2026, 10, 11)
    assert (report["trigger"], report["status"]) == ("weekly", "pending")


async def test_weekly_report_catches_up_after_downtime_until_the_next_monday(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine)
    processor = _processor(db_engine, integration_settings)

    assert await processor.create_weekly(datetime(2026, 10, 18, 20, 0, tzinfo=UTC)) == 1  # domingo
    [report] = await _reports(db_engine)
    assert report["period_end"] == date(2026, 10, 11)  # la semana que ya cerró, no la en curso


async def test_patients_without_schedules_get_no_weekly_report(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await register(client, role="patient")  # sin medicamentos

    assert await _processor(db_engine, integration_settings).create_weekly(MONDAY_8AM) == 0


async def test_weekly_period_follows_the_patient_timezone(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await make_scenario(client, db_engine)
    async with db_engine.begin() as conn:
        await conn.execute(text("UPDATE patients SET timezone = 'Asia/Tokyo'"))
    processor = _processor(db_engine, integration_settings)

    # 13:00 UTC del lunes ya es lunes 22:00 en Tokio: ya toca; 22:59 UTC del domingo no.
    assert await processor.create_weekly(datetime(2026, 10, 11, 22, 59, tzinfo=UTC)) == 0
    assert await processor.create_weekly(datetime(2026, 10, 11, 23, 0, tzinfo=UTC)) == 1


# ─── Generación y avisos ──────────────────────────────────────────────────────


async def test_processing_computes_the_summary_and_the_pdf(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    await _week_of_events(db_engine, scenario)
    processor = _processor(db_engine, integration_settings)
    await processor.create_weekly(MONDAY_8AM)

    assert await processor.process_pending(MONDAY_8AM) == 1

    [report] = await _reports(db_engine)
    assert (report["status"], report["has_pdf"]) == ("ready", True)
    assert report["summary"]["taken"] == 4
    assert report["summary"]["skipped"] == 1
    assert report["summary"]["missed"] == 2
    assert report["summary"]["percentage"] == 57.1
    assert report["summary"]["byMedication"][0]["medicationName"] == "Metformina"
    assert await processor.process_pending(MONDAY_8AM) == 0  # ya no queda nada pendiente


async def test_weekly_report_notifies_caregivers_and_the_patient_once(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    await _week_of_events(db_engine, scenario)
    processor = _processor(db_engine, integration_settings)
    await processor.create_weekly(MONDAY_8AM)
    await processor.process_pending(MONDAY_8AM)

    messages = await outbox(db_engine, "weekly_report")

    # Cuidador y paciente con cuenta, cada uno por correo y Telegram.
    assert sorted(
        (m["channel"], m["payload"].get("to") or m["payload"]["chat_id"]) for m in messages
    ) == sorted(
        [
            ("email", scenario.caregiver_email),
            ("email", scenario.owner_email),
            ("telegram", scenario.caregiver_chat),
            ("telegram", scenario.owner_chat),
        ]
    )
    [report] = await _reports(db_engine)
    for message in messages:
        payload = message["payload"]
        assert payload["link"] == (
            f"http://localhost:5173/patients/{scenario.patient_id}/reports/{report['id']}"
        )
        assert payload["percentage"] == 57.1
        assert "Metformina" not in str(payload)

    # Regenerar el mismo reporte no vuelve a avisar.
    async with db_engine.begin() as conn:
        await conn.execute(text("UPDATE reports SET status = 'pending'"))
    await processor.process_pending(MONDAY_8AM)
    assert len(await outbox(db_engine, "weekly_report")) == 4


async def test_weekly_notifications_respect_preferences_and_consent(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    off = {
        "missedDose": {"email": True, "telegram": True},
        "weeklyReport": {"email": False, "telegram": True},
    }
    await client.put(
        "/users/me/notification-preferences", headers=bearer(scenario.caregiver), json=off
    )
    await consent(
        client, scenario.owner, "notifications", granted=False
    )  # el paciente retira el suyo
    processor = _processor(db_engine, integration_settings)
    await processor.create_weekly(MONDAY_8AM)
    await processor.process_pending(MONDAY_8AM)

    messages = await outbox(db_engine, "weekly_report")

    assert [(m["channel"], m["payload"]["chat_id"]) for m in messages] == [
        ("telegram", scenario.caregiver_chat)
    ]


async def test_manual_reports_do_not_notify(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    created = await client.post(
        f"/patients/{scenario.patient_id}/reports",
        headers=bearer(scenario.caregiver),
        json={"periodEnd": "2026-10-11"},
    )
    assert created.status_code == 202

    await _processor(db_engine, integration_settings).process_pending(MONDAY_8AM)

    assert (await _reports(db_engine))[0]["status"] == "ready"
    assert await outbox(db_engine, "weekly_report") == []


async def test_a_failed_report_notifies_nobody_and_can_be_retried(
    client: AsyncClient,
    db_engine: AsyncEngine,
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = await make_scenario(client, db_engine)
    processor = _processor(db_engine, integration_settings)
    await processor.create_weekly(MONDAY_8AM)

    def boom(*_: Any) -> bytes:
        raise RuntimeError("Ana Metformina")

    monkeypatch.setattr(processor_module, "build_pdf", boom)
    await processor.process_pending(MONDAY_8AM)

    assert (await _reports(db_engine))[0]["status"] == "failed"
    assert await outbox(db_engine, "weekly_report") == []
    assert (
        await processor.process_pending(MONDAY_8AM) == 0
    )  # un reporte fallido no se reintenta solo

    monkeypatch.undo()
    retried = await client.post(
        f"/patients/{scenario.patient_id}/reports",
        headers=bearer(scenario.caregiver),
        json={"periodEnd": "2026-10-11"},
    )
    assert retried.json()["status"] == "pending"
    await processor.process_pending(MONDAY_8AM)
    assert (await _reports(db_engine))[0]["status"] == "ready"


async def test_unicode_names_do_not_break_the_pdf(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    await client.patch(
        f"/patients/{scenario.patient_id}",
        headers=bearer(scenario.owner),
        json={"displayName": "Ana 🌸 田中"},
    )
    processor = _processor(db_engine, integration_settings)
    await processor.create_weekly(MONDAY_8AM)

    await processor.process_pending(MONDAY_8AM)

    assert (await _reports(db_engine))[0]["status"] == "ready"


# ─── API ──────────────────────────────────────────────────────────────────────


async def test_create_report_is_asynchronous_and_idempotent_by_period(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    scenario = await make_scenario(client, db_engine)
    url = f"/patients/{scenario.patient_id}/reports"

    first = await client.post(url, headers=bearer(scenario.owner), json={"periodEnd": "2026-10-11"})
    second = await client.post(
        url, headers=bearer(scenario.caregiver), json={"periodEnd": "2026-10-11"}
    )

    body = first.json()
    assert first.status_code == 202
    assert (body["status"], body["trigger"], body["summary"], body["generatedAt"]) == (
        "pending",
        "manual",
        None,
        None,
    )
    assert (body["periodStart"], body["periodEnd"]) == ("2026-10-05", "2026-10-11")
    assert second.json()["id"] == body["id"]
    assert len(await _reports(db_engine)) == 1


async def test_create_report_defaults_to_yesterday_and_rejects_the_future(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    scenario = await make_scenario(client, db_engine)
    url = f"/patients/{scenario.patient_id}/reports"
    today = datetime.now(BOGOTA).date()

    default = await client.post(url, headers=bearer(scenario.owner))
    future = await client.post(
        url, headers=bearer(scenario.owner), json={"periodEnd": str(today + timedelta(days=1))}
    )
    bad = await client.post(url, headers=bearer(scenario.owner), json={"periodEnd": "ayer"})

    assert default.json()["periodEnd"] == str(today - timedelta(days=1))
    assert_problem(future, 422, "validation_error")
    assert_problem(bad, 422, "validation_error")


async def test_report_lifecycle_through_the_api(
    client: AsyncClient, db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    scenario = await make_scenario(client, db_engine)
    await _week_of_events(db_engine, scenario)
    base = f"/patients/{scenario.patient_id}/reports"
    created = (
        await client.post(
            base, headers=bearer(scenario.caregiver), json={"periodEnd": "2026-10-11"}
        )
    ).json()

    pending = await client.get(f"{base}/{created['id']}", headers=bearer(scenario.caregiver))
    not_ready = await client.get(f"{base}/{created['id']}/pdf", headers=bearer(scenario.caregiver))
    await _processor(db_engine, integration_settings).process_pending(MONDAY_8AM)
    ready = await client.get(f"{base}/{created['id']}", headers=bearer(scenario.caregiver))
    pdf = await client.get(f"{base}/{created['id']}/pdf", headers=bearer(scenario.caregiver))

    assert pending.json()["status"] == "pending"
    assert_problem(not_ready, 409, "report_not_ready")
    body = ready.json()
    assert body["status"] == "ready"
    assert body["generatedAt"] is not None
    assert body["summary"]["percentage"] == 57.1
    assert body["summary"]["byMedication"][0]["taken"] == 4
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"] == 'attachment; filename="reporte-2026-10-11.pdf"'
    assert pdf.content.startswith(b"%PDF")


async def test_list_reports_newest_first_with_cursor(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    scenario = await make_scenario(client, db_engine)
    url = f"/patients/{scenario.patient_id}/reports"
    for end in ("2026-10-04", "2026-10-11", "2026-10-18"):
        await client.post(url, headers=bearer(scenario.owner), json={"periodEnd": end})

    first = (await client.get(url + "?limit=2", headers=bearer(scenario.owner))).json()
    second = (
        await client.get(
            url + f"?limit=2&cursor={first['nextCursor']}", headers=bearer(scenario.owner)
        )
    ).json()

    assert [r["periodEnd"] for r in first["items"]] == ["2026-10-18", "2026-10-11"]
    assert [r["periodEnd"] for r in second["items"]] == ["2026-10-04"]
    assert second["nextCursor"] is None


# ─── Autorización ─────────────────────────────────────────────────────────────


async def test_report_routes_authorization(client: AsyncClient, db_engine: AsyncEngine) -> None:
    scenario = await make_scenario(client, db_engine)
    base = f"/patients/{scenario.patient_id}/reports"
    report = (
        await client.post(base, headers=bearer(scenario.owner), json={"periodEnd": "2026-10-11"})
    ).json()
    stranger = await register(client, role="caregiver")
    routes = [
        ("GET", base),
        ("POST", base),
        ("GET", f"{base}/{report['id']}"),
        ("GET", f"{base}/{report['id']}/pdf"),
    ]
    pairing = (await client.post("/devices/pairing-codes", json={"model": "R"})).json()
    await client.post(
        f"/devices/pairing-codes/{pairing['code']}/confirm",
        headers=bearer(scenario.owner),
        json={"patientId": scenario.patient_id},
    )
    watch = (
        await client.post(
            "/devices/token", json={"grantType": "device_code", "deviceCode": pairing["deviceCode"]}
        )
    ).json()

    for method, path in routes:
        assert_problem(await client.request(method, path), 401, "missing_token")
        assert_problem(
            await client.request(method, path, headers=bearer(stranger)), 404, "patient_not_found"
        )
        denied = await client.request(
            method, path, headers={"Authorization": f"Bearer {watch['accessToken']}"}
        )
        assert_problem(denied, 403, "wrong_token_type")


async def test_reports_of_another_patient_are_404(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    mine = await make_scenario(client, db_engine)
    other = await make_scenario(client, db_engine)
    theirs = (
        await client.post(
            f"/patients/{other.patient_id}/reports",
            headers=bearer(other.owner),
            json={"periodEnd": "2026-10-11"},
        )
    ).json()

    for suffix in ("", "/pdf"):
        response = await client.get(
            f"/patients/{mine.patient_id}/reports/{theirs['id']}{suffix}",
            headers=bearer(mine.owner),
        )
        assert_problem(response, 404, "report_not_found")
    bad = await client.get(
        f"/patients/{mine.patient_id}/reports/no-es-uuid", headers=bearer(mine.owner)
    )
    assert_problem(bad, 404, "report_not_found")


async def test_reports_require_the_health_consent(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    scenario = await make_scenario(client, db_engine)
    await consent(client, scenario.owner, "health_data", granted=False)

    response = await client.get(
        f"/patients/{scenario.patient_id}/reports", headers=bearer(scenario.owner)
    )

    assert_problem(response, 403, "consent_required")


@pytest.fixture
async def strict_client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    app = app_factory(rate_report_patient=RateLimit(limit=2, window_seconds=60))
    async for c in make_client(app):
        yield c


async def test_report_requests_are_rate_limited_per_patient(
    strict_client: AsyncClient, db_engine: AsyncEngine
) -> None:
    scenario = await make_scenario(strict_client, db_engine)
    url = f"/patients/{scenario.patient_id}/reports"

    codes = [
        (
            await strict_client.post(url, headers=bearer(scenario.owner), json={"periodEnd": end})
        ).status_code
        for end in ("2026-10-04", "2026-10-11", "2026-10-18")
    ]
    stranger = await register(strict_client, role="caregiver")
    hidden = await strict_client.post(url, headers=bearer(stranger))

    assert codes == [202, 202, 429]
    assert hidden.status_code == 404  # un extraño nunca ve el 429 de otro paciente
