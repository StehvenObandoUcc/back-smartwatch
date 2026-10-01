import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.modules.medications.schemas import MedicationUpdate, ScheduleCreate, ScheduleUpdate
from app.modules.plan.generator import ScheduleRule, doses_in_range, plan_doses
from app.modules.plan.schemas import PlanOut
from app.modules.plan.service import etag_matches, etag_of

BOGOTA = ZoneInfo("America/Bogota")
MONDAY = date(2026, 10, 5)  # lunes


def _rule(
    times: tuple[time, ...] = (time(8), time(20)),
    days: frozenset[int] = frozenset(range(1, 8)),
    start: date = MONDAY,
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
        days_of_week=days,
        start_date=start,
        end_date=end,
        effective_from=datetime(2026, 10, 1, tzinfo=UTC),
        medication_archived_at=None,
    )


def test_daily_schedule_yields_every_time_every_day() -> None:
    doses = list(doses_in_range(_rule(), BOGOTA, MONDAY, MONDAY + timedelta(days=6)))

    assert len(doses) == 14
    assert doses[0].scheduled_at == datetime(2026, 10, 5, 8, tzinfo=BOGOTA)
    assert doses[0].scheduled_at.utcoffset() == timedelta(hours=-5)


def test_only_selected_weekdays() -> None:
    rule = _rule(days=frozenset({1, 3, 5}))  # lunes, miércoles, viernes

    doses = list(doses_in_range(rule, BOGOTA, MONDAY, MONDAY + timedelta(days=6)))

    assert {d.scheduled_at.isoweekday() for d in doses} == {1, 3, 5}
    assert len(doses) == 6


def test_start_and_end_dates_are_inclusive_bounds() -> None:
    rule = _rule(start=MONDAY + timedelta(days=2), end=MONDAY + timedelta(days=3))

    doses = list(doses_in_range(rule, BOGOTA, MONDAY, MONDAY + timedelta(days=6)))

    assert {d.scheduled_at.date() for d in doses} == {
        MONDAY + timedelta(days=2),
        MONDAY + timedelta(days=3),
    }


def test_range_before_start_yields_nothing() -> None:
    assert (
        list(
            doses_in_range(
                _rule(start=MONDAY), BOGOTA, MONDAY - timedelta(days=5), MONDAY - timedelta(days=1)
            )
        )
        == []
    )


def test_plan_covers_seven_local_days_sorted_across_rules() -> None:
    now = datetime(2026, 10, 5, 3, 30, tzinfo=UTC)  # 22:30 del domingo 4 en Bogotá
    morning = _rule(times=(time(8),), start=date(2026, 10, 1))
    night = _rule(times=(time(23, 30),), start=date(2026, 10, 1))

    doses = plan_doses([night, morning], BOGOTA, now)

    assert len(doses) == 14
    assert doses[0].scheduled_at == datetime(2026, 10, 4, 8, tzinfo=BOGOTA)  # hoy local, ya pasada
    assert doses[-1].scheduled_at == datetime(2026, 10, 10, 23, 30, tzinfo=BOGOTA)
    instants = [d.scheduled_at for d in doses]
    assert instants == sorted(instants)


def test_plan_uses_patient_timezone_not_utc_for_the_day() -> None:
    # 2026-10-05 02:00 UTC es todavía el 4 de octubre en Bogotá y ya el 5 en Madrid.
    now = datetime(2026, 10, 5, 2, tzinfo=UTC)
    rule = _rule(times=(time(9),), start=date(2026, 10, 1))

    bogota = plan_doses([rule], BOGOTA, now)
    madrid = plan_doses([rule], ZoneInfo("Europe/Madrid"), now)

    assert bogota[0].scheduled_at.date() == date(2026, 10, 4)
    assert madrid[0].scheduled_at.date() == date(2026, 10, 5)


def _plan(version: int, generated_at: datetime) -> PlanOut:
    return PlanOut(
        version=version,
        patient_timezone="America/Bogota",
        generated_at=generated_at,
        valid_from=generated_at,
        valid_until=generated_at,
        doses=[],
    )


def test_etag_changes_with_version_and_local_day() -> None:
    base = datetime(2026, 10, 5, 15, tzinfo=UTC)

    same = etag_of(_plan(3, base), BOGOTA)

    assert same == etag_of(_plan(3, base + timedelta(hours=1)), BOGOTA)
    assert same != etag_of(_plan(4, base), BOGOTA)
    assert same != etag_of(_plan(3, base + timedelta(days=1)), BOGOTA)


def test_etag_matches_lists_weak_validators_and_star() -> None:
    etag = '"3-2026-10-05"'

    assert etag_matches('"3-2026-10-05"', etag)
    assert etag_matches('"x", W/"3-2026-10-05"', etag)
    assert etag_matches("*", etag)
    assert not etag_matches('"2-2026-10-05"', etag)
    assert not etag_matches(None, etag)


def test_schedule_normalizes_and_validates() -> None:
    schedule = ScheduleCreate.model_validate(
        {"times": ["20:00", "08:00"], "daysOfWeek": [5, 1], "startDate": "2026-10-05"}
    )

    assert schedule.times == ["08:00", "20:00"]
    assert schedule.days_of_week == [1, 5]


@pytest.mark.parametrize(
    "body",
    [
        {"times": ["08:00", "08:00"], "daysOfWeek": [1], "startDate": "2026-10-05"},
        {"times": ["24:00"], "daysOfWeek": [1], "startDate": "2026-10-05"},
        {"times": ["8:00"], "daysOfWeek": [1], "startDate": "2026-10-05"},
        {"times": ["08:00"], "daysOfWeek": [0], "startDate": "2026-10-05"},
        {"times": ["08:00"], "daysOfWeek": [1, 1], "startDate": "2026-10-05"},
        {"times": [], "daysOfWeek": [1], "startDate": "2026-10-05"},
        {
            "times": ["08:00"],
            "daysOfWeek": [1],
            "startDate": "2026-10-05",
            "endDate": "2026-10-04",
        },
    ],
)
def test_schedule_rejects_invalid_bodies(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScheduleCreate.model_validate(body)


def test_patches_need_a_field_and_reject_forbidden_nulls() -> None:
    for model, body in [
        (MedicationUpdate, {}),
        (MedicationUpdate, {"name": None}),
        (ScheduleUpdate, {}),
        (ScheduleUpdate, {"times": None}),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate(body)

    assert MedicationUpdate.model_validate({"instructions": None}).instructions is None
    assert ScheduleUpdate.model_validate({"endDate": None}).end_date is None
