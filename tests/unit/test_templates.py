from typing import Any

import pytest

from app.modules.notifications.service import mask_email, parse_update
from app.modules.notifications.templates import render_email, render_telegram


@pytest.mark.parametrize("kind", ["verify_email", "reset_password"])
def test_email_templates_include_the_link(kind: str) -> None:
    subject, body = render_email(kind, {"link": "https://web.example/x?token=abc"})

    assert subject
    assert "https://web.example/x?token=abc" in body


def test_unknown_template_fails_loudly() -> None:
    with pytest.raises(ValueError, match="desconocida"):
        render_email("nope", {})


@pytest.mark.parametrize("kind", ["telegram_linked", "telegram_link_failed", "telegram_unlinked"])
def test_telegram_templates_have_text(kind: str) -> None:
    assert render_telegram(kind, {"chat_id": 1})


def test_unknown_telegram_template_fails_loudly() -> None:
    with pytest.raises(ValueError, match="desconocida"):
        render_telegram("nope", {})


def test_mask_email_keeps_only_the_first_letter_and_the_domain() -> None:
    assert mask_email("ana.perez@example.com") == "a***@example.com"


def test_parse_update_accepts_private_text_messages_only() -> None:
    private: dict[str, Any] = {
        "update_id": 7,
        "message": {
            "chat": {"id": 99, "type": "private"},
            "from": {"username": "ana"},
            "text": "/start abc",
        },
    }

    parsed = parse_update(private)

    assert parsed is not None
    assert (parsed.update_id, parsed.chat_id, parsed.text, parsed.username) == (
        7,
        99,
        "/start abc",
        "ana",
    )
    assert (
        parse_update(
            {**private, "message": {**private["message"], "chat": {"id": 1, "type": "group"}}}
        )
        is None
    )
    assert parse_update({"update_id": 1}) is None
