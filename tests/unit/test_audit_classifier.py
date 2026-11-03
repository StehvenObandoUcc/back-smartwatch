import uuid

import pytest

from app.core.audit import AuditTarget, build_entry, classify_action, classify_route
from app.core.auth import Principal
from app.modules.audit.repository import AccessEntry

PATIENT = uuid.uuid4()
USER = Principal(kind="user", id=uuid.uuid4())
WATCH = Principal(kind="device", id=uuid.uuid4(), patient_id=PATIENT)


@pytest.mark.parametrize(
    ("route", "resource"),
    [
        ("/patients/{patientId}", "patient"),
        ("/patients/{patientId}/medications", "medications"),
        ("/patients/{patientId}/medications/{medicationId}", "medications"),
        ("/patients/{patientId}/medications/{medicationId}/schedules", "schedules"),
        ("/patients/{patientId}/medications/{medicationId}/schedules/{scheduleId}", "schedules"),
        ("/patients/{patientId}/plan", "plan"),
        ("/patients/{patientId}/dose-events", "dose_events"),
        ("/patients/{patientId}/dose-history", "dose_history"),
        ("/patients/{patientId}/adherence", "adherence"),
        ("/patients/{patientId}/reports", "reports"),
        ("/patients/{patientId}/reports/{reportId}/pdf", "reports"),
        ("/patients/{patientId}/chat/messages", "chat"),
        ("/patients/{patientId}/consents/{purpose}", "consents"),
        ("/patients/{patientId}/caregivers/{userId}", "caregivers"),
        ("/patients/{patientId}/caregiver-invitations", "caregivers"),
        ("/patients/{patientId}/devices", "devices"),
        ("/patients/{patientId}/algo-nuevo", "patient"),
    ],
)
def test_patient_routes_are_classified(route: str, resource: str) -> None:
    assert classify_route(route) == AuditTarget(resource, patient_in_path=True)


@pytest.mark.parametrize(
    ("route", "resource"),
    [
        ("/devices/me/plan", "plan"),
        ("/devices/me/dose-events", "dose_events"),
        ("/devices/me/chat/messages", "chat"),
    ],
)
def test_watch_routes_take_the_patient_from_the_token(route: str, resource: str) -> None:
    assert classify_route(route) == AuditTarget(resource, patient_in_path=False)


@pytest.mark.parametrize(
    "route",
    [
        "/patients",
        "/health",
        "/auth/login",
        "/users/me",
        "/users/me/consents",
        "/devices/me",
        "/devices/token",
        "/devices/pairing-codes/{code}/confirm",
        "/devices/{deviceId}",
        "/caregiver-invitations/{code}/accept",
        "/telegram/webhook",
        "/pacientes/{patientId}",
    ],
)
def test_other_routes_are_not_audited(route: str) -> None:
    assert classify_route(route) is None


@pytest.mark.parametrize(
    ("method", "action"),
    [("GET", "read"), ("HEAD", "read"), ("POST", "write"), ("PATCH", "write"), ("DELETE", "write")],
)
def test_action_by_method(method: str, action: str) -> None:
    assert classify_action(method) == action


def _entry(**overrides: object) -> AccessEntry | None:
    args: dict[str, object] = {
        "method": "GET",
        "status": 200,
        "route_path": "/patients/{patientId}/medications",
        "path_params": {"patientId": str(PATIENT)},
        "principal": USER,
        "request_id": "r1",
    }
    return build_entry(**{**args, **overrides})  # type: ignore[arg-type]


def test_entry_for_a_user_request() -> None:
    entry = _entry()

    assert entry is not None
    assert (entry.actor_type, entry.actor_id, entry.patient_id) == ("user", USER.id, PATIENT)
    assert (entry.action, entry.resource, entry.request_id) == ("read", "medications", "r1")


def test_entry_for_a_watch_request_uses_the_token_patient() -> None:
    entry = _entry(route_path="/devices/me/plan", path_params={}, principal=WATCH)

    assert entry is not None
    assert entry.patient_id == PATIENT
    assert entry.actor_type == "device"


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": 404},
        {"status": 400},
        {"principal": None},
        {"route_path": None},
        {"route_path": "/users/me"},
        {"path_params": {}},
        {"path_params": {"patientId": "no-es-uuid"}},
        {"route_path": "/devices/me/plan", "principal": USER},
    ],
)
def test_entry_is_skipped(overrides: dict[str, object]) -> None:
    assert _entry(**overrides) is None
