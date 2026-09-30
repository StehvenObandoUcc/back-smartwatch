import asyncio

import pytest

from app.modules.health.service import DependencyUnavailableError, HealthService


class FakeProbe:
    def __init__(self, *, db_error: Exception | None = None, redis_delay: float = 0.0) -> None:
        self.db_error = db_error
        self.redis_delay = redis_delay

    async def ping_database(self) -> None:
        if self.db_error:
            raise self.db_error

    async def ping_redis(self) -> None:
        await asyncio.sleep(self.redis_delay)


async def test_readiness_ok_when_all_dependencies_respond() -> None:
    result = await HealthService(FakeProbe(), timeout_seconds=1).readiness()

    assert result.status == "ok"
    assert result.checks.database == "ok"
    assert result.checks.redis == "ok"


async def test_readiness_fails_when_database_raises() -> None:
    service = HealthService(FakeProbe(db_error=ConnectionError("down")), timeout_seconds=1)

    with pytest.raises(DependencyUnavailableError) as exc_info:
        await service.readiness()

    assert exc_info.value.dependencies == ["database"]


async def test_readiness_fails_when_redis_times_out() -> None:
    service = HealthService(FakeProbe(redis_delay=1), timeout_seconds=0.01)

    with pytest.raises(DependencyUnavailableError) as exc_info:
        await service.readiness()

    assert exc_info.value.dependencies == ["redis"]


async def test_readiness_reports_every_failed_dependency() -> None:
    probe = FakeProbe(db_error=OSError("down"), redis_delay=1)

    with pytest.raises(DependencyUnavailableError) as exc_info:
        await HealthService(probe, timeout_seconds=0.01).readiness()

    assert exc_info.value.dependencies == ["database", "redis"]
