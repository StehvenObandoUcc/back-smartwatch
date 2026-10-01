"""Errores en formato RFC 9457 (`application/problem+json`)."""

from http import HTTPStatus
from typing import Any, ClassVar, cast
from urllib.parse import quote

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
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.extra = extra or {}
        self.headers = headers or {}


class DomainError(AppError):
    """Base de los errores que lanzan los servicios.

    Los servicios no conocen HTTP: eligen la clase según el significado (no existe, conflicto…)
    y aquí se decide el estado HTTP.
    """

    status_code: ClassVar[int] = 400
    default_title: ClassVar[str] = "Petición no válida"

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        title: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status=self.status_code,
            code=code,
            title=title or self.default_title,
            detail=detail,
            headers=headers,
        )


class InvalidRequestError(DomainError):
    status_code = 400
    default_title = "Petición no válida"


class UnauthorizedError(DomainError):
    status_code = 401
    default_title = "No autenticado"

    def __init__(self, code: str, detail: str, *, title: str | None = None) -> None:
        super().__init__(code, detail, title=title, headers={"WWW-Authenticate": "Bearer"})


class ForbiddenError(DomainError):
    status_code = 403
    default_title = "Sin permiso"


class NotFoundError(DomainError):
    status_code = 404
    default_title = "No encontrado"


class ConflictError(DomainError):
    status_code = 409
    default_title = "Conflicto"


class UnprocessableError(DomainError):
    """422 con la misma forma que un error de esquema, para reglas que cruzan campos o estado."""

    status_code = 422
    default_title = "Datos no válidos"

    def __init__(self, loc: list[str | int], msg: str) -> None:
        super().__init__("validation_error", "La petición no cumple las reglas del recurso.")
        self.extra = {"errors": [{"loc": loc, "msg": msg, "type": "value_error"}]}


class RateLimitedError(DomainError):
    status_code = 429
    default_title = "Demasiadas peticiones"

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "rate_limited",
            "Has superado el límite de peticiones. Inténtalo más tarde.",
            headers={"Retry-After": str(retry_after)},
        )


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


def problem_from(error: AppError, instance: str) -> ProblemResponse:
    response = problem(
        status=error.status,
        code=error.code,
        title=error.title,
        detail=error.detail,
        instance=instance,
        extra=error.extra,
    )
    response.headers.update(error.headers)
    return response


def instance_of(request: Request) -> str:
    """Ruta de la petición como URI válida (percent-encoded) para el campo `instance`."""
    return quote(request.url.path, safe="/")


async def _app_error_handler(request: Request, exc: Exception) -> Response:
    return problem_from(cast(AppError, exc), instance_of(request))


# FastAPI responde 400 con este detalle cuando el cuerpo no se puede decodificar (bytes que no
# son JSON/UTF-8). El contrato trata toda petición mal formada como 422 validation_error.
_BODY_PARSE_ERROR = "There was an error parsing the body"


async def _http_error_handler(request: Request, exc: Exception) -> Response:
    exc = cast(StarletteHTTPException, exc)
    if exc.status_code == 400 and exc.detail == _BODY_PARSE_ERROR:
        return _validation_problem(
            request, [{"loc": ["body"], "msg": "JSON mal formado", "type": "json_invalid"}]
        )
    phrase = HTTPStatus(exc.status_code).phrase
    response = problem(
        status=exc.status_code,
        code=phrase.lower().replace(" ", "_").replace("-", "_"),
        title=phrase,
        detail=str(exc.detail) if exc.detail else phrase,
        instance=instance_of(request),
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
    return _validation_problem(request, errors)


def _validation_problem(request: Request, errors: list[dict[str, Any]]) -> Response:
    return problem(
        status=422,
        code="validation_error",
        title="Datos no válidos",
        detail="La petición no cumple el esquema.",
        instance=instance_of(request),
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
        instance=instance_of(request),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
