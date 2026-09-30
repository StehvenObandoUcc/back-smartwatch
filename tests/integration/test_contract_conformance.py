"""Schemathesis: las operaciones implementadas responden según contracts/openapi.yaml.

Genera peticiones (válidas e inválidas) a partir del contrato y comprueba que la app nunca da
500 y que cada respuesta cumple el estado, el Content-Type, las cabeceras y el esquema
documentados.
"""

from typing import Any, cast

import pytest
import schemathesis
from fastapi import FastAPI
from hypothesis import HealthCheck, settings
from schemathesis.checks import CheckFunction, not_a_server_error
from schemathesis.generation.case import Case
from schemathesis.schemas import BaseSchema
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
)

from app.main import create_app
from tests.conftest import CONTRACT_PATH, app_operations

# Los checks están envueltos por decoradores de Schemathesis que mypy no ve como funciones.
CHECKS = cast(
    list[CheckFunction],
    [
        not_a_server_error,
        status_code_conformance,
        content_type_conformance,
        response_headers_conformance,
        response_schema_conformance,
    ],
)


# Solo las operaciones que la app ya implementa (el contrato puede ir por delante).
IMPLEMENTED = sorted(operation_id for _, _, operation_id in app_operations(create_app()))


@pytest.fixture
def contract_schema(integration_app: FastAPI) -> BaseSchema:
    schema = schemathesis.openapi.from_path(CONTRACT_PATH)
    schema.app = integration_app
    return schema


schema = schemathesis.pytest.from_fixture("contract_schema").include(operation_id=IMPLEMENTED)


@schema.parametrize()
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_api_conforms_to_contract(case: Case[Any]) -> None:
    case.call_and_validate(checks=CHECKS)
