"""Cookie del refresh token de la sesión web: nombre, ruta y borrado, compartidos entre módulos."""

from starlette.responses import Response

REFRESH_COOKIE = "__Secure-refresh-token"
REFRESH_COOKIE_PATH = "/auth"


def clear_refresh_cookie(response: Response) -> None:
    """Pide al navegador que borre la cookie del refresh token (mismos atributos que al crearla)."""
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="strict"
    )
