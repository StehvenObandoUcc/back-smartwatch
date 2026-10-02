import pytest

from app.modules.notifications.templates import render_email


@pytest.mark.parametrize("kind", ["verify_email", "reset_password"])
def test_email_templates_include_the_link(kind: str) -> None:
    subject, body = render_email(kind, {"link": "https://web.example/x?token=abc"})

    assert subject
    assert "https://web.example/x?token=abc" in body


def test_unknown_template_fails_loudly() -> None:
    with pytest.raises(ValueError, match="desconocida"):
        render_email("nope", {})
