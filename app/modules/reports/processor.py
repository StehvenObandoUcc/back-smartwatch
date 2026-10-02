"""Trabajo del worker sobre reportes: crear los semanales y generar los pendientes (resumen + PDF).

Al quedar listo un reporte semanal se avisa a cuidadores y paciente (resumen mínimo + enlace al
panel, porque los chats con bots no van cifrados de extremo a extremo).
"""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.modules.doses.loader import load_history
from app.modules.notifications.recipients import resolve_recipients
from app.modules.notifications.repository import OutboxRepository
from app.modules.patients.models import Patient
from app.modules.plan.generator import local_today
from app.modules.plan.repository import PlanRepository
from app.modules.reports.models import Report
from app.modules.reports.repository import ReportRepository
from app.modules.reports.summary import Summary, build_pdf, summarize

logger = get_logger(__name__)

PERIOD_DAYS = 7
WEEKLY_HOUR = 8  # el lunes a las 08:00, hora local del paciente


class ReportProcessor:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._factory = session_factory
        self._web = settings.web_origin
        self._batch = settings.report_batch_size

    # ─── Reportes semanales ───────────────────────────────────────────────────

    async def create_weekly(self, now: datetime | None = None) -> int:
        """Crea (pendiente) el reporte de la última semana cerrada de cada paciente con horarios.

        Desde el lunes a las 08:00 locales y hasta el siguiente lunes; idempotente por
        (paciente, último día), así que si el worker estuvo caído se pone al día al volver.
        """
        now = now or utcnow()
        created = 0
        async with self._factory() as session:
            patients = await ReportRepository(session).patients_with_schedules()
        for patient in patients:
            tz = ZoneInfo(patient.timezone)
            local = now.astimezone(tz)
            if local.isoweekday() == 1 and local.hour < WEEKLY_HOUR:
                continue
            end = local_today(tz, now) - timedelta(days=local.isoweekday())  # último domingo
            start = end - timedelta(days=PERIOD_DAYS - 1)
            async with self._factory() as session:
                rules = await PlanRepository(session).rules(
                    patient.id, start, end, include_archived=True
                )
                if not rules:
                    continue
                if await ReportRepository(session).create_if_absent(
                    patient.id, start, end, "weekly"
                ):
                    created += 1
                await session.commit()
        return created

    # ─── Generación ───────────────────────────────────────────────────────────

    async def process_pending(self, now: datetime | None = None) -> int:
        """Genera hasta `report_batch_size` reportes pendientes; devuelve cuántos se procesaron."""
        now = now or utcnow()
        done = 0
        for _ in range(self._batch):
            async with self._factory() as session:
                report = await ReportRepository(session).next_pending_for_update()
                if report is None:
                    break
                await self._generate(session, report, now)
                await session.commit()
            done += 1
        return done

    async def _generate(self, session: AsyncSession, report: Report, now: datetime) -> None:
        patient = await session.get(Patient, report.patient_id)
        if patient is None:
            report.status = "failed"
            return
        try:
            entries = await load_history(
                session, patient, report.period_start, report.period_end, now
            )
            summary = summarize(entries)
            pdf = await asyncio.to_thread(
                build_pdf,
                patient.display_name,
                report.period_start,
                report.period_end,
                summary,
                now,
            )
        except Exception as exc:
            # Solo el tipo: el mensaje podría traer datos de salud.
            logger.error("report_failed", report_id=str(report.id), error_type=type(exc).__name__)
            report.status = "failed"
            return
        report.summary = summary.to_json()
        report.pdf = pdf
        report.status = "ready"
        report.generated_at = now
        if report.trigger == "weekly":
            await self._notify(session, patient, report, summary)

    async def _notify(
        self, session: AsyncSession, patient: Patient, report: Report, summary: Summary
    ) -> None:
        recipients = await resolve_recipients(session, patient, include_owner=True)
        outbox = OutboxRepository(session)
        link = f"{self._web}/patients/{patient.id}/reports/{report.id}"
        c = summary.counts
        for recipient in recipients:
            for channel in recipient.channels("weekly_report"):
                payload: dict[str, object] = {
                    "patient_name": patient.display_name,
                    "link": link,
                    "percentage": c.percentage,
                    "taken": c.taken,
                    "skipped": c.skipped,
                    "missed": c.missed,
                    "period_start": report.period_start.isoformat(),
                    "period_end": report.period_end.isoformat(),
                }
                if channel == "email":
                    payload["to"] = recipient.email
                else:
                    payload["chat_id"] = recipient.chat_id
                await outbox.enqueue(
                    channel=channel,
                    kind="weekly_report",
                    payload=payload,
                    dedupe_key=f"weekly_report:{report.id}:{recipient.user_id}:{channel}",
                )
