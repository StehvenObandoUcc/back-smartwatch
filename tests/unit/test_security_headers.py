"""Cada respuesta HTTP lleva las cabeceras de seguridad, sin pisar las que ya trae."""

from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from app.core.middleware import SecurityHeadersMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/plain")
    async def plain() -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/chat")
    async def chat() -> Response:
        return Response("x", headers={"Cache-Control": "no-cache"})

    return app


async def test_every_response_gets_the_security_headers() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="https://test") as client:
        response = await client.get("/plain")

    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["cache-control"] == "no-store"


async def test_a_header_set_by_the_endpoint_is_not_overwritten() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="https://test") as client:
        response = await client.get("/chat")

    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-frame-options"] == "DENY"
