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


def forwarded_ip(forwarded_for: list[str], peer: str, hops: int) -> str:
    """IP del cliente tras `hops` proxies de confianza. Cada proxy añade al final de
    `X-Forwarded-For` la IP que lo llamó, así que el cliente es la entrada `hops` desde el final;
    lo que hay antes lo escribe el cliente y no es fiable. Con menos entradas, la más antigua.

    ponytail: quien llame directo a Heroku (sin Vercel) puede inventar una entrada y cambiar de
    clave de límite; los límites por correo y por usuario siguen valiendo. Si hace falta cerrarlo,
    que Vercel añada una cabecera secreta y solo entonces se cuenten 2 saltos."""
    entries = [ip.strip() for value in forwarded_for for ip in value.split(",") if ip.strip()]
    if hops <= 0 or not entries:
        return peer
    return entries[-min(hops, len(entries))]


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    return forwarded_ip(
        request.headers.getlist("x-forwarded-for"), peer, _settings(request).trusted_proxy_hops
    )


SettingsDep = Annotated[Settings, Depends(_settings)]
EngineDep = Annotated[AsyncEngine, Depends(_engine)]
RedisDep = Annotated[Redis, Depends(_redis)]
SessionDep = Annotated[AsyncSession, Depends(_session)]
RateLimiterDep = Annotated[RateLimiter, Depends(_rate_limiter)]
PasswordsDep = Annotated[PasswordService, Depends(_passwords)]
AccessTokensDep = Annotated[AccessTokenService, Depends(_access_tokens)]
ClientIpDep = Annotated[str, Depends(client_ip)]
