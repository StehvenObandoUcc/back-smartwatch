"""La API no expone rutas fuera de contracts/openapi.yaml.

El contrato de una fase se aprueba antes de implementarla, así que puede tener operaciones que la
app aún no sirve; lo que nunca puede pasar es lo contrario.
"""

from app.main import create_app
from tests.conftest import app_operations, contract_operations


def test_app_has_no_routes_outside_contract() -> None:
    extra = app_operations(create_app()) - contract_operations()

    assert not extra, f"Rutas fuera del contrato: {sorted(extra)}"


def test_health_is_implemented() -> None:
    implemented = app_operations(create_app())

    assert ("get", "/health", "getHealth") in implemented
    assert ("get", "/health/ready", "getReadiness") in implemented
