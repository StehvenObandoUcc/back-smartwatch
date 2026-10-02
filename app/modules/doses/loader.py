"""Carga el historial de un paciente (eventos + dosis omitidas calculadas) para un rango de fechas.

Lo comparten la API (historial, adherencia), el reporte semanal y la alerta de dosis omitida.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.doses.history import HistoryEntry, build_history
from app.modules.doses.repository import DoseEventRepository
from app.modules.patients.models import Patient
from app.modules.plan.generator import day_start
from app.modules.plan.repository import PlanRepository


async def load_history(
    session: AsyncSession, patient: Patient, first: date, last: date, now: datetime
) -> list[HistoryEntry]:
    tz = ZoneInfo(patient.timezone)
    events = await DoseEventRepository(session).in_range(
        patient.id, day_start(tz, first), day_start(tz, last + timedelta(days=1))
    )
    rules = await PlanRepository(session).rules(patient.id, first, last, include_archived=True)
    return build_history(rules, events, tz, first, last, now)
