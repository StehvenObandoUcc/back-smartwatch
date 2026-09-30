from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient

from app.core.errors import AppError, register_error_handlers
from tests.conftest import make_client


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/app-error")
    async def app_error() -> None:
        raise AppError(
            status=409,
            code="already_exists",
            title="Ya existe",
            detail="El recurso ya existe.",
            extra={"field": "name"},
        )

    @app.get("/http-error")
    async def http_error() -> None:
        raise HTTPException(status_code=403, detail="Sin acceso.", headers={"X-Test": "1"})

    @app.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("dato sensible que no debe salir")

    return app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async for c in make_client(_app()):
        yield c


def _assert_problem(response_json: dict[str, object], *, status: int, code: str) -> None:
    assert response_json["status"] == status
    assert response_json["code"] == code
    assert response_json["type"] == "https://api.example.com/problems/" + code.replace("_", "-")
    assert {"title", "detail"} <= response_json.keys()


async def test_app_error_is_problem_json(client: AsyncClient) -> None:
    response = await client.get("/app-error")

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    _assert_problem(body, status=409, code="already_exists")
    assert body["field"] == "name"
    assert body["instance"] == "/app-error"


async def test_http_exception_is_problem_json(client: AsyncClient) -> None:
    response = await client.get("/http-error")

    assert response.status_code == 403
    assert response.headers["x-test"] == "1"
    body = response.json()
    _assert_problem(body, status=403, code="forbidden")
    assert body["detail"] == "Sin acceso."


async def test_unknown_route_is_problem_json(client: AsyncClient) -> None:
    response = await client.get("/no-existe")

    assert response.status_code == 404
    _assert_problem(response.json(), status=404, code="not_found")


async def test_validation_error_is_problem_json(client: AsyncClient) -> None:
    response = await client.get("/items/abc")

    assert response.status_code == 422
    body = response.json()
    _assert_problem(body, status=422, code="validation_error")
    assert body["errors"][0]["loc"] == ["path", "item_id"]


async def test_unhandled_error_hides_details(client: AsyncClient) -> None:
    response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    _assert_problem(body, status=500, code="internal_error")
    assert "sensible" not in response.text
