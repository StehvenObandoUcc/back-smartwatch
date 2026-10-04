import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.chat.prompt import PromptDose, build_system_prompt, shorten_for_watch
from app.modules.chat.provider import (
    DeepSeekProvider,
    FakeChatProvider,
    ProviderError,
    StreamEvent,
    Turn,
)

BOGOTA = ZoneInfo("America/Bogota")
NOW = datetime(2026, 10, 7, 9, 30, tzinfo=BOGOTA)


def _dose(
    hour: int, status: str = "pendiente", day: int = 7, note: str | None = None
) -> PromptDose:
    return PromptDose(
        datetime(2026, 10, day, hour, 0, tzinfo=BOGOTA), "Metformina", "1 tableta", note, status
    )


# ─── Prompt ───────────────────────────────────────────────────────────────────


def test_prompt_lists_doses_with_local_time_and_status() -> None:
    prompt = build_system_prompt(
        NOW, [_dose(8, "tomada"), _dose(20, note="con comida"), _dose(8, day=8)], short=False
    )

    assert "miércoles 07/10/2026 09:30" in prompt
    assert "- hoy 08:00: Metformina, 1 tableta — tomada" in prompt
    assert "- hoy 20:00: Metformina, 1 tableta (con comida) — pendiente" in prompt
    assert "- mañana 08:00: Metformina, 1 tableta — pendiente" in prompt


def test_prompt_without_doses_says_so() -> None:
    assert "No hay dosis programadas" in build_system_prompt(NOW, [], short=False)


def test_prompt_carries_the_guardrails_and_the_style() -> None:
    web = build_system_prompt(NOW, [], short=False)
    watch = build_system_prompt(NOW, [], short=True)

    for rule in ("No cambies ni sugieras cambiar dosis", "No diagnostiques", "emergencias"):
        assert rule in web
        assert rule in watch
    assert "3 frases" in watch
    assert "3 frases" not in web


def test_prompt_never_contains_identifying_data() -> None:
    # PromptDose no tiene campos de nombre, documento ni contacto: el prompt no puede filtrarlos.
    fields = set(PromptDose.__dataclass_fields__)

    assert fields == {"at", "medication", "dosage", "instructions", "status"}


def test_prompt_caps_the_number_of_doses() -> None:
    doses = [_dose(8) for _ in range(100)]

    assert build_system_prompt(NOW, doses, short=False).count("— pendiente") == 40


# ─── Respuesta corta del reloj ────────────────────────────────────────────────


def test_watch_reply_is_plain_text_of_at_most_three_sentences() -> None:
    text = "**Esta noche** toca la metformina. A las 20:00.\n- Con comida.\n- Sin prisa. Una más."

    assert shorten_for_watch(text) == "Esta noche toca la metformina. A las 20:00. Con comida."


def test_watch_reply_keeps_short_answers_and_handles_empty() -> None:
    assert shorten_for_watch("Hoy no tienes dosis.") == "Hoy no tienes dosis."
    assert shorten_for_watch("  \n ") == ""


# ─── Proveedores ──────────────────────────────────────────────────────────────


async def test_fake_provider_streams_words_and_usage() -> None:
    events = [
        e async for e in FakeChatProvider().stream("sistema", [Turn("user", "hola")], max_tokens=10)
    ]

    assert "".join(e.text or "" for e in events) == "Respuesta simulada a: hola"
    assert events[-1].usage is not None


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _chunk(content: str | None = None, usage: dict[str, int] | None = None) -> str:
    body = {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "deepseek-flash",
        "choices": [] if content is None else [{"index": 0, "delta": {"content": content}}],
    }
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n"


def _deepseek(handler: httpx2.MockTransport) -> DeepSeekProvider:
    settings = _settings(chat_provider="deepseek", deepseek_api_key="sk-clave-de-prueba")
    return DeepSeekProvider(settings, http_client=httpx2.AsyncClient(transport=handler))


async def test_deepseek_streams_text_and_usage_through_the_openai_sdk() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        sse = (
            _chunk("Hola ")
            + _chunk("mundo")
            + _chunk(usage={"prompt_tokens": 40, "completion_tokens": 2, "total_tokens": 42})
            + "data: [DONE]\n\n"
        )
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse)

    provider = _deepseek(httpx2.MockTransport(handler))
    events = [
        e
        async for e in provider.stream(
            "sistema",
            [Turn("user", "antes"), Turn("assistant", "ok"), Turn("user", "ahora")],
            max_tokens=77,
        )
    ]

    assert "".join(e.text or "" for e in events) == "Hola mundo"
    usage = events[-1].usage
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (40, 2)
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-clave-de-prueba"
    assert body["model"] == "deepseek-flash"
    assert body["max_tokens"] == 77
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["messages"][0]["content"] == "sistema"
    await provider.aclose()


@pytest.mark.parametrize("status", [401, 429, 500, 503])
async def test_deepseek_http_errors_become_provider_errors_without_leaking(status: int) -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(status, json={"error": {"message": "sk-clave-de-prueba hola"}})
    )

    with pytest.raises(ProviderError) as error:
        _ = [
            e async for e in _deepseek(transport).stream("s", [Turn("user", "hola")], max_tokens=5)
        ]

    assert "sk-clave" not in error.value.reason
    assert "hola" not in error.value.reason


async def test_deepseek_network_errors_become_provider_errors() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("sin red")

    with pytest.raises(ProviderError):
        _ = [
            e
            async for e in _deepseek(httpx2.MockTransport(handler)).stream(
                "s", [Turn("user", "hola")], max_tokens=5
            )
        ]


async def test_stream_event_defaults() -> None:
    assert StreamEvent().text is None


# ─── Configuración ────────────────────────────────────────────────────────────


def test_deepseek_requires_its_key() -> None:
    with pytest.raises(ValidationError, match="DEEPSEEK_API_KEY"):
        _settings(chat_provider="deepseek")


@pytest.mark.parametrize("env", ["staging", "production"])
def test_fake_chat_provider_is_refused_outside_local_and_test(env: str) -> None:
    with pytest.raises(ValidationError, match="solo local y test"):
        _settings(app_env=env, email_provider="resend", resend_api_key="re_x", chat_provider="fake")


def test_placeholder_problem_type_base_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="PROBLEM_TYPE_BASE"):
        _settings(
            app_env="production",
            email_provider="resend",
            resend_api_key="re_x",
            chat_provider="deepseek",
            deepseek_api_key="sk-x",
        )
