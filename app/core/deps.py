from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.core.security import AccessTokenService, PasswordService


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def _redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


async def _session(request: Request) -> AsyncIterator[AsyncSession]:
    """Una sesión por petición. Los servicios hacen commit; lo no confirmado se descarta."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


def _rate_limiter(request: Request) -> RateLimiter:
    return RateLimiter(_redis(request))


def _passwords(request: Request) -> PasswordService:
    passwords: PasswordService = request.app.state.passwords
    return passwords


def _access_tokens(request: Request) -> AccessTokenService:
    tokens: AccessTokenService = request.app.state.access_tokens
    return tokens


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


SettingsDep = Annotated[Settings, Depends(_settings)]
EngineDep = Annotated[AsyncEngine, Depends(_engine)]
RedisDep = Annotated[Redis, Depends(_redis)]
SessionDep = Annotated[AsyncSession, Depends(_session)]
RateLimiterDep = Annotated[RateLimiter, Depends(_rate_limiter)]
PasswordsDep = Annotated[PasswordService, Depends(_passwords)]
AccessTokensDep = Annotated[AccessTokenService, Depends(_access_tokens)]
ClientIpDep = Annotated[str, Depends(client_ip)]
