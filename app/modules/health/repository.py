from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class DependencyProbe(Protocol):
    async def ping_database(self) -> None: ...

    async def ping_redis(self) -> None: ...


class HealthRepository:
    def __init__(self, engine: AsyncEngine, redis: Redis) -> None:
        self._engine = engine
        self._redis = redis

    async def ping_database(self) -> None:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def ping_redis(self) -> None:
        await self._redis.ping()
