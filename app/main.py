from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.db import create_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.modules.health.router import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Engine y Redis no abren conexiones hasta el primer uso.
        app.state.engine = create_engine(settings)
        app.state.redis = Redis.from_url(str(settings.redis_url))
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.engine.dispose()

    # La documentación interactiva solo en local: el contrato publicado es contracts/openapi.yaml.
    docs_enabled = settings.app_env == "local"
    app = FastAPI(
        title="API de recordatorios de medicamentos",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url="/openapi.json" if docs_enabled else None,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    return app
