"""Configuración segura fuera de local: la clave JWT debe ser larga; lo demás ya se valida."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings

PRODUCTION: dict[str, Any] = {
    "database_url": "postgres://u:p@h:5432/db",
    "redis_url": "rediss://h:6379/0",
    "app_env": "production",
    "email_provider": "resend",
    "resend_api_key": "re_x",
    "chat_provider": "deepseek",
    "deepseek_api_key": "sk-x",
    "problem_type_base": "https://api.real.app/problems/",
}


def _settings(**overrides: Any) -> Settings:
    return Settings(**{**PRODUCTION, **overrides})


def test_short_jwt_secret_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(jwt_secret="corta")


def test_long_jwt_secret_is_accepted_in_production() -> None:
    assert _settings(jwt_secret="x" * 32).app_env == "production"


def test_short_jwt_secret_is_still_fine_locally() -> None:
    local = _settings(
        app_env="local", chat_provider="fake", email_provider="console", jwt_secret="corta"
    )

    assert local.app_env == "local"
