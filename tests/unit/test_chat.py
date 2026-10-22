import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.chat.prompt import (
    CREATE_MEDICATION_TOOL,
    PromptDose,
    build_system_prompt,
    describe_medication,
    shorten_for_watch,
)
from app.modules.chat.provider import (
    DeepSeekProvider,
    FakeChatProvider,
    ProviderError,
    StreamEvent,
    ToolCall,
    Turn,
)
from app.modules.chat.schemas import WatchChatRequest
from app.modules.chat.service import _draft

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


# ─── Acciones del asistente en el reloj ───────────────────────────────────────


@pytest.mark.parametrize(
    ("times", "days", "expected"),
    [
        (["08:00"], [1, 2, 3, 4, 5, 6, 7], "Losartán 50 mg a las 08:00 todos los días"),
        (
            ["08:00", "20:00"],
            [1, 3, 5],
            "Losartán 50 mg a las 08:00 y 20:00 los lunes, miércoles y viernes",
        ),
        (
            ["07:00", "15:00", "23:00"],
            [7],
            "Losartán 50 mg a las 07:00, 15:00 y 23:00 los domingos",
        ),
    ],
)
def test_medication_summary_reads_naturally(
    times: list[str], days: list[int], expected: str
) -> None:
    assert describe_medication("Losartán", "50 mg", times, days) == expected


def test_only_the_watch_prompt_can_add_medications() -> None:
    assert "create_medication" in build_system_prompt(NOW, [], short=True)
    assert "create_medication" not in build_system_prompt(NOW, [], short=False)


@pytest.mark.parametrize("body", [{}, {"message": "hola", "confirmActionId": str(uuid.uuid4())}])
def test_watch_request_needs_exactly_one_of_message_or_confirmation(body: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        WatchChatRequest.model_validate(body)


def test_tool_arguments_are_validated_like_the_web() -> None:
    valid = ToolCall(
        "create_medication", '{"name": "Losartán", "dosage": "50 mg", "times": ["08:00"]}'
    )
    draft = _draft([ToolCall("otra", "{}"), valid])

    assert draft is not None
    assert draft.days_of_week == [1, 2, 3, 4, 5, 6, 7]
    assert (
        _draft([ToolCall("create_medication", '{"name": "X", "dosage": "1", "times": ["8"]}')])
        is None
    )
    assert _draft([ToolCall("create_medication", "no es json")]) is None
    assert _draft([]) is None


def _tool_chunk(index: int, name: str | None, arguments: str) -> str:
    function = {"arguments": arguments} | ({"name": name} if name else {})
    call = {"index": index, "function": function} | (
        {"id": "c1", "type": "function"} if name else {}
    )
    body = {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "delta": {"tool_calls": [call]}}],
    }
    return f"data: {json.dumps(body)}\n\n"


async def test_deepseek_sends_tools_and_joins_streamed_tool_calls() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        sse = (
            _tool_chunk(0, "create_medication", '{"name": "Los')
            + _tool_chunk(0, None, 'artán", "dosage": "50 mg", "times": ["08:00"]}')
            + "data: [DONE]\n\n"
        )
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse)

    provider = _deepseek(httpx2.MockTransport(handler))
    events = [
        e
        async for e in provider.stream(
            "s", [Turn("user", "agrega")], max_tokens=5, tools=[CREATE_MEDICATION_TOOL]
        )
    ]

    [call] = [e.tool_call for e in events if e.tool_call]
    assert call.name == "create_medication"
    assert json.loads(call.arguments)["name"] == "Losartán"
    assert json.loads(seen[0].content)["tools"][0]["function"]["name"] == "create_medication"
    await provider.aclose()


async def test_deepseek_without_tools_does_not_send_them() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content="data: [DONE]\n\n"
        )

    provider = _deepseek(httpx2.MockTransport(handler))
    _ = [e async for e in provider.stream("s", [Turn("user", "hola")], max_tokens=5)]

    assert "tools" not in json.loads(seen[0].content)
    await provider.aclose()
