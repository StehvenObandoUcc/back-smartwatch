import itertools
from typing import Any

from httpx import AsyncClient, Response

REFRESH_COOKIE = "__Secure-refresh-token"
PASSWORD = "contraseña-segura-123"

_counter = itertools.count()


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}{next(_counter)}@example.com"


async def register(
    client: AsyncClient,
    *,
    role: str = "patient",
    email: str | None = None,
    display_name: str = "Ana",
    timezone: str = "America/Bogota",
) -> dict[str, Any]:
    """Registra un usuario y devuelve el cuerpo; deja la cookie de refresh en el cliente."""
    response = await client.post(
        "/auth/register",
        json={
            "email": email or unique_email(role),
            "password": PASSWORD,
            "displayName": display_name,
            "role": role,
            "timezone": timezone,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['tokens']['accessToken']}"}


def assert_problem(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == code
