import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import UnprocessableError
from app.modules.doses.history import EventRecord, HistoryEntry, build_history, count
from app.modules.doses.service import resolve_range
from app.modules.plan.generator import ScheduleRule, is_scheduled

BOGOTA = ZoneInfo("America/Bogota")
NOW = datetime(2026, 10, 10, 18, 0, tzinfo=BOGOTA)  # sábado, 18:00 hora local
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def _rule(
    times: tuple[time, ...] = (time(12),),
    effective_from: datetime = LONG_AGO,
    archived_at: datetime | None = None,
    start: date = date(2026, 10, 1),
    end: date | None = None,
) -> ScheduleRule:
    return ScheduleRule(
        schedule_id=uuid.uuid4(),
        medication_id=uuid.uuid4(),
        medication_name="Metformina",
        dosage="1 tableta",
        instructions=None,
        color="#3B82F6",
        times=times,
        days_of_week=frozenset(range(1, 8)),
        start_date=start,
        end_date=end,
        effective_from=effective_from,
        medication_archived_at=archived_at,
    )


def _event(rule: ScheduleRule, at: datetime, status: str = "TAKEN") -> EventRecord:
    return EventRecord(
        rule.schedule_id,
        rule.medication_id,
        rule.medication_name,
        rule.dosage,
        at.astimezone(UTC),
        status,
        at + timedelta(minutes=5),
    )


def _history(
    rules: list[ScheduleRule],
    events: list[EventRecord],
    first: date = date(2026, 10, 8),
    last: date = date(2026, 10, 10),
    now: datetime = NOW,
) -> list[HistoryEntry]:
    return build_history(rules, events, BOGOTA, first, last, now)


def test_unrecorded_overdue_doses_are_missed_and_newest_first() -> None:
    entries = _history([_rule()], [])

    assert [e.status for e in entries] == ["MISSED"] * 3
    assert [e.scheduled_at.date() for e in entries] == [
        date(2026, 10, 10),
        date(2026, 10, 9),
        date(2026, 10, 8),
    ]


def test_recorded_dose_is_not_missed_and_keeps_its_status() -> None:
    rule = _rule()
    events = [
        _event(rule, datetime(2026, 10, 9, 12, tzinfo=BOGOTA), "TAKEN"),
        _event(rule, datetime(2026, 10, 8, 12, tzinfo=BOGOTA), "SKIPPED"),
    ]

    entries = _history([rule], events)

    assert [e.status for e in entries] == ["MISSED", "TAKEN", "SKIPPED"]
    assert entries[1].acted_at is not None
    assert entries[0].acted_at is None
    assert count(entries).percentage == 33.3


def test_missed_only_after_the_sixty_minute_window() -> None:
    rule = _rule(times=(time(17, 30), time(16, 59)))

    entries = _history([rule], [], first=date(2026, 10, 10), last=date(2026, 10, 10))

    # A las 18:00: la de 16:59 ya pasó 61 min; la de 17:30 sigue en ventana.
    assert [e.scheduled_at.time() for e in entries] == [time(16, 59)]


def test_dose_with_event_inside_window_still_appears() -> None:
    rule = _rule(times=(time(17, 30),))
    event = _event(rule, datetime(2026, 10, 10, 17, 30, tzinfo=BOGOTA))

    entries = _history([rule], [event], first=date(2026, 10, 10), last=date(2026, 10, 10))

    assert [e.status for e in entries] == ["TAKEN"]


def test_doses_before_the_schedule_existed_are_not_missed() -> None:
    rule = _rule(effective_from=datetime(2026, 10, 9, 13, tzinfo=BOGOTA))

    entries = _history([rule], [])

    assert [e.scheduled_at.date() for e in entries] == [date(2026, 10, 10)]


def test_doses_after_archiving_are_not_missed() -> None:
    rule = _rule(archived_at=datetime(2026, 10, 9, 13, tzinfo=BOGOTA))

    entries = _history([rule], [])

    assert [e.scheduled_at.date() for e in entries] == [date(2026, 10, 9), date(2026, 10, 8)]


def test_percentage_is_none_without_expired_doses() -> None:
    assert count(_history([_rule(start=date(2026, 10, 11))], [])).percentage is None


def test_percentage_counts_taken_over_everything_expired() -> None:
    rule = _rule()
    events = [_event(rule, datetime(2026, 10, d, 12, tzinfo=BOGOTA)) for d in (8, 9)]

    counts = count(_history([rule], events))

    assert (counts.taken, counts.skipped, counts.missed) == (2, 0, 1)
    assert counts.percentage == 66.7


def test_is_scheduled_matches_exact_instants_only() -> None:
    rule = _rule(times=(time(8), time(20)))

    assert is_scheduled(rule, BOGOTA, datetime(2026, 10, 5, 8, tzinfo=BOGOTA))
    assert is_scheduled(rule, BOGOTA, datetime(2026, 10, 5, 13, tzinfo=UTC))  # 08:00 Bogotá en UTC
    assert not is_scheduled(rule, BOGOTA, datetime(2026, 10, 5, 8, 1, tzinfo=BOGOTA))
    assert not is_scheduled(
        rule, BOGOTA, datetime(2026, 9, 30, 8, tzinfo=BOGOTA)
    )  # antes del inicio


def test_range_defaults_and_limits() -> None:
    now = datetime(2026, 10, 10, 3, tzinfo=UTC)  # 22:00 del 9 en Bogotá

    assert resolve_range(None, None, BOGOTA, now) == (date(2026, 10, 3), date(2026, 10, 9))
    assert resolve_range(None, date(2026, 10, 20), BOGOTA, now) == (
        date(2026, 10, 14),
        date(2026, 10, 20),
    )
    assert resolve_range(date(2026, 7, 12), date(2026, 10, 9), BOGOTA, now)[0] == date(2026, 7, 12)
    with pytest.raises(UnprocessableError):
        resolve_range(date(2026, 7, 11), date(2026, 10, 9), BOGOTA, now)  # 91 días
    with pytest.raises(UnprocessableError):
        resolve_range(date(2026, 10, 9), date(2026, 10, 8), BOGOTA, now)
