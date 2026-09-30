"""La API no expone rutas fuera de contracts/openapi.yaml, ni le faltan rutas del contrato."""

from typing import Any

import yaml

from app.main import create_app
from tests.conftest import CONTRACT_PATH

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _operations(spec: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (method, path, op["operationId"])
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if method in HTTP_METHODS
    }


def test_app_routes_match_contract() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert _operations(create_app().openapi()) == _operations(contract)
