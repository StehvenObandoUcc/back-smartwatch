from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración leída solo de variables de entorno (y `.env` en desarrollo)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    database_url: PostgresDsn
    redis_url: RedisDsn

    db_pool_size: int = 10
    db_max_overflow: int = 10
    dependency_timeout_seconds: float = 2.0

    problem_type_base: str = "https://api.example.com/problems/"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # campos obligatorios salen del entorno
