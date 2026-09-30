import asyncio
from collections.abc import Awaitable, Callable

from app.core.logging import get_logger
from app.modules.health.repository import DependencyProbe
from app.modules.health.schemas import ReadinessChecks, ReadinessOut

logger = get_logger(__name__)


class DependencyUnavailableError(Exception):
    def __init__(self, dependencies: list[str]) -> None:
        super().__init__(", ".join(dependencies))
        self.dependencies = dependencies


class HealthService:
    def __init__(self, probe: DependencyProbe, timeout_seconds: float) -> None:
        self._probe = probe
        self._timeout = timeout_seconds

    async def readiness(self) -> ReadinessOut:
        """Comprueba Postgres y Redis en paralelo; falla si alguno no responde a tiempo."""
        results = await asyncio.gather(
            self._check("database", self._probe.ping_database),
            self._check("redis", self._probe.ping_redis),
        )
        failed = [name for name, ok in results if not ok]
        if failed:
            raise DependencyUnavailableError(failed)
        return ReadinessOut(checks=ReadinessChecks())

    async def _check(self, name: str, ping: Callable[[], Awaitable[None]]) -> tuple[str, bool]:
        try:
            async with asyncio.timeout(self._timeout):
                await ping()
        except Exception as exc:
            logger.warning("dependency_unavailable", dependency=name, error_type=type(exc).__name__)
            return name, False
        return name, True
