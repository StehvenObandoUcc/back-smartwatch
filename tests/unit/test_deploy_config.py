"""Heroku entrega `postgres://` y Redis con TLS autofirmado: la configuración debe adaptarlos."""

import pytest

from app.core.config import Settings
from app.core.db import sqlalchemy_url
from app.main import redis_tls_options

DSN = "postgres://user:s3cret@host.example:5432/db"


def _settings(**overrides: object) -> Settings:
    return Settings(**{"database_url": DSN, **overrides})  # type: ignore[arg-type]


def test_heroku_postgres_url_gets_the_asyncpg_driver() -> None:
    url = sqlalchemy_url(_settings())

    assert url == "postgresql+asyncpg://user:s3cret@host.example:5432/db"


def test_ssl_is_requested_only_when_enabled() -> None:
    assert sqlalchemy_url(_settings(database_ssl=True)).endswith("/db?ssl=require")
    assert "ssl=" not in sqlalchemy_url(_settings(database_ssl=False))


def test_local_url_already_asyncpg_is_left_alone() -> None:
    local = "postgresql+asyncpg://smartwatch:smartwatch@localhost:5433/smartwatch"

    assert sqlalchemy_url(_settings(database_url=local)) == local


@pytest.mark.parametrize(("skip", "expected"), [(True, {"ssl_cert_reqs": "none"}), (False, {})])
def test_redis_tls_skips_verification_only_on_heroku(skip: bool, expected: dict[str, str]) -> None:
    assert redis_tls_options(_settings(redis_skip_cert_verify=skip)) == expected
