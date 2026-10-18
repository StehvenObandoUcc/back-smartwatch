import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger

logger = get_logger("app.access")

REQUEST_ID_HEADER = b"x-request-id"


class RequestContextMiddleware:
    """Asigna un `X-Request-ID` y registra una línea de acceso por petición.

    ASGI puro (sin BaseHTTPMiddleware) para no añadir latencia. Solo registra método,
    ruta, estado y duración: nunca cuerpos, cabeceras ni query strings.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER)
        request_id = _sanitize(incoming) or str(uuid.uuid4())
        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((REQUEST_ID_HEADER, request_id.encode()))
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                logger.info(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round((time.perf_counter() - start) * 1000, 2),
                )


SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    # HSTS: el navegador solo habla por HTTPS con la API (Heroku ya sirve solo HTTPS).
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    # Respuestas JSON de una API: nada de scripts ni de marcos.
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    # Datos de salud: sin caché intermedia (el endpoint del chat ya fija el suyo).
    (b"cache-control", b"no-store"),
)


class SecurityHeadersMiddleware:
    """Añade cabeceras de seguridad a cada respuesta HTTP que no las traiga ya."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in SECURITY_HEADERS if h[0] not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _sanitize(value: bytes | None) -> str | None:
    if not value or len(value) > 64:
        return None
    text = value.decode("latin-1")
    return text if all(c.isalnum() or c in "-_" for c in text) else None
