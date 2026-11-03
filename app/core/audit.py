"""Registro de accesos a datos de salud (ver ADR 0012).

Un middleware ASGI puro mira cada petición terminada con éxito y, si fue un acceso a datos de salud
de un paciente, inserta una fila en `health_data_access_log`. Así cubre la web y el reloj sin tocar
los servicios. El principal autenticado lo deja `current_user` / `current_device` en el estado de
la petición; la ruta casada (plantilla) la deja el enrutador de Starlette en `scope["route"]`.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.auth import Principal
from app.core.logging import get_logger
from app.modules.audit.repository import AccessEntry, AccessLogRepository

logger = get_logger("app.audit")

# Primer segmento tras `/patients/{patientId}/` -> recurso registrado.
_PATIENT_RESOURCES = {
    "medications": "medications",
    "plan": "plan",
    "dose-events": "dose_events",
    "dose-history": "dose_history",
    "adherence": "adherence",
    "reports": "reports",
    "chat": "chat",
    "consents": "consents",
    "caregivers": "caregivers",
    "caregiver-invitations": "caregivers",
    "devices": "devices",
}

# Rutas del reloj: el paciente sale de su token, no de la ruta.
_DEVICE_RESOURCES = {
    "/devices/me/plan": "plan",
    "/devices/me/dose-events": "dose_events",
    "/devices/me/chat/messages": "chat",
}

_PATIENT_PARAM = "patientId"


@dataclass(frozen=True, slots=True)
class AuditTarget:
    resource: str
    # True: el paciente va en la ruta (`{patientId}`); False: sale del token del reloj.
    patient_in_path: bool


def classify_route(route_path: str) -> AuditTarget | None:
    """Recurso de salud al que da acceso una ruta (plantilla), o None si no se audita."""
    device_resource = _DEVICE_RESOURCES.get(route_path)
    if device_resource is not None:
        return AuditTarget(device_resource, patient_in_path=False)
    parts = route_path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "patients" or parts[1] != "{" + _PATIENT_PARAM + "}":
        return None
    if len(parts) == 2:
        return AuditTarget("patient", patient_in_path=True)
    if parts[2] == "medications" and "schedules" in parts[3:]:
        return AuditTarget("schedules", patient_in_path=True)
    return AuditTarget(_PATIENT_RESOURCES.get(parts[2], "patient"), patient_in_path=True)


def classify_action(method: str) -> Literal["read", "write"]:
    return "read" if method in ("GET", "HEAD") else "write"


def build_entry(
    *,
    method: str,
    status: int,
    route_path: str | None,
    path_params: dict[str, str],
    principal: Principal | None,
    request_id: str | None,
) -> AccessEntry | None:
    """La fila a registrar para una petición terminada, o None si no es un acceso auditable."""
    if principal is None or route_path is None or status >= 400:
        return None
    target = classify_route(route_path)
    if target is None:
        return None
    patient_id = principal.patient_id
    if target.patient_in_path:
        try:
            patient_id = uuid.UUID(path_params[_PATIENT_PARAM])
        except (KeyError, ValueError):
            return None
    if patient_id is None:
        return None
    return AccessEntry(
        actor_type=principal.kind,
        actor_id=principal.id,
        patient_id=patient_id,
        action=classify_action(method),
        resource=target.resource,
        method=method,
        status=status,
        request_id=request_id,
    )


class AuditMiddleware:
    """Registra los accesos a datos de salud. ASGI puro: no guarda en búfer ni toca la respuesta.

    Solo observa `http.response.start` para saber el estado (también sirve con SSE: el flujo no se
    retiene). Va dentro de `RequestContextMiddleware` para tener el `request_id`. Si el insert
    falla se registra `audit_write_failed` y se sigue: la respuesta ya salió.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_code = 0
        # El estado debe existir antes de la petición: `request.state` escribe en este dict.
        scope.setdefault("state", {})

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)
        if 0 < status_code < 400:
            await self._record(scope, status_code)

    async def _record(self, scope: Scope, status_code: int) -> None:
        try:
            route = scope.get("route")
            entry = build_entry(
                method=scope["method"],
                status=status_code,
                route_path=getattr(route, "path", None),
                path_params=scope.get("path_params", {}),
                principal=scope["state"].get("principal"),
                request_id=structlog.contextvars.get_contextvars().get("request_id"),
            )
            if entry is None:
                return
            factory = scope["app"].state.session_factory
            async with factory() as session:
                await AccessLogRepository(session).add(entry)
                await session.commit()
        except Exception as exc:
            logger.error("audit_write_failed", error=type(exc).__name__)
