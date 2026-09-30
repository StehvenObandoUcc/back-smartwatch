"""Errores en formato RFC 9457 (`application/problem+json`)."""

from http import HTTPStatus
from typing import Any, cast

import orjson
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.core.config import get_settings
from app.core.logging import get_logger

PROBLEM_JSON = "application/problem+json"

logger = get_logger(__name__)


class ProblemResponse(Response):
    media_type = PROBLEM_JSON

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)


class AppError(Exception):
    """Error de dominio que se traduce a problem+json.

    `code` es estable y legible por máquina (snake_case); el slug del `type` se deriva de él.
    """

    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.extra = extra or {}


def problem(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str | None = None,
    extra: dict[str, Any] | None = None,
) -> ProblemResponse:
    body: dict[str, Any] = {
        "type": get_settings().problem_type_base + code.replace("_", "-"),
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }
    if instance is not None:
        body["instance"] = instance
    if extra:
        body.update(extra)
    return ProblemResponse(content=body, status_code=status)


async def _app_error_handler(request: Request, exc: Exception) -> Response:
    exc = cast(AppError, exc)
    return problem(
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        instance=request.url.path,
        extra=exc.extra,
    )


async def _http_error_handler(request: Request, exc: Exception) -> Response:
    exc = cast(StarletteHTTPException, exc)
    phrase = HTTPStatus(exc.status_code).phrase
    response = problem(
        status=exc.status_code,
        code=phrase.lower().replace(" ", "_").replace("-", "_"),
        title=phrase,
        detail=str(exc.detail) if exc.detail else phrase,
        instance=request.url.path,
    )
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_error_handler(request: Request, exc: Exception) -> Response:
    exc = cast(RequestValidationError, exc)
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return problem(
        status=422,
        code="validation_error",
        title="Datos no válidos",
        detail="La petición no cumple el esquema.",
        instance=request.url.path,
        extra={"errors": errors},
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> Response:
    # Solo el tipo de excepción: el mensaje podría contener datos de salud.
    logger.error("unhandled_error", path=request.url.path, error_type=type(exc).__name__)
    return problem(
        status=500,
        code="internal_error",
        title="Error interno",
        detail="Ocurrió un error inesperado.",
        instance=request.url.path,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
