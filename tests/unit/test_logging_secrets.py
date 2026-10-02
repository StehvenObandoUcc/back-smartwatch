import logging

from app.core.logging import configure_logging


def test_http_client_loggers_do_not_print_urls_with_the_bot_token() -> None:
    configure_logging("INFO", json=False)

    # httpx registra "HTTP Request: POST https://api.telegram.org/bot<TOKEN>/sendMessage" en INFO.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
