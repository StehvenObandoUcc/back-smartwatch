from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.core.config import RateLimit
from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import (
    PASSWORD,
    REFRESH_COOKIE,
    assert_problem,
    bearer,
    register,
    unique_email,
)


def _set_cookie_header(response_headers: list[str]) -> str:
    [header] = [h for h in response_headers if h.startswith(REFRESH_COOKIE)]
    return header


# ─── Registro ─────────────────────────────────────────────────────────────────


async def test_register_opens_web_session_with_refresh_cookie(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "email": "Ana@Example.com",
            "password": PASSWORD,
            "displayName": "Ana",
            "role": "caregiver",
            "timezone": "America/Bogota",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ana@example.com"
    assert body["user"]["role"] == "caregiver"
    assert body["user"]["patientId"] is None
    assert body["tokens"]["tokenType"] == "Bearer"
    assert body["tokens"]["expiresIn"] == 900
    assert "refreshToken" not in body["tokens"]

    cookie = _set_cookie_header(response.headers.get_list("set-cookie")).lower()
    for attribute in ("httponly", "secure", "samesite=strict", "path=/auth", "max-age=2592000"):
        assert attribute in cookie


async def test_register_rejects_duplicate_email_ignoring_case(client: AsyncClient) -> None:
    await register(client, email="dup@example.com")

    response = await client.post(
        "/auth/register",
        json={
            "email": "DUP@example.com",
            "password": PASSWORD,
            "displayName": "Otra",
            "role": "caregiver",
            "timezone": "UTC",
        },
    )

    assert_problem(response, 409, "email_taken")


@pytest.mark.parametrize(
    ("field", "value"),
    [("timezone", "Marte/Olympus"), ("password", "corta"), ("role", "admin"), ("email", "x")],
)
async def test_register_validates_input(client: AsyncClient, field: str, value: str) -> None:
    payload = {
        "email": unique_email(),
        "password": PASSWORD,
        "displayName": "Ana",
        "role": "patient",
        "timezone": "America/Bogota",
    }
    payload[field] = value

    response = await client.post("/auth/register", json=payload)

    assert_problem(response, 422, "validation_error")
    assert response.json()["errors"][0]["loc"] == ["body", field]


async def test_validation_errors_do_not_echo_the_password(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "no-es-correo", "password": "secreto-xyz-123"}
    )

    assert_problem(response, 422, "validation_error")
    assert "secreto-xyz-123" not in response.text


# ─── Login ────────────────────────────────────────────────────────────────────


async def test_login_returns_access_token_and_cookie(client: AsyncClient) -> None:
    await register(client, email="login@example.com")
    client.cookies.clear()

    response = await client.post(
        "/auth/login", json={"email": "LOGIN@example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "login@example.com"
    assert client.cookies[REFRESH_COOKIE]


@pytest.mark.parametrize("email", ["login@example.com", "nadie@example.com"])
async def test_login_rejects_bad_credentials_without_revealing_which(
    client: AsyncClient, email: str
) -> None:
    await register(client, email="login@example.com")

    response = await client.post("/auth/login", json={"email": email, "password": "incorrecta"})

    assert_problem(response, 401, "invalid_credentials")


# ─── Refresh rotativo ─────────────────────────────────────────────────────────


async def test_refresh_rotates_cookie_and_returns_new_access_token(client: AsyncClient) -> None:
    session = await register(client)
    first_cookie = client.cookies[REFRESH_COOKIE]

    response = await client.post("/auth/refresh")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"accessToken", "tokenType", "expiresIn"}
    assert client.cookies[REFRESH_COOKIE] != first_cookie
    me = await client.get("/users/me", headers={"Authorization": f"Bearer {body['accessToken']}"})
    assert me.json()["id"] == session["user"]["id"]


async def test_refresh_reuse_revokes_whole_family(client: AsyncClient) -> None:
    await register(client)
    stolen = client.cookies[REFRESH_COOKIE]
    assert (await client.post("/auth/refresh")).status_code == 200
    legit = client.cookies[REFRESH_COOKIE]

    # Alguien presenta el token ya rotado: se revoca toda la sesión.
    client.cookies.set(REFRESH_COOKIE, stolen, path="/auth")
    reused = await client.post("/auth/refresh")

    assert_problem(reused, 401, "refresh_token_reused")
    assert "max-age=0" in _set_cookie_header(reused.headers.get_list("set-cookie")).lower()
    client.cookies.set(REFRESH_COOKIE, legit, path="/auth")
    assert_problem(await client.post("/auth/refresh"), 401, "invalid_refresh_token")


async def test_refresh_without_cookie_is_unauthorized(client: AsyncClient) -> None:
    assert_problem(await client.post("/auth/refresh"), 401, "missing_refresh_token")


async def test_refresh_with_unknown_token_clears_cookie(client: AsyncClient) -> None:
    client.cookies.set(REFRESH_COOKIE, "inventado", path="/auth")

    response = await client.post("/auth/refresh")

    assert_problem(response, 401, "invalid_refresh_token")
    assert "max-age=0" in _set_cookie_header(response.headers.get_list("set-cookie")).lower()


# ─── Logout ───────────────────────────────────────────────────────────────────


async def test_logout_revokes_session_and_clears_cookie(client: AsyncClient) -> None:
    await register(client)
    cookie = client.cookies[REFRESH_COOKIE]

    response = await client.post("/auth/logout")

    assert response.status_code == 204
    assert "max-age=0" in _set_cookie_header(response.headers.get_list("set-cookie")).lower()
    client.cookies.set(REFRESH_COOKIE, cookie, path="/auth")
    assert_problem(await client.post("/auth/refresh"), 401, "invalid_refresh_token")


async def test_logout_without_cookie_is_idempotent(client: AsyncClient) -> None:
    assert (await client.post("/auth/logout")).status_code == 204


# ─── Límites de peticiones ────────────────────────────────────────────────────


@pytest.fixture
async def strict_client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    app = app_factory(
        rate_register_ip=RateLimit(limit=1, window_seconds=60),
        rate_login_email=RateLimit(limit=2, window_seconds=60),
    )
    async for c in make_client(app):
        yield c


async def test_register_is_rate_limited_per_ip(strict_client: AsyncClient) -> None:
    await register(strict_client)

    response = await strict_client.post(
        "/auth/register",
        json={
            "email": unique_email(),
            "password": PASSWORD,
            "displayName": "Ana",
            "role": "patient",
            "timezone": "UTC",
        },
    )

    assert_problem(response, 429, "rate_limited")
    assert 0 < int(response.headers["retry-after"]) <= 60


async def test_login_is_rate_limited_per_email(strict_client: AsyncClient) -> None:
    credentials = {"email": "victima@example.com", "password": "incorrecta"}
    for _ in range(2):
        assert (await strict_client.post("/auth/login", json=credentials)).status_code == 401

    assert_problem(await strict_client.post("/auth/login", json=credentials), 429, "rate_limited")


# ─── CORS ─────────────────────────────────────────────────────────────────────


async def test_cors_allows_credentials_only_for_web_origin(client: AsyncClient) -> None:
    preflight = {"Access-Control-Request-Method": "POST"}

    allowed = await client.options(
        "/auth/refresh", headers={"Origin": "http://localhost:5173", **preflight}
    )
    denied = await client.options(
        "/auth/refresh", headers={"Origin": "https://evil.example", **preflight}
    )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in denied.headers


async def test_access_token_works_as_bearer(client: AsyncClient) -> None:
    session = await register(client)

    assert (await client.get("/users/me", headers=bearer(session))).status_code == 200
