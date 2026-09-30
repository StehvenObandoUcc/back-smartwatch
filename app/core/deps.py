from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def _redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


SettingsDep = Annotated[Settings, Depends(_settings)]
EngineDep = Annotated[AsyncEngine, Depends(_engine)]
RedisDep = Annotated[Redis, Depends(_redis)]
