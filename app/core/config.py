from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RateLimit(BaseModel):
    """`limit` peticiones cada `window_seconds` segundos."""

    limit: int
    window_seconds: int


class Settings(BaseSettings):
    """Configuración leída solo de variables de entorno (y `.env` en desarrollo)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_nested_delimiter="__", extra="ignore"
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    database_url: PostgresDsn
    redis_url: RedisDsn

    db_pool_size: int = 10
    db_max_overflow: int = 10
    dependency_timeout_seconds: float = 2.0

    problem_type_base: str = "https://api.example.com/problems/"

    # Origen del panel web: único origen con CORS y credenciales (cookie de refresh).
    web_origin: str = "http://localhost:5173"

    # Tokens
    jwt_secret: SecretStr
    jwt_issuer: str = "back-smartwatch"
    access_token_ttl_seconds: int = 900
    user_refresh_ttl_days: int = 30
    device_refresh_idle_days: int = 90

    # Argon2id (valores por defecto de argon2-cffi / RFC 9106 "low memory").
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4

    # Límites de peticiones
    rate_register_ip: RateLimit = RateLimit(limit=5, window_seconds=3600)
    rate_login_ip: RateLimit = RateLimit(limit=20, window_seconds=900)
    rate_login_email: RateLimit = RateLimit(limit=5, window_seconds=900)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # campos obligatorios salen del entorno
