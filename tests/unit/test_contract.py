"""Coherencia entre la app y contracts/openapi.yaml.

- La app nunca expone rutas fuera del contrato.
- El contrato de una fase se aprueba antes de implementarla, así que puede adelantarse a la app.
- Al cerrar una fase se sube `info.x-closed-phase`: desde ese momento todas las operaciones con
  `x-phase` menor o igual deben estar implementadas.
"""

from app.main import create_app
from tests.conftest import app_operations, contract_operations, contract_phases, load_contract


def test_app_has_no_routes_outside_contract() -> None:
    extra = app_operations(create_app()) - contract_operations()

    assert not extra, f"Rutas fuera del contrato: {sorted(extra)}"


def test_every_contract_operation_declares_its_phase() -> None:
    missing = [op for op, phase in contract_phases().items() if not isinstance(phase, int)]

    assert not missing, f"Operaciones sin x-phase: {sorted(missing)}"


def test_closed_phases_are_fully_implemented() -> None:
    closed_phase = load_contract()["info"]["x-closed-phase"]
    required = {
        op
        for op, phase in contract_phases().items()
        if isinstance(phase, int) and phase <= closed_phase
    }

    pending = required - app_operations(create_app())

    assert not pending, f"Fase {closed_phase} cerrada con rutas sin implementar: {sorted(pending)}"
