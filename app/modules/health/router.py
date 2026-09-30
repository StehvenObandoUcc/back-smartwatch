from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import EngineDep, RedisDep, SettingsDep
from app.core.errors import PROBLEM_JSON, AppError
from app.modules.health.repository import HealthRepository
from app.modules.health.schemas import HealthOut, ReadinessOut
from app.modules.health.service import DependencyUnavailableError, HealthService

router = APIRouter(prefix="/health", tags=["health"])

_DEPENDENCY_NAMES = {"database": "Postgres", "redis": "Redis"}


def get_health_service(engine: EngineDep, redis: RedisDep, settings: SettingsDep) -> HealthService:
    return HealthService(HealthRepository(engine, redis), settings.dependency_timeout_seconds)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]


@router.get("", operation_id="getHealth", summary="Liveness")
async def get_health() -> HealthOut:
    return HealthOut()


@router.get(
    "/ready",
    operation_id="getReadiness",
    summary="Readiness",
    responses={
        503: {"description": "Alguna dependencia no responde.", "content": {PROBLEM_JSON: {}}}
    },
)
async def get_readiness(service: HealthServiceDep) -> ReadinessOut:
    try:
        return await service.readiness()
    except DependencyUnavailableError as exc:
        names = ", ".join(_DEPENDENCY_NAMES[d] for d in exc.dependencies)
        raise AppError(
            status=503,
            code="dependency_unavailable",
            title="Dependencia no disponible",
            detail=f"{names} no responde.",
        ) from exc
