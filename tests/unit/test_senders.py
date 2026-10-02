import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.notifications.senders import (
    ConsoleEmailSender,
    DeliveryError,
    Message,
    ResendEmailSender,
)

MESSAGE = Message(
    kind="verify_email",
    payload={"to": "ana@example.com", "link": "https://web.example/verify-email?token=abc"},
    dedupe_key="verify_email:1",
)


def _sender(handler: httpx.MockTransport) -> ResendEmailSender:
    return ResendEmailSender(
        httpx.AsyncClient(transport=handler), "re_clave_de_prueba", "App <a@b.c>"
    )


async def test_resend_posts_the_rendered_email_with_an_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "x"})

    await _sender(httpx.MockTransport(handler)).send(MESSAGE)

    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer re_clave_de_prueba"
    assert request.headers["idempotency-key"] == "verify_email:1"
    assert body["to"] == ["ana@example.com"]
    assert body["from"] == "App <a@b.c>"
    assert "token=abc" in body["text"]


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (500, True), (503, True), (401, False), (422, False)]
)
async def test_resend_errors_are_classified_without_leaking_the_response(
    status: int, retryable: bool
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, json={"message": "ana@example.com token=abc"})
    )

    with pytest.raises(DeliveryError) as error:
        await _sender(transport).send(MESSAGE)

    assert error.value.retryable is retryable
    assert error.value.reason == f"resend: http {status}"


async def test_resend_network_errors_are_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("lento")

    with pytest.raises(DeliveryError) as error:
        await _sender(httpx.MockTransport(handler)).send(MESSAGE)

    assert error.value.retryable is True
    assert error.value.reason == "resend: ConnectTimeout"


async def test_console_sender_does_not_need_the_network() -> None:
    await ConsoleEmailSender().send(MESSAGE)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_resend_provider_requires_its_key() -> None:
    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        _settings(email_provider="resend")

    assert _settings(email_provider="resend", resend_api_key="re_x").email_provider == "resend"


@pytest.mark.parametrize("env", ["staging", "production"])
def test_console_provider_is_refused_outside_local_and_test(env: str) -> None:
    with pytest.raises(ValidationError, match="solo local y test"):
        _settings(app_env=env, email_provider="console")
