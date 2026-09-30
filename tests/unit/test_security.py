import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.logging import _redact
from app.core.security import (
    AccessClaims,
    AccessTokenService,
    InvalidTokenError,
    PasswordService,
    TokenType,
    hash_token,
    new_opaque_token,
)


def test_access_token_round_trip() -> None:
    tokens = AccessTokenService(get_settings())
    patient_id = uuid.uuid4()
    claims = AccessClaims(subject=uuid.uuid4(), token_type=TokenType.DEVICE, patient_id=patient_id)

    decoded = tokens.decode(tokens.issue(claims, datetime.now(UTC)))

    assert decoded == claims


def test_access_token_expires() -> None:
    tokens = AccessTokenService(get_settings())
    issued = datetime.now(UTC) - timedelta(seconds=tokens.ttl_seconds + 5)
    token = tokens.issue(
        AccessClaims(subject=uuid.uuid4(), token_type=TokenType.USER, role="patient"), issued
    )

    with pytest.raises(InvalidTokenError):
        tokens.decode(token)


def test_access_token_rejects_other_secret() -> None:
    settings = get_settings()
    other = AccessTokenService(
        settings.model_copy(
            update={"jwt_secret": SecretStr("otro-secreto-distinto-0123456789abcdef0123")}
        )
    )
    token = other.issue(
        AccessClaims(subject=uuid.uuid4(), token_type=TokenType.USER, role="patient"),
        datetime.now(UTC),
    )

    with pytest.raises(InvalidTokenError):
        AccessTokenService(settings).decode(token)


async def test_password_hash_and_verify() -> None:
    passwords = PasswordService(get_settings())
    hashed = await passwords.hash("contraseña-segura")

    assert hashed.startswith("$argon2id$")
    assert await passwords.verify(hashed, "contraseña-segura")
    assert not await passwords.verify(hashed, "otra")
    assert not await passwords.verify(None, "contraseña-segura")
    assert not await passwords.verify("no-es-un-hash", "x")


def test_opaque_tokens_are_random_and_hashed() -> None:
    first, second = new_opaque_token(), new_opaque_token()

    assert first != second
    assert len(hash_token(first)) == 64
    assert hash_token(first) != first


def test_logs_redact_secrets() -> None:
    event = _redact(None, "info", {"event": "x", "password": "p", "refresh_token": "r", "ok": 1})

    assert event == {"event": "x", "password": "[REDACTED]", "refresh_token": "[REDACTED]", "ok": 1}
